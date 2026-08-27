### 1. `content_summary` — 内容汇总型 · aggregation / summary
Fixed 5-layer structure; AI dynamically composes summary, chart, list and source components according to content characteristics.
**Structure formula:** 汇总主题 + 核心结论 + 结构化内容 + 更新状态 + 原文入口 (aggregation topic + core conclusion + structured content + update status + source entry)

Per-section component palette:
- `title` (汇总主题): `text`, `image`, `source_tag`, `update_time`
- `core` (核心结论): `core_value`, `change_value`, `conclusion_text`
- `content` (结构化内容): `donut_chart`, `line_chart`, `tags`, `list`
- `status` (更新状态): `update_notice`, `change_notice`, `source_status`
- `operation` (原文入口): `primary_button`, `secondary_button`, `selector`
