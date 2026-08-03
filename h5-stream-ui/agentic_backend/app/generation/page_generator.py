"""
Agent A — Page Structure Generator.

Generates the HTML page SHELL with placeholders for each section defined
in the layout plan. Does NOT render actual data — only structural containers
and placeholder markers that the Component Generator fills in later.

The output is a complete HTML fragment with well-defined placeholder markers
that the Composer can parse and replace with generated component HTML.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

PAGE_GENERATE_USER_TEMPLATE = """## Task
Generate an HTML page SHELL with structural containers and placeholders for each section.
Do NOT render actual data values — only layout containers with `<!-- placeholder -->` inside.

## Layout Plan
```json
{plan_json}
```

## Placeholder Rules (MUST follow)
For EVERY section in the plan's `sections` array, insert a placeholder pair:

```
<!-- COMP_PLACEHOLDER:0:header -->
<div class="..."><!-- placeholder --></div>
<!-- /COMP_PLACEHOLDER:0:header -->
```

- `N` = the section's index (0, 1, 2, ...)
- `type` = the `section_type` field from the plan
- Format: `COMP_PLACEHOLDER:N:type` on BOTH opening and closing tags
- Use the section's `layout_direction` and `grid_columns` to structure the placeholder container
- Do NOT include actual data values — just `<!-- placeholder -->` inside each component area

## Critical Rules
1. First character MUST be '<' — start the root element immediately
2. Single root element
3. NO markdown fences (```), NO preamble or commentary
4. NO <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link> tags
5. Use Tailwind utility classes for ALL styling
6. Sections MUST appear in visual_priority order (0 = first)
7. Use the style_preferences from the plan (accent_color, card_radius, spacing_scale, harmony_mode)
8. Output ONLY the HTML shell — nothing else"""


async def generate_page_shell(
    plan: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    interaction_logger: "LlmInteractionLogger | None" = None,
    log_label: str = "page_generate",
) -> str:
    """Generate the HTML page shell with placeholders for all sections.

    Args:
        plan: Layout plan dict from the plan step.
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.
        interaction_logger: Optional logger for LLM interactions.
        log_label: Label for interaction log entries.

    Returns:
        Complete HTML shell string with COMP_PLACEHOLDER markers.
    """
    system_prompt = prompt_loader.load_for_step("page_generate")

    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)

    # Truncate plan if needed — but keep section structure intact
    plan_str = plan_json
    if len(plan_str) > 2000:
        # Keep sections array but trim data_summary values
        compact_plan = {
            "card_type": plan.get("card_type"),
            "sections": plan.get("sections", []),
            "style_preferences": plan.get("style_preferences", {}),
            "needs_charts": plan.get("needs_charts", False),
            "needs_pagination": plan.get("needs_pagination", False),
            "needs_interactions": plan.get("needs_interactions", False),
        }
        plan_str = json.dumps(compact_plan, ensure_ascii=False, indent=2)

    user_prompt = PAGE_GENERATE_USER_TEMPLATE.format(plan_json=plan_str)

    if interaction_logger:
        llm.set_logger(interaction_logger, log_label)

    logger.info("Page Generator: system=%d chars, user=%d chars, sections=%d",
                 len(system_prompt), len(user_prompt),
                 len(plan.get("sections", [])))

    html = await llm.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="page_generate",
        max_tokens=4096,
    )

    # Basic validation: must have at least one placeholder and start with <
    if html and not html.strip().startswith("<"):
        logger.warning("Page shell does not start with '<', wrapping in div")
        html = f'<div class="w-full">{html}</div>'

    return html
