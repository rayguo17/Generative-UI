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

    user_prompt = prompt_loader.load_raw("page_generate/page_generate_user.md").format(plan_json=plan_str)

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
