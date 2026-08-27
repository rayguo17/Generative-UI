### 3. `action_execution` — 行动执行型 · action execution
The execution process runs in a live window; when the task completes, a desktop card carries the result and the next step.
**Structure formula:** 任务结果 + 核心结论 + 成果摘要 + 待确认项 + 成果入口 (task result + core conclusion + outcome summary + pending confirmations + outcome entry)

Per-section component palette:
- `title` (任务结果): `text`, `icon`, `status_tag`, `update_time`
- `core` (核心结论): `result_text`, `conclusion_text`, `core_value`
- `content` (成果摘要): `value`, `list`, `table`, `thumbnail`
- `status` (待确认项): `status_tag`, `alert_notice`, `pending_notice`
- `operation` (成果入口): `primary_button`, `secondary_button`, `switch`, `selector`