<!-- Shared prefix loaded from component_generate_shared.md -->

## BLOCK COMPONENT STRUCTURE (MUST)

A minimal bordered container for a single cohesive callout, note, or paragraph group. **Include an image only if the data contains an image URL** alongside the text content. No rounded corners on the container, no background fill, no card-like visual mass.

```
<div class="border border-default p-4">
  <!-- image here if data has one -->
  <p class="text-heading font-medium text-base">Best Time to Visit</p>
  <p class="text-primary text-sm mt-1">March to May is the best time, with mild weather and blossoms.</p>
  <p class="text-secondary text-xs mt-2">Avoid weekends for fewer crowds.</p>
  <p class="text-tertiary text-xs mt-1">Source: visit-hangzhou.gov.cn</p>
</div>
```

- Uses only `border border-default` + `p-4` -- NO `bg-surface`, NO `rounded-[20px]`, NO `shadow`.
- The border subtly defines the region without card-like visual mass.
- Whenever fits, consider using no border (just padding).
- No rounded corners on the container (the image inside CAN be rounded).
- Include an image only if the data contains an image URL (Standalone: `w-full object-cover rounded-xl`).
- Best for: a single callout, note, tip, or cohesive paragraph group that needs to stand apart from surrounding content but isn't a multi-item list.

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- Only render `<img>` if the src starts with http, https, or data:image.
- Always use `object-cover`; round the corners of visible images.
- For a single section image: use Standalone (`w-full object-cover rounded-xl`).

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
