### 4. `status_overview` — 状态概览型 · status overview
Fixed 5-layer structure; AI dynamically composes components according to the data.
**Structure formula:** 标题信息 + 核心状态 + 详细指标 + 异常提醒 + 下一步操作 (title info + core status + detailed metrics + anomaly alerts + next-step operation)

Per-section component palette:
- `title` (标题信息): `text`, `icon`, `status_tag`, `update_time`
- `core` (核心状态): `core_value`, `progress_chart`, `conclusion_text`
- `content` (详细指标): `value`, `list`, `table`, `chart`
- `status` (异常提醒): `status_tag`, `alert_notice`, `pending_notice`
- `operation` (下一步操作): `primary_button`, `secondary_button`, `switch`, `selector`