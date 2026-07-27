# Harmony static style specification (H5 fragment)

All **MUST** rules below are mandatory for Harmony-style mobile card outputs in this project.

The model outputs an **HTML fragment** with **Tailwind utility classes** (and inline `style` where needed). The host page supplies the shell; do not emit a full Vue page or duplicate global layout.

## 1) Card definition and skeleton (MUST)

- Use a **single root card container**.
- Root should be mobile-first and include:
  - `width: 100%`
  - reasonable max width
  - `rounded-[20px]`
  - clipped overflow
- Card uses column structure with a clear content region.
- Root layout should behave like `display:flex; flex-direction:column;` in spirit.
- Content region should be an explicit inner container and stay scroll-safe for long content.
- Use whitespace separation; avoid decorative section dividers unless content semantics require them.

## 2) Responsive layout behavior (MUST)

For any horizontal composite row (cover + text, left-right info blocks, button groups, metrics row):

- Parent row uses `flex`.
- Main content block uses `flex-1` and/or `min-w-0` (`w-0 grow` equivalent acceptable).
- Fixed-size block (icon/image/button) uses `shrink-0`.
- Multi-tag / multi-attribute rows should support wrap (`flex-wrap`) when width is tight.
- Align per semantics (`items-center` or `items-start`), not random per row.

This is mandatory so one generated H5 adapts across phone/fold/tablet widths.

## 3) Spacing rhythm (MUST)

- Follow 4px grid rhythm: `4/8/12/16/...`.
- Typical intra-block spacing is `8px` or `12px`.
- Keep spacing coherent across sibling blocks (avoid random one-off gaps).

## 4) Typography system (MUST)

- Prefer HarmonyOS-like sans stack (`HarmonyOS Sans`, `PingFang SC`, system sans-serif fallback).
- Minimum readable font size is `10px`.
- Hierarchy:
  - Title/summary: `14/16/18px`, medium/bold.
  - Body/list: `12/14/16px`.
  - Meta/tag: `10/12px`.
- Text color hierarchy should follow layered prominence (primary > secondary > tertiary), using opacity/lightness deltas.

## 5) Header generation rule (MUST)

- Header is optional.
- Only render card header when source data clearly has top-level app identity metadata (icon + app name).
- If identity is missing/ambiguous, do **not** fabricate header metadata.
- Never “promote” normal content fields into app header.
- If a “more” affordance is rendered in header, it must be backed by real data intent; do not fabricate generic “更多”.

## 6) Button matrix (MUST)

Use one of three semantic button types:

1. **Primary**
   - for highest-priority CTA
   - typical color: `bg-[#0A59F7] text-white`
2. **Filled-secondary**
   - for normal secondary actions
   - light tinted background with colored text
3. **Text**
   - no filled background; low-emphasis action

Size:

- Small control: height around `28px`
- Large control: height around `40px`
- No fixed width unless user explicitly requires it.

Disabled state:

- must be visually obvious (muted contrast + non-interactive affordance).

Recommended color matrix (Harmony-like):

- Primary: `bg-[#0A59F7] text-white`
- Filled-secondary normal: light neutral bg + brand text
- Filled-secondary warning: light neutral bg + warning text (`#E84026` family)
- Text button: no background, brand/warning text only

## 7) Region/background semantics (MUST)

- For grouped neutral info blocks: use very light neutral backgrounds.
- For semantic state blocks (success/warning/error/info): use low-opacity tint background + deeper same-hue text.
- Do not use saturated solid backgrounds for large non-primary regions.
- Region spacing should stay `8px`/`12px` (or 4px multiples).

### 7.1 Decorative bitmap vs. aspect ratio (MUST)

**Scope:** These bullets apply **only** when the card intentionally includes a **non-primary decorative `<img>`** (background / bottom band). Cards **without** such a layer—plain tables, charts, text blocks, primary-image cards—are **unchanged**; do not add decorative chrome “because of this section”.

When a card uses a **remote decorative bitmap** whose **height ≫ width** (high aspect ratio):

- **Dense structured card + decorative URL (any domain):** **`08-special-data-processing.md` §2.3.0a** is the **silent-brief default** (`inset-0` + vertical scrim) so atmosphere is not cut off by a short **`h-*`** band under long content. Use **§2.3.0** **only** when the brief **explicitly** wants a **lower strip**; fixed **`h-80`** alone often leaves a **white cap** above the strip (title / list header rows).
- **Do not** default to **only** `h-36`–`h-52` + **`object-cover`** for tall sources unless the brief explicitly wants a **narrow** band; if you must use a narrow band, switch to **`object-contain`** or accept visibly incomplete atmosphere.
- **Readability:** one attenuation path by default — **sibling** gradient overlay, **not** heavy `from-white/50+` on white cards **plus** low `<img>` opacity (see `08-special-data-processing.md` §2.3.1).

## 8) Shape and corner consistency (MUST)

- Rectangular elements (images, chips, blocks, buttons) should use even radii and stay consistent (`8/12/14/20...`).
- Avoid mixing many unrelated corner styles in one card.

## 9) Text overflow strategy (MUST)

- Long text must never break layout.
- Use context-appropriate truncation:
  - title: one-line ellipsis or two-line clamp
  - subtitle/meta/tag: one-line ellipsis
  - button text: no-wrap first, then ellipsis if constrained

Overflow + responsive are coupled: missing `min-w-0` / `shrink-0` is considered a layout error.

## 10) Dark / immersive guidance

- In immersive dark cards, use white-based text hierarchy and restrained translucent surfaces.
- Avoid large over-saturated color blocks unless data semantics require emphasis.

## Tailwind mapping hints

- Card root: `rounded-[20px] overflow-hidden p-3`
- Keep 4px rhythm with Tailwind scale (`gap-1/2/3/4`, `p-2/3/4`, `mb-2/3`)
- Overflow helpers:
  - single-line: `truncate`
  - two-line: `line-clamp-2` (or equivalent clamp style)
