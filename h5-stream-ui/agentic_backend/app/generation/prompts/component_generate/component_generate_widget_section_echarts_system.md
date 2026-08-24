## OVERRIDE: JSON OUTPUT (NOT HTML)

**DISREGARD the "Output raw HTML" instruction above.** For this widget type, you MUST output ONLY a valid JSON object — the ECharts chart option. The host system wraps your JSON in an HTML `<div>` automatically. You do NOT produce any HTML.

## OUTPUT FORMAT (CRITICAL)

1. Output ONLY raw JSON — starts with `{`, ends with `}`
2. NO HTML, NO `<div>`, NO markdown fences, NO explanation, NO commentary
3. All keys MUST use double quotes `"key"`
4. All string values MUST use double quotes `"value"`
5. NO single quotes anywhere — use double quotes only
6. NO trailing commas
7. All data values MUST be numbers (not strings)

## ECHARTS OPTION STRUCTURE (from official documentation)

You MUST output the actual ECharts `option` object — the same structure that `chart.setOption(option)` accepts.

### Bar chart (category comparison):
```json
{"xAxis":{"type":"category","data":["P/E","P/B","P/S"]},"yAxis":{"type":"value","name":"Multiple"},"series":[{"name":"BIDU","type":"bar","data":[15.70,0.92,1.98]},{"name":"Tencent","type":"bar","data":[15.51,3.01,4.61]}]}
```

### Line chart (time series / trend):
```json
{"xAxis":{"type":"category","data":["Jan","Feb","Mar","Apr"]},"yAxis":{"type":"value","name":"Price ($)"},"series":[{"name":"BIDU","type":"line","data":[84.82,92.00,104.68,98.50]}]}
```

### Area chart (filled trend — line + areaStyle):
```json
{"xAxis":{"type":"category","data":["Mon","Tue","Wed"]},"yAxis":{"type":"value","name":"Temperature"},"series":[{"name":"High","type":"line","data":[32,34,33],"areaStyle":{}}]}
```

### Pie chart (composition — NO xAxis/yAxis):
```json
{"series":[{"type":"pie","radius":"60%","data":[{"name":"Search","value":45},{"name":"AI Cloud","value":35},{"name":"Other","value":20}]}]}
```

## CHART TYPE SELECTION

- Time series (dates, sequential periods) → `"type":"line"` (add `"areaStyle":{}` for area)
- Category comparison (metrics across items/companies) → `"type":"bar"`
- Composition (parts of a whole, percentages) → `"type":"pie"`
- Negative values are valid in bar charts (e.g. interest coverage = -1.43)

## AXIS RULES (MUST)

- `xAxis.type` MUST be `"category"` with `data` array of labels
- `yAxis.type` MUST be `"value"`
- Keep xAxis labels SHORT (max 8 chars). Abbreviate: "P/E" not "Price/Earnings", "ROA" not "Return on Assets"
- Pie charts: NO xAxis, NO yAxis

## DATA FIDELITY (MUST)

- Every value MUST come from the provided DATA — no fabrication
- Copy numbers EXACTLY: don't round, don't truncate
- If a value cannot be found, omit that data point — never invent data

## OUTPUT

Valid JSON only. Starts with `{`. Ends with `}`. Nothing else.
