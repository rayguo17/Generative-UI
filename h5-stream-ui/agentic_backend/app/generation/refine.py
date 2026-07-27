"""
Pass 4 — Refine: Self-check and correction of the generated HTML.

This pass (~2000-2800 tokens total) reviews the generated HTML against
key rules and makes targeted fixes. It runs locally on the 4K LLM and
focuses on the most common issues:

1. Forbidden elements (script, style, etc.)
2. Missing responsive primitives
3. Phantom/fabricated content
4. Missing data-interactions on interactive elements
5. Image classification errors

The refine pass receives the generated HTML and produces corrected HTML.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

REFINE_USER_TEMPLATE = """## Task
Review and fix the generated HTML fragment below. Focus on rule violations, not redesign.

## Generated HTML
```html
{html_fragment}
```

## Layout Plan (for reference)
```json
{plan_summary}
```

## Fix Instructions
1. Check for forbidden tags: <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link> — remove any found
2. Check for markdown fences or JSON wrappers — strip them
3. Verify every visible string/URL comes from the data — remove fabricated buttons, links, or text
4. Check responsive primitives: flex rows must have min-w-0 on main content, shrink-0 on fixed items
5. Verify data-interactions JSON is valid double-quoted JSON
6. Check image placement: primary images as visible <img>, decorative as absolute background layer

## Output
Return the CORRECTED HTML fragment. If no fixes needed, return the original HTML unchanged.
Output ONLY the HTML — start with '<', no markdown, no commentary."""


async def refine_html(
    html: str,
    plan: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> str:
    """Review and correct the generated HTML.

    Args:
        html: Generated HTML fragment to review.
        plan: Layout plan dict that guided generation.
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.

    Returns:
        Corrected HTML fragment (or original if no issues found).
    """
    system_prompt = prompt_loader.load_for_step("refine")

    # Compact plan summary
    plan_summary = _summarize_plan(plan)

    # Truncate HTML if needed to fit in context
    html_fragment = _truncate_html_for_context(html, max_chars=1500)

    user_prompt = REFINE_USER_TEMPLATE.format(
        html_fragment=html_fragment,
        plan_summary=plan_summary,
    )

    logger.info("Refine: system=%d chars, user=%d chars",
                 len(system_prompt), len(user_prompt))

    result = await llm.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="refine",
        max_tokens=4096,   # High for thinking models
    )

    # Clean up the output — remove any markdown or commentary the LLM may have added
    cleaned = _clean_html_output(result)
    if not cleaned or len(cleaned) < 10:
        logger.warning("Refine produced empty/invalid output, keeping original HTML")
        return html

    return cleaned


def _summarize_plan(plan: dict) -> str:
    """Create a minimal plan summary for the refine prompt."""
    lines = [
        f"card_type: {plan.get('card_type', 'simple_card')}",
        f"sections: {len(plan.get('sections', []))}",
    ]
    for s in plan.get("sections", []):
        stype = s.get("section_type", "?")
        bindings = [b.get("visual_role", "?") for b in s.get("data_bindings", [])]
        lines.append(f"  - {stype}: {', '.join(bindings) if bindings else 'no bindings'}")
    lines.append(f"needs_pagination: {plan.get('needs_pagination', False)}")
    lines.append(f"needs_interactions: {plan.get('needs_interactions', False)}")
    return "\n".join(lines)


def _truncate_html_for_context(html: str, max_chars: int = 1500) -> str:
    """Truncate HTML to fit in the refine context window.

    Keeps the opening structure and truncates from the middle of the body.
    """
    if len(html) <= max_chars:
        return html

    # Try to keep the first ~70% and last ~20% of the content
    head_size = int(max_chars * 0.7)
    tail_size = int(max_chars * 0.2)

    # Find a good split point in the middle
    head = html[:head_size]
    tail = html[-tail_size:]

    # Try to split at a tag boundary
    last_close = head.rfind(">")
    if last_close > head_size // 2:
        head = html[:last_close + 1]

    return f"{head}\n<!-- ... truncated for context ... -->\n{tail}"


def _clean_html_output(text: str) -> str:
    """Clean LLM output: strip markdown fences, JSON wrappers, and commentary."""
    text = text.strip()

    # Remove markdown code fences
    if text.startswith("```html"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    # Remove JSON wrappers like {"html": "..."}
    if text.startswith('{"html"'):
        import json
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "html" in data:
                text = data["html"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Ensure it starts with '<'
    if text and not text.startswith("<"):
        # Find the first '<' in the text
        lt_pos = text.find("<")
        if lt_pos > 0:
            text = text[lt_pos:]

    return text.strip()
