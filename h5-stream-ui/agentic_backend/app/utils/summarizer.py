"""
Summarization Gateway Agent — structural indexer for long user input.

Role: runs ONCE when user input first arrives. It does NOT preserve
detailed content. Instead it produces a concise structural summary
that tells the downstream plan agent WHAT is available — the purpose,
content categories, data shape, and where to find details.

The full original is always saved to ContextStore. The plan agent uses
this summary to decide layout, then searches ContextStore for specific
details (scenic spot descriptions, URLs, prices, etc.) as needed.

Strategy:
  1. If input ≤ 50% of the token budget → pass through unchanged
  2. If input > 50% → generate a structural index summary
     - For very long input (>8K tokens): recursive chunk → extract
       structure from each chunk → merge into one index
  3. Full original saved to ContextStore before summarising

The summary output is ~200-500 tokens and contains ONLY:
  - Purpose: what the user wants (travel plan, stock report, employee list…)
  - Content categories: what kinds of information are present
  - Data shape: arrays, objects, key fields (names only, NOT values)
  - Media inventory: count of images, videos, URLs by type
  - Interaction hints: any UI/layout preferences the user mentioned
  - Section map: heading structure with item counts, no body content
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from app.config import AppConfig, LlmConfig
from app.shared.llm_client import LlmClient
from app.utils.token_counter import count_tokens
from app.utils.context_store import ContextStore

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

# Chunking constants
CHUNK_SIZE = 3000      # Tokens per chunk for structural extraction
CHUNK_OVERLAP = 100    # Overlap for heading continuity
BUDGET_THRESHOLD = 0.50  # Summarise if input > 50% of token budget


INDEXER_SYSTEM_PROMPT = """You are a structural indexer. You do NOT summarize content — you catalog
WHAT exists so a downstream agent can decide what to look up.

## Your Output (200-500 words max)
Produce a concise structural index with these sections:

### 1. Purpose
One sentence: what does the user want to build? (e.g. "Travel plan card for a 5-day trip",
"Employee clock-in dashboard", "Stock analysis report with 12 metrics")

### 2. Content Categories
Bullet list of information types present:
  - e.g. "Scenic spots (12 items, each with name, description, image, rating)"
  - e.g. "Daily itinerary (5 days, each with 3-5 activities)"
  - e.g. "Financial metrics (P/E, market cap, revenue, 12 fields per stock)"
  - e.g. "Image URLs (8 total: 5 scenic, 2 logo, 1 hero)"
  - e.g. "Video links (3 YouTube embeds)"
  - Use field NAMES only — NO actual values, prices, URLs, descriptions

### 3. Data Shape
Describe the structure:
  - Top-level fields: e.g. "title, date, author, items[]"
  - Array fields and their item shapes: e.g. "items[]: {name, description, image_url, price, rating}"
  - Nested objects: e.g. "items[].location: {lat, lng, address}"
  - Names and types only — NO sample values

### 4. Media Inventory
Count by type:
  - Images: N (breakdown by role if clear: hero, thumbnail, decorative)
  - Videos: N (YouTube, direct mp4, etc.)
  - External links: N
  - NO actual URLs

### 5. UI / Interaction Hints
Any user preferences mentioned:
  - Layout style: e.g. "wants card-based layout", "asked for timeline view"
  - Interactions: e.g. "needs pagination for 30+ items", "click to open map"
  - Style notes: e.g. "mentioned dark theme", "wants HarmonyOS style"
  - If none mentioned, say "No explicit preferences — use defaults"

### 6. Section Map
The heading structure with item counts:
  - e.g. "## Day 1: Tokyo (4 activities)"
  - e.g. "## Scenic Spots (12 items)"
  - e.g. "## Financial Data (3 tables)"
  - Headings and counts only — NO body content, NO descriptions

