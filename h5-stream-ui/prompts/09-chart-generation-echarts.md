# ECharts chart generation specification (integrated from legacy vis prompts)

**Fragment-only output note:** The model output is a JSON `html` fragment **without** `<script>`. Prefer **HTML table** or **simple CSS-based visuals** for data inside the fragment. Full ECharts (`echarts.init`) requires `<script>` and is **not** used in fragment mode unless product explicitly allows an exception.

For non-fragment / full-page generation (if ever enabled elsewhere), the rules below apply to in-page ECharts.

Strictly follow decision rules and mobile chart constraints below when embedding charts is allowed in context.

## 1) Analysis objective

Before rendering, infer:

- D (Dimension): dimensions count
- M (Measure): numeric measures count
- T (Temporal): time dimension presence/count
- C (Categorical): category dimension presence/count

Then choose chart type via rule priority:
`physical/readability constraints > D/M/T/C structure > data volume`.

## 2) Chart type decision rules

### 2.0 Forced table fallback (highest priority)

Use **table** when any condition holds:

1. D0M1+ (only metrics, no dimensions)
2. D2M2+ (2 dimensions + 2+ measures)
3. D3+ (3+ dimensions)
4. T2M1 (two independent temporal dimensions)
5. Overload: single-dimension categories > 80, or line legend series > 14

### 2.A Single dimension (D1)

#### A1. T1 (time)
- T1M1:
  - **area** if min > 0 and `(max-min)/max > 0.5`
  - else **line**
- T1M2+: **line**

#### A2. C1 (category)
- C1M1:
  - **pie** iff:
    1) all values > 0
    2) category count in [3, 10]
    3) distribution not too skewed: `(max-min)/total <= 0.8`
  - else **bar** if category count <= 40
    - if label too long (>4 Chinese chars) or category count in [6,40], prefer horizontal bar
  - else **table**
- C1M2+:
  - **bar** if category count <= 80
  - else **table**

### 2.B Two dimensions (D2)

#### B1. C1 + T1
- **multi-series line** if category count <= 14 and time points <= 14
- else **table**

#### B2. C2
- **grouped/stacked bar** if total records <= 80
- else **table**

## 3) Mobile Harmony chart constraints (strict mode mandatory)

For non-table charts, `echarts` option should follow:

- `grid`: `{ top: '15%', bottom: '10%', left: '12%', right: '5%', containLabel: true }`
- `legend`: bottom-aligned, `itemWidth: 12`, `itemHeight: 12`, `textStyle.fontSize: 10`
- axis label font size: 10
- for dense/long x labels: use rotate (e.g., 30) or line-wrapping formatter
- color palette: `['#0A59F7', '#41BAF7', '#7262FD', '#FFB03B', '#F76B1C']`
- tooltip should be mobile-safe (`confine: true`)

## 4) H5 output contract for charts

When chart is selected:

1. Include ECharts CDN in `<head>`:
   - `https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js`
2. Render a chart container with explicit height (e.g., `height: 220px` or higher).
3. Build pure JSON-like `option` object and call:
   - `const chart = echarts.init(dom);`
   - `chart.setOption(option);`
4. Add resize support:
   - `window.addEventListener('resize', () => chart.resize());`

When table is selected:

- Render a readable mobile table/list card from the same dataset.
- Do not duplicate full dataset in both chart and full table unless user explicitly asks.

## 5) Safety and robustness rules

- Do not use `eval`, dynamic `Function`, or arbitrary JS code generation.
- Avoid function callbacks in chart option when possible; prefer static/serializable option fields.
- Keep scripts concise and deterministic for streaming preview.

## 6) Text payload adaptation

If payload is unstructured text:

- Extract candidate metrics and dimensions first.
- If extraction confidence is low, fall back to table/summary view instead of forcing an unreliable chart.
