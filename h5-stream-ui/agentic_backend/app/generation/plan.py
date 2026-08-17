"""
Plan: Generate a structured content plan from the user query.

The Planner agent accepts a short user input (e.g. "Help me plan a oneday
trip to Hangzhou"), detects the topic/intent, and produces a structured plan
with per-section widget assignments and data requirements.

The output is JSONL — one JSON object per line — that downstream agents consume:
  1. Researcher agent reads data_needed per section → gathers data
  2. Composer agent reads section specs + gathered data → generates HTML

Pipeline:
  1. Load system prompt + inject topic layout guidance
  2. LLM generates JSONL text
  3. parse_plan_jsonl() — line-by-line parser
  4. verify_plan_quality() — semantic quality checks
  5. If checks fail → compact feedback → regenerate (max 2 retries)
  6. validate_plan() — normalise and apply defaults
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader
from app.utils.token_counter import count_tokens

if TYPE_CHECKING:
    from app.utils.plan_metrics import PlanMetricsRecorder

logger = logging.getLogger(__name__)

class PlanGenerationError(Exception):
    """Raised when plan generation fails all retry attempts.

    Surfaces the failure so callers (e.g. the orchestrator) can decide how to
    handle it (shell with placeholders, hard fallback, etc.) instead of silently
    continuing with a fallback plan.
    """


# Maximum regeneration attempts (1 initial + 2 retries)
MAX_REGENERATIONS = 2

# Directory containing topic-specific layout guidance
_TOPIC_LAYOUTS_DIR = Path(__file__).resolve().parent / "prompts" / "topic_layouts"

# ── Valid value sets ──────────────────────────────────────────────────

VALID_TOPICS = frozenset({
    "travel_plan", "stock_analysis", "weather", "product_listing", "general",
})

VALID_WIDGETS = frozenset({
    "lead", "body_list", "body_numbered_list", "body_grid",
    "body_block", "body_chips", "body_timeline", "body_cards", "body_table",
})
# Think more about this card type generation, possibly, each section could be a card, and we can do recursive generation
# for large chunk data generation.
VALID_CARD_TYPES = frozenset({
    "simple_card", "data_table", "dashboard", "form",
    "list_detail", "chart_view", "multi_section",
})

VALID_RESEARCH_STRATEGIES = frozenset({
    "single_lookup", "search_all", "iterate_days", "none",
})

VALID_SPACING = frozenset({"compact", "normal", "relaxed"})


# ── JSONL template shown in the user prompt ──────────────────────────

PLAN_JSONL_TEMPLATE = """## Output Format (JSONL — one JSON object per line)

Line 1 — topic classification:
{"topic": "<topic>", "intent": "<one-line summary of what the user wants>"}

Line 2 — global structure:
{"global": {"desc": "<one paragraph describing the overall page structure and flow>", "card_type": "multi_section"}}

Lines 3+ — sections (one per content block, numbered 0, 1, 2, ...):
{"section": <N>, "title": "<name>", "widget": "<widget_name>", "desc": "<what it shows>", "data": "<data fields needed>", "research": "<strategy>", "repeatable": <bool>, "est_count": <number or null>}

