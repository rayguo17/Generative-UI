## CARD CHART OVERLAY

You are filling ONE ECharts option for a **card** section. The host wraps your JSON in an already-sized `<div data-echarts>`. Output JSON only — no HTML, no theme colors, no backgroundColor.

### Chart-type map (MUST)

The user prompt lists `chart_components`. Map them as follows:

| Component(s) | ECharts |
|---|---|
| `line_chart` and/or `chart` | `"type":"line"` time series |
| `threshold_line` (with or without `line_chart`) | same line series **plus** `markLine` at each threshold value from the data |
| `donut_chart` | `"type":"pie"`, `"radius":["40%","60%"]` |
| `progress_chart` | pie (or a single-value gauge) from the given progress fields |

If several chart components appear together (e.g. `line_chart` + `threshold_line`), emit **one** option that combines them — one `series` array, not two charts.

### markLine format (MUST)

When `threshold_line` is in the components, add a `markLine` to the series. Use OBJECTS, not arrays:

```json
{"series":[{"name":"Price","type":"line","data":[93.26,104.68,90.87],"markLine":{"data":[{"yAxis":95,"name":"threshold"}]}}]}
```

- `"yAxis": <number>` — the threshold value from the data (e.g. `alert_threshold: 95.0` → `"yAxis":95`)
- Do NOT use `[["threshold",95]]` — that is invalid ECharts syntax
- The threshold value MUST come from the input data, not invented

### Timeline (MUST)

- `xAxis.data` MUST come from date/time fields in the provided DATA (e.g. `price_history[].date`, `dates`, `timestamps`). Copy labels EXACTLY.
- **NEVER invent labels.** If the data has no date/timeline field, **omit `xAxis.data` entirely** — the chart will render without axis labels rather than with fabricated ones.
- Bad: data has `recent_prices: [93.26, 104.68]` but no dates → LLM invents `["1/1/2025","1/2/2025"]` ← **WRONG**
- Good: data has `recent_prices: [93.26, 104.68]` but no dates → omit `xAxis.data` entirely ← **CORRECT**
- Series numeric arrays MUST be the matching values in the same order (e.g. `recent_prices`).

### Array Alignment (MUST)

- `xAxis.data` length MUST equal `series[0].data` length — always.
- If you omit `xAxis.data` (no timeline field), the chart renders with numeric indices.

### Compact JSON (MUST)

- One object. Starts with `{`, ends with `}`.
- No markdown fences, no commentary, no trailing commas, double quotes only.
- Do NOT emit `backgroundColor`, `textStyle`, or axis colors — the host injects theme.

### OUTPUT

Valid JSON only.
