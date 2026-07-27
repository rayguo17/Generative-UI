# Tailwind + H5 stack guidance

1. **Do not** add Tailwind CDN or `tailwind.config` in your output — the **host shell** already loads Tailwind. Your `html` fragment should use **utility classes** and **inline `style`** only.

2. Use Tailwind utility classes for layout and styling. Avoid `<style>` tags in the fragment (forbidden by output format).

3. **Mobile-first**: assume a phone-width viewport; use responsive classes (`sm:`, `md:`) when helpful.

4. **Responsive primitives (must use for horizontal compositions)**:
   - Parent row: `flex`.
   - Main content region (text block): `flex-1 min-w-0` (or `w-0 grow`).
   - Fixed media/action region (image/icon/button): `shrink-0`.
   - Potentially long chip/attribute rows: `flex-wrap`.
   - If you omit these primitives, wide/narrow containers will break (overflow or non-adaptive fixed-width look).

5. **Icons**:
   - Prefer inline SVG or Unicode symbols. Avoid external icon packs unless necessary.

6. **Assets**:
   - If the payload includes image URLs, you may render `<img>` with sensible `alt` text derived from nearby labels.
   - **Decorative backgrounds:** **silent default** for dense cards + atmosphere URL is **`absolute inset-0`** on the decorative wrapper (**§2.3.0a**), not a short **`bottom-0 h-80`** strip (long cards leave a **white cap** over title / table header). Bottom-strip **`h-*`** only when the brief asks for lower-only accent. Avoid `h-full` on `absolute bottom-0` without a defined parent height. Do not paint **solid `bg-white`** on the full `z-10` sheet — it hides the image.

7. **Performance / streaming**:
   - Avoid huge base64 blobs unless the user requests it.
   - Prefer semantic HTML for accessibility (`button`, `nav`, `main`, `section`, labels).
   - **Streaming**: start the `html` JSON string with the outer wrapper quickly so the preview can show layout early.
