"""
Pass 2 — Plan: Generate a structured layout plan from the analysis.

This pass (~2000-2800 tokens total) takes the analysis result and produces
a detailed LayoutPlan JSON. The plan is a compact intermediate representation
that guides the HTML generation step, allowing it to focus on producing
well-formed HTML rather than also figuring out layout.

Output: dict matching the LayoutPlan schema.
"""

from __future__ import annotations

import json
import logging

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
    {"trigger_element": "row_button" | "card_root" | "chip", "action_type": "openUrl" | "setPage" | "updateData", "params_source": "$.path.to.url", "condition": "if field exists"}
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


async def create_layout_plan(
    query: str,
    analysis: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> dict:
    """Generate a layout plan from the analysis result.

    Args:
        query: Raw user prompt (instructions + data).
        analysis: Analysis result dict from the classify step.
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.

    Returns:
        Dict matching LayoutPlan schema.
    """
    system_prompt = prompt_loader.load_for_step("plan")

    # Build a compact representation of the user's data
    data_context = _build_data_context(query, analysis)

    user_prompt = f"""## Task
Create a detailed layout plan for an H5 card based on the analysis below.

## Analysis
- Intent: {analysis.get('summary', 'Information card')}
- Type: {analysis.get('intent', 'card')}
- Complexity: {analysis.get('complexity', 2)}/5
- Has interactions: {analysis.get('has_interactions', False)}
- Has images: {analysis.get('has_images', False)}
- Is tabular: {analysis.get('data_is_tabular', False)}
- Needed modules: {', '.join(analysis.get('needed_modules', [])) or 'none'}

## Data Context
{data_context}

## User's Original Request
{query[:800]}

## Output
Return a JSON object following this exact schema:
{PLAN_JSON_SCHEMA}

Key rules:
- sections: ordered top-to-bottom as they should appear in the card
- section_type: choose the best match for the data's visual structure
- data_bindings: map EVERY visible data field to its source path; use "$." prefix for paths
- visual_priority: 0 = most prominent (rendered first), higher = less prominent
- is_repeatable: true ONLY for sections that iterate over an array
- style_preferences.harmony_mode: true if the user asked for HarmonyOS style or if it suits the card type
- needs_pagination: true only if data has >10 rows/items
- Keep the plan concise — the next step will turn this into HTML"""

    result = await llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="plan",
        max_tokens=4096,   # High for thinking models (qwen3 etc.)
    )

    # Apply defaults for missing fields
    result.setdefault("card_type", "simple_card")
    result.setdefault("sections", [])
    result.setdefault("data_summary", {})
    result.setdefault("interaction_intents", [])
    result.setdefault("style_preferences", {
        "accent_color": "#0A59F7",
        "card_radius": "20px",
        "spacing_scale": "normal",
        "harmony_mode": False,
    })
    result.setdefault("needs_charts", "chart" in analysis.get("needed_modules", []))
    result.setdefault("needs_pagination", False)
    result.setdefault("needs_interactions", analysis.get("has_interactions", False))
    result.setdefault("estimated_complexity", "low")

    logger.info("Plan result: card_type=%s, sections=%d, interactions=%d",
                 result.get("card_type"), len(result.get("sections", [])),
                 len(result.get("interaction_intents", [])))

    return result


def _build_data_context(query: str, analysis: dict) -> str:
    """Build a compact data context string from the analysis."""
    fields = analysis.get("data_fields", [])
    if not fields:
        # Extract from query directly
        query_clean = query.strip()
        if len(query_clean) > 600:
            return query_clean[:600] + "..."
        return query_clean

    lines = ["Data fields found:"]
    for f in fields[:20]:  # Cap at 20 fields
        name = f.get("name", "?")
        ftype = f.get("type", "string")
        sample = f.get("sample_value", "")
        path = f.get("path", "$")
        sample_str = f" = {json.dumps(sample, ensure_ascii=False)}" if sample else ""
        lines.append(f"  {name} ({ftype}) @ {path}{sample_str}")

    return "\n".join(lines)
