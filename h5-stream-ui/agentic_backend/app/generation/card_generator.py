"""
Card Generator — HTML agent for a card plan.

Given a card layout plan plus researched section data, generate_card()
produces ONE self-contained HTML fragment. Chart sections emit an *empty*
`<div data-echarts="" data-chart-section="...">` slot; GenerationComposer
fills the attribute with JSON from generate_echarts_option().

Validation is format-level (first char '<', no forbidden tags, no markdown
fences) plus slot checks: chart sections must appear as empty data-echarts
divs with a height class and data-chart-section. Theme / JSON quality is
the composer's job, not this agent's.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

from app.generation.card_charts import chart_sections
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

# Opening tag of a data-echarts slot; value may be empty.
_CHART_DIV_RE = re.compile(
    r"<div\b([^>]*?)\bdata-echarts=(['\"])(.*?)\2([^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_SECTION_ATTR_RE = re.compile(
    r"""\bdata-chart-section=(['"])([^'"]+)\1""",
    re.IGNORECASE,
)
_HEIGHT_CLASS_RE = re.compile(r"\bh-(?:full|\d+|\[[^\]]+\])\b")

# Field names whose values belong in the echarts agent, not the HTML agent.
_SERIES_NAME_RE = re.compile(
    r"(history|dates?|timestamps?|_series|price_history|recent_prices)$",
    re.IGNORECASE,
)


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
    # for better efficiency, we can use templated HTML for generation.

    user_prompt = _build_user_prompt(plan, data_by_section, issue_history=None)

    html = ""
    issue_history: list[str] = []
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
            issue_history.append(f"Attempt {attempt + 1}: LLM call failed ({e})")
            if attempt < MAX_RETRIES:
                user_prompt = _build_user_prompt(
                    plan, data_by_section, issue_history=issue_history,
                )
                continue
            break

        ok, issues = _validate_card_html(candidate, plan)
        if ok:
            html = candidate
            logger.info("Card generate attempt %d: PASSED (%d chars)", attempt, len(html))
            break

        logger.warning("Card generate attempt %d: invalid fragment — %s",
                       attempt, "; ".join(issues[:3]))
        issue_history.extend(f"Attempt {attempt + 1}: {i}" for i in issues)
        if attempt < MAX_RETRIES:
            user_prompt = _build_user_prompt(
                plan, data_by_section, issue_history=issue_history,
            )
        else:
            # Keep the last candidate if it's at least markup-ish
            if candidate and candidate.strip().startswith("<"):
                html = candidate

    if not html:
        logger.error("Card generate failed after %d attempts — using fallback fragment",
                     MAX_RETRIES + 1)
        html = _fallback_card_html(plan)

    return html


# ── Helpers ──────────────────────────────────────────────────────────

def _strip_series_fields(data_by_section: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Drop array/timeline fields the HTML agent must not copy into data-echarts."""
    slim: dict[str, dict[str, Any]] = {}
    for name, payload in data_by_section.items():
        if not isinstance(payload, dict):
            slim[name] = payload
            continue
        slim[name] = {
            k: v for k, v in payload.items()
            if not (
                _SERIES_NAME_RE.search(str(k))
                or (isinstance(v, list) and v and isinstance(v[0], (dict, int, float)))
            )
        }
    return slim


def _build_user_prompt(
    plan: dict[str, Any],
    data_by_section: dict[str, dict[str, Any]],
    *,
    issue_history: "list[str] | None",
) -> str:
    """Build the user prompt: plan + per-section data + accumulated issue history."""
    plan_json = json.dumps(plan, ensure_ascii=False)
    data_json = json.dumps(_strip_series_fields(data_by_section), ensure_ascii=False)

    prompt = f"""## Task
Generate the card HTML fragment. Chart sections get an EMPTY data-echarts slot — do not fill JSON. The HTML is otherwise final.

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

    if issue_history:
        history = "\n".join(f"- {i}" for i in issue_history)
        prompt += f"""

## ⚠️ ALL PREVIOUS ATTEMPTS FAILED — these are EVERY issue found so far (do NOT repeat any of them):
{history}

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


def _validate_card_html(html: str, plan: "dict[str, Any] | None" = None) -> tuple[bool, list[str]]:
    """Validation of the card HTML fragment. Returns (ok, issues).

    Format-level checks plus slot checks: when the plan assigns chart
    components, the fragment must contain an empty data-echarts element
    with a height class and data-chart-section. JSON / theme are filled later.
    """
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

    if plan:
        expected_names = [s.get("name") for s in chart_sections(plan)]
        slots = list(_CHART_DIV_RE.finditer(stripped))
        if expected_names and not slots:
            issues.append(
                "MISSING_CHART: chart sections "
                f"{expected_names} must render as "
                '<div class="h-48 w-full" data-echarts="" data-chart-section="...">; '
                "icon/text rows or gray boxes are not a chart"
            )
        seen: set[str] = set()
        for i, m in enumerate(slots, start=1):
            tag = m.group(0)
            attrs = (m.group(1) or "") + (m.group(4) or "")
            json_str = m.group(3) or ""
            if json_str.strip():
                issues.append(
                    f"CHART_SLOT_NOT_EMPTY: chart div #{i} must leave data-echarts empty; "
                    "JSON is filled by a downstream agent"
                )
            if not _HEIGHT_CLASS_RE.search(tag):
                issues.append(
                    f"CHART_NO_HEIGHT: chart div #{i} has no explicit height class "
                    "(h-full / h-40 / h-48 / ...) — percentage or missing heights "
                    "render the chart INVISIBLE"
                )
            sec_m = _SECTION_ATTR_RE.search(attrs)
            if not sec_m:
                issues.append(
                    f"CHART_NO_SECTION_ATTR: chart div #{i} needs "
                    'data-chart-section="<section name>" so the composer can fill it'
                )
            else:
                seen.add(sec_m.group(2))
        missing = [n for n in expected_names if n and n not in seen]
        if missing and slots:
            issues.append(
                f"CHART_SECTION_MISMATCH: missing data-chart-section for {missing}"
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