## Critical Rules
- DO NOT include any actual data values (no prices, no URLs, no descriptions, no numbers except counts)
- DO NOT include any proper nouns beyond what's needed to identify a section
- The full original is saved elsewhere; your job is to INDEX it, not reproduce it
- Target: 150-350 words. Be dense. No filler."""


async def summarize_if_needed(
    query: str,
    *,
    token_budget: int = 4000,
    config: AppConfig,
    context_store: ContextStore,
    session_id: str,
    interaction_logger: Optional["LlmInteractionLogger"] = None,
) -> tuple[str, bool]:
    """Generate a structural index if the user query exceeds the token budget threshold.

    Returns:
        (processed_text, was_summarised) — if summarised, the full original
        is saved to context_store and processed_text is the structural index.
    """
    input_tokens = count_tokens(query)
    threshold = int(token_budget * BUDGET_THRESHOLD)

    logger.info("Summarizer check: %d tokens input, threshold=%d", input_tokens, threshold)

    if input_tokens <= threshold:
        logger.info("Input fits within budget, no summarisation needed")
        return query, False

    # Save the full original BEFORE summarising
    store_path = context_store.save(session_id, query, metadata={
        "input_tokens": str(input_tokens),
        "threshold": str(threshold),
        "action": "structural_index",
    })
    logger.info("Saved full input to %s (%d tokens)", store_path, input_tokens)

    # Lightweight LLM client for indexing
    llm = LlmClient(
        LlmConfig(
            base_url=config.local.base_url,
            api_key=config.local.api_key,
            model=config.local.model,
        ),
        token_budget=token_budget,
        supports_json_mode=False,
        thinking_enabled=False,  # Disable reasoning to save output tokens
        no_think_enabled=config.no_think_enabled,
        no_think_directive=config.no_think_directive,
        interaction_logger=interaction_logger,
        log_label="summarize",
    )

    # Generate structural index
    if input_tokens > 8000:
        # Very long: recursive structural extraction
        index_text = await _recursive_index(query, token_budget, llm)
    else:
        # Single-pass index
        index_text = await _single_index(query, llm, token_budget)

    index_tokens = count_tokens(index_text)
    logger.info("Structural index: %d → %d tokens (%.0f%%)",
                 input_tokens, index_tokens,
                 (index_tokens / max(input_tokens, 1)) * 100)

    # Append context store reference
    index_text += (
        f"\n\n---\n"
        f"> 📁 **Full input saved** ({input_tokens} tokens). "
        f"Search context store session `{session_id}` for specific details "
        f"(descriptions, URLs, prices, dates, numbers)."
    )

    return index_text, True


# ── Internal: single-pass indexing ───────────────────────────────

# Overhead for the user prompt wrapper: "## Content to Index\n\n{ sampled }"
_USER_PROMPT_BOILERPLATE = 12  # tokens

# Output reserve for the index response (matches max_tokens below)
_OUTPUT_RESERVE = 1024


async def _single_index(
    text: str, llm: LlmClient, token_budget: int = 4000,
) -> str:
    """Generate a structural index in one call.

    Uses token-aware sampling: if the full text fits within the available
    budget (budget − system prompt − output reserve), the whole text is
    used. Otherwise the opening (which carries purpose/intent) gets ~60%
    of the available tokens and heading structure gets the remaining ~40%.
    """
    system_tokens = count_tokens(INDEXER_SYSTEM_PROMPT)
    available = token_budget - system_tokens - _USER_PROMPT_BOILERPLATE - _OUTPUT_RESERVE
    text_tokens = count_tokens(text)

    if text_tokens <= available:
        # Full text fits — no truncation needed
        sampled = text
        logger.info("Single index: full text fits (%d tokens, %d available)", text_tokens, available)
    else:
        # Token-aware sampling: opening gets ~60%, heading structure gets ~40%
        opening_budget = int(available * 0.6)
        heading_budget = available - opening_budget

        opening_text = _truncate_to_tokens(text, opening_budget)
        all_headings = _extract_headings(text)
        if all_headings and all_headings != "(no headings found)":
            heading_text = _truncate_to_tokens(
                "## Section Map (headings only)\n" + all_headings, heading_budget,
            )
            sampled = opening_text + "\n\n...\n\n" + heading_text
        else:
            sampled = _truncate_to_tokens(text, available)
            heading_text = ""

        sampled_tokens = count_tokens(sampled)
        logger.info(
            "Single index: sampled %d → %d tokens (%.0f%%) [opening=%dtok, headings=%dtok]",
            text_tokens, sampled_tokens,
            (sampled_tokens / max(text_tokens, 1)) * 100,
            count_tokens(opening_text), count_tokens(heading_text) if all_headings else 0,
        )

    try:
        result = await llm.generate(
            system_prompt=INDEXER_SYSTEM_PROMPT,
            user_prompt=f"## Content to Index\n\n{sampled}",
            temperature=0.1,
            max_tokens=1024,
        )
        return result.strip() if result else _fallback_index(text)
    except Exception as e:
        logger.error("Indexing call failed: %s", e)
        return _fallback_index(text)


# ── Internal: recursive indexing for very long inputs ────────────

async def _recursive_index(
    text: str,
    token_budget: int,
    llm: LlmClient,
    depth: int = 0,
) -> str:
    """For very long inputs: chunk, extract structure from each, merge."""
    if depth > 2:
        return _fallback_index(text)

    chunks = _chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
    logger.info("Index level %d: %d chunks", depth, len(chunks))

    if len(chunks) == 1:
        return await _single_index(text, llm, token_budget)

    # Extract structure from each chunk in parallel
    tasks = [
        _extract_chunk_structure(chunk, i, len(chunks), llm)
        for i, chunk in enumerate(chunks)
    ]
    structures = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge structural extracts
    merged = ""
    for i, result in enumerate(structures):
        if isinstance(result, Exception):
            logger.error("Chunk %d structure extraction failed: %s", i, result)
            merged += f"\n[Chunk {i+1}]: {_extract_headings(chunks[i])}\n"
        else:
            merged += f"\n{result}\n"

    # If merged is still too large, do a final index pass
    merged_tokens = count_tokens(merged)
    threshold = int(token_budget * BUDGET_THRESHOLD)
    if merged_tokens > threshold:
        return await _single_index(merged, llm, token_budget)

    return merged


async def _extract_chunk_structure(
    text: str, chunk_idx: int, total: int, llm: LlmClient,
) -> str:
    """Extract only the structural skeleton from one chunk."""
    headings = _extract_headings(text)

    # Token-aware sample: reserve ~200 tokens for the system prompt + boilerplate,
    # ~400 for output → leave ~200 tokens for the opening sample
    sample = _truncate_to_tokens(text, 200)

    prompt = (
        f"## Chunk {chunk_idx + 1}/{total} Structure\n\n"
        f"### Opening sample\n{sample}\n\n"
        f"### All headings in this chunk\n{headings}\n\n"
        f"Output a brief structural note for this chunk: "
        f"what content categories, how many items per heading, "
        f"any media URLs found (count only, no actual URLs). "
        f"3-5 lines max."
    )

    try:
        result = await llm.generate(
            system_prompt="Extract only structural metadata. No actual content values.",
            user_prompt=prompt,
            temperature=0.1,
            max_tokens=400,
        )
        return result.strip() if result else f"Chunk {chunk_idx + 1}: {headings}"
    except Exception as e:
        logger.error("Chunk %d structure failed: %s", chunk_idx, e)
        return f"Chunk {chunk_idx + 1}: {headings}"


# ── Helpers ──────────────────────────────────────────────────────

def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Take as much text as fits within max_tokens, at paragraph boundaries."""
    if count_tokens(text) <= max_tokens:
        return text

    # Binary-ish search: take increasing fractions until we hit the budget
    # Approximate: 1 token ≈ 4 chars for Latin, ≈ 2 for CJK
    target_chars = max_tokens * 4  # conservative upper bound

    # Walk paragraph by paragraph to find the cut point
    paragraphs = text.split("\n\n")
    result: list[str] = []
    used = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if used + para_tokens > max_tokens and result:
            # Try to include at least part of this paragraph
            remaining = max_tokens - used
            if remaining > 30:  # Only bother if we have meaningful space left
                # Take first `remaining` tokens worth of chars from this paragraph
                partial = _take_first_n_tokens(para, remaining)
                if partial:
                    result.append(partial)
            break
        result.append(para)
        used += para_tokens

    return "\n\n".join(result)


