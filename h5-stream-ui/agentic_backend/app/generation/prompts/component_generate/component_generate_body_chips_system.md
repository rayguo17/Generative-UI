<!-- Shared prefix loaded from component_generate_shared.md -->

## CHIPS COMPONENT STRUCTURE (MUST)

A flex-wrap row of compact chip/tag pills. For single-dimensional short tokens — categories, tags, status badges, keyword clusters.

```
<div class="flex flex-wrap gap-2">
  <span class="inline-block border border-default rounded-md px-3 py-1 text-xs text-secondary">Tag A</span>
  <span class="inline-block border border-default rounded-md px-3 py-1 text-xs text-secondary">Tag B</span>
  <span class="inline-block bg-elevated px-3 py-1 text-xs text-secondary">Tag C</span>
</div>
```

- Each chip: `border border-default rounded-md px-3 py-1 text-xs` OR `bg-elevated px-3 py-1 text-xs` (tinted variant).
- `flex-wrap` so chips wrap to the next line on narrow screens.
- `gap-2` between chips.
- No card wrapper — just the chip elements themselves.
- `rounded-md` (6px) is the ONLY exception to the no-rounded-corners rule — it applies to small inline chip/tag elements only.

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- Only render `<img>` if src starts with http/https/data:image.
- For chip icons: use a small inline SVG or `<img>` 16-24px (Icon tier).

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
