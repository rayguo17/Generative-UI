"""
Agent B — Component Generator.

Generates HTML for ONE individual section/component at a time. Receives
a focused context package: section spec + retrieved data.
Produces a self-contained HTML fragment that replaces a placeholder in the
page shell.

Each call is independent — components can be generated sequentially or in
parallel batches (though local Ollama typically handles one at a time).
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


async def generate_component(
    section_context: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    interaction_logger: "LlmInteractionLogger | None" = None,
) -> str:
    """Generate HTML for a single component/section.

    Args:
        section_context: Dict with keys:
            - index: section index in the plan
            - spec: section spec dict (widget, title, desc, data_needed, etc.)
            - data: retrieved data dict (field_path → value mappings)
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.
        interaction_logger: Optional logger for LLM interactions.

    Returns:
        HTML fragment string for this component.
    """
    spec = section_context.get("spec", {})
    data = section_context.get("data", {})
    idx = section_context.get("index", 0)

    widget = spec.get("widget", "body_list")
    # Load the per-widget system prompt (falls back to the general
    # component_generate_system.md when no per-type file exists).
    system_prompt = prompt_loader.load_component_system(widget)

    layout_direction = spec.get("layout_direction", "vertical")
    grid_columns = spec.get("grid_columns")
    is_repeatable = spec.get("is_repeatable", False)
    visual_priority = spec.get("visual_priority", idx)

    # Format retrieved data — if it's a dict, pretty-print; otherwise use as string
    if isinstance(data, dict):
        retrieved_data = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        retrieved_data = str(data)

    user_prompt = prompt_loader.load_raw("component_generate/component_user.md").format(
        section_index=idx,
        widget=widget,
        layout_direction=layout_direction,
        grid_columns=grid_columns if grid_columns else "N/A",
        is_repeatable=is_repeatable,
        visual_priority=visual_priority,
        retrieved_data=retrieved_data,
    )

    # Truncate retrieved data if very long (>1500 chars)
    if len(retrieved_data) > 1500:
        user_prompt = user_prompt.replace(
            retrieved_data,
            retrieved_data[:1500] + "\n... (truncated, see context store for full data)"
        )

    if interaction_logger:
        llm.set_logger(interaction_logger, f"component_{idx}_{widget}")

    logger.info("Component Generator [%d:%s]: system=%d chars, user=%d chars, data_keys=%d",
                 idx, widget, len(system_prompt), len(user_prompt),
                 len(data) if isinstance(data, dict) else 0)

    html = await llm.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name=f"component_{idx}",
        max_tokens=4096,
    )

    # Basic validation
    if html and not html.strip().startswith("<"):
        logger.warning("Component [%d] does not start with '<', wrapping", idx)
        html = f'<div>{html}</div>'

    return html