Available widgets: lead, body_list, body_numbered_list, body_grid, body_block, body_chips, body_timeline, body_cards, body_table
Research strategies: single_lookup, search_all, iterate_days, none
Topics: travel_plan, stock_analysis, weather, product_listing, general"""


# ── Main entry point ─────────────────────────────────────────────────

async def create_layout_plan(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    metrics: "PlanMetricsRecorder | None" = None,
    session_id: str = "",
    plan_fail_mode: str = "error",
) -> dict[str, Any]:
    """Generate a structured content plan with topic detection and widget assignment.

    Attempts up to 1 + MAX_REGENERATIONS times. Each attempt is recorded
    in the metrics recorder for observability.
    """
    system_prompt = _build_system_prompt(prompt_loader)
    model = llm._client.model if hasattr(llm, '_client') else "unknown"
    query_preview = query[:80]

    user_prompt = _build_user_prompt(query, feedback=None)
    plan: dict[str, Any] = {}
    final_success = False

    for attempt in range(MAX_REGENERATIONS + 1):
        t_start = time.monotonic()

        # ── Call LLM ────────────────────────────────────────────
        raw = ""
        parse_failed = False
        try:
            raw = await llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                step_name=f"plan{'_retry' + str(attempt) if attempt > 0 else ''}",
                max_tokens=4096,
                log_label=f"plan{'_retry' + str(attempt) if attempt > 0 else ''}",
            )
        except Exception as e:
            logger.error("Plan attempt %d failed: %s", attempt, e)
            if metrics:
                metrics.record_attempt(
                    session_id=session_id, attempt=attempt,
                    success=False, parse_failed=True,
                    failure_reasons=[f"LLM exception: {str(e)[:100]}"],
                    duration_ms=(time.monotonic() - t_start) * 1000,
                    model=model, query_preview=query_preview,
                )
            if attempt < MAX_REGENERATIONS:
                user_prompt = _build_user_prompt(
                    query, feedback="LLM call failed — please try again."
                )
                continue
            if plan_fail_mode == "error":
                raise PlanGenerationError(
                    f"Plan LLM call failed on final attempt: {e}"
                ) from e
            return _fallback_plan()

        output_tokens = count_tokens(raw)
        duration_ms = (time.monotonic() - t_start) * 1000

        # ── Parse JSONL ─────────────────────────────────────────
        plan, parse_errors = parse_plan_jsonl(raw)

        if parse_errors:
            parse_failed = True
            logger.warning("Plan attempt %d: %d parse errors: %s",
                           attempt, len(parse_errors),
                           [e[:80] for e in parse_errors[:3]])

        # ── Validate structure ──────────────────────────────────
        plan = validate_plan(plan)

        # ── Quality checks ──────────────────────────────────────
        passed, issues = verify_plan_quality(plan, query)

        # Record the attempt
        if metrics:
            metrics.record_attempt(
                session_id=session_id, attempt=attempt,
                success=passed and not parse_failed,
                parse_failed=parse_failed,
                failure_reasons=parse_errors + issues,
                regenerate_succeeded=None if attempt == 0 else (passed and not parse_failed),
                input_tokens=count_tokens(system_prompt) + count_tokens(user_prompt),
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                card_type=plan.get("card_type", ""),
                section_count=len(plan.get("sections", [])),
                binding_count=0,  # No longer tracking bindings
                model=model,
                query_preview=query_preview,
            )

        # ── Success or retry ────────────────────────────────────
        all_issues = parse_errors + issues
        if not all_issues:
            final_success = True
            logger.info("Plan attempt %d: PASSED (topic=%s, %d sections)",
                         attempt, plan.get("topic", "?"),
                         len(plan.get("sections", [])))
            break

        logger.warning("Plan attempt %d: %d issues — %s",
                       attempt, len(all_issues),
                       "; ".join(all_issues[:3]))

        if attempt < MAX_REGENERATIONS:
            feedback = _build_feedback(parse_errors, issues)
            user_prompt = _build_user_prompt(query, feedback=feedback)
        else:
            logger.error("Plan failed after %d attempts. plan_fail_mode=%s",
                         MAX_REGENERATIONS + 1, plan_fail_mode)
            if plan_fail_mode == "error":
                raise PlanGenerationError(
                    f"Plan generation failed after {MAX_REGENERATIONS + 1} attempts "
                    f"(last issues: {all_issues[:3]})"
                )
            return _fallback_plan()

    return plan


# ── System prompt assembly ────────────────────────────────────────────

def _build_system_prompt(prompt_loader: PromptLoader) -> str:
    """Load the plan system prompt and inject topic layout guidance."""
    system_prompt = prompt_loader.load_for_step("plan")
    topic_guidance = _load_topic_layouts()
    return system_prompt.replace("{{TOPIC_LAYOUT_GUIDANCE}}", topic_guidance)


def _load_topic_layouts() -> str:
    """Load all topic layout guidance files and concatenate them.

    Returns a markdown string with all topic-specific layout sections.
    """
    parts: list[str] = []
    if _TOPIC_LAYOUTS_DIR.is_dir():
        for fpath in sorted(_TOPIC_LAYOUTS_DIR.glob("*.md")):
            try:
                text = fpath.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            except Exception:
                logger.warning("Failed to read topic layout file: %s", fpath)
    if not parts:
        # Fallback: minimal general guidance
        return """## General Layout
