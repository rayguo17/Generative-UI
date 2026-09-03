"""
Researcher Agent — gathers data for each section in a plan.

Consumes the plan output from the Planner agent and produces a
dictionary of gathered data keyed by section index.

Data retrieval strategy:
  1. Match the plan's topic + intent to pre-cached research in the local
     Research Store (file-based cache populated offline via web search).
  2. Load the matching research files as source text.
  3. Ask an LLM to extract relevant data per section from those files
     (same pattern as content_retriever.py — raw text output, no JSON).
  4. If no research store match → fall back to mock/placeholder data.

The research store is pre-populated offline (e.g., via cloud LLM / manual
web search) so the local LLM pipeline runs without internet access.

Interface:
    Input:  plan: dict[str, Any]  — the validated plan from plan.py
            llm: LlmClient | None — LLM client for extraction (if None, uses raw text)
            context: dict[str, Any] | None  — optional user context / session state
    Output: dict[int, dict[str, Any]]  — {section_index: gathered_data}

    gathered_data per section:
        For single_lookup:  {"fields": {field_name: value, ...}}
        For search_all:     {"items": [{field: value, ...}, ...], "count": N}
        For iterate_days:   {"items": [{field: value, ...}, ...], "count": N}
        For none:           {}
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

from app.utils.token_counter import count_tokens

logger = logging.getLogger(__name__)

# Max tokens of research data embedded in the LLM prompt per section call.
# Configurable via RESEARCH_MAX_CONTEXT_TOKENS env var. Lower values reduce
# input tokens and help prevent LLM repetition loops on large datasets.
_MAX_CONTEXT_TOKENS = int(os.getenv("RESEARCH_MAX_CONTEXT_TOKENS", "1500"))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* to at most *max_tokens* tokens.

    Uses ``count_tokens`` for the final check so the truncation is
    accurate regardless of CJK vs Latin content ratio.
    """
    if not text or count_tokens(text) <= max_tokens:
        return text
    # Estimate: 4 chars per token (English average).  CJK is denser
    # (~2 chars/token) so this may overshoot for CJK-heavy text — the
    # trim loop below handles that.
    max_chars = max_tokens * 4
    truncated = text[:max_chars]
    # Trim back if the estimate was too generous
    while count_tokens(truncated) > max_tokens and len(truncated) > 100:
        max_chars = int(max_chars * 0.9)
        truncated = text[:max_chars]
    return truncated.rstrip() + "\n... (truncated)"

class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"

# ── Research Store ─────────────────────────────────────────────────────

# Default location relative to this file
_DEFAULT_STORE_DIR = Path(__file__).resolve().parent.parent.parent / "research_store"

# Chunk size for LLM extraction (tokens per chunk of research data)
CHUNK_TOKENS = 2500
CHUNK_OVERLAP = 100
MAX_DEPTH = 3
MAX_RESEARCH_ITERATIONS = 5  # max chunked extraction iterations per section

# Threshold: if combined research data > this fraction of token_budget, chunk
BUDGET_THRESHOLD = 0.50

# =========================================================================
# System prompt — tells the LLM how to extract data from research files
# =========================================================================

RESEARCHER_SYSTEM_PROMPT = """You extract data for a UI section from pre-cached research files.

Given:
- A section specification (widget type, title, what data it needs)
- A set of research documents (web search results stored as markdown)

Extract the specific data values this section needs and output them as a
concise text summary. Include actual values, names, URLs, descriptions,
prices, ratings, times, and numbers exactly as they appear in the research.

**⚠️ The fields you extract MUST come from the "Data Needed" field in the
section specification — NOT from the examples below.**  Every section asks
for different data; the example only shows the OUTPUT FORMAT, not which
fields to use.

## Item Count Constraint
If the section spec includes an "Items Needed" count (e.g. "Items Needed: 4"),
extract EXACTLY that many items — no more, no less.  Prioritize the most
relevant / highest-rated / best-matching items from the research data.
If the research contains more items than needed, keep only the top N.

## Completion Flag
At the very end of your output, append exactly ONE line indicating whether
the section's data requirement has been fully satisfied:

--- REQUIREMENT_FULFILLED: true ---

Use `true` when:
- All requested fields have real values (not just "N/A")
- The item count matches or exceeds "Items Needed"
- No critical data is missing
- ⚠️ If ANY field is "N/A", you MUST set this to `false`

Use `false` when:
- Some fields are still "N/A" or placeholder values
- Fewer items were found than requested
- You only saw part of the research data and expect more in later chunks

This flag is MANDATORY — every response must end with it.

## Output Format
Output plain text — NOT JSON. List each field and its value(s).

For single-value fields (one set of values):
```
field_name: "extracted value"
another_field: "another value"
```

For list fields (multiple items):
```
items (N total):
  - field1: "value from source", field2: "value from source", ...
  - field1: "another value", field2: "another value", ...
```

### Format example (fields here are JUST AN EXAMPLE — use YOUR Data Needed):
```
destination: "Hangzhou West Lake"
weather: "Spring, 15-25°C, occasional rain"
hero_description: "Experience the best of Hangzhou in one day..."

items (5 total):
  - name: "Three Pools Mirroring the Moon", rating: ★★★★★, price: ¥55, ...
  - name: "Leifeng Pagoda", rating: ★★★★★, price: ¥40, ...
  ...
--- REQUIREMENT_FULFILLED: true ---
```

## Rules
- **Read "Data Needed" in the section spec — extract THOSE fields, not the example fields**
- **Respect "Items Needed" — if it says 4, output exactly 4 items (the best ones)**
- **⚠️ ANTI-HALLUCINATION: If the research data in this window contains NO relevant
  information for the requested fields, write "N/A" for each field and set
  REQUIREMENT_FULFILLED to false.  Do NOT invent names, addresses, prices, or any
  other values from context clues.  Guessing is worse than "N/A".**
- Include ALL items for list fields — don't sample just the first one
- Copy values EXACTLY: don't truncate URLs, don't round numbers
- Preserve markdown formatting from the source where useful (tables, bullets)
- If a value cannot be found, write "N/A"
- If you only see part of the data (this is a chunk), just extract what's here
- Output ONLY the data — no preamble, no commentary, no markdown fences
- **Always end with `--- REQUIREMENT_FULFILLED: true/false ---`**"""


