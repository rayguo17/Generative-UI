<!-- Shared prefix loaded from component_generate_shared.md -->

## TABLE COMPONENT STRUCTURE (MUST)

A semantic HTML table for tabular data with 2+ columns. Header row on elevated background; data rows separated by borders.

```
<div class="overflow-x-auto">
  <table class="w-full">
    <thead>
      <tr class="bg-elevated">
        <th class="text-left px-3 py-2 text-xs font-medium text-secondary">Column A</th>
        <th class="text-left px-3 py-2 text-xs font-medium text-secondary">Column B</th>
      </tr>
    </thead>
    <tbody>
      <tr class="border-b border-default">
        <td class="px-3 py-2 text-sm text-primary">Value 1</td>
        <td class="px-3 py-2 text-sm text-primary">Value 2</td>
      </tr>
      <tr class="border-b border-default">
        <td class="px-3 py-2 text-sm text-primary">Value 3</td>
        <td class="px-3 py-2 text-sm text-primary">Value 4</td>
      </tr>
    </tbody>
  </table>
</div>
```

- `<thead>` row: `bg-elevated` on `<tr>` — visually separates headers from data.
- Data rows (`<tbody>`): no background; separated by `border-b border-default`.
- Always set background on the `<tr>` level, not individual `<td>`/`<th>`.
- For interactive (hoverable) rows: `hover:bg-elevated` on `<tr>`.
- `overflow-x-auto` wrapper for horizontal scroll on narrow screens.
- NO rounded corners on the table or cells (rounded is for cards only).
- DO NOT truncate cell content unless explicitly narrow column.
- Render ALL rows from the data — never sample.

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- For per-row images: Thumbnail (`w-10 h-10 rounded-full object-cover` for avatars, `w-12 h-12 rounded-lg object-cover` for thumbnails).

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