When no specific topic guidance is available, use your best judgment:
- Section 0 must be `lead` — it frames the page with a title and overview.
- Choose widgets that match the CONTENT SHAPE, not the topic name.
- Be specific in the `data` field about what the researcher needs to find."""
    return "\n\n".join(parts)


# ── JSONL Parser ──────────────────────────────────────────────────────

def parse_plan_jsonl(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse the refined JSONL plan output into the internal dict format.

    Each line is parsed independently. Malformed lines are skipped.
    Section lines are self-contained — no cascading context needed.
    The researcher will attach data to sections later.

    Returns:
        (plan_dict, error_messages) — plan is always a dict (may be empty).
    """
    plan: dict[str, Any] = {
        "topic": "general",
        "intent": "",
        "global_desc": "",
        "card_type": "multi_section",
        "sections": [],
    }
    errors: list[str] = []

    lines = _extract_json_lines(text)

    for i, line in enumerate(lines):
        obj = _safe_json_parse(line)
        if obj is None:
            errors.append(f"line {i}: unparseable JSON — {line[:60]}")
            continue

        # ── Topic line ──
        if "topic" in obj:
            plan["topic"] = str(obj.get("topic", "general"))
            plan["intent"] = str(obj.get("intent", ""))
            continue

        # ── Global line ──
        if "global" in obj and isinstance(obj["global"], dict):
            g = obj["global"]
            plan["global_desc"] = str(g.get("desc", ""))
            plan["card_type"] = str(g.get("card_type", "multi_section"))
            continue

        # ── Section line ──
        if "section" in obj and "widget" in obj:
            section = {
                "index": int(obj.get("section", len(plan["sections"]))),
                "title": str(obj.get("title", "")),
                "widget": str(obj.get("widget", "body_block")),
                "desc": str(obj.get("desc", "")),
                "data_needed": str(obj.get("data", "")),
                "research_strategy": str(obj.get("research", "none")),
                "is_repeatable": bool(obj.get("repeatable", False)),
                "est_count": _parse_est_count(obj.get("est_count")),
            }
            plan["sections"].append(section)
            continue

        # ── Legacy interaction line (pass through for now) ──
        if "interaction" in obj:
            # Ignored in new format — interactions are now implied by widget
            # and handled by the composer. Keep parsing for forward compat.
            continue

        # ── Legacy data line (pass through) ──
        if "data" in obj and isinstance(obj["data"], dict):
            # Old-format data summary — ignored in new format
            continue

        # ── Unrecognised line ──
        errors.append(f"line {i}: unrecognised object — {line[:60]}")

    return plan, errors


