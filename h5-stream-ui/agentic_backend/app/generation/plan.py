"""
Plan: Generate a structured layout plan from the user query.

Uses JSONL (JSON Lines) output format — one simple JSON object per line.
Each line is independently parseable, so a single malformed line doesn't
break the entire plan. Lines cascade: section lines are followed by their
child binding lines until the next section line appears.

Pipeline:
  1. LLM generates JSONL text
  2. parse_plan_jsonl() — stateful line-by-line parser
  3. verify_plan_quality() — semantic quality checks
  4. If checks fail → compact feedback appended → regenerate (max 2 retries)
  5. validate_plan() — normalise and apply defaults
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader
from app.utils.token_counter import count_tokens

if TYPE_CHECKING:
    from app.utils.plan_metrics import PlanMetricsRecorder

logger = logging.getLogger(__name__)

# Maximum regeneration attempts
MAX_REGENERATIONS = 2

# ── JSONL format shown in the prompt ───────────────────────────────

PLAN_JSONL_TEMPLATE = """Output ONE valid JSON object per line. Lines cascade — read in order:

{"card": "<type>", "complexity": "<low|medium|high>", "charts": <bool>, "pagination": <bool>, "interactions": <bool>}
{"style": {"accent": "<hex>", "radius": "<CSS>", "spacing": "<compact|normal|relaxed>", "harmony": <bool>}}
{"section": <N>, "type": "<section_type>", "layout": "<horizontal|vertical|grid>", "columns": <int|null>, "repeatable": <bool>}
{"binding": {"path": "$.field", "role": "<visual_role>", "fallback": "N/A"}}
{"binding": {"path": "$.other", "role": "<role>", "fallback": "—"}}
{"section": <N+1>, "type": "...", "layout": "...", ...}
{"binding": ...}
...
{"interaction": {"trigger": "<card_root|row_button>", "action": "<openUrl|setPage|updateData>", "source": "$.path"}}
{"data": {"key": "value", ...}}

RULES:
- One JSON object per line. Each line is complete — no trailing commas, no unclosed braces.
- Lines form a cascade: a {"section":...} line starts a section; all {"binding":...} lines
  that follow belong to that section until the next {"section":...} line.
