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
    data_context = query # Truncate if very long

    user_prompt = prompt_loader.load_raw("generate/generate_user.md").format(
        plan_json=plan_json,
        data_context=data_context,
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
