"""Unit tests for card HTML slots, theme inject, and chart fill."""
import json

from app.generation.card_charts import fill_card_charts, inject_chart_theme
from app.generation.card_generator import (
    _build_user_prompt,
    _strip_series_fields,
    _validate_card_html,
)

PLAN = {
    "style_template": "dark_data_tile",
    "intent": "BIDU",
    "sections": [
        {"name": "title", "components": ["text", "status_tag"]},
        {"name": "core", "components": ["core_value", "change_value"]},
        {"name": "content", "components": ["line_chart", "threshold_line"]},
        {"name": "status", "components": ["alert_condition"]},
        {"name": "operation", "components": ["primary_button"]},
    ],
}

EMPTY_SLOT = (
    '<div class="w-full h-full bg-neutral-900 flex flex-col">'
    '<div class="h-48 w-full" data-echarts="" data-chart-section="content"></div>'
    "</div>"
)


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    extra = f" | {detail}" if detail else ""
    print(f"{status} {label}{extra}")
    if not cond:
        raise SystemExit(1)


# 1. Empty slot with height + section attr is valid (HTML agent contract)
ok, issues = _validate_card_html(EMPTY_SLOT, PLAN)
check("1 empty slot accepted", ok, str(issues))

# 2. Chartless fragment still rejected
chartless = (
    '<div class="w-full h-full bg-neutral-900 flex flex-col gap-3 p-3">'
    '<div class="text-sm text-neutral-500">历史价格走势</div></div>'
)
ok, issues = _validate_card_html(chartless, PLAN)
check(
    "2 chartless rejected MISSING_CHART",
    (not ok) and any(i.startswith("MISSING_CHART") for i in issues),
    str(issues),
)

# 3. Filled JSON in the HTML agent is now a failure
filled = EMPTY_SLOT.replace(
    'data-echarts=""',
    """data-echarts='{"series":[{"type":"line","data":[1]}]}'""",
)
ok, issues = _validate_card_html(filled, PLAN)
check(
    "3 filled slot rejected CHART_SLOT_NOT_EMPTY",
    (not ok) and any("CHART_SLOT_NOT_EMPTY" in i for i in issues),
    str(issues),
)

# 4. Percentage height / no height class
zero_h = (
    '<div class="bg-neutral-900 flex flex-col w-full h-full">'
    '<div style="height:100%" data-echarts="" data-chart-section="content"></div>'
    "</div>"
)
ok, issues = _validate_card_html(zero_h, PLAN)
check(
    "4 no height class rejected CHART_NO_HEIGHT",
    (not ok) and any("CHART_NO_HEIGHT" in i for i in issues),
    str(issues),
)

# 5. Missing data-chart-section
no_sec = (
    '<div class="bg-neutral-900 flex flex-col w-full h-full">'
    '<div class="h-48 w-full" data-echarts=""></div></div>'
)
ok, issues = _validate_card_html(no_sec, PLAN)
check(
    "5 missing section attr rejected",
    (not ok) and any("CHART_NO_SECTION_ATTR" in i for i in issues),
    str(issues),
)

# 6. Wrong section name
wrong = EMPTY_SLOT.replace('data-chart-section="content"', 'data-chart-section="core"')
ok, issues = _validate_card_html(wrong, PLAN)
check(
    "6 wrong section name rejected CHART_SECTION_MISMATCH",
    (not ok) and any("CHART_SECTION_MISMATCH" in i for i in issues),
    str(issues),
)

# 7. No-chart plan + chartless fragment passes
no_chart_plan = {
    **PLAN,
    "sections": [
        {**s, "components": ["text", "value"]} if isinstance(s, dict) else s
        for s in PLAN["sections"]
    ],
}
ok, issues = _validate_card_html("<div>x</div>", no_chart_plan)
check("7 no-chart plan accepts chartless fragment", ok, str(issues))

# 8. fill_card_charts writes matching empty attr
opt = '{"series":[{"type":"line","data":[1]}]}'
filled_html = fill_card_charts(EMPTY_SLOT, {"content": opt})
check(
    "8 fill writes matching slot",
    "data-echarts='{\"series\"" in filled_html
    and 'data-chart-section="content"' in filled_html,
    filled_html[filled_html.find("data-echarts"):filled_html.find("data-echarts") + 80]
    if "data-echarts" in filled_html else filled_html,
)