- section numbering: 0, 1, 2, ... (sequential, top-to-bottom visual order).
- If a section has NO bindings, output the section line without binding lines.
- {"card":...} and {"style":...} must appear BEFORE the first section line.
- {"data":...} is optional — include it when the query has structured data fields.
- binding role: card_title | metric_value | row_label | image_src | button_url | text_content | chip_label
- section type: header | hero_image | metrics_grid | data_table | chart_area | card_list | form_fields | text_block | button_group | footer
- card type: simple_card | data_table | dashboard | form | list_detail | chart_view | multi_section"""


# ── Valid value sets (shared with validate_plan) ────────────────────

VALID_CARD_TYPES = frozenset({
    "simple_card", "data_table", "dashboard", "form",
    "list_detail", "chart_view", "multi_section",
})

VALID_SECTION_TYPES = frozenset({
    "header", "hero_image", "metrics_grid", "data_table",
    "chart_area", "card_list", "form_fields", "text_block",
    "button_group", "footer",
})

VALID_DIRECTIONS = frozenset({"horizontal", "vertical", "grid"})
VALID_VISUAL_ROLES = frozenset({
    "card_title", "metric_value", "row_label", "image_src",
    "button_url", "text_content", "chip_label",
})
VALID_ACTION_TYPES = frozenset({"openUrl", "setPage", "updateData"})
VALID_COMPLEXITIES = frozenset({"low", "medium", "high"})
VALID_SPACING = frozenset({"compact", "normal", "relaxed"})


# ── Main entry point ───────────────────────────────────────────────

async def create_layout_plan(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    metrics: "PlanMetricsRecorder | None" = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Generate a layout plan with verification and regeneration.

    Attempts up to 1 + MAX_REGENERATIONS times. Each attempt is recorded
    in the metrics recorder for observability.
    """
    system_prompt = prompt_loader.load_for_step("plan")
    model = llm._client.model if hasattr(llm, '_client') else "unknown"
    query_preview = query[:80]

    user_prompt = _build_user_prompt(query, feedback=None)
    plan: dict[str, Any] = {}
    final_success = False

    for attempt in range(MAX_REGENERATIONS + 1):
        t_start = time.monotonic()

        # ── Call LLM ──────────────────────────────────────────
        raw = ""
        parse_failed = False
        try:
            raw = await llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                step_name=f"plan{'_retry' + str(attempt) if attempt > 0 else ''}",
                max_tokens=4096,
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
            return _fallback_plan()

        output_tokens = count_tokens(raw)
        duration_ms = (time.monotonic() - t_start) * 1000

        # ── Parse JSONL ───────────────────────────────────────
        plan, parse_errors = parse_plan_jsonl(raw)

        if parse_errors:
            parse_failed = True
            logger.warning("Plan attempt %d: %d parse errors: %s",
                           attempt, len(parse_errors),
                           [e[:80] for e in parse_errors[:3]])

        # ── Validate structure ────────────────────────────────
        plan = validate_plan(plan)

        # ── Quality checks ────────────────────────────────────
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
                binding_count=sum(len(s.get("data_bindings", [])) for s in plan.get("sections", [])),
                model=model,
                query_preview=query_preview,
            )

        # ── Success or retry ──────────────────────────────────
        all_issues = parse_errors + issues
        if not all_issues:
            final_success = True
            logger.info("Plan attempt %d: PASSED (%d sections, %d bindings)",
                         attempt,
                         len(plan.get("sections", [])),
                         sum(len(s.get("data_bindings", [])) for s in plan.get("sections", [])))
            break

        logger.warning("Plan attempt %d: %d issues — %s",
                       attempt, len(all_issues),
                       "; ".join(all_issues[:3]))

        if attempt < MAX_REGENERATIONS:
            # Build targeted feedback for the retry
            feedback = _build_feedback(parse_errors, issues)
            user_prompt = _build_user_prompt(query, feedback=feedback)
        else:
            logger.error("Plan failed after %d attempts. Using best-effort plan.",
                         MAX_REGENERATIONS + 1)

    return plan


# ── JSONL Parser ────────────────────────────────────────────────────

