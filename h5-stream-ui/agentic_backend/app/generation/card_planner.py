"""
Card Plan: generate a layout plan for a fixed-surface UI card.

Sibling of plan.py (which plans long-form pages). Given a card-intent user
query — normally together with the IntentResult produced by the intent
classifier — the Card Planner decides:

  1. ONE content display template — the content distribution across the
     card's 5 sections (title / core / content / status / operation)
  2. which components each used section employs, and what data it needs

The output is JSONL — one JSON object per line — that downstream card agents
consume:
  1. Researcher agent reads data_needed per section → gathers data
  2. Card generator reads template + sections → renders the card

Pipeline (mirrors plan.py):
  1. Load card_plan_system.md verbatim (hand-crafted, self-contained)
  2. LLM generates JSONL text
  3. parse_card_plan_jsonl() — line-by-line parser
  4. verify_card_plan_quality() — semantic quality checks
  5. If checks fail → compact feedback → regenerate (max 2 retries)
  6. validate_card_plan() — normalise and apply defaults
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.generation.intent_classifier import (
    IntentResult,
    _SURFACE_RE,
    _extract_surface_size,
)
from app.generation.llm_client import GenerationLlmClient
from app.generation.plan import (
    VALID_RESEARCH_STRATEGIES,
    VALID_TOPICS,
    _extract_json_lines,
    _parse_est_count,
    _safe_json_parse,
)
from app.prompts.loader import PromptLoader
from app.utils.token_counter import count_tokens

if TYPE_CHECKING:
    from app.utils.plan_metrics import PlanMetricsRecorder

logger = logging.getLogger(__name__)


class CardPlanGenerationError(Exception):
    """Raised when card-plan generation fails all retry attempts.

    Mirrors PlanGenerationError — surfaces the failure so callers can decide
    how to handle it instead of silently continuing with a fallback plan.
    """


# Maximum regeneration attempts (1 initial + 2 retries)
MAX_REGENERATIONS = 2

# System prompt file under app/generation/prompts/ (loaded verbatim)
PROMPT_FILE = "card_plan_system.md"

# ── Valid value sets ──────────────────────────────────────────────────

VALID_THEMES = frozenset({"modern-saas-light"})

DEFAULT_THEME = "modern-saas-light"

VALID_CONTENT_TEMPLATES = frozenset({
    "content_summary", "monitoring", "action_execution", "status_overview",
})

# The 5 card sections, in canonical stacking order
CARD_SECTION_ORDER = ("title", "core", "content", "status", "operation")
VALID_SECTIONS = frozenset(CARD_SECTION_ORDER)

VALID_TIERS = frozenset({"S", "M", "L"})

# Components that plot a time series — they REQUIRE a paired timeline field.
# progress_bar and pie_chart are NOT time-series (progress indicator, proportions).
TIME_SERIES_COMPONENTS = frozenset({"line_chart", "threshold_line", "bar_chart"})

# Component palette per template per section (must match card_plan_system.md).
# A component may only be used in the section(s) where that template lists it.
VALID_COMPONENTS: dict[str, dict[str, frozenset[str]]] = {
    "content_summary": {
        "title": frozenset({"text", "image", "source_tag", "update_time"}),
        "core": frozenset({"core_value", "change_value", "conclusion_text"}),
        "content": frozenset({"pie_chart", "line_chart", "tags", "list"}),
        "status": frozenset({"update_notice", "change_notice", "source_status"}),
        "operation": frozenset({"primary_button", "secondary_button", "selector"}),
    },
    "monitoring": {
        "title": frozenset({"text", "icon", "status_tag", "update_time"}),
        "core": frozenset({"core_value", "change_value", "target_tag"}),
        "content": frozenset({"line_chart", "threshold_line", "list", "selector"}),
        "status": frozenset({"alert_condition", "status_notice", "switch"}),
        "operation": frozenset({"primary_button", "secondary_button", "selector"}),
    },
    "action_execution": {
        "title": frozenset({"text", "icon", "status_tag", "update_time"}),
        "core": frozenset({"result_text", "conclusion_text", "core_value"}),
        "content": frozenset({"value", "list", "table", "thumbnail"}),
        "status": frozenset({"status_tag", "alert_notice", "pending_notice"}),
        "operation": frozenset({"primary_button", "secondary_button", "switch", "selector"}),
    },
    "status_overview": {
        "title": frozenset({"text", "icon", "status_tag", "update_time"}),
        "core": frozenset({"core_value", "progress_bar", "conclusion_text"}),
        "content": frozenset({"value", "list", "table", "bar_chart"}),
        "status": frozenset({"status_tag", "alert_notice", "pending_notice"}),
        "operation": frozenset({"primary_button", "secondary_button", "switch", "selector"}),
    },
}

# Sections each content template requires. All four templates share the fixed
# 5-layer structure, and the planner composes status/operation dynamically per
# payload — so only the anchor sections (identity + core conclusion/state) are
# enforced here.
TEMPLATE_REQUIRED_SECTIONS: dict[str, frozenset[str]] = {
    "content_summary": frozenset({"title", "core"}),
    "monitoring": frozenset({"title", "core"}),
    "action_execution": frozenset({"title", "core"}),
    "status_overview": frozenset({"title", "core"}),
}

# How many sections a size tier can carry (progressive disclosure)
TIER_MAX_SECTIONS = {"S": 3, "M": 4, "L": 5}

# ── JSONL template shown in the user prompt ──────────────────────────

CARD_PLAN_JSONL_TEMPLATE = """## Output Format (JSONL — one compact JSON object per line)

