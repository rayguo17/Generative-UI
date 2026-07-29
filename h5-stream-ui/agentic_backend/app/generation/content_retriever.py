"""
Content Retriever — LLM-based data retrieval per section from the context store.

Strategy (LLM-primary):
  1. Load the full user input from ContextStore for this session.
  2. If context + prompt fits within the token budget → single LLM call to
     extract data values for the section's field paths.
  3. If context exceeds budget → recursive chunking:
     a. Split context into token-sized overlapping chunks
     b. For each chunk, call LLM to extract matching data
     c. Merge results: non-null values from any chunk fill gaps,
        array items accumulate across chunks
  4. Fall back to working_query if context store is empty or all calls fail.

The output is a dict mapping field_path → resolved value, fed into the
Component Generator (Agent B).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from app.config import AppConfig, LlmConfig
from app.shared.llm_client import LlmClient
from app.utils.context_store import ContextStore
from app.utils.token_counter import count_tokens

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

# Chunk size for recursive retrieval (tokens per chunk)
CHUNK_TOKENS = 2500
# Overlap between chunks to avoid splitting data items
CHUNK_OVERLAP = 100
# Threshold: if context > this fraction of token_budget, chunk
BUDGET_THRESHOLD = 0.50
# Maximum depth for recursive calls (safety limit)
MAX_DEPTH = 3

RETRIEVER_SYSTEM_PROMPT = """You extract specific data values from a user's input for a UI component.
Given field paths to resolve and the full text (or a chunk of it), return a JSON
object mapping each field_path to its resolved value.

## Rules
- Map each field_path to the actual value found in the text.
- If a field_path represents an array (contains `[]`), return ALL items found:
  use keys like `$.items[0].name`, `$.items[1].name`, etc.
  Also include a `$.items.length` key with the total count.
- If a value cannot be found in this text, set it to null.
- DO NOT fabricate values — only extract what exists in the source.
- For image URLs: verify they start with http/https/data:image
- For numeric values: keep the original formatting
- For dates/times: keep as-is

