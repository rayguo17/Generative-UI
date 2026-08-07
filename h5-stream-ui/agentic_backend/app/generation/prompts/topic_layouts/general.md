## General Layout

When the topic is `general` (or no specific topic matches), follow this structural guidance:

### Recommended Structure

1. **Lead** (section 0) — Content hero: title, subtitle, key highlight or summary, optional hero image
2. **Primary content** — Choose the widget that best matches the content shape:
   - List-like content → `body_list`
   - Multi-layer items → `body_cards`
   - Steps/rankings → `body_numbered_list`
   - Metrics/comparisons → `body_grid`
   - Tabular data → `body_table`
3. **Secondary content** (optional) — Additional detail section
4. **Footer** (optional) — Attribution, timestamp, source link

### Widget Selection Rules

- Match the widget to the DATA SHAPE, not the topic name:
  - Ordered/sequential data → `body_numbered_list`
  - Unordered same-shaped items → `body_list`
  - Items with 3+ distinct content layers → `body_cards`
  - 2-4 values meant to be compared → `body_grid`
  - Chronological events → `body_timeline`
  - Short keyword/tag collections → `body_chips`
  - Single narrative block → `body_block`
  - >2 columns of structured data → `body_table`

### Data Needs

General topics vary widely. Be specific about data requirements in each section's `data` field:
- Name every field the researcher needs to find
- Specify field types (text, url, number, date)
- Note any constraints (e.g., "must be a valid URL", "3-5 sentences max")
- Mark optional fields clearly