def _parse_est_count(value: Any) -> int | None:
    """Parse est_count — accept numbers, numeric strings, or null."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if isinstance(value, str):
        try:
            n = int(value)
            return n if n > 0 else None
        except ValueError:
            return None
    return None


# ── Quality verifier ──────────────────────────────────────────────────

def verify_plan_quality(plan: dict, query: str) -> tuple[bool, list[str]]:
    """Check the plan for semantic quality issues.

    Returns:
        (passed, issues) — passed is True when there are NO issues.
    """
    issues: list[str] = []

    sections = plan.get("sections", [])

    # 1. Must have at least one section
    if not sections:
        issues.append("NO_SECTIONS: plan has zero sections — at least one section is required")
        return False, issues  # Fatal

    # 2. First section must be lead
    if sections[0].get("widget") != "lead":
        issues.append("MISSING_LEAD: section 0 must use the 'lead' widget — it frames the page")

    # 3. Topic must be valid
    if plan.get("topic") not in VALID_TOPICS:
        issues.append(f"INVALID_TOPIC: '{plan.get('topic')}' is not a recognized topic")

    # 4. All widgets must be valid
    for i, s in enumerate(sections):
        w = s.get("widget", "")
        if w not in VALID_WIDGETS:
            issues.append(f"INVALID_WIDGET: section {i} has unknown widget '{w}'")

    # 5. All research strategies must be valid
    for i, s in enumerate(sections):
        rs = s.get("research_strategy", "")
        if rs not in VALID_RESEARCH_STRATEGIES:
            issues.append(f"INVALID_RESEARCH: section {i} has unknown strategy '{rs}'")

    # 6. Each section should have a data_needed description (unless research=none)
    for i, s in enumerate(sections):
        if s.get("research_strategy") != "none" and not s.get("data_needed", "").strip():
            issues.append(f"MISSING_DATA_NEEDED: section {i} ('{s.get('title')}') has research={s.get('research_strategy')} but no data_needed description")

    # 7. Section indices should be sequential
    for i, s in enumerate(sections):
        if s.get("index", i) != i:
            issues.append(f"NON_SEQUENTIAL_INDEX: section at position {i} has index {s.get('index')}")

    # 8. Global description check (non-blocking — auto-filled in validate_plan)
    if not plan.get("global_desc", "").strip():
        issues.append("EMPTY_GLOBAL_DESC: global description is empty (auto-filled by validate_plan)")

    # 9. Intent should not be empty
    if not plan.get("intent", "").strip():
        issues.append("EMPTY_INTENT: intent is empty — should summarize what the user wants")

    # 10. Check for duplicate section titles (possible LLM hallucination)
    titles = [s.get("title", "") for s in sections]
    if len(titles) != len(set(titles)):
        issues.append("DUPLICATE_TITLES: multiple sections have the same title — each section should be distinct")

    return len(issues) == 0, issues


# ── Validate & normalise (safety net) ─────────────────────────────────

def validate_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise the plan dict. Applies defaults for missing/invalid fields.

    This runs AFTER parse_plan_jsonl(), so the input dict should already
    have the right shape — this is a safety net.
    """
    plan: dict[str, Any] = {}

    # --- topic ---
    topic = str(raw.get("topic", "general"))
    plan["topic"] = topic if topic in VALID_TOPICS else "general"

    # --- intent ---
    plan["intent"] = str(raw.get("intent", ""))

    # --- global_desc (auto-fill if empty) ---
    gd = str(raw.get("global_desc", "")).strip()
    if not gd:
        # LLM didn't output the global line — derive a default from intent/sections
        intent = str(raw.get("intent", "")).strip()
        sections = raw.get("sections", [])
        if intent:
            gd = intent
        elif sections:
            titles = [s.get("title", "") for s in sections if s.get("title")]
            gd = f"A multi-section page with: {', '.join(titles[:5])}."
        else:
            gd = "A multi-section content page."
    plan["global_desc"] = gd

    # --- card_type ---
    ct = raw.get("card_type", "multi_section")
    plan["card_type"] = ct if ct in VALID_CARD_TYPES else "multi_section"

    # --- sections ---
    raw_sections = raw.get("sections", [])
    if not isinstance(raw_sections, list):
        raw_sections = []

    clean_sections: list[dict[str, Any]] = []
    for i, s in enumerate(raw_sections):
        if not isinstance(s, dict):
            continue

        widget = str(s.get("widget", "body_block"))
        if widget not in VALID_WIDGETS:
            widget = "body_block"

        research = str(s.get("research_strategy", "none"))
        if research not in VALID_RESEARCH_STRATEGIES:
            research = "none"

        clean_sections.append({
            "index": int(s.get("index", i)),
            "title": str(s.get("title", f"Section {i}")),
            "widget": widget,
            "desc": str(s.get("desc", "")),
            "data_needed": str(s.get("data_needed", "")),
            "research_strategy": research,
            "is_repeatable": bool(s.get("is_repeatable", False)),
            "est_count": _parse_est_count(s.get("est_count")),
        })

    # Ensure at least a lead section exists
    has_lead = any(s["widget"] == "lead" for s in clean_sections)
    if not has_lead and clean_sections:
        clean_sections[0]["widget"] = "lead"
        if not clean_sections[0]["title"]:
            clean_sections[0]["title"] = "Overview"

    plan["sections"] = clean_sections

    return plan


# ── Helpers ───────────────────────────────────────────────────────────

