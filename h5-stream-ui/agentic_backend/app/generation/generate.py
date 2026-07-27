"""
Pass 3 — Generate: Produce the HTML fragment from the layout plan.

This is the main generation pass (~2800-3500 tokens total). It receives the
layout plan and user data and produces the complete HTML fragment.

This is the only pass that streams output to the frontend. Tokens are
yielded as they arrive from the local LLM.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

GENERATE_USER_TEMPLATE = """## Task
Generate a complete H5 HTML fragment based on the layout plan below. Output ONLY the raw HTML fragment — start with '<', no markdown fences, no explanations.

## Layout Plan
```json
{plan_json}
```

## Data to Render
```
{data_context}
```

## Original User Request
{user_request}

## Critical Rules (must follow exactly)
1. First character MUST be '<' — start the root element immediately
2. Single root element — typically <div class="...">
3. NO markdown fences (```), NO JSON wrappers, NO preamble or commentary
4. NO <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link> tags
5. Use Tailwind utility classes for ALL styling (host provides Tailwind CDN)
6. For rows: use flex with flex-1 min-w-0 on main content, shrink-0 on fixed elements
7. Use truncate or line-clamp-2 for text overflow
8. Root: w-full, rounded-[20px], overflow-hidden
9. Single primary accent color unless data warrants more
10. Use data-interactions='{{"onClick":[{{"type":"...","params":{{...}}}}]}}' for any interactive elements
11. For images: classify as primary (main <img>), supporting (small inline), or decorative (absolute inset-0 background layer)
12. Render ALL array items — never sample just the first one
13. No fabricated content — every visible string must come from the data
14. Output ONLY the HTML — no text before or after"""


async def generate_html_stream(
    query: str,
    plan: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> AsyncIterator[str]:
    """Generate the HTML fragment from the layout plan, streaming tokens.

    Args:
        query: Raw user prompt (instructions + data).
        plan: Layout plan dict from the plan step.
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.

    Yields:
        HTML tokens as they arrive from the local LLM.
    """
    needs_charts = plan.get("needs_charts", False)
    needs_interactions = plan.get("needs_interactions", False)

    system_prompt = prompt_loader.load_for_step(
        "generate",
        needs_charts=needs_charts,
        needs_interactions=needs_interactions,
    )

    # Compact plan representation
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)

    # Data context — the raw user data
    data_context = query[:1200]  # Truncate if very long

    # Original request (keep short to save tokens)
    user_request = query[:400]

    user_prompt = GENERATE_USER_TEMPLATE.format(
        plan_json=plan_json,
        data_context=data_context,
        user_request=user_request,
    )

    logger.info("Generate: system=%d chars, user=%d chars",
                 len(system_prompt), len(user_prompt))

    async for token in llm.generate_stream(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="generate",
        max_tokens=8192,   # High for thinking models + full HTML output
    ):
        yield token


async def generate_html(
    query: str,
    plan: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> str:
    """Generate the complete HTML fragment (non-streaming)."""
    parts: list[str] = []
    async for token in generate_html_stream(query, plan, llm, prompt_loader):
        parts.append(token)
    return "".join(parts)
