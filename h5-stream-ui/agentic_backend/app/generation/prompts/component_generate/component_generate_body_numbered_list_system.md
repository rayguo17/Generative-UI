<!-- Shared prefix loaded from component_generate_shared.md -->

## NUMBERED LIST COMPONENT STRUCTURE (MUST)

Layout the items in a vertical flex list — **ONE item per row**, stacked top-to-bottom. Each item has a numbered marker (1, 2, 3, …) beside its content.

Derive the retrieved data into multiple discrete items: each item is ONE top-level list row. Never group multiple items into a single row, and never fabricate items not present in the data.

```
<div class="flex flex-col divide-y border-default">
  <div class="py-4 flex gap-4">
    <span class="w-8 h-8 flex items-center justify-center rounded-full bg-accent text-primary text-xs font-medium shrink-0">1</span>
    <div class="flex-1 min-w-0">
      <p class="text-primary">Item title</p>
      <p class="text-secondary text-sm">desc</p>
    </div>
  </div>
  <div class="py-4 flex gap-4">
    <span class="w-8 h-8 flex items-center justify-center rounded-full bg-accent text-primary text-xs font-medium shrink-0">2</span>
    <div class="flex-1 min-w-0">
      <p class="text-primary">Item title</p>
      <p class="text-secondary text-sm">desc</p>
    </div>
  </div>
</div>
```

- **ONE item per row — NEVER nest `grid grid-cols-*` inside a list item.**
- If you need a multi-column grid, that is `body_cards` / `body_grid`, NOT this — do not use a grid here.
- `divide-y` separates EACH item.
- No background, no rounded corner, no border radius, no padding wrapper.
- Each item uses `py-3`/`py-4`.
- The numbered marker: `w-8 h-8 rounded-full bg-accent` with the number inside.
- Render ALL items stacked (never sample, never 2-per-row).

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- Only render `<img>` if src starts with http/https/data:image.
- For per-item images, use Thumbnail: `w-16 h-16 rounded-lg object-cover shrink-0`.
- Always `object-cover`; round visible image corners.

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