def _build_user_prompt(query: str, feedback: str | None = None) -> str:
    """Build the user prompt, optionally with regeneration feedback."""
    prompt = f"""## Task
Analyze this user request. Detect the topic, plan the content structure,
assign widgets to each section, and specify what data each section needs.

## User Request
{query}

{PLAN_JSONL_TEMPLATE}

## Key Rules
- Output in JSONL format — ONE valid JSON object per line — each line starts with '{{' and ends with '}}'. Normal JSON output would be rejected.
- ⚠️ COMPACT JSON ONLY: each object must be on a SINGLE line — no indentation, no newlines inside an object. Bad: multi-line pretty-printed JSON. Good: `{{"key":"value"}}` on one line.
- Section 0 MUST be 'lead' — it frames the entire page
- Choose widgets that match the CONTENT SHAPE, not just the topic name
- The 'data' field should be specific: name each field, its type, and any constraints
- The 'research' field tells the researcher how to gather data: single_lookup | search_all | iterate_days | none
- est_count: use a number if you can estimate from the request, null if unknown
- Keep each line concise — downstream agents will read this plan"""

    if feedback:
        prompt += f"""

## ⚠️ PREVIOUS ATTEMPT HAD ISSUES — FIX THESE:
{feedback}

Please regenerate. Output ONLY the JSONL lines. Fix ALL issues listed above."""

    return prompt


def _build_feedback(parse_errors: list[str], quality_issues: list[str]) -> str:
    """Build compact feedback for regeneration."""
    lines = []
    for e in parse_errors[:3]:
        lines.append(f"- PARSE ERROR: {e}")
    for issue in quality_issues[:5]:
        lines.append(f"- QUALITY CHECK FAILED: {issue}")
    return "\n".join(lines) if lines else "- Unknown error. Please try again."

def _extract_json_lines(text: str) -> list[str]:
    """Extract individual JSON objects from text.

    Handles: raw JSONL, pretty-printed multi-line JSON, markdown-fenced
    blocks, and mixed content with commentary between objects.

    Uses a character-level brace-matching scanner that tracks string context
    and escape sequences. Each returned string is one complete ``{...}``
    JSON object — even if it spans multiple lines in the source.
    """
    # Strip thinking tags first
    text = re.sub(r'<think[^>]*>.*?</think>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<think[^>]*>.*$', '', text, flags=re.IGNORECASE | re.DOTALL)

    # If the entire text is inside a markdown fence, extract it
    fence = re.search(r'```(?:jsonl|json)?\s*\n?(.*?)```', text, re.DOTALL)
    if fence:
        text = fence.group(1)

    lines: list[str] = []
    depth = 0
    in_string = False
    escape_next = False
    buf: list[str] = []

    for char in text:
        if in_string:
            buf.append(char)
            if escape_next:
                escape_next = False
            elif char == '\\':
                escape_next = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            if depth > 0:
                buf.append(char)
        elif char == '{':
            if depth == 0:
                buf = ['{']
                depth = 1
            else:
                buf.append(char)
                depth += 1
        elif char == '}':
            if depth > 0:
                buf.append(char)
                depth -= 1
                if depth == 0:
                    lines.append(''.join(buf))
                    buf = []
        else:
            if depth > 0:
                buf.append(char)

    return lines


def _safe_json_parse(text: str) -> dict | None:
    """Parse a single line of JSON, returning None on any failure."""
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _fallback_plan() -> dict:
    """Minimal fallback plan when all attempts fail."""
    return {
        "topic": "general",
        "intent": "Generic content display",
        "global_desc": "A simple content card with a lead section and a text body.",
        "card_type": "simple_card",
        "sections": [
            {
                "index": 0, "title": "Overview", "widget": "lead",
                "desc": "Page lead with title and summary",
                "data_needed": "title text, summary text",
                "research_strategy": "none", "is_repeatable": False,
                "est_count": None,
            },
            {
                "index": 1, "title": "Content", "widget": "body_block",
                "desc": "Main content body",
                "data_needed": "content text",
                "research_strategy": "none", "is_repeatable": False,
                "est_count": None,
            },
        ],
        "style_preferences": {
            "accent_color": "#0A59F7",
            "card_radius": "20px",
            "spacing_scale": "normal",
            "harmony_mode": False,
        },
    }