Line 1 — topic:
{"topic": "<topic>", "intent": "<one-line summary of what the user wants>"}

Line 2 — layout:
{"layout": {"template": "<content_template>", "surface_size": "<NxM or null>", "tier": "S|M|L", "desc": "<content distribution across sections>"}}

Line 3 — style:
{"style": {"theme": "modern-saas-light"}}

Lines 4+ — sections (only the sections the tier budget allows, canonical order title → core → content → status → operation):
{"section": "<name>", "estimated_height": <number>, "components": ["<component>", ...], "search_query": "<web search query for this section's data>", "data": [{"name": "<field_name>", "description": "<type + meaning>"}, ...], "research": "<strategy>", "repeatable": <bool>, "est_count": <number or null>}

Content templates: content_summary, monitoring, action_execution, status_overview
Theme: always use "modern-saas-light" (unless the host provides a different theme)
Research strategies: single_lookup, search_all, iterate_days, table_lookup, none
Topics: travel_plan, stock_analysis, weather, product_listing, general"""


# ── Main entry point ─────────────────────────────────────────────────

async def create_card_plan(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    intent_result: "IntentResult | None" = None,
    metrics: "PlanMetricsRecorder | None" = None,
    session_id: str = "",
    plan_fail_mode: str = "error",
) -> dict[str, Any]:
    """Generate a card layout plan: content template + per-section specs.

    Args:
        intent_result: the IntentResult from the intent classifier, when the
            caller came through the classify-intent entry point. Supplies the
            authoritative surface_size (the planner prompt also receives it).
        plan_fail_mode: "error" raises CardPlanGenerationError after all
            retries; anything else returns a minimal fallback plan.

    Attempts up to 1 + MAX_REGENERATIONS times. Each attempt is recorded
    in the metrics recorder for observability.
    """
    system_prompt = prompt_loader.load_raw(PROMPT_FILE)
    model = llm._client.model if hasattr(llm, "_client") else "unknown"
    query_preview = query[:80]

    # The classifier's surface size is authoritative — the LLM only copies it.
    surface_size = _resolve_surface_size(intent_result, query)
    tier = _tier_from_surface_size(surface_size)

    user_prompt = _build_user_prompt(
        query, surface_size=surface_size, tier=tier, feedback=None,
    )
    plan: dict[str, Any] = {}

    for attempt in range(MAX_REGENERATIONS + 1):
        t_start = time.monotonic()
        label = f"card_plan{'_retry' + str(attempt) if attempt > 0 else ''}"

        # ── Call LLM ────────────────────────────────────────────
        raw = ""
        parse_failed = False
        try:
            raw = await llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                step_name=label,
                max_tokens=6144,
                log_label=label,
            )
        except Exception as e:
            logger.error("Card plan attempt %d failed: %s", attempt, e)
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
                    query, surface_size=surface_size, tier=tier,
                    feedback="LLM call failed — please try again.",
                )
                continue
            if plan_fail_mode == "error":
                raise CardPlanGenerationError(
                    f"Card plan LLM call failed on final attempt: {e}"
                ) from e
            return _fallback_card_plan(surface_size, tier)

        output_tokens = count_tokens(raw)
        duration_ms = (time.monotonic() - t_start) * 1000

        # ── Parse JSONL ─────────────────────────────────────────
        plan, parse_errors = parse_card_plan_jsonl(raw)

        if parse_errors:
            parse_failed = True
            logger.warning("Card plan attempt %d: %d parse errors: %s",
                           attempt, len(parse_errors),
                           [e[:80] for e in parse_errors[:3]])

        # ── Validate structure ──────────────────────────────────
        plan = validate_card_plan(plan)

        # The classifier-resolved surface/tier is authoritative.
        plan["surface_size"] = surface_size
        plan["tier"] = tier

        # Auto-drop optional sections to fit tier budget (uses authoritative tier).
        # The LLM consistently generates 5 sections even for tier M (max 4).
        # Auto-drop from the bottom of canonical order (operation, then status).
        max_sections = TIER_MAX_SECTIONS.get(tier, 4)
        while len(plan.get("sections", [])) > max_sections:
            dropped = plan["sections"].pop()
            logger.info("Auto-dropped section '%s' (tier %s budget %d)",
                        dropped.get("name"), tier, max_sections)

        # ── Quality checks ──────────────────────────────────────
        passed, issues = verify_card_plan_quality(plan, query)

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
                card_type=plan.get("layout_template", ""),
                section_count=len(plan.get("sections", [])),
                binding_count=0,
                model=model,
                query_preview=query_preview,
            )

        # ── Success or retry ────────────────────────────────────
        all_issues = parse_errors + issues
        if not all_issues:
            logger.info("Card plan attempt %d: PASSED (layout=%s, style=%s, %d sections, tier=%s)",
                        attempt, plan.get("layout_template", "?"),
                        plan.get("style_template", "?"),
                        len(plan.get("sections", [])), plan.get("tier", "?"))
            break

        logger.warning("Card plan attempt %d: %d issues — %s",
                       attempt, len(all_issues),
                       "; ".join(all_issues[:3]))

        if attempt < MAX_REGENERATIONS:
            feedback = _build_feedback(parse_errors, issues)
            user_prompt = _build_user_prompt(
                query, surface_size=surface_size, tier=tier, feedback=feedback,
            )
        else:
            logger.error("Card plan failed after %d attempts. plan_fail_mode=%s",
                         MAX_REGENERATIONS + 1, plan_fail_mode)
            if plan_fail_mode == "error":
                raise CardPlanGenerationError(
                    f"Card plan generation failed after {MAX_REGENERATIONS + 1} attempts "
                    f"(last issues: {all_issues[:3]})"
                )
            return _fallback_card_plan(surface_size, tier)

    return plan


# ── Surface size helpers ──────────────────────────────────────────────

def _resolve_surface_size(intent_result: "IntentResult | None", query: str) -> str | None:
    """Resolve the authoritative surface size (grid units, e.g. '4x6').

    Prefers the intent classifier's result; falls back to scanning the raw
    query — the grid size is deterministic text.
    """
    if intent_result is not None and intent_result.surface_size:
        return intent_result.surface_size
    return _extract_surface_size(None, query)


def _tier_from_surface_size(surface_size: str | None) -> str:
    """Map a grid size to a progressive-disclosure tier: S / M / L.

    2x2 (4 cells) → S; 2x4 / 4x2 (8 cells) → M; 4x4+ (≥12 cells) → L.
    Unknown/missing sizes default to M.
    """
    if not surface_size:
        return "M"
    m = _SURFACE_RE.search(surface_size)
    if not m:
        return "M"
    cells = int(m.group(1)) * int(m.group(2))
    if cells <= 4:
        return "S"
    if cells <= 8:
        return "M"
    return "L"


# ── JSONL Parser ──────────────────────────────────────────────────────

def parse_card_plan_jsonl(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse the JSONL card-plan output into the internal dict format.

    Each line is parsed independently. Malformed lines are skipped.
    Section lines are self-contained — no cascading context needed.

    Returns:
        (plan_dict, error_messages) — plan is always a dict (may be empty).
    """
    plan: dict[str, Any] = {
        "topic": "general",
        "intent": "",
        "surface_size": None,
        "tier": "M",
        "layout_template": "content_summary",
        "layout_desc": "",
        "style_template": "neutral_minimal",
        "style_theme": DEFAULT_THEME,
        "style_desc": "",
        "sections": [],
    }
    errors: list[str] = []

    lines, truncation_warnings = _extract_json_lines(text)

    # Report truncation as parse errors (triggers retry) — mirrors plan.py
    for warning in truncation_warnings:
        errors.append(f"TRUNCATION: {warning}")

    for i, line in enumerate(lines):
        obj = _safe_json_parse(line)
        if obj is None:
            # Try stripping // comments (LLM sometimes inserts them inside JSON)
            clean_line = re.sub(r'//.*$', '', line).strip()
            if clean_line != line:
                obj = _safe_json_parse(clean_line)
                if obj is not None:
                    logger.info("Stripped // comment from line %d", i)
                    line = clean_line
        if obj is None:
            errors.append(f"line {i}: unparseable JSON — {line[:60]}")
            continue

        # ── Topic line ──
        if "topic" in obj:
            plan["topic"] = str(obj.get("topic", "general"))
            plan["intent"] = str(obj.get("intent", ""))
            continue

        # ── Layout line ──
        if "layout" in obj and isinstance(obj["layout"], dict):
            lay = obj["layout"]
            plan["layout_template"] = str(lay.get("template", "content_summary"))
            plan["layout_desc"] = str(lay.get("desc", ""))
            if lay.get("surface_size"):
                plan["surface_size"] = str(lay["surface_size"])
            if lay.get("tier"):
                plan["tier"] = str(lay["tier"])
            continue

        # ── Style line ──
        if "style" in obj and isinstance(obj["style"], dict):
            sty = obj["style"]
            plan["style_theme"] = str(sty.get("theme", DEFAULT_THEME))
            plan["style_desc"] = str(sty.get("desc", ""))
            continue

        # ── Section line (section name is a string, NOT an index) ──
        if "section" in obj:
            name = obj.get("section")
            if not isinstance(name, str):
                errors.append(
                    f"line {i}: section must be a name "
                    f"({'/'.join(CARD_SECTION_ORDER)}) — got {name!r}"
                )
                continue
            components = obj.get("components", [])
            if not isinstance(components, list):
                components = [components] if components else []
            section = {
                "name": name.strip().lower(),
                "estimated_height": obj.get("estimated_height"),
                "components": [str(c).strip() for c in components],
                "search_query": str(obj.get("search_query", obj.get("desc", ""))),
                "data_needed": _normalize_data_needed(obj.get("data")),
                "research_strategy": str(obj.get("research", "none")),
                "is_repeatable": bool(obj.get("repeatable", False)),
                "est_count": _parse_est_count(obj.get("est_count")),
            }
            plan["sections"].append(section)
            continue

        # ── Unrecognised line ──
        errors.append(f"line {i}: unrecognised object — {line[:60]}")

    return plan, errors