## Output
Return ONLY a flat JSON object keyed by field_path. No markdown. No commentary.
Start with '{' and end with '}'."""


async def retrieve_section_data(
    section: dict,
    *,
    session_id: str,
    working_query: str,
    plan_data_summary: dict | None = None,
    context_store: ContextStore,
    config: AppConfig,
    interaction_logger: "LlmInteractionLogger | None" = None,
) -> dict:
    """Retrieve relevant data for one section from the context store using LLM.

    Primary strategy: LLM-based extraction with recursive chunking for large inputs.
    No programmatic regex fallback — the LLM is always called.

    Args:
        section: Section spec dict from the plan (section_type, data_bindings, etc.)
        session_id: Session ID for context store lookup.
        working_query: The current working query (original or indexed).
        plan_data_summary: The plan's data_summary dict (backup data).
        context_store: ContextStore instance for searching full input.
        config: AppConfig for LLM client setup.
        interaction_logger: Optional interaction logger.

    Returns:
        Dict mapping field_paths (e.g. "$.title", "$.items[0].name") to values.
    """
    data_bindings = section.get("data_bindings", [])
    if not data_bindings:
        return {}

    field_paths = [b.get("field_path", "") for b in data_bindings if b.get("field_path")]
    if not field_paths:
        return {}

    section_type = section.get("section_type", "unknown")

    # Load full context from store
    full_text = context_store.load(session_id)
    if not full_text:
        logger.info("Retriever [%s]: context store empty, using working_query", section_type)
        full_text = working_query

    context_tokens = count_tokens(full_text)
    threshold = int(config.token_budget * BUDGET_THRESHOLD)

    logger.info("Retriever [%s]: context=%d tokens, threshold=%d, %d field_paths",
                 section_type, context_tokens, threshold, len(field_paths))

    # Build the LLM client
    llm = LlmClient(
        LlmConfig(
            base_url=config.local.base_url,
            api_key=config.local.api_key,
            model=config.local.model,
        ),
        token_budget=config.token_budget,
        supports_json_mode=False,
        thinking_enabled=False,  # Disable reasoning to save output tokens
        interaction_logger=interaction_logger,
        log_label=f"retrieve_{section_type}",
    )

    # Estimate prompt overhead
    prompt_overhead = count_tokens(RETRIEVER_SYSTEM_PROMPT) + 200  # +user prompt boilerplate
    available_for_context = max(threshold - prompt_overhead, 500)

    if context_tokens <= available_for_context:
        # ── Single-pass retrieval ──────────────────────────────
        resolved = await _single_retrieve(
            full_text, field_paths, section_type, llm,
        )
    else:
        # ── Recursive chunked retrieval ────────────────────────
        logger.info("Retriever [%s]: context too large (%d > %d), chunking",
                     section_type, context_tokens, available_for_context)
        resolved = await _recursive_retrieve(
            full_text, field_paths, section_type, llm,
            chunk_tokens=CHUNK_TOKENS,
            depth=0,
        )

    # Fill gaps from plan_data_summary
    if plan_data_summary:
        for fp in field_paths:
            if resolved.get(fp) is None:
                key = _field_path_to_key(fp)
                if key in plan_data_summary:
                    resolved[fp] = str(plan_data_summary[key])

    # Final fallback markers
    for fp in field_paths:
        if resolved.get(fp) is None:
            resolved[fp] = f"[context store: {fp}]"

    resolved_count = sum(1 for v in resolved.values() if v is not None and not str(v).startswith("[context store"))
    logger.info("Retriever [%s]: %d/%d paths resolved (%.0f%%)",
                 section_type, resolved_count, len(field_paths),
                 (resolved_count / max(len(field_paths), 1)) * 100)

    return resolved


# ── Single-pass retrieval ──────────────────────────────────────────

async def _single_retrieve(
    context: str,
    field_paths: list[str],
    section_type: str,
    llm: LlmClient,
) -> dict:
    """Extract data values in a single LLM call."""
    user_prompt = _build_retrieval_prompt(context, field_paths, section_type, chunk_info="")

    try:
        response = await llm.generate(
            system_prompt=RETRIEVER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=2048,
        )
        return _parse_retrieval_response(response, field_paths)
    except Exception as e:
        logger.error("Single retrieve failed: %s", e)
        return {fp: None for fp in field_paths}


# ── Recursive chunked retrieval ────────────────────────────────────

async def _recursive_retrieve(
    text: str,
    field_paths: list[str],
    section_type: str,
    llm: LlmClient,
    chunk_tokens: int,
    depth: int = 0,
) -> dict:
    """For large contexts: split into chunks, extract from each, merge results."""
    if depth > MAX_DEPTH:
        logger.warning("Retriever: max depth %d reached, falling back to single-pass", MAX_DEPTH)
        return await _single_retrieve(text[:chunk_tokens * 4], field_paths, section_type, llm)

    chunks = _chunk_text(text, chunk_tokens, CHUNK_OVERLAP)
    logger.info("Retriever level %d: %d chunks (target %d tok/chunk)",
                 depth, len(chunks), chunk_tokens)

    if len(chunks) == 1:
        return await _single_retrieve(text, field_paths, section_type, llm)

    # Extract data from each chunk in parallel (up to concurrency limit)
    tasks = [
        _retrieve_from_chunk(chunk, i, len(chunks), field_paths, section_type, llm)
        for i, chunk in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge all chunk results
    merged: dict = {}
    for i, result in enumerate(chunk_results):
        if isinstance(result, Exception):
            logger.error("Chunk %d retrieval failed: %s", i, result)
            continue
        if isinstance(result, dict):
            merged = _merge_retrieval_results(merged, result)
            logger.debug("Chunk %d: %d resolved values after merge", i,
                         sum(1 for v in merged.values() if v is not None))

    return merged


async def _retrieve_from_chunk(
    text: str,
    chunk_idx: int,
    total: int,
    field_paths: list[str],
    section_type: str,
    llm: LlmClient,
) -> dict:
    """Extract data from a single chunk."""
    chunk_info = f"Chunk {chunk_idx + 1}/{total}"
    user_prompt = _build_retrieval_prompt(text, field_paths, section_type, chunk_info)

    try:
        response = await llm.generate(
            system_prompt=RETRIEVER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=1536,
        )
        return _parse_retrieval_response(response, field_paths)
    except Exception as e:
        logger.error("Chunk %d retrieve failed: %s", chunk_idx, e)
        return {fp: None for fp in field_paths}


# ── Prompt building ────────────────────────────────────────────────

def _build_retrieval_prompt(
    context: str,
    field_paths: list[str],
    section_type: str,
    chunk_info: str,
) -> str:
    """Build the user prompt for a retrieval call."""
    chunk_note = f"\n> ⚠️ This is {chunk_info} of the full input. " \
                 f"Some field paths may not appear in this chunk — set those to null.\n" \
        if chunk_info else ""

    # Truncate context if still too large after chunking
    max_context_chars = 6000
    if len(context) > max_context_chars:
        context = context[:max_context_chars] + "\n... (truncated)"

    return (
        f"## Section Type\n{section_type}\n\n"
        f"## Field Paths to Resolve\n```json\n{json.dumps(field_paths, ensure_ascii=False)}\n```\n\n"
        f"{chunk_note}"
        f"## Source Text\n```\n{context}\n```\n\n"
        f"Extract the values for each field_path from the source text above. "
        f"Return a JSON object keyed by field_path. "
        f"For array fields (containing `[]`), output ALL items found as "
        f"`$.path[0].field`, `$.path[1].field`, etc. "
        f"Also include `$.path.length` with the item count. "
        f"Set missing fields to null."
    )


# ── Response parsing ───────────────────────────────────────────────

def _parse_retrieval_response(response: str | None, field_paths: list[str]) -> dict:
    """Parse the LLM's JSON response, with multi-strategy fallback."""
    if not response:
        return {fp: None for fp in field_paths}

    response = response.strip()

    strategies = [
        # 1. Direct JSON parse
        lambda r: json.loads(r),
        # 2. Extract from markdown fence
        lambda r: json.loads(m.group(1).strip()) if (m := re.search(r'```(?:json)?\s*([\s\S]*?)```', r)) else None,
        # 3. Find outermost JSON object
        lambda r: json.loads(m.group(0)) if (m := re.search(r'\{[\s\S]*\}', r)) else None,
    ]

    for strategy in strategies:
        try:
            result = strategy(response)
            if isinstance(result, dict) and result:
                # Validate against requested field_paths
                validated = {}
                for fp in field_paths:
                    validated[fp] = result.get(fp)
                return validated
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue

    logger.warning("Retriever: could not parse response (%d chars)", len(response))
    return {fp: None for fp in field_paths}


