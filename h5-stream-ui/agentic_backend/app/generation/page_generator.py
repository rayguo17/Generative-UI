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
import os
import re
from typing import TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

MAX_SHELL_RETRIES = 2

# Config: detect + retry on forbidden CSS classes (gated by env var)
_CLASS_CHECK_ENABLED = os.getenv("PAGE_SHELL_CLASS_CHECK", "true").lower() in (
    "1", "true", "yes", "on",
)

_PLACEHOLDER_RE = re.compile(r'<!-- COMP_PLACEHOLDER:(?:section_)?(\d+):(\w+) -->')

# Forbidden class patterns — detected in class="..." attributes
_FORBIDDEN_CLASS_RE = re.compile(
    r'\b('
    r'bg-gradient-to-\w+'      # gradient direction (bg-gradient-to-r, etc.)
    r'|from-[\w-]+'             # gradient color stops (from-blue-50, etc.)
    r'|to-[\w-]+'               # gradient color stops (to-indigo-50, etc.)
    r'|via-[\w-]+'              # gradient midpoints (via-purple-200, etc.)
    r'|dark:[\w:-]+'            # dark mode variants (dark:bg-surface, etc.)
    r'|bg-white'                # raw white background
    r'|bg-gray-\d+'             # raw gray backgrounds
    r'|bg-black'                # raw black background
    r'|text-white'              # raw white text
    r'|text-gray-\d+'           # raw gray text
    r'|text-black'              # raw black text
    r'|border-gray-\d+'         # raw gray borders
    r')\b'
)


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

    plan_str = json.dumps(plan, ensure_ascii=False, indent=2)

    user_prompt = prompt_loader.load_raw("page_generate/page_generate_user.md").format(plan_json=plan_str)

    if interaction_logger:
        llm.set_logger(interaction_logger, log_label)

    logger.info("Page Generator: system=%d chars, user=%d chars, sections=%d",
                 len(system_prompt), len(user_prompt),
                 len(plan.get("sections", [])))

    sections = plan.get("sections", [])
    expected_count = len(sections)
    expected_indices = set(range(expected_count))
    feedback = ""

    for attempt in range(MAX_SHELL_RETRIES + 1):
        current_prompt = user_prompt
        if attempt > 0 and feedback:
            current_prompt += (
                f"\n\n## PREVIOUS ATTEMPT HAD ISSUES — FIX THESE:\n{feedback}\n\n"
                f"Please regenerate with the correct placeholders."
            )

        html = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=current_prompt,
            step_name=f"page_generate{'_retry' + str(attempt) if attempt > 0 else ''}",
            max_tokens=4096,
            log_label=log_label,
        )

        # Check 1: Count placeholders
        placeholders = _PLACEHOLDER_RE.findall(html)
        found_count = len(placeholders)
        found_indices = {int(idx) for idx, _ in placeholders}
        placeholders_ok = found_count >= expected_count and expected_indices.issubset(found_indices)

        # Check 2: Forbidden CSS classes
        forbidden_classes = _find_forbidden_classes(html) if _CLASS_CHECK_ENABLED else []

        # Both checks pass → done
        if placeholders_ok and not forbidden_classes:
            logger.info("Page shell attempt %d: %d/%d placeholders, no forbidden classes",
                         attempt + 1, found_count, expected_count)
            break

        # Build combined feedback
        feedback_parts = []

        # Placeholder feedback
        if not placeholders_ok:
            missing = expected_indices - found_indices
            section_list = ", ".join(
                f"{i}:{s.get('widget', 'body_block')}" for i, s in enumerate(sections)
            )
            if found_count == 0:
                feedback_parts.append(
                    f"Your output has NO placeholders. You MUST include exactly "
                    f"{expected_count} markers (one per section). "
                    f"Format: <!-- COMP_PLACEHOLDER:N:type --> "
                    f"Expected: {section_list}"
                )
            else:
                pf = f"Expected {expected_count} placeholders, found {found_count}."
                if missing:
                    pf += f" Missing indices: {sorted(missing)}."
                pf += f" Required markers: {section_list}"
                feedback_parts.append(pf)

        # Forbidden classes feedback
        if forbidden_classes:
            unique = list(dict.fromkeys(forbidden_classes))  # deduplicate
            feedback_parts.append(
                f"FORBIDDEN CSS CLASSES: The following classes are NOT allowed: "
                f"{', '.join(unique[:10])}. "
                f"Use ONLY theme utility classes (bg-surface, bg-elevated, bg-page, "
                f"text-heading, text-primary, text-secondary, text-tertiary, "
                f"bg-accent, border-default, etc.). "
                f"NO gradients (bg-gradient-*), NO dark: variants, "
                f"NO raw colors (bg-white, text-gray-*)."
            )
            logger.warning("Page shell attempt %d: %d forbidden classes found: %s",
                           attempt + 1, len(unique), ', '.join(unique[:5]))

        feedback = "\n".join(feedback_parts)

        if attempt < MAX_SHELL_RETRIES:
            logger.warning("Page shell attempt %d: issues found — retrying", attempt + 1)
        else:
            logger.error("Page shell: max retries reached. %s", feedback)

    # Fallback: strip forbidden classes silently (if retries didn't fix them)
    html = _strip_forbidden_colors(html)

    # Basic validation: must start with <
    if html and not html.strip().startswith("<"):
        logger.warning("Page shell does not start with '<', wrapping in div")
        html = f'<div class="w-full">{html}</div>'

    return html


def _find_forbidden_classes(html: str) -> list[str]:
    """Find forbidden CSS classes in the HTML.

    Scans for: gradients (bg-gradient-to-*, from-*, to-*, via-*),
    dark: variants, raw colors (bg-white, text-gray-*, etc.).

    Returns a list of the forbidden class strings found.
    """
    found = _FORBIDDEN_CLASS_RE.findall(html)
    return found


def _strip_forbidden_colors(html: str) -> str:
    """Remove forbidden CSS classes that violate the theme palette.

    This is a fallback — runs after the retry loop. If the LLM still
    outputs forbidden classes after MAX_SHELL_RETRIES, strip them
    silently and replace with theme equivalents.

    Strips: bg-white, bg-gray-*, text-gray-*, text-white, border-gray-*, etc.
    Also strips: bg-gradient-*, from-*, to-*, via-*, dark:* (removed entirely).
    """
    # Strip gradient + dark: classes entirely (no theme equivalent)
    gradient_re = re.compile(
        r'\b(bg-gradient-to-\w+|from-[\w-]+|to-[\w-]+|via-[\w-]+|dark:[\w:-]+)\b'
    )
    html = gradient_re.sub('', html)
    # Clean up double spaces left by removal
    html = re.sub(r'\s{2,}', ' ', html)
    html = re.sub(r'class="\s+', 'class="', html)
    html = re.sub(r'\s+"', '"', html)

    # Replace raw color classes with theme equivalents
    color_re = re.compile(
        r'\b(bg-white|bg-gray-\d+|text-white|text-gray-\d+|border-gray-\d+|bg-black|text-black)\b'
    )
    def _replace(match):
        cls = match.group(1)
        if cls == "bg-white" or cls.startswith("bg-gray"):
            return "bg-surface"
        if cls == "text-white" or cls.startswith("text-gray"):
            return "text-primary"
        if cls.startswith("border-gray"):
            return "border-default"
        if cls == "bg-black":
            return "bg-surface"
        if cls == "text-black":
            return "text-heading"
        return cls
    result = color_re.sub(_replace, html)
    if result != html:
        logger.info("Stripped forbidden color classes from page shell (fallback)")
    return result