def parse_plan_jsonl(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse JSONL plan output into the internal dict format.

    Each line is parsed independently. Malformed lines are skipped.
    Lines cascade: section lines open a context that binding lines attach to.

    Returns:
        (plan_dict, error_messages) — plan is always a dict (may be empty).
    """
    plan: dict[str, Any] = {
        "sections": [],
        "interaction_intents": [],
        "style_preferences": {},
        "data_summary": {},
    }
    errors: list[str] = []
    current_section: dict[str, Any] | None = None

    lines = _extract_json_lines(text)

    for i, line in enumerate(lines):
        obj = _safe_json_parse(line)
        if obj is None:
            errors.append(f"line {i}: unparseable JSON — {line[:60]}")
            continue

        # ── Card metadata ──
        if "card" in obj:
            plan["card_type"] = obj["card"]
            if "complexity" in obj:
                plan["estimated_complexity"] = obj["complexity"]
            plan["needs_charts"] = bool(obj.get("charts", False))
            plan["needs_pagination"] = bool(obj.get("pagination", False))
            plan["needs_interactions"] = bool(obj.get("interactions", False))
            continue

        # ── Style ──
        if "style" in obj and isinstance(obj["style"], dict):
            plan["style_preferences"] = obj["style"]
            continue

        # ── Section (opens a new section context) ──
        if "section" in obj and "type" in obj:
            # Flush previous section
            current_section = {
                "section_type": obj["type"],
                "layout_direction": obj.get("layout", "vertical"),
                "grid_columns": obj.get("columns"),
                "visual_priority": obj.get("section", len(plan["sections"])),
                "is_repeatable": bool(obj.get("repeatable", False)),
                "data_bindings": [],
            }
            plan["sections"].append(current_section)
            continue

        # ── Binding (attaches to current section) ──
        if "binding" in obj and isinstance(obj["binding"], dict):
            b = obj["binding"]
            binding = {
                "field_path": str(b.get("path", "$")),
                "visual_role": str(b.get("role", "text_content")),
                "fallback": b.get("fallback"),
            }
            if current_section is not None:
                current_section["data_bindings"].append(binding)
            else:
                # Binding without a section — create an implicit text_block
                current_section = {
                    "section_type": "text_block",
                    "layout_direction": "vertical",
                    "grid_columns": None,
                    "visual_priority": len(plan["sections"]),
                    "is_repeatable": False,
                    "data_bindings": [binding],
                }
                plan["sections"].append(current_section)
            continue

        # ── Interaction ──
        if "interaction" in obj and isinstance(obj["interaction"], dict):
            plan["interaction_intents"].append(obj["interaction"])
            continue

        # ── Data summary ──
        if "data" in obj and isinstance(obj["data"], dict):
            plan["data_summary"] = obj["data"]
            continue

        # ── Unrecognised line ──
        errors.append(f"line {i}: unrecognised object — {line[:60]}")

    return plan, errors


# ── Quality verifier ────────────────────────────────────────────────

def verify_plan_quality(plan: dict, query: str) -> tuple[bool, list[str]]:
    """Check the plan for semantic quality issues.

    These are separate from structural validation (validate_plan).
    They catch cases where the plan is syntactically valid but
    semantically wrong — e.g. empty sections, missing bindings,
    card_type mismatch.

    Returns:
        (passed, issues) — passed is True when there are NO issues.
    """
    issues: list[str] = []

    sections = plan.get("sections", [])

    # 1. Must have at least one section
    if not sections:
        issues.append("NO_SECTIONS: plan has zero sections — at least one section is required")
        return False, issues  # Fatal — nothing to render

    # 2. Check if plan looks like a generic fallback
    all_text_block = all(s.get("section_type") == "text_block" for s in sections)
    has_any_bindings = any(s.get("data_bindings") for s in sections)
    if all_text_block and not has_any_bindings and len(sections) == 1:
        issues.append("GENERIC_FALLBACK: single text_block with no data bindings — plan may be a parse-failure fallback")

    # 3. Data bindings should exist when query has structured data
    if _query_has_data(query) and not _plan_has_bindings(plan):
        issues.append("MISSING_BINDINGS: query appears to have structured data but plan has no data_bindings")

    # 4. Card type heuristic: array/list data → card type should handle it
    if _query_has_arrays(query) and plan.get("card_type") == "simple_card":
        issues.append("CARD_TYPE_MISMATCH: query contains list/array data but card_type is 'simple_card' — consider list_detail, data_table, or multi_section")

    # 5. data_summary should be populated when query has data fields
    if _query_has_data(query) and not plan.get("data_summary"):
        issues.append("EMPTY_DATA_SUMMARY: query has data but data_summary is empty")

    # 6. Section-specific sanity: card_list without repeatable=true
    for i, s in enumerate(sections):
        if s.get("section_type") == "card_list" and not s.get("is_repeatable"):
            issues.append(f"CARD_LIST_NOT_REPEATABLE: section {i} is card_list but is_repeatable is false")
            break  # One warning is enough

    # 7. metrics_grid with no grid_columns
    for i, s in enumerate(sections):
        if s.get("section_type") == "metrics_grid" and not s.get("grid_columns"):
            issues.append(f"METRICS_GRID_NO_COLUMNS: section {i} is metrics_grid but grid_columns is not set")
            break

    return len(issues) == 0, issues


# ── Validate & normalise (harness) ──────────────────────────────────

def validate_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise the plan dict. Applies defaults for any missing/invalid fields.

    This runs AFTER parse_plan_jsonl(), so the input dict should already
    have the right shape — this is a safety net.
    """
    plan: dict[str, Any] = {}

    # --- card_type ---
    ct = raw.get("card_type", "simple_card")
    plan["card_type"] = ct if ct in VALID_CARD_TYPES else "simple_card"

    # --- sections ---
    raw_sections = raw.get("sections", [])
    if not isinstance(raw_sections, list) or not raw_sections:
        raw_sections = [{
            "section_type": "text_block",
            "data_bindings": [],
            "layout_direction": "vertical",
            "visual_priority": 0,
            "is_repeatable": False,
        }]

    clean_sections = []
    for i, s in enumerate(raw_sections):
        if not isinstance(s, dict):
            continue
        st = s.get("section_type", "text_block")
        if st not in VALID_SECTION_TYPES:
            st = "text_block"

        bindings = s.get("data_bindings", [])
        if not isinstance(bindings, list):
            bindings = []
        clean_bindings = []
        for b in bindings:
            if not isinstance(b, dict):
                continue
            fp = str(b.get("field_path", "$")).strip()
            vr = str(b.get("visual_role", "text_content")).strip()
            fb = b.get("fallback", None)
            if vr not in VALID_VISUAL_ROLES:
                vr = "text_content"
            clean_bindings.append({
                "field_path": fp if fp else "$",
                "visual_role": vr,
                "fallback": str(fb) if fb is not None else None,
            })

        direction = s.get("layout_direction", "vertical")
        if direction not in VALID_DIRECTIONS:
            direction = "vertical"

        grid_cols = s.get("grid_columns")
        if not isinstance(grid_cols, int) or grid_cols < 1 or grid_cols > 4:
            grid_cols = None

        clean_sections.append({
            "section_type": st,
            "data_bindings": clean_bindings,
            "layout_direction": direction,
            "grid_columns": grid_cols,
            "visual_priority": int(s.get("visual_priority", i)),
            "is_repeatable": bool(s.get("is_repeatable", False)),
        })

    plan["sections"] = clean_sections

    # --- data_summary ---
    ds = raw.get("data_summary", {})
    plan["data_summary"] = ds if isinstance(ds, dict) else {}

    # --- interaction_intents ---
    raw_intents = raw.get("interaction_intents", [])
    if not isinstance(raw_intents, list):
        raw_intents = []
    clean_intents = []
    for intent in raw_intents:
        if not isinstance(intent, dict):
            continue
        at = intent.get("action_type", intent.get("action", ""))
        if at not in VALID_ACTION_TYPES:
            continue
        clean_intents.append({
            "trigger_element": str(intent.get("trigger_element", intent.get("trigger", "card_root"))),
            "action_type": at,
            "params_source": str(intent.get("params_source", intent.get("source", "$"))),
            "condition": intent.get("condition"),
        })
    plan["interaction_intents"] = clean_intents

    # --- style_preferences ---
    sp = raw.get("style_preferences", {})
    if not isinstance(sp, dict):
        sp = {}
    # Normalise keys — JSONL uses short names, internal dict uses full names
    accent = sp.get("accent_color", sp.get("accent", "#0A59F7"))
    radius = sp.get("card_radius", sp.get("radius", "20px"))
    spacing = sp.get("spacing_scale", sp.get("spacing", "normal"))
    if spacing not in VALID_SPACING:
        spacing = "normal"
    harmony = sp.get("harmony_mode", sp.get("harmony", False))
    plan["style_preferences"] = {
        "accent_color": str(accent),
        "card_radius": str(radius),
        "spacing_scale": spacing,
        "harmony_mode": bool(harmony),
    }

    # --- flags ---
    plan["needs_charts"] = bool(raw.get("needs_charts", False))
    plan["needs_pagination"] = bool(raw.get("needs_pagination", False))
    plan["needs_interactions"] = bool(raw.get("needs_interactions", False))

    # --- complexity ---
    cx = raw.get("estimated_complexity", "low")
    plan["estimated_complexity"] = cx if cx in VALID_COMPLEXITIES else "low"

    return plan


# ── Helpers ─────────────────────────────────────────────────────────

def _build_user_prompt(query: str, feedback: str | None = None) -> str:
    """Build the user prompt, optionally with regeneration feedback."""
    prompt = f"""## Task
Analyze this user request for H5 card generation. Infer the intent,
extract data fields, and create a detailed layout plan.

## User Request
{query}

## Output Format (JSONL — one JSON object per line)
{PLAN_JSONL_TEMPLATE}

## Key Rules
- Output ONE valid JSON object per line — each line starts with '{{' and ends with '}}'
- Lines cascade: {{"section":...}} opens a section; following {{"binding":...}} lines belong to it
- card_type: infer from data shape (array→list_detail/data_table, metrics→dashboard, single→simple_card)
- section_type: choose the best visual match for each data group
- binding path: use "$." prefix for JSON paths; map EVERY visible field
- binding role: card_title, metric_value, row_label, image_src, button_url, text_content, chip_label
- harmony_mode: true if user asks for HarmonyOS style
- charts: true if data has numeric trends/comparisons
- pagination: true if data has >10 rows/items
- interactions: true if user mentions clicks/links/navigation/pagination
- Keep each line concise — the next step will turn this into HTML"""

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

    Handles: raw JSONL, markdown-fenced blocks, and mixed content.
    Each returned line is one complete JSON object string.
    """
    # Strip thinking tags first
    text = re.sub(r'<think[^>]*>.*?</think>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<think[^>]*>.*$', '', text, flags=re.IGNORECASE | re.DOTALL)

    # If the entire text is inside a markdown fence, extract it
    fence = re.search(r'```(?:jsonl|json)?\s*\n?(.*?)```', text, re.DOTALL)
    if fence:
        text = fence.group(1)

    lines: list[str] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Find the outermost {...} on this line
        start = line.find("{")
        end = line.rfind("}")
        if start >= 0:
            if end > start:
                # Complete JSON object — extract just the {...} part
                lines.append(line[start:end + 1])
            else:
                # Has opening brace but no closing brace — malformed line,
                # include it so _safe_json_parse will report the error
                lines.append(line[start:])
        # Lines without { are commentary — silently skipped

    return lines


def _safe_json_parse(text: str) -> dict | None:
    """Parse a single line of JSON, returning None on any failure."""
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _query_has_data(query: str) -> bool:
    """Heuristic: does the query contain structured data?"""
    # JSON objects, markdown tables, key:value pairs, or lists
    return bool(
        re.search(r'\{[^{}]*\}', query) or
        re.search(r'\|.*\|.*\|', query) or
        re.search(r'^\s*[-*]\s+', query, re.MULTILINE)
    )


def _query_has_arrays(query: str) -> bool:
    """Heuristic: does the query contain array/list data?"""
    return bool(
        re.search(r'\[\s*\{', query) or  # JSON array of objects
        re.search(r'items?\s*:', query, re.IGNORECASE) or
        re.search(r'list|array|rows|entries', query, re.IGNORECASE)
    )


def _plan_has_bindings(plan: dict) -> bool:
    """Check if any section has data_bindings."""
    for s in plan.get("sections", []):
        if s.get("data_bindings"):
            return True
    return False


def _fallback_plan() -> dict:
    """Minimal fallback plan when all attempts fail."""
    return {
        "card_type": "simple_card",
        "sections": [{
            "section_type": "header", "data_bindings": [],
            "layout_direction": "vertical", "visual_priority": 0,
            "is_repeatable": False, "grid_columns": None,
        }, {
            "section_type": "text_block", "data_bindings": [],
            "layout_direction": "vertical", "visual_priority": 1,
            "is_repeatable": False, "grid_columns": None,
        }],
        "data_summary": {}, "interaction_intents": [],
        "style_preferences": {
            "accent_color": "#0A59F7", "card_radius": "20px",
            "spacing_scale": "normal", "harmony_mode": False,
        },
        "needs_charts": False, "needs_pagination": False,
        "needs_interactions": False, "estimated_complexity": "low",
    }