# ── Result merging ─────────────────────────────────────────────────

def _merge_retrieval_results(existing: dict, new: dict) -> dict:
    """Merge two retrieval result dicts.

    Rules:
    - Non-null values from `new` fill gaps in `existing`
    - Existing non-null values are preserved (first-found wins)
    - Array length keys (`$.path.length`) take the MAX across chunks
    - Array item keys are merged: keep existing items, add new ones
    """
    merged = dict(existing)

    for key, value in new.items():
        if key.endswith(".length"):
            # Array length: take max
            try:
                new_val = int(value) if value is not None else 0
                old_val = int(merged.get(key, 0)) if merged.get(key) is not None else 0
                merged[key] = max(old_val, new_val)
            except (ValueError, TypeError):
                merged[key] = value if value is not None else merged.get(key)
        elif value is not None:
            if key not in merged or merged[key] is None:
                merged[key] = value
            # If both have non-null values for the same key, keep existing
            # (first chunk found it, likely more complete)

    return merged


# ── Chunking ───────────────────────────────────────────────────────

def _chunk_text(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into token-aware chunks at paragraph boundaries.

    Reuses the same algorithm as summarizer._chunk_text().
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)

        if current_tokens + para_tokens > target_tokens and current:
            chunks.append("\n\n".join(current))
            # Overlap: keep last paragraph(s) for continuity
            overlap_chars = overlap_tokens * 4
            prev = "\n\n".join(current)
            overlap_text = prev[-overlap_chars:] if len(prev) > overlap_chars else ""
            if overlap_text and "\n\n" in overlap_text:
                overlap_text = overlap_text.split("\n\n", 1)[-1]
            current = [overlap_text] if overlap_text else []
            current_tokens = count_tokens(overlap_text) if overlap_text else 0

        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ── Helpers ────────────────────────────────────────────────────────

def _field_path_to_key(field_path: str) -> str:
    """Convert a field path to a potential dict key.

    "$.title" → "title"
    "$.summary.total" → "total"
    """
    cleaned = re.sub(r'\[\d*\]', '', field_path)
    cleaned = re.sub(r'\[\]', '', cleaned)
    parts = cleaned.rsplit(".", 1)
    return parts[-1] if parts else field_path