# ── Quality verifier ──────────────────────────────────────────────────

def verify_card_plan_quality(plan: dict, query: str) -> tuple[bool, list[str]]:
    """Check the card plan for semantic quality issues.

    Returns:
        (passed, issues) — passed is True when there are NO issues.
    """
    issues: list[str] = []

    sections = plan.get("sections", [])
    layout = plan.get("layout_template", "")
    tier = plan.get("tier", "M")

    # 1. Layout template must be valid
    if layout not in VALID_CONTENT_TEMPLATES:
        issues.append(f"INVALID_LAYOUT_TEMPLATE: '{layout}' is not a recognized content template")


    # 3. Must have at least one section
    if not sections:
        issues.append("NO_SECTIONS: card plan has zero sections — at least title + one more are required")
        return False, issues  # Fatal

    # 4. Title section is required (it anchors the card)
    names = [s.get("name", "") for s in sections]
    if "title" not in names:
        issues.append("MISSING_TITLE: the 'title' section is required — it anchors the card")

    # 5. Template-required sections must be present
    required = TEMPLATE_REQUIRED_SECTIONS.get(layout, frozenset())
    for req in sorted(required):
        if req not in names:
            issues.append(f"MISSING_REQUIRED_SECTION: template '{layout}' requires a '{req}' section")

    # 6. Section names must be valid
    for n in names:
        if n not in VALID_SECTIONS:
            issues.append(f"INVALID_SECTION_NAME: '{n}' is not one of {'/'.join(CARD_SECTION_ORDER)}")

    # 7. No duplicate sections
    if len(names) != len(set(names)):
        issues.append("DUPLICATE_SECTIONS: each of the 5 sections may appear at most once")

    # 8. Sections must follow the canonical stacking order
    order_idx = [CARD_SECTION_ORDER.index(n) for n in names if n in VALID_SECTIONS]
    if order_idx != sorted(order_idx):
        issues.append("NON_CANONICAL_ORDER: sections must follow title → core → content → status → operation")

    # 9. Tier budget: don't plan more sections than the surface can carry
    max_sections = TIER_MAX_SECTIONS.get(tier if tier in VALID_TIERS else "M", 4)
    if len(sections) > max_sections:
        issues.append(
            f"TIER_OVERFLOW: tier {tier} carries at most {max_sections} sections, "
            f"planned {len(sections)} — drop optional sections (operation first)"
        )

    # 10. Components must exist in the template's palette for that section
    palette_for_layout = VALID_COMPONENTS.get(layout, {})
    for s in sections:
        n = s.get("name", "?")
        comps = s.get("components", [])
        if not comps:
            issues.append(f"EMPTY_COMPONENTS: section '{n}' lists no components")
        for c in comps:
            if c not in palette_for_layout.get(n, frozenset()):
                issues.append(
                    f"INVALID_COMPONENT: '{c}' is not available in section '{n}' "
                    f"for template '{layout}'"
                )

    # 11. Research strategies must be valid
    for s in sections:
        rs = s.get("research_strategy", "")
        if rs not in VALID_RESEARCH_STRATEGIES:
            issues.append(f"INVALID_RESEARCH: section '{s.get('name')}' has unknown strategy '{rs}'")

    # 12. data_needed required unless research=none (array of {name, description})
    for s in sections:
        data_needed = s.get("data_needed", [])
        if s.get("research_strategy") != "none" and not data_needed:
            issues.append(
                f"MISSING_DATA_NEEDED: section '{s.get('name')}' has research="
                f"{s.get('research_strategy')} but no data fields"
            )
        # 12a. research=none NOT allowed when data fields exist
        if s.get("research_strategy") == "none" and data_needed:
            issues.append(
                f"RESEARCH_NONE_WITH_DATA: section '{s.get('name')}' has "
                f"{len(data_needed)} data field(s) but research='none' — "
                f"use 'single_lookup' or 'table_lookup' instead"
            )
        for i, f in enumerate(data_needed):
            if not isinstance(f, dict) or not (isinstance(f.get("name"), str) and f.get("name")):
                issues.append(
                    f"INVALID_DATA_FIELD: section '{s.get('name')}' data[{i}] "
                    f"must be an object with a non-empty 'name'"
                )

        # 12b. Series components must pair a timeline field
        components = s.get("components", [])
        has_series = any(c in TIME_SERIES_COMPONENTS for c in components)
        if has_series:
            names = [f.get("name", "").lower() for f in data_needed if isinstance(f, dict)]
            timeline_keywords = ("date", "time", "timestamp")
            if not any(any(kw in n for kw in timeline_keywords) for n in names):
                issues.append(
                    f"MISSING_TIMELINE: section '{s.get('name')}' uses a time-series "
                    f"component ({'/'.join(TIME_SERIES_COMPONENTS.intersection(components))}) "
                    f"but declares no timeline data field (e.g. dates/timestamps)"
                )
            # 12c. Chart sections should use table_lookup, not single_lookup
            if s.get("research_strategy") == "single_lookup":
                issues.append(
                    f"CHART_NEEDS_TABLE_LOOKUP: section '{s.get('name')}' has chart "
                    f"components but research='single_lookup' — use 'table_lookup' "
                    f"for numeric/tabular time-series data"
                )

    # 13. Topic must be valid
    if plan.get("topic") not in VALID_TOPICS:
        issues.append(f"INVALID_TOPIC: '{plan.get('topic')}' is not a recognized topic")

    # 14. Intent should not be empty
    if not plan.get("intent", "").strip():
        issues.append("EMPTY_INTENT: intent is empty — should summarize what the user wants")

    return len(issues) == 0, issues