def _take_first_n_tokens(text: str, max_tokens: int) -> str:
    """Take approximately the first max_tokens worth of text."""
    # 1 token ≈ 4 chars for Latin text (conservative)
    max_chars = max_tokens * 4
    truncated = text[:max_chars]
    # Walk back to the last complete word boundary
    if len(truncated) >= max_chars and len(text) > max_chars:
        # Try to end at a sentence or clause boundary
        for delim in ["\n", ". ", "。", "; ", "；", ", ", "，", " "]:
            last = truncated.rfind(delim)
            if last > max_chars * 0.6:
                truncated = truncated[:last + len(delim.rstrip())]
                break
    return truncated


def _extract_headings(text: str) -> str:
    """Extract all markdown headings with their line counts."""
    import re
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^#{1,4}\s+', stripped):
            result.append(stripped)
    return "\n".join(result) if result else "(no headings found)"


def _fallback_index(text: str) -> str:
    """Minimal structural index when LLM indexing fails."""
    import re
    headings = _extract_headings(text)
    lines = text.split("\n")

    # Count rough content types
    url_count = len(re.findall(r'https?://', text))
    image_count = len(re.findall(r'\.(?:jpg|jpeg|png|gif|webp|svg)\b', text, re.IGNORECASE))
    list_items = len(re.findall(r'^\s*[-*]\s+', text, re.MULTILINE))
    json_blocks = len(re.findall(r'\{[^{}]*\}', text))

    return (
        f"### 1. Purpose\nUser input ({len(text)} chars, {url_count} URLs, "
        f"{image_count} images).\n\n"
        f"### 2. Content Categories\n"
        f"- List items: ~{list_items}\n"
        f"- JSON blocks: {json_blocks}\n"
        f"- URLs: {url_count} ({image_count} images)\n\n"
        f"### 3. Data Shape\n(Parse failed — see context store for full input)\n\n"
        f"### 4. Media Inventory\n"
        f"- Images: {image_count}\n"
        f"- Links: {url_count}\n\n"
        f"### 5. UI / Interaction Hints\nNone detected.\n\n"
        f"### 6. Section Map\n{headings}"
    )


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
            # Overlap: keep last paragraph(s)
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
