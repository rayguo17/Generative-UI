"""
Plan: Generate a structured layout plan directly from the user query.

This single pass (~2500-3200 tokens total) replaces the old classify+plan pair.
It analyzes the user's intent, extracts data structure, and produces a
detailed LayoutPlan JSON — all in one LLM call.

A harness layer (`validate_plan`) runs after the LLM to enforce schema
correctness before the plan is handed to the generate step.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

PLAN_JSON_SCHEMA = """{
  "card_type": "simple_card" | "data_table" | "dashboard" | "form" | "list_detail" | "chart_view" | "multi_section",
  "sections": [
    {
      "section_type": "header" | "hero_image" | "metrics_grid" | "data_table" | "chart_area" | "card_list" | "form_fields" | "text_block" | "button_group" | "footer",
      "data_bindings": [{"field_path": "$.path.to.field", "visual_role": "card_title" | "metric_value" | "row_label" | "image_src" | "button_url" | "text_content" | "chip_label", "fallback": "N/A"}],
      "layout_direction": "horizontal" | "vertical" | "grid",
      "grid_columns": 2,
      "visual_priority": 0,
      "is_repeatable": false
    }
  ],
  "data_summary": {"key": "sample value", "row_count": 5},
  "interaction_intents": [
    {"trigger_element": "row_button" | "card_root", "action_type": "openUrl" | "setPage" | "updateData", "params_source": "$.path.to.url"}
  ],
  "style_preferences": {
    "accent_color": "#0A59F7",
    "card_radius": "20px",
    "spacing_scale": "compact" | "normal" | "relaxed",
    "harmony_mode": false
  },
  "needs_charts": false,
  "needs_pagination": false,
  "needs_interactions": false,
  "estimated_complexity": "low" | "medium" | "high"
}"""

# ── Valid section types, card types, layout directions ────────────

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


async def create_layout_plan(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> dict[str, Any]:
    """Generate a layout plan directly from the user query.

    The plan step now combines what classify + plan used to do:
    it infers intent from the user's text + data, extracts fields,
    and produces the layout plan — all in one call.
    """
    system_prompt = prompt_loader.load_for_step("plan")

    user_prompt = f"""## Task
Analyze this user request for H5 card generation. First infer the intent
and extract data fields, then create a detailed layout plan.

## User Request
{query}

## Output
Return a JSON object following this exact schema:
{PLAN_JSON_SCHEMA}

Key rules:
- card_type: infer from the data shape and user instructions
- sections: ordered top-to-bottom as they should appear in the card
- section_type: choose the best match for the data's visual structure
- data_bindings: map EVERY visible data field to its source path; use "$." prefix
- visual_priority: 0 = most prominent (rendered first), higher = less prominent
- is_repeatable: true ONLY for sections that iterate over an array
- harmony_mode: true if user asks for HarmonyOS style or it suits the card
- needs_charts: true if data is numeric trends/comparisons
- needs_pagination: true if data has >10 rows/items
- needs_interactions: true if user mentions clicks/links/navigation/pagination
- Keep the plan concise — the next step will turn this into HTML"""

    raw = await llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="plan",
        max_tokens=4096,
    )

    plan = validate_plan(raw)
    logger.info("Plan: card_type=%s, sections=%d, interactions=%d",
                 plan.get("card_type"), len(plan.get("sections", [])),
                 len(plan.get("interaction_intents", [])))
    return plan


# ── Harness layer ─────────────────────────────────────────────────

def validate_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Harness: validate and normalise the plan JSON from the LLM.

    Ensures every field has a valid value. If the LLM output is
    malformed or missing fields, sensible defaults are applied so
    the generate step always receives a workable plan.
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
        at = intent.get("action_type", "")
        if at not in VALID_ACTION_TYPES:
            continue
        clean_intents.append({
            "trigger_element": str(intent.get("trigger_element", "card_root")),
            "action_type": at,
            "params_source": str(intent.get("params_source", "$")),
            "condition": intent.get("condition"),
        })
    plan["interaction_intents"] = clean_intents

    # --- style_preferences ---
    sp = raw.get("style_preferences", {})
    if not isinstance(sp, dict):
        sp = {}
    spacing = sp.get("spacing_scale", "normal")
    if spacing not in VALID_SPACING:
        spacing = "normal"
    plan["style_preferences"] = {
        "accent_color": str(sp.get("accent_color", "#0A59F7")),
        "card_radius": str(sp.get("card_radius", "20px")),
        "spacing_scale": spacing,
        "harmony_mode": bool(sp.get("harmony_mode", False)),
    }

    # --- flags ---
    plan["needs_charts"] = bool(raw.get("needs_charts", False))
    plan["needs_pagination"] = bool(raw.get("needs_pagination", False))
    plan["needs_interactions"] = bool(raw.get("needs_interactions", False))

    # --- complexity ---
    cx = raw.get("estimated_complexity", "low")
    plan["estimated_complexity"] = cx if cx in VALID_COMPLEXITIES else "low"

    return plan