# ── Validate & normalise (safety net) ─────────────────────────────────

def validate_card_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise the card plan dict. Applies defaults for
    missing/invalid fields. Runs AFTER parse_card_plan_jsonl() as a safety net.
    """
    plan: dict[str, Any] = {}

    # --- topic ---
    topic = str(raw.get("topic", "general"))
    plan["topic"] = topic if topic in VALID_TOPICS else "general"

    # --- intent ---
    plan["intent"] = str(raw.get("intent", ""))

    # --- surface / tier (overridden by the caller afterwards) ---
    plan["surface_size"] = raw.get("surface_size")
    tier = str(raw.get("tier", "M")).upper()
    plan["tier"] = tier if tier in VALID_TIERS else "M"

    # --- layout ---
    layout = str(raw.get("layout_template", "content_summary"))
    plan["layout_template"] = layout if layout in VALID_CONTENT_TEMPLATES else "content_summary"
    plan["layout_desc"] = str(raw.get("layout_desc", ""))

    # --- style ---
    theme = str(raw.get("style_theme", DEFAULT_THEME))
    plan["style_theme"] = theme if theme in VALID_THEMES else DEFAULT_THEME
    plan["style_desc"] = str(raw.get("style_desc", ""))

    # --- sections: filter invalid, dedupe, sort into canonical order ---
    raw_sections = raw.get("sections", [])
    if not isinstance(raw_sections, list):
        raw_sections = []

    clean_sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", ""))
        if name not in VALID_SECTIONS or name in seen:
            continue
        seen.add(name)

        research = str(s.get("research_strategy", "none"))
        if research not in VALID_RESEARCH_STRATEGIES:
            research = "none"

        palette = VALID_COMPONENTS.get(plan["layout_template"], {}).get(name, frozenset())
        components = [c for c in s.get("components", []) if c in palette]

        data_needed = _normalize_data_needed(s.get("data_needed"))

        # Auto-fix: research='none' with data fields → single_lookup
        if research == "none" and data_needed:
            research = "single_lookup"
            logger.info("Auto-fixed section '%s': research none→single_lookup (has %d data fields)",
                        name, len(data_needed))

        # Auto-fix: chart components with single_lookup → table_lookup
        has_chart = any(c in TIME_SERIES_COMPONENTS for c in components)
        if has_chart and research == "single_lookup":
            research = "table_lookup"
            logger.info("Auto-fixed section '%s': research single_lookup→table_lookup (has chart components)",
                        name)

        clean_sections.append({
            "name": name,
            "estimated_height": s.get("estimated_height"),
            "components": components,
            "search_query": str(s.get("search_query", s.get("desc", ""))),
            "data_needed": data_needed,
            "research_strategy": research,
            "is_repeatable": bool(s.get("is_repeatable", False)),
            "est_count": _parse_est_count(s.get("est_count")),
        })

    clean_sections.sort(key=lambda s: CARD_SECTION_ORDER.index(s["name"]))

    plan["sections"] = clean_sections

    return plan


# ── Helpers ───────────────────────────────────────────────────────────

def _build_user_prompt(
    query: str,
    *,
    surface_size: str | None,
    tier: str,
    feedback: str | None = None,
) -> str:
    """Build the user prompt, optionally with regeneration feedback."""
    surface_line = (
        f"Surface size: {surface_size} (tier {tier}) — copy it into the layout line verbatim."
        if surface_size else
        f"No surface size given — plan for tier {tier} and use null for surface_size."
    )

    prompt = f"""## Task
