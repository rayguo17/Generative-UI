"""
Card Generator — renders the final HTML fragment for a card plan.

The card-pipeline equivalent of page_generator.py: given a card layout plan
(from card_planner.py) plus the researched section data (one JSON object per
section, index-aligned), generate_card() produces ONE self-contained HTML
fragment (single root <div>, Tailwind classes) that a fixed-surface frontend
can drop in directly.

Validation is format-level (first char '<', no forbidden tags, no markdown
fences) with a retry-with-feedback loop — semantic quality stays the
verifier's job, as with the page pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

PROMPT_FILE = "card_generate/card_generate_system.md"
MAX_RETRIES = 2

_FORBIDDEN_TAGS = ("html", "head", "body", "script", "style", "meta", "template", "link")
_FORBIDDEN_RE = re.compile(r"<\s*/?\s*(?:" + "|".join(_FORBIDDEN_TAGS) + r")[\s>]", re.IGNORECASE)
_FENCE_RE = re.compile(r"```")


# ── Main entry point ─────────────────────────────────────────────────

async def generate_card(
    plan: dict[str, Any],
    sections_data: "list[dict[str, Any]] | dict[str, Any] | None",
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    interaction_logger: "LlmInteractionLogger | None" = None,
    log_label: str = "card_generate",
) -> str:
    """Generate the final card HTML fragment.

    Args:
        plan: validated card plan dict from card_planner.create_card_plan().
        sections_data: research payload — either a list index-aligned with
            plan["sections"] (the debug_output/card_plan_data_*.json shape),
            or a dict keyed by section name. None → sections get empty data.
        log_label: label for interaction log entries.

    Returns:
        HTML fragment starting with '<'. Falls back to a minimal fragment
        when every attempt fails (never returns an empty string).
    """
    system_prompt = prompt_loader.load_raw(PROMPT_FILE)
    data_by_section = _normalize_sections_data(plan, sections_data)

    user_prompt = _build_user_prompt(plan, data_by_section, feedback=None)

    html = ""
    for attempt in range(MAX_RETRIES + 1):
        label = f"{log_label}{'_retry' + str(attempt) if attempt > 0 else ''}"
        try:
            candidate = await llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                step_name=label,
                max_tokens=4096,
                log_label=label,
            )
        except Exception as e:
            logger.error("Card generate attempt %d: LLM call failed: %s", attempt, e)
            if attempt < MAX_RETRIES:
                user_prompt = _build_user_prompt(
                    plan, data_by_section,
                    feedback="LLM call failed — please regenerate the fragment.",
                )
                continue
            break

        ok, issues = _validate_card_html(candidate)
        if ok:
            html = candidate
            logger.info("Card generate attempt %d: PASSED (%d chars)", attempt, len(html))
            break

        logger.warning("Card generate attempt %d: invalid fragment — %s",
                       attempt, "; ".join(issues[:3]))
        if attempt < MAX_RETRIES:
            user_prompt = _build_user_prompt(
                plan, data_by_section,
                feedback="; ".join(issues[:5]),
            )
        else:
            # Keep the best candidate if it's at least markup-ish
            if candidate and candidate.strip().startswith("<"):
                html = candidate
            html = ""

    if not html:
        logger.error("Card generate failed after %d attempts — using fallback fragment",
                     MAX_RETRIES + 1)
        html = _fallback_card_html(plan)

    return html


# ── Helpers ──────────────────────────────────────────────────────────

def _build_user_prompt(
    plan: dict[str, Any],
    data_by_section: dict[str, dict[str, Any]],
    *,
    feedback: str | None,
) -> str:
    """Build the user prompt: plan + per-section data + optional feedback."""
    plan_json = json.dumps(plan, ensure_ascii=False)
    data_json = json.dumps(data_by_section, ensure_ascii=False)

    prompt = f"""## Task
Generate the final card HTML fragment — no placeholders, this IS the render.

## Card Plan
```json
{plan_json}
```

## Card Data (one object per planned section, keyed by section name)
```json
{data_json}
```

## Output
Raw HTML fragment only — first character '<'. No fences, no commentary."""

    if feedback:
        prompt += f"""

## ⚠️ PREVIOUS ATTEMPT HAD ISSUES — FIX THESE:
{feedback}

Please regenerate the fragment. Output ONLY the raw HTML."""
    return prompt


def _normalize_sections_data(
    plan: dict[str, Any],
    data: "list[dict[str, Any]] | dict[str, Any] | None",
) -> dict[str, dict[str, Any]]:
    """Map research data to section names.

    Tolerates two shapes:
    - list (index-aligned with plan["sections"]) — the card_plan_data_*.json
      shape produced by the cloud research step;
    - dict already keyed by section name.

    Sections missing from the data get an empty dict (the prompt renders '—').
    """
    sections = plan.get("sections", [])
    result: dict[str, dict[str, Any]] = {}

    if isinstance(data, dict):
        for s in sections:
            if isinstance(s, dict):
                name = s.get("name")
                value = data.get(name) if isinstance(data.get(name), dict) else {}
                result[name] = value
        return result

    if isinstance(data, list):
        for i, s in enumerate(sections):
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            value = data[i] if i < len(data) and isinstance(data[i], dict) else {}
            result[name] = value
        return result

    for s in sections:
        if isinstance(s, dict):
            result[s.get("name")] = {}
    return result


def _validate_card_html(html: str) -> tuple[bool, list[str]]:
    """Format-level validation of the card fragment. Returns (ok, issues)."""
    issues: list[str] = []
    if not html or not html.strip():
        return False, ["EMPTY_FRAGMENT: no HTML returned"]
    stripped = html.strip()
    if not stripped.startswith("<"):
        issues.append("NOT_AN_HTML_FRAGMENT: first character must be '<'")
    if _FENCE_RE.search(stripped):
        issues.append("MARKDOWN_FENCE: output must be raw HTML, no ``` fences")
    if _FORBIDDEN_RE.search(stripped):
        issues.append(
            "FORBIDDEN_TAG: html/head/body/script/style/meta/template/link are not allowed"
        )
    return len(issues) == 0, issues


def _fallback_card_html(plan: dict[str, Any]) -> str:
    """Minimal neutral fragment so downstream never sees an empty string."""
    title = plan.get("intent") or plan.get("topic") or "Card"
    return (
        '<div class="w-full h-full rounded-[20px] overflow-hidden bg-white '
        'border border-neutral-200 p-4 flex flex-col gap-2">'
        f'<p class="text-sm font-medium text-neutral-900 truncate">{title}</p>'
        '<p class="flex-1 text-xs text-neutral-500">'
        "Card content unavailable — please retry."
        "</p></div>"
    )
