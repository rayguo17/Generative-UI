"""Shared chart constants and post-processing for the card pipeline.

Kept out of card_generator.py so composer / component_generator can import
them without a circular dependency on the HTML agent.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

CHART_COMPONENTS = frozenset({
    "line_chart", "threshold_line", "chart", "progress_chart", "donut_chart",
})

DARK_STYLES = frozenset({"dark_data_tile", "tint_gradient", "full_bleed_media"})

# Opening <div ... data-echarts=... data-chart-section=...> (attr order free).
_CHART_SLOT_RE = re.compile(
    r"<div\b([^>]*?)\bdata-echarts=(['\"])(.*?)\2([^>]*)>",
    re.IGNORECASE | re.DOTALL,
)
_SECTION_ATTR_RE = re.compile(
    r"""\bdata-chart-section=(['"])([^'"]+)\1""",
    re.IGNORECASE,
)

_DARK_THEME = {
    "backgroundColor": "transparent",
    "textStyle": {"color": "#ffffff"},
}

_DARK_AXIS = {
    "axisLabel": {"color": "rgba(255,255,255,0.7)"},
    "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.2)"}},
}


def section_has_chart(section: dict) -> bool:
    """True if this card-plan section lists any chart component."""
    comps = section.get("components") or []
    return any(c in CHART_COMPONENTS for c in comps)


def chart_sections(plan: dict) -> list[dict]:
    """Plan sections that need an ECharts option filled in."""
    return [
        s for s in plan.get("sections", [])
        if isinstance(s, dict) and section_has_chart(s)
    ]


def inject_chart_theme(json_str: str, style_template: str | None) -> str:
    """Merge theme keys into an ECharts option. Compact one-line JSON out.

    Dark styles get a transparent background + light axis/label colors when
    those keys are missing. Light styles only get backgroundColor:transparent
    if absent. Series colors the model set are left alone.
    """
    try:
        opts = json.loads(json_str)
    except (ValueError, TypeError):
        logger.warning("inject_chart_theme: unparseable JSON, returning as-is")
        return json_str
    if not isinstance(opts, dict):
        return json_str

    style = style_template or ""
    if style in DARK_STYLES:
        if opts.get("backgroundColor") in (None, "", "#fff", "#ffffff", "white"):
            opts["backgroundColor"] = _DARK_THEME["backgroundColor"]
        text = opts.get("textStyle")
        if not isinstance(text, dict):
            opts["textStyle"] = dict(_DARK_THEME["textStyle"])
        elif "color" not in text:
            text["color"] = _DARK_THEME["textStyle"]["color"]
        _ensure_dark_axis(opts, "xAxis")
        _ensure_dark_axis(opts, "yAxis")
    else:
        if "backgroundColor" not in opts:
            opts["backgroundColor"] = "transparent"

    return json.dumps(opts, ensure_ascii=False, separators=(",", ":"))


def _ensure_dark_axis(opts: dict, key: str) -> None:
    axis = opts.get(key)
    if axis is None:
        return
    axes = axis if isinstance(axis, list) else [axis]
    for ax in axes:
        if not isinstance(ax, dict):
            continue
        if "axisLabel" not in ax:
            ax["axisLabel"] = dict(_DARK_AXIS["axisLabel"])
        elif isinstance(ax["axisLabel"], dict) and "color" not in ax["axisLabel"]:
            ax["axisLabel"]["color"] = _DARK_AXIS["axisLabel"]["color"]
        if "axisLine" not in ax:
            ax["axisLine"] = json.loads(json.dumps(_DARK_AXIS["axisLine"]))
        elif isinstance(ax["axisLine"], dict):
            ls = ax["axisLine"].setdefault("lineStyle", {})
            if isinstance(ls, dict) and "color" not in ls:
                ls["color"] = _DARK_AXIS["axisLine"]["lineStyle"]["color"]


def fill_card_charts(html: str, options_by_section: dict[str, str]) -> str:
    """Write compacted JSON into empty data-echarts attrs matched by section.

    Only replaces slots whose current data-echarts value is empty. Unknown
    sections and already-filled slots are left alone. Missing JSON for a
    named slot is left empty (the host shows a blank chart area).
    """
    if not options_by_section:
        return html

    def _replace(match: re.Match) -> str:
        before, quote, current, after = match.group(1), match.group(2), match.group(3), match.group(4)
        if current.strip():
            return match.group(0)
        attrs = before + after
        sec_m = _SECTION_ATTR_RE.search(attrs)
        if not sec_m:
            return match.group(0)
        name = sec_m.group(2)
        json_str = options_by_section.get(name)
        if not json_str:
            return match.group(0)
        # Prefer single quotes so compact JSON (double-quoted keys) is legal HTML.
        escaped = json_str.replace("'", "&#39;")
        return f"<div{before}data-echarts='{escaped}'{after}>"

    return _CHART_SLOT_RE.sub(_replace, html)