The user request below was already classified as a CARD intent. Plan the card:
choose ONE content display template and ONE style template, then spec each
used section's components and data needs.

## User Request
{query}

## Surface
{surface_line}

{CARD_PLAN_JSONL_TEMPLATE}

## Key Rules
- Output in JSONL format — ONE valid JSON object per line — each line starts with '{{' and ends with '}}'. Normal JSON output would be rejected.
- ⚠️ COMPACT JSON ONLY: each object must be on a SINGLE line — no indentation, no newlines inside an object.
- Line order is mandatory: topic → layout → style → sections (canonical order title → core → content → status → operation).
- Exactly ONE layout template and ONE style template.
- The 'section' field is a NAME (title/core/content/status/operation), not a number.
- Pick components that fit the data each section needs to display; emit only the sections the tier budget allows.
- The 'search_query' field is a web search query (NOT a description) — include entity + data type keywords so search results return real data pages.
- The 'data' field names each field and its type for the researcher. DO NOT include actual data values."""

    if feedback:
        prompt += f"""

## ⚠️ PREVIOUS ATTEMPT HAD ISSUES — FIX THESE:
{feedback}

Please regenerate. Output ONLY the JSONL lines. Fix ALL issues listed above."""

    return prompt


def _normalize_data_needed(value: Any) -> list[dict[str, str]]:
    """Normalise the section 'data' field into a list of {name, description} objects.

    The prompt asks for an array of objects, but tolerates:
    - a plain string (legacy output) → one item with that name
    - a dict (single object) → treated as a one-element list
    - a list mixing dicts and strings → dicts kept, strings converted
    Items without a usable name are dropped.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    fields: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            desc = item.get("description", item.get("desc", ""))
            if isinstance(name, str) and name.strip():
                fields.append({"name": name.strip(), "description": str(desc).strip()})
        elif isinstance(item, str) and item.strip():
            fields.append({"name": item.strip(), "description": ""})
    return fields


