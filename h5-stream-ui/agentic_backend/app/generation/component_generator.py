"""
Agent B — Component Generator.

Generates HTML for ONE individual section/component at a time. Receives
a focused context package: section spec + retrieved data + style preferences.
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

COMPONENT_USER_TEMPLATE = """## Component to Generate

- **Section index**: {section_index}
- **Section type**: `{section_type}`
- **Layout direction**: {layout_direction}
- **Grid columns**: {grid_columns}
- **Is repeatable**: {is_repeatable}
- **Visual priority**: {visual_priority}

## Data Bindings
```json
{data_bindings_json}
```

## Data for This Component
```
{retrieved_data}
```

## Style Context
- **Accent color**: {accent_color}
- **Card radius**: {card_radius}
- **Spacing scale**: {spacing_scale}
- **HarmonyOS mode**: {harmony_mode}

## Instructions
Generate ONLY the HTML fragment for this ONE component. It will replace a
placeholder in the page shell. Do NOT include the page root container or
other sections. Output raw HTML with a single root element."""


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
            - spec: section spec dict (section_type, data_bindings, layout_direction, etc.)
            - data: retrieved data dict (field_path → value mappings)
            - style: style preferences dict (accent_color, card_radius, etc.)
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.
        interaction_logger: Optional logger for LLM interactions.

    Returns:
        HTML fragment string for this component.
    """
    system_prompt = prompt_loader.load_for_step("component_generate")

    spec = section_context.get("spec", {})
    data = section_context.get("data", {})
    style = section_context.get("style", {})
    idx = section_context.get("index", 0)

    section_type = spec.get("section_type", "text_block")
    layout_direction = spec.get("layout_direction", "vertical")
    grid_columns = spec.get("grid_columns")
    is_repeatable = spec.get("is_repeatable", False)
    visual_priority = spec.get("visual_priority", idx)

    # Format data bindings for the prompt
    data_bindings = spec.get("data_bindings", [])
    data_bindings_json = json.dumps(data_bindings, ensure_ascii=False, indent=2)

    # Format retrieved data — if it's a dict, pretty-print; otherwise use as string
    if isinstance(data, dict):
        retrieved_data = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        retrieved_data = str(data)

    accent_color = style.get("accent_color", "#0A59F7")
    card_radius = style.get("card_radius", "20px")
    spacing_scale = style.get("spacing_scale", "normal")
    harmony_mode = style.get("harmony_mode", False)

    user_prompt = COMPONENT_USER_TEMPLATE.format(
        section_index=idx,
        section_type=section_type,
        layout_direction=layout_direction,
        grid_columns=grid_columns if grid_columns else "N/A",
        is_repeatable=is_repeatable,
        visual_priority=visual_priority,
        data_bindings_json=data_bindings_json,
        retrieved_data=retrieved_data,
        accent_color=accent_color,
        card_radius=card_radius,
        spacing_scale=spacing_scale,
        harmony_mode=harmony_mode,
    )

    # Truncate retrieved data if very long (>1500 chars)
    if len(retrieved_data) > 1500:
        user_prompt = user_prompt.replace(
            retrieved_data,
            retrieved_data[:1500] + "\n... (truncated, see context store for full data)"
        )

    if interaction_logger:
        llm.set_logger(interaction_logger, f"component_{idx}_{section_type}")

    logger.info("Component Generator [%d:%s]: system=%d chars, user=%d chars, data_keys=%d",
                 idx, section_type, len(system_prompt), len(user_prompt),
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
