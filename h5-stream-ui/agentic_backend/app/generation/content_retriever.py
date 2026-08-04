"""
Content Retriever — LLM-based data retrieval per section from the context store.

Strategy:
  1. Load the full user input from ContextStore for this session.
  2. Ask the LLM to extract relevant data for a section as raw text.
  3. If context exceeds budget → recursive chunking: extract from each chunk,
     concatenate results.
  4. Return raw text directly — NO JSON parsing. The Component Generator
     (Agent B) receives the raw text and renders from it.

No JSON, no structured parsing, no validation. The retriever's job is to
find and surface relevant data; the component generator decides how to use it.
"""

from __future__ import annotations

import asyncio
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

# When output was truncated (finish_reason=length), reduce context by this factor
# to leave more room for the extracted data. E.g. 0.6 → keep 60% of original context.
TRUNCATION_RETRY_RATIO = 0.6

RETRIEVER_SYSTEM_PROMPT = """You extract relevant data for a UI component from a user's source text.

Given:
- A section type (header, metrics_grid, card_list, etc.)
- The field paths this section needs data for
- A source text (full user input or a chunk of it)

Extract the specific data values this component needs and output them as a
concise text summary. Include actual values, names, URLs, descriptions, and
numbers exactly as they appear in the source.

## Output Format
Output plain text — NOT JSON. List each field and its value(s):

```
title: "Summer Travel Plan"
icon_url: https://example.com/icon.png
items (5 total):
  - name: "Tokyo Tower", image: https://..., price: $29
  - name: "Mount Fuji", image: https://..., price: $49
  ...
summary.total: 42
```

## Rules
- Include ALL items for array fields — don't sample just the first one
- Copy values exactly: don't truncate URLs, don't round numbers
- If a value cannot be found, write "N/A"
- If you only see part of the data (this is a chunk), just extract what's here
- Output ONLY the data — no preamble, no commentary, no markdown fences"""


async def retrieve_section_data(
    section: dict,
    *,
    session_id: str,
    working_query: str,
    plan_data_summary: dict | None = None,
    context_store: ContextStore,
    config: AppConfig,
    interaction_logger: "LlmInteractionLogger | None" = None,
) -> str:
    """Retrieve relevant data for one section and return it as raw text.

    No JSON parsing — the raw LLM response is passed directly to the
    Component Generator. If the LLM fails, falls back to the working query.

    Returns:
        Raw text with the extracted data for this section.
    """
    data_bindings = section.get("data_bindings", [])
    section_type = section.get("section_type", "unknown")

    # Build a human-readable summary of what data is needed
    field_paths = [b.get("field_path", "") for b in data_bindings if b.get("field_path")]
    roles = {b.get("field_path", ""): b.get("visual_role", "text") for b in data_bindings}

    if not field_paths:
        # No bindings → no data needed for this section
        return ""

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
        no_think_enabled=config.no_think_enabled,
        no_think_directive=config.no_think_directive,
        interaction_logger=interaction_logger,
        log_label=f"retrieve_{section_type}",
    )

    # Estimate available space for context
    prompt_overhead = count_tokens(RETRIEVER_SYSTEM_PROMPT) + 200
    available_for_context = max(threshold - prompt_overhead, 500)

    if context_tokens <= available_for_context:
        retrieved = await _retrieve_single(
            full_text, field_paths, roles, section_type, llm,
        )
    else:
        logger.info("Retriever [%s]: context too large (%d > %d), chunking",
                     section_type, context_tokens, available_for_context)
        retrieved = await _retrieve_chunked(
            full_text, field_paths, roles, section_type, llm,
        )

    # Fallback: if LLM returned nothing, use working_query as context
    if not retrieved or not retrieved.strip():
        logger.warning("Retriever [%s]: empty response, using working_query snippet", section_type)
        retrieved = _fallback_context(field_paths, roles, full_text, plan_data_summary)

    logger.info("Retriever [%s]: %d chars of data retrieved",
                 section_type, len(retrieved))

    return retrieved


# ── Single-pass retrieval ──────────────────────────────────────────

