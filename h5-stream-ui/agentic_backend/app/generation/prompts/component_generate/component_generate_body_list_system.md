<!-- Shared prefix loaded from component_generate_shared.md -->

## VERTICAL LIST COMPONENT STRUCTURE (MUST)

Layout the components in a vertical flex list format. You should derived the data given by the user into list of multiple data in the highest scope. The whole HTML fragment should follow the following structure.

```
<div class="flex flex-col divide-y border-default">
  <div class="py-4 text-primary">
    Item 1
  </div>
  <div class="py-4 text-primary">
    Item 2
  </div>
  <div class="py-4 text-primary">
    Item 3
  </div>
</div>
```

- No background, no rounded corner and no border radius, no padding wrapper — just `divide-y` or `border-b` with `border-default` on items.
- Each item uses `py-3` (12 px) or `py-4` (16 px) for touch-friendly vertical spacing.
- Text uses `text-primary` (primary) or `text-secondary` (secondary/meta).

## IMAGE HANDLING

Classify each image before placing it. Pick the ONE tier that matches this section.

1. **Standalone Image** — a single visible image that IS the section's content.
   `<img class="w-full object-cover rounded-xl">`
   - **When to use**: the section is "about" one image — an illustration, diagram,
     screenshot, or a single hero-like photo within a body_block.
   - **Avoid**: per-item images in a list (→ Thumbnail/Card), or a background (→ Decorative).

2. **Card Image** — an image at the top of each card item, text below it.
   Image: `w-full object-cover`; card: `bg-surface rounded-[20px]`.
   - **When to use**: each item has an image + 3+ text layers (image + title + desc) —
     `body_cards`, or an image-led `body_list`.
   - **Avoid**: items with no image (text-only → plain list), or a single section image (→ Standalone).

3. **Thumbnail / Avatar** — a small photo beside each item's text.
   Avatar: `w-10 h-10 rounded-full object-cover` (people/identities);
   thumbnail: `w-16 h-16 rounded-lg object-cover` (places/products).
   - **When to use**: each list/timeline/table/grid item has a small identifying
     image that accompanies the text but isn't the focus — `body_list`,
     `body_timeline`, `body_table` rows, `body_grid` cells.
   - **Avoid**: if the image IS the item's focus (→ Card Image), or it's a non-photo
     icon (→ Icon).

4. **Icon** — a tiny NON-PHOTO graphic: inline SVG or a small `<img>` 16–24px.
   - **When to use**: a label/decoration — a metric icon next to a number, a chip
     icon, a section-type indicator (`body_chips`, `body_grid` metrics).
   - **Avoid**: photos (→ Thumbnail), or large images (→ Standalone/Card).

5. **Decorative Background** — a background image behind the section's content
   (rare; only when the section explicitly wants a bg image). The image MUST be
   attenuated so text stays readable — use ONE of:
   - **Darkening gradient overlay** (preferred):
     `<div class="absolute inset-0 bg-gradient-to-t from-black/70 via-black/30 to-transparent"></div>`
   - **Backdrop blur**: `backdrop-blur-md` + `[background:rgba(0,0,0,0.45)]` on the content container.
   Structure (gradient option):
   ```
   <div class="relative">
     <img src="…" class="absolute inset-0 w-full h-full object-cover">
     <div class="absolute inset-0 bg-gradient-to-t from-black/70 via-black/30 to-transparent"></div>
     <div class="relative z-10">…content…</div>
   </div>
   ```
   - **When to use**: the data/context explicitly calls for an image behind the
     content (a banner-like body section, a themed backdrop).
   - **Avoid**: content images (→ Standalone/Card/Thumbnail); never put text on an
     un-attenuated background; the image is NOT rounded.

General rules:
- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- **Always `object-cover`**; round the corners of Standalone / Card / Thumbnail images.