# 9. Second section's empty slot is left alone
two_slots = (
    '<div class="flex flex-col">'
    '<div class="h-48 w-full" data-echarts="" data-chart-section="content"></div>'
    '<div class="h-40 w-full" data-echarts="" data-chart-section="status"></div>'
    "</div>"
)
partial = fill_card_charts(two_slots, {"content": opt})
check(
    "9 other section slot left empty",
    'data-echarts=\'{"series"' in partial
    and 'data-echarts="" data-chart-section="status"' in partial,
    partial,
)

# 10. Missing JSON leaves the slot empty
untouched = fill_card_charts(EMPTY_SLOT, {"status": opt})
check("10 missing JSON leaves slot empty", untouched == EMPTY_SLOT)

# 11. Already-filled slot is not overwritten
already = EMPTY_SLOT.replace('data-echarts=""', "data-echarts='{\"old\":true}'")
same = fill_card_charts(already, {"content": opt})
check("11 filled slot not overwritten", same == already)

# 12. inject_chart_theme on theme-less option + dark_data_tile
themed = json.loads(inject_chart_theme(
    '{"xAxis":{"type":"category","data":["a"]},"series":[{"type":"line","data":[1]}]}',
    "dark_data_tile",
))
check(
    "12 dark theme injects transparent bg",
    themed.get("backgroundColor") == "transparent"
    and themed.get("textStyle", {}).get("color") == "#ffffff",
    json.dumps(themed),
)

# 13. Light style only gets transparent bg if absent; no forced text color
light = json.loads(inject_chart_theme(
    '{"series":[{"type":"pie","data":[{"value":1}]}]}',
    "neutral_minimal",
))
check(
    "13 light style transparent bg, no forced textStyle",
    light.get("backgroundColor") == "transparent" and "textStyle" not in light,
    json.dumps(light),
)

# 14. HTML agent user prompt strips series arrays
slim = _strip_series_fields({
    "content": {
        "alert_threshold": 95.0,
        "price_history": [{"date": "2026-07-16", "close": 112.82}],
        "recent_prices": [112.82, 107.24],
        "support_level": 95.0,
    }
})
prompt = _build_user_prompt(PLAN, {
    "content": {
        "alert_threshold": 95.0,
        "price_history": [{"date": "2026-07-16", "close": 112.82}],
        "recent_prices": [112.82, 107.24],
    }
}, issue_history=None)
check(
    "14 series fields stripped from HTML prompt",
    "price_history" not in slim["content"]
    and "recent_prices" not in slim["content"]
    and slim["content"]["alert_threshold"] == 95.0
    and "price_history" not in prompt
    and "EMPTY data-echarts" in prompt,
    str(slim),
)

# 15. Issue history still accumulates
p1 = _build_user_prompt(PLAN, {}, issue_history=None)
p2 = _build_user_prompt(PLAN, {}, issue_history=["Attempt 1: MISSING_CHART: ..."])
p3 = _build_user_prompt(
    PLAN, {},
    issue_history=["Attempt 1: MISSING_CHART: ...", "Attempt 2: CHART_NO_HEIGHT: ..."],
)
check(
    "15 issue history accumulates",
    "PREVIOUS" not in p1
    and "Attempt 1: MISSING_CHART" in p2
    and "Attempt 1: MISSING_CHART" in p3
    and "Attempt 2: CHART_NO_HEIGHT" in p3,
)

from app.generation.card_screenshot import surface_pixels, wrap_card_html

check("16 4x6 → 300x450", surface_pixels("4x6") == (300, 450))
check("17 4x4 → 300x300", surface_pixels("4x4") == (300, 300))
check("18 4x2 → 300x150", surface_pixels("4x2") == (300, 150))
check("19 2x4 → 150x300 (order kept)", surface_pixels("2x4") == (150, 300))
check("20 missing size → 300x300", surface_pixels(None) == (300, 300))
check("21 unparseable → 300x300", surface_pixels("large") == (300, 300))

wrapped = wrap_card_html('<div class="w-full h-full">CARD</div>', 300, 450)
check(
    "22 wrap has sized #card-surface and fragment inside #root",
    'id="card-surface"' in wrapped
    and 'style="width:300px;height:450px;overflow:hidden"' in wrapped
    and '<div class="w-full h-full">CARD</div>' in wrapped
    and wrapped.find('id="root"') < wrapped.find("CARD"),
)

print("all checks passed")