async def _retrieve_single(
    context: str,
    field_paths: list[str],
    roles: dict[str, str],
    section_type: str,
    llm: LlmClient,
) -> str:
    """Extract data in a single LLM call. Retries with reduced context if truncated."""
    user_prompt = _build_retrieval_prompt(context, field_paths, roles, section_type)

    try:
        response = await llm.generate(
            system_prompt=RETRIEVER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=2048,
        )
    except Exception as e:
        logger.error("Single retrieve failed: %s", e)
        return ""

    # Check if output was truncated — if so, retry with less context
    if llm.last_finish_reason == "length":
        logger.warning(
            "Retriever [%s]: output truncated (finish_reason=length). "
            "Retrying with %.0f%% context to leave more room for output.",
            section_type, TRUNCATION_RETRY_RATIO * 100,
        )
        reduced_context = _truncate_context(context, TRUNCATION_RETRY_RATIO)
        reduced_prompt = _build_retrieval_prompt(
            reduced_context, field_paths, roles, section_type,
        )
        try:
            response = await llm.generate(
                system_prompt=RETRIEVER_SYSTEM_PROMPT,
                user_prompt=reduced_prompt,
                temperature=0.1,
                max_tokens=2048,  # Same output budget, less input = more headroom
            )
        except Exception as e:
            logger.error("Retriever retry also failed: %s", e)
            return _clean_response(response)  # Use the truncated first attempt

    return _clean_response(response)


# ── Chunked retrieval ──────────────────────────────────────────────

