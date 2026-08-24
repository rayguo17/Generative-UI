<!-- Shared prefix loaded from component_generate_shared.md -->

## CARDS COMPONENT STRUCTURE (MUST)

A vertical stack of card containers — used when each item has 2+ distinct content layers (title + description + metrics). Each card has rounded corners and surface background.

**If the data contains image URLs**: include the image at the top of each card:
```
<div class="flex flex-col gap-3">
  <div class="rounded-[20px] bg-surface overflow-hidden">
    <img class="w-full h-40 object-cover" src="...">
    <div class="p-3">
      <p class="text-primary font-medium">Card title</p>
      <p class="text-secondary text-sm">Description here.</p>
      <span class="text-tertiary text-xs">Price/rating</span>
    </div>
  </div>
</div>
```

**If the data has NO image URLs**: use a text-only card layout (no placeholder images):
```
<div class="flex flex-col gap-3">
  <div class="rounded-[20px] bg-surface p-4">
    <p class="text-primary font-medium">Card title</p>
    <p class="text-secondary text-sm mt-1">Description here.</p>
    <span class="text-tertiary text-xs">Price/rating</span>
  </div>
</div>
```

- Each card: `rounded-[20px] bg-surface` — rounded corners ONLY on cards (this is the exception).
- `overflow-hidden` only when a card has an image at the top.
- Cards stacked vertically: `flex flex-col gap-3`.
- Render ALL items from the data array — NEVER sample first item only.
- For nested cards (card inside a card): use `bg-elevated` for the inner card.

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image).
- Do NOT use `picsum.photos` or other placeholder image services.
- If no image URLs are in the data, omit images entirely — use the text-only card layout.

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
