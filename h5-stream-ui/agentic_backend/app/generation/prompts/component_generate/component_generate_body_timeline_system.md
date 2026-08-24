<!-- Shared prefix loaded from component_generate_shared.md -->

## TIMELINE COMPONENT STRUCTURE (MUST)

Display the components in a timeline format with the following structure.

```
<div class="flex flex-col divide-y border-default">
  <div class="relative flex gap-4 pb-6">
    <div class="flex flex-col items-center shrink-0">
      <div class="w-3 h-3 rounded-full bg-accent"></div>
      <div class="flex-1 border-l border-default"></div>
    </div>
    <div class="flex-1 min-w-0 pb-2">
      <span class="text-heading text-sm font-medium">Event title</span>
      <p class="text-secondary text-xs mt-1">Event description or details.</p>
    </div>
  </div>
  <div class="relative flex gap-4 pb-6">
    <div class="flex flex-col items-center shrink-0">
      <div class="w-3 h-3 rounded-full bg-accent"></div>
      <div class="flex-1 border-l border-default"></div>
    </div>
    <div class="flex-1 min-w-0 pb-2">
      <span class="text-heading text-sm font-medium">Another event</span>
      <p class="text-secondary text-xs mt-1">Another description.</p>
    </div>
  </div>
  <div class="relative flex gap-4">
    <div class="flex flex-col items-center shrink-0">
      <div class="w-3 h-3 rounded-full bg-accent"></div>
    </div>
    <div class="flex-1 min-w-0">
      <span class="text-heading text-sm font-medium">Final event</span>
      <p class="text-secondary text-xs mt-1">No connector line after the last item.</p>
    </div>
  </div>
</div>
```

- Uses a vertical connector line (`border-l border-default`) with accent-coloured dots (`w-3 h-3 rounded-full bg-accent`) as timeline markers.
- The last item omits the connector line (no trailing divider).
- Each timeline item uses `gap-4` horizontal spacing between the dot column and the content column.
- No background, no rounded corners (except the dot itself which is `rounded-full`), no padding wrapper.
- Text uses `text-heading` for the event title and `text-secondary` for the description.

## IMAGE HANDLING

Classify each image before placing it. Pick the ONE tier that matches this section.

1. **Thumbnail / Avatar** - a small photo beside each item's text.
   Avatar: `w-10 h-10 rounded-full object-cover` (people/identities);
   thumbnail: `w-16 h-16 rounded-lg object-cover` (places/products).
   - **When to use**: each list/timeline/table/grid item has a small identifying
     image that accompanies the text but isn't the focus - `body_list`,
     `body_timeline`, `body_table` rows, `body_grid` cells.
   - **Avoid**: if the image IS the item's focus (-> Card Image), or it's a non-photo
     icon (-> Icon).

2. **Icon** - a tiny NON-PHOTO graphic: inline SVG or a small `<img>` 16-24px.
   - **When to use**: a label/decoration - a metric icon next to a number, a chip
     icon, a section-type indicator (`body_chips`, `body_grid` metrics).
   - **Avoid**: photos (-> Thumbnail), or large images (-> Standalone/Card).

General rules:
- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- **Always `object-cover`**; round the corners of Standalone / Card / Thumbnail images.