# =========================================================================
# Table system prompt — for table_lookup strategy (numeric/tabular data)
# =========================================================================

RESEARCHER_TABLE_SYSTEM_PROMPT = """You extract numeric/tabular data from pre-cached research files and output it as a markdown table.

Given:
- A section specification (widget type, title, what data it needs)
- A set of research documents (web search results stored as markdown)

Extract ONLY the data values specified in the "Data Needed" field and output
them as a markdown table. Include the values exactly as they appear in the
research — do not round, summarize, or invent values.

**⚠️ The columns you extract MUST come from the "Data Needed" field in the
section specification — extract ONLY those fields, nothing else.**

## Output Format (Markdown Table)
First line: header row with pipe-separated column names.
Second line: separator row (--- for each column).
Subsequent lines: one data row per line, pipe-separated.

For comparison data (metrics across entities):
```
| metric | bidu | tencent | kuaishou |
|---|---|---|---|
| P/E | 15.70 | 15.51 | 9.98 |
| P/B | 0.92 | 3.01 | 1.90 |
```

For time series data (prices over time):
```
| date | price | volume |
|---|---|---|
| 2026-08-01 | 104.68 | 25568 |
| 2026-08-02 | 105.27 | 31000 |
```

## Completion Flag
At the very end of your output, append exactly ONE line:

--- REQUIREMENT_FULFILLED: true ---

Use `true` when:
- All requested fields have real values (not just "N/A")
- No critical data is missing
- ⚠️ If ANY field is "N/A", you MUST set this to `false`

Use `false` when:
- Some fields are still "N/A" or placeholder values
- Fewer items were found than requested

## Rules
- Use the exact field names from "Data Needed" as column headers
- One row per item/metric/date
- Numeric values should be numbers (15.70, not "15.70")
- If a value is text (not numeric), include it as-is
- If a value cannot be found, write "N/A"
- Copy numbers EXACTLY: don't round, don't truncate
- Output ONLY the markdown table — no preamble, no commentary, no markdown fences around it
- **Always end with `--- REQUIREMENT_FULFILLED: true/false ---`**"""


# ── Public interface ──────────────────────────────────────────────────

