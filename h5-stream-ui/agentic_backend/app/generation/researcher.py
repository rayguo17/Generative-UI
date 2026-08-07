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

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

from app.utils.token_counter import count_tokens

logger = logging.getLogger(__name__)

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

## Output Format
Output plain text — NOT JSON. List each field and its value(s):

```
destination: "Hangzhou West Lake"
weather: "Spring, 15-25°C, occasional rain"
hero_description: "Experience the best of Hangzhou in one day..."

items (5 total):
  - name: "Three Pools Mirroring the Moon", rating: ★★★★★, price: ¥55, ...
  - name: "Leifeng Pagoda", rating: ★★★★★, price: ¥40, ...
  ...
```

## Rules
- Include ALL items for list fields — don't sample just the first one
- Copy values EXACTLY: don't truncate URLs, don't round numbers
- Preserve markdown formatting from the source where useful (tables, bullets)
- If a value cannot be found, write "N/A"
- If you only see part of the data (this is a chunk), just extract what's here
- Output ONLY the data — no preamble, no commentary, no markdown fences"""


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
    prompt_overhead = count_tokens(RESEARCHER_SYSTEM_PROMPT) + 200
    # GenerationLlmClient stores budget on _client; tolerate both access paths
    budget = getattr(llm, 'token_budget', None)
    if budget is None and hasattr(llm, '_client'):
        budget = getattr(llm._client, 'token_budget', None)
    available_for_context = max(
        int((budget or 4096) * BUDGET_THRESHOLD) - prompt_overhead, 500,
    )

    if context_tokens <= available_for_context:
        extracted = await _extract_single(llm, combined_text, user_prompt, section_type)
    else:
        logger.info("Researcher [%s]: context too large (%d > %d), chunking",
                     section_type, context_tokens, available_for_context)
        extracted = await _extract_chunked(
            llm, combined_text, user_prompt, section_type,
        )

    if not extracted or not extracted.strip():
        logger.warning("Researcher [%s]: empty LLM response, falling back to mock", section_type)
        return _mock_gather(strategy, data_needed, section)

    # Package the extracted text — the component generator will render from it
    est_count = section.get("est_count")
    if strategy == "single_lookup":
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
) -> str:
    """Extract data in a single LLM call.

    Uses llm._client.generate() directly (bypassing GenerationLlmClient wrappers)
    so we can pass a custom temperature and control the log label per section.
    """
    # Update the log label so each section appears as a distinct entry
    if hasattr(llm, '_client') and hasattr(llm._client, '_log_label'):
        llm._client._log_label = f"research_{section_type}"

    try:
        response = await llm._client.generate(
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=2048,
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
) -> str:
    """For large research data: split into chunks, extract from each, merge."""
    if depth > MAX_DEPTH:
        logger.warning("Researcher: max depth %d, truncating", MAX_DEPTH)
        return await _extract_single(
            llm, text[:CHUNK_TOKENS * 4], user_prompt_template, section_type,
        )

    chunks = _chunk_text(text, CHUNK_TOKENS, CHUNK_OVERLAP)
    logger.info("Researcher level %d: %d chunks", depth, len(chunks))

    if len(chunks) == 1:
        return await _extract_single(llm, text, user_prompt_template, section_type)

    tasks = [
        _extract_single(
            llm, chunk,
            user_prompt_template + f"\n\n> ⚠️ This is chunk {i+1} of {len(chunks)}. Only extract what's in this chunk.",
            section_type,
        )
        for i, chunk in enumerate(chunks)
    ]
    chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

    parts: list[str] = []
    for i, result in enumerate(chunk_results):
        if isinstance(result, Exception):
            logger.error("Chunk %d extraction failed: %s", i, result)
        elif result and result.strip():
            parts.append(result.strip())

    return "\n".join(parts)


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
    max_context_chars = 6000
    if len(context) > max_context_chars:
        context = context[:max_context_chars] + "\n... (truncated)"

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
    """Split text into token-aware chunks at paragraph boundaries."""
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