def _build_feedback(parse_errors: list[str], quality_issues: list[str]) -> str:
    """Build compact feedback for regeneration.

    When parse errors occurred, remind the model of the JSON ground rules it
    keeps violating — naming the failing line alone hasn't stopped small
    models from inventing a NEW syntax error on the next attempt.
    """
    lines = []
    for e in parse_errors[:3]:
        lines.append(f"- PARSE ERROR: {e}")
    for issue in quality_issues[:5]:
        lines.append(f"- QUALITY CHECK FAILED: {issue}")

    if parse_errors:
        lines.append("")
        lines.append("JSON ground rules — re-check every line against ALL of these:")
        lines.append("- NO comments (// or /* */) — JSON does not support them.")
        lines.append("- Every key and every string value is double-quoted: {\"name\": \"value\"}, never {\"name: value\"}.")
        lines.append("- One complete object per line — do NOT break a line mid-object.")
        lines.append("- Close every string and every brace before the line ends.")

    return "\n".join(lines) if lines else "- Unknown error. Please try again."


def _fallback_card_plan(surface_size: str | None = None, tier: str = "M") -> dict[str, Any]:
    """Minimal fallback card plan when all attempts fail."""
    return {
        "topic": "general",
        "intent": "Generic summary card",
        "surface_size": surface_size,
        "tier": tier,
        "layout_template": "content_summary",
        "layout_desc": "A minimal summary card with a title and a single core value.",
        "style_template": "neutral_minimal",
        "style_theme": DEFAULT_THEME,
        "style_desc": "Default neutral style — no domain-specific recipe.",
        "sections": [
            {
                "name": "title", "estimated_height": 60, "components": ["text"],
                "search_query": "",
                "data_needed": [{"name": "title_text", "description": "text"}],
                "research_strategy": "none", "is_repeatable": False,
                "est_count": None,
            },
            {
                "name": "core", "estimated_height": 120, "components": ["core_value", "conclusion_text"],
                "search_query": "",
                "data_needed": [
                    {"name": "core_value", "description": "text or number"},
                    {"name": "conclusion", "description": "text"},
                ],
                "research_strategy": "none", "is_repeatable": False,
                "est_count": None,
            },
        ],
    }