async def gather_section_data(
    plan: dict[str, Any],
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,

    research_store_dir: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    """Gather data for every section in the plan.

    Tries the local research store first, using LLM-based extraction
    when a config is provided. Falls back to mock data if no cached
    research is available.

    Args:
        plan: Validated plan dict from plan.py. Expected keys:
              topic, intent, sections (list of section dicts).
        research_store_dir: Path to the research_store directory.
        config: AppConfig for creating an LLM client for extraction.
        interaction_logger: Optional interaction logger for LLM calls.

    Returns:
        Dict mapping section index → gathered data.
    """
    store_dir = Path(research_store_dir) if research_store_dir else _DEFAULT_STORE_DIR

    # ── Load cached research ─────────────────────────────────
    topic = plan.get("topic", "general")
    intent = plan.get("intent", "")
    research_files = _load_research_for_topic(topic, intent, store_dir)

    if research_files:
        print(f"\n{c(f'  ✓ Found {len(research_files)} research files for topic \"{topic}\"', Colors.GREEN)}")
        logger.info("Researcher: using local research store — %d files for topic '%s'",
                     len(research_files), topic)
    else:
        logger.info("Researcher: no cached research for topic='%s' — using mock data",
                     topic)
        return await _gather_all_mock(plan)

    # ── Build LLM client if config provided ──────────────────
    combined_text = "\n\n---\n\n".join(
        f"## File: {name}\n{content}" for name, content in research_files.items()
    )

    # ── Gather per section ───────────────────────────────────
    result: dict[int, dict[str, Any]] = {}

    for section in plan.get("sections", []):
        idx = section.get("index", 0)
        strategy = section.get("research_strategy", "none")
        data_needed = section.get("data_needed", "")
        title = section.get("title", f"Section {idx}")

        logger.info("Researcher: section %d ('%s') — strategy=%s",
                     idx, title, strategy)

        if strategy == "none" or not data_needed.strip():
            result[idx] = {}
            continue

        if llm:
            try:
                result[idx] = await _gather_with_llm(
                    llm, combined_text, section, strategy, data_needed,
                )
            except Exception as e:
                logger.error("Researcher LLM extraction failed for section %d: %s", idx, e)
                result[idx] = _mock_gather(strategy, data_needed, section)
        else:
            # No LLM — return the best-matching file content as raw text
            result[idx] = _gather_raw(research_files, section, strategy, data_needed)

    return result


# ── LLM-based extraction ──────────────────────────────────────────────

async def _gather_with_llm(
    llm: GenerationLlmClient,
    combined_text: str,
    section: dict[str, Any],
    strategy: str,
    data_needed: str,
) -> dict[str, Any]:
    """Use an LLM to extract relevant data for one section from the research text.

    Returns a structured dict suitable for the component generator:
      - single_lookup → {"fields_text": "raw extracted text"}
      - search_all / iterate_days → {"items_text": "raw extracted text", "count": N}
    """
    section_type = section.get("widget", "unknown")
    section_title = section.get("title", "")

    user_prompt = _build_researcher_prompt(
        combined_text, section_title, section_type, data_needed, strategy,
    )

    context_tokens = count_tokens(combined_text)
    # Select system prompt based on strategy
    if strategy == "table_lookup":
        sys_prompt = RESEARCHER_TABLE_SYSTEM_PROMPT
    else:
        sys_prompt = RESEARCHER_SYSTEM_PROMPT
    prompt_overhead = count_tokens(sys_prompt) + 200
    # GenerationLlmClient stores budget on _client; tolerate both access paths
    budget = getattr(llm, 'token_budget', None)
    if budget is None and hasattr(llm, '_client'):
        budget = getattr(llm._client, 'token_budget', None)
    available_for_context = max(
        int((budget or 4096) * BUDGET_THRESHOLD) - prompt_overhead, 500,
    )

    if context_tokens <= available_for_context:
        # Single-call path: context is already embedded in user_prompt
        # (truncated by _build_researcher_prompt). Pass empty context so
        # _extract_single doesn't re-inject the full untruncated text.
        raw = await _extract_single(llm, "", user_prompt, section_type, system_prompt=sys_prompt)
        # Strip the FULFILLED flag (used for early-stop in chunked path; not
        # needed here, but the LLM may still emit it per the system prompt).
        extracted, _ = _parse_fulfilled_flag(raw)
    else:
        logger.info("Researcher [%s]: context too large (%d > %d), chunking",
                     section_type, context_tokens, available_for_context)
        est_count = section.get("est_count")
        extracted = await _extract_chunked(
            llm, combined_text, user_prompt, section_type,
            est_count=est_count, system_prompt=sys_prompt,
        )

    if not extracted or not extracted.strip():
        logger.warning("Researcher [%s]: empty LLM response, falling back to mock", section_type)
        return _mock_gather(strategy, data_needed, section)

    # Cap extracted text size to prevent oversized research output
    MAX_EXTRACTED_CHARS = 3000
    if len(extracted) > MAX_EXTRACTED_CHARS:
        logger.info("Researcher [%s]: truncating extracted text from %d to %d chars",
                     section_type, len(extracted), MAX_EXTRACTED_CHARS)
        extracted = extracted[:MAX_EXTRACTED_CHARS]

    # Package the extracted text — the component generator will render from it
    est_count = section.get("est_count")
    if strategy == "table_lookup":
        return {"table_data": extracted}
    elif strategy == "single_lookup":
        return {"fields_text": extracted}
    else:
        # Try to count items from the extracted text
        item_count = _estimate_item_count(extracted, est_count)
        return {"items_text": extracted, "count": item_count}


async def _extract_single(
    llm: GenerationLlmClient,
    context: str,
    user_prompt: str,
    section_type: str,
    system_prompt: str = RESEARCHER_SYSTEM_PROMPT,
) -> str:
    """Extract data in a single LLM call.

    Uses llm._client.generate() directly (bypassing GenerationLlmClient wrappers)
    so we can pass a custom temperature and control the log label per section.

    When *context* differs from the research data embedded in *user_prompt*,
    the context is injected into the prompt by replacing the `` ``` ``-fenced
    research-data block. This lets callers (e.g. _extract_chunked) supply a
    subset of the full research text without rebuilding the whole prompt.
    """
    # Pass log_label directly to generate() to avoid race conditions when
    # multiple concurrent research tasks share the same client (parallel mode).
    label = f"research_{section_type}"

    # If a context was supplied, embed it into the prompt by replacing the
    # `` ``` ``-fenced research-data block.  The default _build_researcher_prompt
    # always wraps context in a fenced block ending with a blank line, so this
    # regex is robust against full-replacement.
    if context:
        if count_tokens(context) > _MAX_CONTEXT_TOKENS:
            context = _truncate_to_tokens(context, _MAX_CONTEXT_TOKENS)
        prompt = _inject_context_into_prompt(user_prompt, context)
    else:
        prompt = user_prompt

    try:
        response = await llm._client.generate(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.1,
            max_tokens=2048,
            log_label=label,
        )
    except Exception as e:
        logger.error("Researcher LLM call failed: %s", e)
        return ""

    return _clean_response(response)


async def _extract_chunked(
    llm: GenerationLlmClient,
    text: str,
    user_prompt_template: str,
    section_type: str,
    depth: int = 0,
    est_count: int | None = None,
    system_prompt: str = RESEARCHER_SYSTEM_PROMPT,
) -> str:
    """For large research data: sliding-window iterative extraction.

    Instead of pre-chunking, each iteration calculates how much text fits
    in the remaining token budget and takes a window of that size.  The
    window slides forward through the text with overlap, and each LLM call
    receives the accumulated output from all previous windows so the model
    can **add**, **refine**, or **replace** entries.

    After each window, the LLM's ``REQUIREMENT_FULFILLED`` flag is checked.
    If the data requirement is satisfied (all fields found, item count met),
    the loop stops early — no need to process remaining windows.

    The final result is the output of the *last* iteration — a single
    coherent dataset, not a concatenation of independent extracts.
    """
    if depth > MAX_DEPTH:
        logger.warning("Researcher: max depth %d, truncating", MAX_DEPTH)
        return await _extract_single(
            llm, "", user_prompt_template, section_type, system_prompt=system_prompt,
        )

    # If the full text fits in one call, don't bother chunking
    budget = _get_token_budget(llm)
    output_reserve = 1000
    fixed_overhead = _compute_fixed_overhead(user_prompt_template, total_chunks=None)
    if count_tokens(text) <= budget - fixed_overhead - output_reserve:
        # Context is already embedded (truncated) in user_prompt_template.
        # Pass "" so _extract_single doesn't re-inject the full text.
        return await _extract_single(llm, "", user_prompt_template, section_type)

    # ── Sliding-window iterative extraction ───────────────────────
    position = 0           # character offset into *text*
    iteration = 0
    accumulated = ""

    while position < len(text):
        accum_tokens = count_tokens(accumulated)

        # How many tokens can this window's data occupy?
        available = budget - fixed_overhead - accum_tokens - output_reserve

        if available < 300:
            logger.warning(
                "Researcher iter %d: budget exhausted "
                "(accum=%d + fixed=%d + reserve=%d > budget=%d), stopping",
                iteration + 1, accum_tokens, fixed_overhead,
                output_reserve, budget,
            )
            break

        # Take a token-sized window from the current position
        window_text = _take_window(text, position, max_tokens=min(available, CHUNK_TOKENS))
        if not window_text:
            break

        window_tokens = count_tokens(window_text)
        logger.info(
            "Researcher iter %d: pos=%d window=%d tokens accum=%d tokens budget=%d",
            iteration + 1, position, window_tokens, accum_tokens, budget,
        )

        # Build the prompt for this iteration
        enriched_prompt = _build_chunk_iteration_prompt(
            user_prompt_template, accumulated,
            chunk_index=iteration, total=None,
            est_count=est_count,
        )

        # If previous iteration left N/A values, tell the LLM to look for them
        if iteration > 0 and accumulated:
            prev_na_count = _count_na_values(accumulated)
            if prev_na_count > 0:
                enriched_prompt += (
                    f"\n\n> ⚠️ **{prev_na_count} fields are still N/A** — "
                    f"The previous extraction left {prev_na_count} field(s) as N/A. "
                    f"Check the current window for the missing values and update them. "
                    f"If the data truly doesn't exist in this window, keep N/A."
                )

        extracted = await _extract_single(
            llm, window_text, enriched_prompt, section_type,
        )

        if extracted and extracted.strip():
            # Strip the FULFILLED flag before storing as accumulated
            clean_extracted, is_fulfilled = _parse_fulfilled_flag(extracted)
            accumulated = clean_extracted.strip()
            na_count = _count_na_values(accumulated)
            logger.info("Researcher iter %d: fulfilled=%s, na_count=%d, accum_len=%d",
                        iteration + 1, is_fulfilled, na_count, len(accumulated))
        else:
            logger.warning("Researcher iter %d returned empty, keeping previous accumulated",
                           iteration + 1)
            is_fulfilled = False
            na_count = 0

        # Early stop: requirement satisfied, no need to process more windows
        if is_fulfilled:
            logger.info("Researcher iter %d: requirement fulfilled, stopping early "
                        "(skipping remaining text from position %d)",
                        iteration + 1, position)
            break

        # Slide forward: advance by window size minus overlap.
        # Overlap is in characters (4 chars ≈ 1 token); cap at ⅓ of window
        # so we never stall on huge chunks.
        overlap_chars = min(CHUNK_OVERLAP * 4, len(window_text) // 3)
        position += max(len(window_text) - overlap_chars, 1)
        iteration += 1

        # Safety valve — prevent excessive iterations
        if iteration >= MAX_RESEARCH_ITERATIONS:
            logger.warning("Researcher: max iterations (%d) reached, stopping", MAX_RESEARCH_ITERATIONS)
            break

    return accumulated


# ── Budget & windowing helpers ─────────────────────────────────────────

def _get_token_budget(llm: GenerationLlmClient) -> int:
    """Resolve the token budget from an LLM client, with sensible default."""
    if hasattr(llm, '_client') and getattr(llm._client, 'token_budget', None):
        return llm._client.token_budget
    if hasattr(llm, 'token_budget') and llm.token_budget:
        return llm.token_budget
    return 4096


def _compute_fixed_overhead(
    user_prompt_template: str,
    total_chunks: int | None,
) -> int:
    """Token cost of everything that is NOT chunk data or accumulated output.

    This includes: system prompt, section boilerplate, merge instructions
    boilerplate, and the chunk indicator.  It does NOT include the actual
    research data or the previous accumulated output, which vary per call.
    """
    system_tokens = count_tokens(RESEARCHER_SYSTEM_PROMPT)

    # Section boilerplate = user_prompt_template without the research-data block
    section_text = re.sub(
        r'## Research Data \(cached web search results\).*$',
        '', user_prompt_template, flags=re.DOTALL,
    )
    section_tokens = count_tokens(section_text)

    # Merge boilerplate (the fixed text of _build_chunk_iteration_prompt
    # when previous_output="" — chunk note + empty merge section)
    empty_prompt = _build_chunk_iteration_prompt(
        "", "", chunk_index=1 if total_chunks else 0,
        total=total_chunks,
    )
    merge_tokens = count_tokens(empty_prompt)

    return system_tokens + section_tokens + merge_tokens


def _take_window(text: str, start: int, max_tokens: int) -> str:
    """Take roughly *max_tokens* from *text* starting at character offset *start*.

    Returns a substring broken at the nearest paragraph boundary for
    readability.  Returns empty string when *start* is past the end.
    """
    if start >= len(text):
        return ""

    max_chars = max_tokens * 4  # rough char → token ratio
    end = min(start + max_chars, len(text))
    window = text[start:end]

    if len(window) <= max_chars * 0.5:
        return window  # already small enough, don't trim further

    # Try to break at the last paragraph boundary within the window
    last_break = window.rfind('\n\n')
    if last_break > max_chars * 0.5:
        return window[:last_break]

    # Fall back to the last newline
    last_nl = window.rfind('\n')
    if last_nl > max_chars * 0.5:
        return window[:last_nl]

    return window


# ── No-LLM fallback: return raw matching file content ─────────────────

def _gather_raw(
    research_files: dict[str, str],
    section: dict[str, Any],
    strategy: str,
    data_needed: str,
) -> dict[str, Any]:
    """When no LLM is available, return the best-matching file(s) as raw text.

    The downstream component generator can still use this — it just has to
    work a bit harder to find the relevant bits.
    """
    section_title = section.get("title", "")
    matching = _match_section_to_files(data_needed, section_title, research_files)

    if not matching:
        return _mock_gather(strategy, data_needed, section)

    # Combine matching file contents
    combined = "\n\n".join(matching.values())

    if strategy == "single_lookup":
        return {"fields_text": combined[:3000]}
    else:
        est_count = section.get("est_count")
        return {"items_text": combined[:5000], "count": est_count or 0}


# ── Fulfilled-flag parsing ────────────────────────────────────────────

_FULFILLED_RE = re.compile(
    r'(?:-{1,}\s*)?REQUIREMENT[_\s]?FUL(?:FILL|FL|FIL)(?:ED|MENT)?\s*:\s*(true|false)(?:\s*-{1,})?',
    re.IGNORECASE,
)


def _parse_fulfilled_flag(text: str) -> tuple[str, bool]:
    """Extract and remove the ``REQUIREMENT_FULFILLED`` flag from *text*.

    Returns ``(text_without_flag, is_fulfilled)``.  If no flag is found
    the original text is returned unchanged with ``is_fulfilled=False``.

    Also scans for "N/A" values in the extracted text. If N/A is present
    and the LLM claimed fulfilled=true, overrides to false — the LLM
    should not claim completion when fields are still missing.
    """
    match = _FULFILLED_RE.search(text)
    if not match:
        return text, False
    is_fulfilled = match.group(1).lower() == "true"
    clean = _FULFILLED_RE.sub("", text).strip()

    # Check for N/A values — if present, requirement is NOT fulfilled
    na_count = _count_na_values(clean)
    if na_count > 0 and is_fulfilled:
        logger.info("Researcher: LLM claimed fulfilled=true but %d N/A values found, overriding to false", na_count)
        is_fulfilled = False

    return clean, is_fulfilled


def _count_na_values(text: str) -> int:
    """Count 'N/A' placeholder values in extracted text.

    Matches: N/A, n/a, N/A., n/a:, "N/A", etc. — but not substrings
    like 'banana' or 'NASA'. Uses word-boundary matching.
    """
    # Match N/A as a standalone value (after : or = or at start of line)
    # Also matches "N/A" in quotes
    na_matches = re.findall(r'(?<![a-zA-Z])[Nn]/[Aa](?![a-zA-Z])', text)
    return len(na_matches)


# ── Prompt building ──────────────────────────────────────────────────

def _build_researcher_prompt(
    context: str,
    section_title: str,
    section_type: str,
    data_needed: str,
    strategy: str,
    chunk_label: str = "",
) -> str:
    """Build the user prompt for an LLM extraction call."""
    chunk_note = ""
    if chunk_label:
        chunk_note = f"\n> ⚠️ This is {chunk_label}. Only extract what's here.\n"

    # Truncate context if still too large
    if count_tokens(context) > _MAX_CONTEXT_TOKENS:
        context = _truncate_to_tokens(context, _MAX_CONTEXT_TOKENS)

    return (
        f"## Section\n"
        f"- **Title**: {section_title}\n"
        f"- **Widget**: {section_type}\n"
        f"- **Research Strategy**: {strategy}\n"
        f"- **Data Needed**: {data_needed}\n"
        f"{chunk_note}"
        f"## Research Data (cached web search results)\n"
        f"```\n{context}\n```\n\n"
        f"Extract the data values for this section from the research above. "
        f"Output as plain text — list each field and its value(s). "
        f"Include ALL items. Copy values exactly from the source."
    )


def _build_chunk_iteration_prompt(
    base_prompt: str,
    previous_output: str,
    *,
    chunk_index: int,
    total: int | None,
    est_count: int | None = None,
) -> str:
    """Enrich *base_prompt* with chunk-awareness and iterative-merge context.

    - Always appends a chunk-position indicator.  When *total* is known it
      shows ``📄 Window N of M``; otherwise just ``📄 Window N``.
    - When *previous_output* is non-empty, appends the accumulated extract
      from earlier chunks together with merge instructions.  The LLM is
      expected to produce the **complete** dataset (not just new additions).
    - When *est_count* is provided, appends an item-count constraint so the
      LLM knows to stop adding items once the target is met.

    The research-data block inside *base_prompt* is NOT modified here —
    ``_extract_single`` / ``_inject_context_into_prompt`` handles that
    per chunk.
    """
    prompt = base_prompt.rstrip()

    # ── Chunk position indicator ─────────────────────────────────
    position_label = f"{chunk_index + 1} of {total}" if total else f"{chunk_index + 1}"
    if chunk_index == 0:
        chunk_note = (
            f"\n\n> 📄 **Window {position_label}** — "
            f"This is the first window. Extract all relevant data you can find. "
            f"If the research data above does NOT contain the requested information, "
            f"output \"N/A\" for each field — do NOT invent or guess values."
        )
    else:
        chunk_note = (
            f"\n\n> 📄 **Window {position_label}** — "
            f"Previous windows have already been processed. See the accumulated "
            f"output below and merge in any new data from this window."
        )
    prompt += chunk_note

    # ── Item-count constraint ────────────────────────────────────
    if est_count is not None and est_count > 0:
        prompt += (
            f"\n\n> 🔢 **Items Needed: {est_count}** — "
            f"Extract exactly {est_count} items, no more. If the research data "
            f"contains more candidates, keep only the {est_count} most relevant / "
            f"highest-rated ones. If fewer than {est_count} are available, "
            f"extract all and mark REQUIREMENT_FULFILLED as false."
        )

    # ── Iterative merge section (only after first chunk) ──────────
    if previous_output:
        total_ref = f" (Window {chunk_index + 1} of {total})" if total else f" (Window {chunk_index + 1})"
        prompt += (
            f"\n\n## 📋 Previously Extracted Data (from windows 1–{chunk_index})\n"
            f"Below is the complete output accumulated so far. "
            f"Your job is to **merge** data from the current window"
            f"{total_ref} into this dataset.\n\n"
            f"### Merge rules:\n"
            f"- **ADD** any new fields or items that appear in the current window "
            f"but are missing from the previous output.\n"
            f"- **REFINE** existing values if the current window has more accurate "
            f"or complete information for the same field/item.\n"
            f"- **REPLACE** placeholder values (N/A, mock data, partial values) "
            f"with real data from the current window.\n"
            f"- **KEEP** all previously extracted data that still looks correct "
            f"and is not contradicted by the current window.\n"
            f"- Output the **COMPLETE** merged dataset — not just the additions. "
            f"The final output should look identical in structure to the "
            f"previous output, but with any new or improved data incorporated.\n\n"
            f"### Previous output:\n"
            f"```\n{previous_output}\n```\n\n"
            f"Now extract data from the current window and output the complete "
            f"merged dataset. Start your response with the first field/item "
            f"and include everything — do NOT write \"previous output was\" "
            f"or any preamble."
        )

    return prompt


# ── Response cleanup ─────────────────────────────────────────────────

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


def _inject_context_into_prompt(user_prompt: str, context: str) -> str:
    """Replace the research-data block in *user_prompt* with *context*.

    ``_build_researcher_prompt`` wraps the research data inside:
        ## Research Data (cached web search results)
        ``` … ```

    This helper replaces everything between those triple-backtick fences
    (inclusive) with a new fenced block containing *context*.  If the
    pattern is not found the context is appended to the prompt.
    """
    # Match from "## Research Data" through the closing ``` (inclusive).
    # The original builder always emits exactly this heading.
    pattern = r'(## Research Data \(cached web search results\)\s*\n)```.*?```'
    replacement = rf'\1```\n{context}\n```'
    new_prompt, count = re.subn(pattern, replacement, user_prompt, flags=re.DOTALL)

    if count == 0:
        logger.warning("_inject_context_into_prompt: research-data block not found, appending context")
        new_prompt = user_prompt + f"\n\n## Research Data (cached web search results)\n```\n{context}\n```"

    return new_prompt


# ── Item counting ────────────────────────────────────────────────────

def _estimate_item_count(extracted_text: str, est_count: int | None = None) -> int:
    """Estimate the number of items in extracted text.

    Looks for patterns like:
      - "items (5 total):"
      - "- name: ..." (bullet list items)
      - "## N. Title" (numbered sections)
    """
    # Explicit count in text
    count_match = re.search(r'(\d+)\s+total', extracted_text, re.IGNORECASE)
    if count_match:
        return int(count_match.group(1))

    # Count bullet items (lines starting with - or *)
    bullets = re.findall(r'^\s*[-*]\s+\w', extracted_text, re.MULTILINE)
    if bullets:
        return len(bullets)

    # Count numbered sections
    numbered = re.findall(r'^##\s+\d+\.', extracted_text, re.MULTILINE)
    if numbered:
        return len(numbered)

    return est_count or 0


# ── Research Store loading ────────────────────────────────────────────

def _load_research_for_topic(
    topic: str,
    intent: str,
    store_dir: Path,
) -> dict[str, str] | None:
    """Load research files for a plan topic/intent from the local store.

    Returns a dict of {filename → file_content} if a match is found,
    or None if no cached research exists for this topic.
    """
    index_path = store_dir / "index.json"
    if not index_path.is_file():
        logger.info("Research store index not found at %s", index_path)
        return None

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse research store index: %s", e)
        return None

    topic_data = index.get("topics", {}).get(topic)
    if not topic_data:
        logger.info("Research store: no data for topic '%s'", topic)
        return None

    queries = topic_data.get("queries", {})
    matching_query = _find_matching_query(queries, intent, topic)

    if not matching_query:
        return None

    file_paths = matching_query.get("files", [])
    return _load_files(file_paths, store_dir)


def _find_matching_query(
    queries: dict[str, dict],
    intent: str,
    topic: str,
) -> dict | None:
    """Find the best matching query entry based on keyword overlap with the intent."""
    if not queries:
        return None

    intent_lower = intent.lower()
    best_match: tuple[dict, int] | None = None

    for query_id, query_data in queries.items():
        keywords = query_data.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in intent_lower)
        if score > 0 and (best_match is None or score > best_match[1]):
            best_match = (query_data, score)

    if best_match:
        logger.info("Research store: matched query '%s' (score=%d)",
                     best_match[0].get("label", "?"), best_match[1])
        return best_match[0]

    # Fallback: if only one query exists for this topic, use it
    if len(queries) == 1:
        only_query = next(iter(queries.values()))
        logger.info("Research store: using only available query '%s'",
                     only_query.get("label", "?"))
        return only_query

    return None


def _load_files(file_paths: list[str], store_dir: Path) -> dict[str, str]:
    """Load all research files into a {filename → content} dict."""
    files: dict[str, str] = {}
    for rel_path in file_paths:
        full_path = store_dir / rel_path
        if full_path.is_file():
            try:
                files[rel_path] = full_path.read_text(encoding="utf-8")
                logger.debug("Research store: loaded %s (%d chars)",
                             rel_path, len(files[rel_path]))
            except OSError as e:
                logger.warning("Failed to read research file %s: %s", rel_path, e)
        else:
            logger.debug("Research store: file not found — %s", rel_path)
    return files


# ── Section-to-file matching (for no-LLM fallback) ───────────────────

# Keywords that map section data_needed descriptions to research store file names.
_SECTION_FILE_MAP: list[tuple[str, list[str]]] = [
    ("overview",       ["overview", "destination", "weather", "hero", "lead",
                        "trip overview", "summary", "intro"]),
    ("scenic_spots",   ["scenic", "attraction", "spot", "sight", "landmark",
                        "image", "photo", "rating"]),
    ("itinerary",      ["itinerary", "timeline", "schedule", "time slot",
                        "day plan", "route", "hour"]),
    ("parking_transport", ["parking", "transport", "traffic", "bus", "metro",
                           "subway", "bike", "taxi", "driving"]),
    ("dining",         ["dining", "restaurant", "food", "cuisine", "dish",
                        "eat", "lunch", "dinner", "chef"]),
    ("travel_tips",    ["tip", "advice", "season", "bring", "wear", "pack",
                        "photo spot", "etiquette", "custom"]),
]


def _match_section_to_files(
    data_needed: str,
    section_title: str,
    research_files: dict[str, str],
) -> dict[str, str]:
    """Find which research file(s) best match the section's data needs."""
    search_text = (section_title + " " + data_needed).lower()
    scored: list[tuple[str, int]] = []

    for file_path, content in research_files.items():
        file_stem = Path(file_path).stem
        score = 0

        for pattern, keywords in _SECTION_FILE_MAP:
            if pattern in file_stem:
                for kw in keywords:
                    if kw.lower() in search_text:
                        score += 1
                if any(part in search_text for part in file_stem.split("_")):
                    score += 3
                break

        if score > 0:
            scored.append((file_path, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    matched = {fp: research_files[fp] for fp, sc in scored if sc >= 1}

    if matched:
        logger.info("Section data match: files=%s", [Path(f).stem for f in matched])

    return matched


# ── Mock / fallback ──────────────────────────────────────────────────

async def _gather_all_mock(plan: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Return mock data for every section (no research store available)."""
    result: dict[int, dict[str, Any]] = {}
    for section in plan.get("sections", []):
        idx = section.get("index", 0)
        strategy = section.get("research_strategy", "none")
        data_needed = section.get("data_needed", "")
        if strategy == "none" or not data_needed.strip():
            result[idx] = {}
        else:
            result[idx] = _mock_gather(strategy, data_needed, section)
    return result


def _mock_gather(
    strategy: str,
    data_needed: str,
    section: dict[str, Any],
) -> dict[str, Any]:
    """Return mock/placeholder data when no research store data is available."""
    fields = _parse_fields_from_description(data_needed)
    est_count = section.get("est_count")

    if strategy == "single_lookup":
        return {"fields": {f: f"[MOCK] {f} value" for f in fields}}

    if strategy in ("search_all", "iterate_days"):
        item_count = est_count if isinstance(est_count, int) and est_count > 0 else 3
        items = [{f: f"[MOCK] item {i} {f}" for f in fields} for i in range(item_count)]
        return {"items": items, "count": len(items)}

    return {"fields": {f: f"[MOCK] {f}" for f in fields}}


def _parse_fields_from_description(data_needed: str) -> list[str]:
    """Extract field names from a natural-language data_needed description."""
    fields: list[str] = []
    text = data_needed
    if ":" in text:
        parts = text.split(":", 1)
        if "for each" in parts[0].lower():
            text = parts[1]

    matches = re.findall(r'(\w+(?:_\w+)*)\s*\([^)]+\)', text)
    if matches:
        fields = matches
    else:
        parts = [p.strip() for p in text.split(",")]
        for p in parts:
            word_match = re.match(r'(\w+(?:_\w+)*)', p)
            if word_match:
                fields.append(word_match.group(1))

    return fields if fields else ["title", "description"]


# ── Chunking ─────────────────────────────────────────────────────────

def _chunk_text(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into token-aware chunks at paragraph boundaries.""" # This is breading in paragraph boundaries, see if we able to make it break at sentence boundaries.
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)

        if current_tokens + para_tokens > target_tokens and current:
            chunks.append("\n\n".join(current))
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


# ── Data attachment (for merging research results into plan) ──────────

def attach_data_to_plan(
    plan: dict[str, Any],
    gathered: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Attach gathered data to each section in the plan.

    Returns a new plan dict (deep copy) with each section's
    `gathered_data` field populated.
    """
    import copy
    enriched = copy.deepcopy(plan)
    for section in enriched.get("sections", []):
        idx = section.get("index", 0)
        section["gathered_data"] = gathered.get(idx, {})
    return enriched