async def _retrieve_chunked(
    text: str,
    field_paths: list[str],
    roles: dict[str, str],
    section_type: str,
    llm: LlmClient,
    depth: int = 0,
) -> str:
    """For large contexts: split into chunks, extract from each, concatenate."""
    if depth > MAX_DEPTH:
        logger.warning("Retriever: max depth %d, falling back to single-pass", MAX_DEPTH)
        return await _retrieve_single(text[:CHUNK_TOKENS * 4], field_paths, roles, section_type, llm)

    chunks = _chunk_text(text, CHUNK_TOKENS, CHUNK_OVERLAP)
    logger.info("Retriever level %d: %d chunks", depth, len(chunks))

    if len(chunks) == 1:
        return await _retrieve_single(text, field_paths, roles, section_type, llm)

    # Extract from each chunk in parallel
    tasks = [
        _retrieve_from_chunk(chunk, i, len(chunks), field_paths, roles, section_type, llm)
        for i, chunk in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Concatenate non-empty results
    parts: list[str] = []
    for i, result in enumerate(chunk_results):
        if isinstance(result, Exception):
            logger.error("Chunk %d retrieval failed: %s", i, result)
        elif result and result.strip():
            parts.append(result.strip())

    return "\n".join(parts)


async def _retrieve_from_chunk(
    text: str,
    chunk_idx: int,
    total: int,
    field_paths: list[str],
    roles: dict[str, str],
    section_type: str,
    llm: LlmClient,
) -> str:
    """Extract data from a single chunk. Retries with less context if truncated."""
    chunk_label = f"Chunk {chunk_idx + 1} of {total}"
    user_prompt = _build_retrieval_prompt(text, field_paths, roles, section_type, chunk_label)

    try:
        response = await llm.generate(
            system_prompt=RETRIEVER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=1536,
        )
    except Exception as e:
        logger.error("Chunk %d retrieve failed: %s", chunk_idx, e)
        return ""

    # Retry with reduced context if truncated
    if llm.last_finish_reason == "length":
        logger.warning(
            "Retriever chunk %d: output truncated. Retrying with %.0f%% context.",
            chunk_idx, TRUNCATION_RETRY_RATIO * 100,
        )
        reduced_text = _truncate_context(text, TRUNCATION_RETRY_RATIO)
        reduced_prompt = _build_retrieval_prompt(
            reduced_text, field_paths, roles, section_type, chunk_label,
        )
        try:
            response = await llm.generate(
                system_prompt=RETRIEVER_SYSTEM_PROMPT,
                user_prompt=reduced_prompt,
                temperature=0.1,
                max_tokens=1536,
            )
        except Exception as e:
            logger.error("Chunk %d retry also failed: %s", chunk_idx, e)

    return _clean_response(response)


# ── Prompt building ────────────────────────────────────────────────

def _build_retrieval_prompt(
    context: str,
    field_paths: list[str],
    roles: dict[str, str],
    section_type: str,
    chunk_label: str = "",
) -> str:
    """Build the user prompt for a retrieval call."""
    # Summarise what fields are needed in a readable format
    fields_list = "\n".join(
        f"  - {fp} ({roles.get(fp, 'text')})" for fp in field_paths
    )

    chunk_note = ""
    if chunk_label:
        chunk_note = (
            f"\n> ⚠️ This is {chunk_label} of the full input. "
            f"Only extract what's in this chunk. If a field isn't here, write N/A.\n"
        )

    # Truncate context if still too large
    max_context_chars = 6000
    if len(context) > max_context_chars:
        context = context[:max_context_chars] + "\n... (truncated)"

    return (
        f"## Section Type\n{section_type}\n\n"
        f"## Fields Needed\n{fields_list}\n"
        f"{chunk_note}"
        f"## Source Text\n```\n{context}\n```\n\n"
        f"Extract the data values for the fields above from the source text. "
        f"Output as plain text — list each field and its value(s). "
        f"Include ALL array items. Copy values exactly from the source."
    )


# ── Response cleanup ───────────────────────────────────────────────

def _clean_response(response: str | None) -> str:
    """Basic cleanup: strip thinking tags and markdown fences."""
    if not response:
        return ""

    text = response.strip()

    # Strip thinking tags
    text = re.sub(r'<think[^>]*>.*?</think>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<think[^>]*>.*$', '', text, flags=re.IGNORECASE | re.DOTALL)

    # Strip markdown fences
    text = re.sub(r'```(?:\w+)?\s*\n?', '', text)
    text = text.replace('```', '')

    return text.strip()


# ── Context truncation (for length retry) ────────────────────────

def _truncate_context(text: str, ratio: float) -> str:
    """Reduce context to ~ratio of its original token count.

    Takes content from both the head (has intro/purpose) and tail
    (often has the actual data), dropping the middle. Handles large
    paragraphs by splitting mid-paragraph if needed.
    """
    target_tokens = int(count_tokens(text) * ratio)
    if count_tokens(text) <= target_tokens:
        return text

    # Walk paragraphs from start, then from end, building a budget
    paragraphs = text.split("\n\n")
    total = len(paragraphs)
    taken: set[int] = set()
    result_parts: list[tuple[int, str]] = []  # (index, text)
    used = 0
    half_target = target_tokens // 2

    # Pass 1: take from the front
    for i, para in enumerate(paragraphs):
        if used >= half_target:
            break
        para_tokens = count_tokens(para)
        if used + para_tokens > half_target and result_parts:
            # Partial: take what fits from this paragraph
            remaining = half_target - used
            partial = _take_tokens(para, remaining)
            if partial:
                result_parts.append((i, partial))
                used += count_tokens(partial)
            taken.add(i)
            break
        result_parts.append((i, para))
        used += para_tokens
        taken.add(i)

    # Pass 2: take from the back (skip already-taken)
    tail_parts: list[tuple[int, str]] = []
    tail_used = 0
    for i in range(total - 1, -1, -1):
        if i in taken:
            continue
        if tail_used >= half_target:
            break
        para = paragraphs[i]
        para_tokens = count_tokens(para)
        if tail_used + para_tokens > half_target and tail_parts:
            remaining = half_target - tail_used
            partial = _take_tokens(para, remaining, from_end=True)
            if partial:
                tail_parts.append((i, partial))
                tail_used += count_tokens(partial)
            taken.add(i)
            break
        tail_parts.append((i, para))
        tail_used += para_tokens
        taken.add(i)

    # Stitch together in original order
    all_parts = result_parts + tail_parts
    all_parts.sort(key=lambda x: x[0])

    # Insert an ellipsis where there's a gap
    final: list[str] = []
    prev_idx = -1
    for idx, content in all_parts:
        if prev_idx >= 0 and idx > prev_idx + 1:
            final.append("...")
        final.append(content)
        prev_idx = idx

    return "\n\n".join(final)


def _take_tokens(text: str, max_tokens: int, from_end: bool = False) -> str:
    """Take approximately max_tokens worth of text.

    If from_end=True, takes from the end of the text.
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text

    if from_end:
        truncated = text[-max_chars:]
        # Walk forward to the first sentence/clause boundary
        for delim in [". ", "\n", "。", "; ", "，", ", ", " "]:
            pos = truncated.find(delim)
            if pos > max_chars * 0.2:
                return truncated[pos + len(delim.rstrip()):]
        return truncated
    else:
        truncated = text[:max_chars]
        # Walk back to the last sentence/clause boundary
        for delim in ["\n", ". ", "。", "; ", "；", ", ", "，", " "]:
            pos = truncated.rfind(delim)
            if pos > max_chars * 0.6:
                return truncated[:pos + len(delim.rstrip())]
        return truncated


# ── Fallback ───────────────────────────────────────────────────────

def _fallback_context(
    field_paths: list[str],
    roles: dict[str, str],
    full_text: str,
    plan_data_summary: dict | None,
) -> str:
    """Build a fallback context when the LLM returns nothing."""
    parts: list[str] = []

    # Use headings from the full text
    headings = re.findall(r'^#{1,4}\s+(.+)$', full_text, re.MULTILINE)
    if headings:
        parts.append("Available sections: " + ", ".join(headings[:10]))

    # Include plan_data_summary if available
    if plan_data_summary:
        for key, value in plan_data_summary.items():
            parts.append(f"{key}: {value}")

    # List the field paths we need
    parts.append("\nFields needed:")
    for fp in field_paths:
        parts.append(f"  - {fp} ({roles.get(fp, 'text')})")

    return "\n".join(parts) if parts else "[No data available for this section]"


# ── Chunking ───────────────────────────────────────────────────────

def _chunk_text(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into token-aware chunks at paragraph boundaries."""
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
