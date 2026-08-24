<!-- Shared prefix loaded from component_generate_shared.md -->

## GRID COMPONENT STRUCTURE (MUST)

The whole HTML fragment should follow the grid structures (tailwind `grid`, `grid-cols-*`).

- Keep each grid item's content concise — **summarize the data** to fit the cell. If a description is long, write a shorter version rather than overflowing.
- Every grid item should have `min-w-0` (prevents content from stretching the cell width).
- Use either `border-default` or `bg-surface` on grid items for visual separation.
- Default to 2 columns (`grid-cols-2`). Use 3 columns only if each cell's content is very small.

Example:
```
<div class="grid grid-cols-2 gap-3">
  <div class="min-w-0 border-default p-3">
    <p class="text-sm font-medium">Leifeng Pagoda</p>
    <p class="text-xs text-secondary">Panoramic sunset views over West Lake</p>
  </div>
  <div class="min-w-0 border-default p-3">
    <p class="text-sm font-medium">Broken Bridge</p>
    <p class="text-xs text-secondary">Legendary White Snake meeting spot</p>
  </div>
  <div class="min-w-0 border-default p-3">
    <p class="text-sm font-medium">Lingyin Temple</p>
    <p class="text-xs text-secondary">Ancient Buddhist temple with stone carvings</p>
  </div>
  <div class="min-w-0 border-default p-3">
    <p class="text-sm font-medium">Su Causeway</p>
    <p class="text-xs text-secondary">2.8km willow-lined causeway for cycling</p>
  </div>
</div>
```

### col-span (edge case only)
Use `col-span-{n}` ONLY when the last row is incomplete (fewer items than columns).
Example: 3 items in a 2-column grid → the 3rd item gets `col-span-2`:
```
Item 1 | Item 2
Item 3 (col-span-2)
```
- NEVER use `col-span` when the grid is fully populated (e.g., 4 items in a 2-column grid — each item takes one cell, no col-span on any).
- `col-span` is for the LAST item only — never apply it to items in the middle.

### Flex with image inside a grid item
It is allowed to include further layout within an item (e.g., flex with an image + text). When using flex inside a grid item, the text container should have `min-w-0`.

```
<div class="min-w-0 bg-surface p-3 flex items-center gap-2 col-span-2">
  <img class="w-16 h-16 rounded-lg object-cover shrink-0" src="...">
  <div class="min-w-0">
    <p class="text-sm font-medium">莲藕排骨汤</p>
    <p class="text-xs text-secondary">湖北名菜</p>
  </div>
</div>
```
## IMAGE HANDLING

Classify each image before placing it. Pick the ONE tier that matches this section.

1. **Standalone Image** — a single visible image that IS the section's content.
   `<img class="w-full object-cover rounded-xl">`
   - **When to use**: the section is "about" one image — an illustration, diagram,
     screenshot, or a single hero-like photo within a body_block.
   - **Avoid**: per-item images in a list (→ Thumbnail/Card), or a background (→ Decorative).

2. **Thumbnail / Avatar** — a small photo beside each item's text.
   Avatar: `w-10 h-10 rounded-full object-cover` (people/identities);
   thumbnail: `w-16 h-16 rounded-lg object-cover` (places/products).
   - **When to use**: a label/decoration — a metric icon next to a number, a chip
     icon, a section-type indicator metrics.

3. **Icon** — a tiny NON-PHOTO graphic: unicode, emoji, or a small `<img>` 16–24px.
   - **When to use**: a label/decoration — a metric icon next to a number, a chip
     icon, a section-type indicator metrics.

General rules:
- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- **Always use** thumbnail or icon when the image counts is the same as the grid items count, **otherwise** avoid using them and use standalone image.
- **Always `object-cover`**; round the corners of Standalone / Card / Thumbnail images.