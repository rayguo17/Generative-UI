### 2. `monitoring` — 持续监控型 · continuous monitoring
Fixed 5-layer structure; AI dynamically composes trend, threshold and alert components according to the monitoring target.
**Structure formula:** 监控对象 + 当前数值 + 变化趋势 + 提醒条件 + 下一步操作 (monitoring target + current value + change trend + alert condition + next-step operation)

Per-section component palette:
- `title` (监控对象): `text`, `icon`, `status_tag`, `update_time`
- `core` (当前数值): `core_value`, `change_value`, `target_tag`
- `content` (变化趋势): `line_chart`, `threshold_line`, `list`, `selector`
- `status` (提醒条件): `alert_condition`, `status_notice`, `switch`
- `operation` (下一步操作): `primary_button`, `secondary_button`, `selector`
