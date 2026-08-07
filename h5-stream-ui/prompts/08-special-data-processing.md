# Special data processing specification (from configs)

Follow all mandatory rules below (product default). Do not sample or drop list data; preserve field fidelity.

## 1) Array rendering (mandatory)

If a field is an array, render all items with iteration logic.

- Do not sample only first item.
- Do not hardcode index access patterns like `[0]`, `[1]`, `.slice(0,1)` for list rendering.
- Keep list item structure consistent across items.

## 2) Image field processing

### 2.1 Base rule

- Use `<img>` to render image-like fields.
- Do not use CSS `background-image` as the primary way to render data images.

### 2.1.1 Image semantic role classification (mandatory)

Before placing any image, classify it into one of three roles:

1. **Primary image** (core content carrier)
   - The image itself is the main information target (e.g., product hero, poster, single-image card).
   - Render as visible `<img>` in main content area.
2. **Supporting image** (icon/avatar/flag/logo)
   - Assists text or row data.
   - Render as small inline `<img>` near corresponding text/row.
3. **Decorative background image** (atmosphere texture)
   - Not the primary factual carrier; usually ornamental.
   - May be rendered as card background layer with low visual weight (see 2.3).

Do not treat all valid URLs as primary images by default.

### 2.2 Raw URL validity

Treat image as valid only if URL starts with `http`, `https`, or `data:image`.

- Normal image invalid: skip rendering that image node.
- Logo/icon-like invalid (field names such as `logo`, `logo_url`, `icon`): do not render broken `<img>`; render a simple fallback avatar/icon block instead.

### 2.3 Decorative/background image policy (mandatory exception)

Although the base rule prefers `<img>`, decorative images are an explicit exception:

- If an image is judged as **decorative background image**, it should not occupy a large standalone content block.
- Place it as a low-prominence background layer (or subtle bottom/edge overlay) behind the information area.
- Keep content readability first:
  - text/rows must remain high-contrast and unobstructed,
  - reduce prominence with **one** readable mechanism by default: **soft gradient mask on a sibling `div`**, keeping the decorative `<img>` at **full opacity** unless 2.3.3 applies (already-faint source). **Do not** "stack" strong `<img>` opacity + strong dark overlay (see 2.3.1).
  - avoid pushing key information below the fold.
- **Color tokens:** Use `var(--color-page-bg)` for gradient overlays instead of hardcoded `#0A0A0A`. Use `var(--color-surface)` for card surfaces instead of hardcoded `#1A1A1A`. See `05.1-core-design-principles.md` — Theme System.
- Typical decorative hints include schema fields such as `bg`, `background`, `backdrop` when paired with **structured row data** (atmosphere URL, not row-level icons).

### 2.3.0 Optional - bottom-accent band only (explicit "lower strip" brief)

**Class (generic, any domain):** **Dense structured factual UI** (rankings, leaderboards, multi-column tables, comparison lists, >= 3 homogeneous rows) **plus** a **secondary** bitmap URL classified under 2.3.2 as **decorative** - same rules for every vertical that matches that shape.

**Scope (does not affect other flows):** Applies **only** when that combination occurs. Cards **without** a decorative URL (plain lists, charts, text-only, **primary-image** cards) are **unchanged** - do not add a decorative layer "to satisfy this section".

Use **2.3.0** **only** when the **user brief explicitly** asks for a **lower-only** / **bottom-strip** atmosphere (minimal header texture). **Do not** use 2.3.0 as the **silent** default - see **2.3.0a**.

**Why fixed `h-72`-`h-96` often "too short":** A **constant pixel height** is shorter than many real cards (title + **column-header row** + many rows). Anything **above** the band sits on the root **`[background:var(--color-surface)]`** only - a large "cap" including the **table header row** is **expected** for this pattern, not a load failure.

1. **Band container:** `absolute inset-x-0 bottom-0 z-0 pointer-events-none` + **`h-72` / `h-80` / `h-96`** **or** `min-h-[60%] max-h-[90%]` if you must stay in band mode without going full **2.3.0a**.
   - **Forbidden:** `h-full` / missing height / ultra-short `h-36`-`h-52` for tall decorative `object-cover` sources.

2. **Image:** `w-full h-full object-cover` + inline `style="object-position: center bottom;"` by default. **No** `opacity-20`-`opacity-35` on `<img>`.

3. **Overlay (short band):** sibling `absolute inset-0 bg-gradient-to-t from-[color:var(--color-page-bg)]/25 to-transparent` - tune **`from ... /15`-`/35`** only. Use `[background:linear-gradient(to top, color-mix(in srgb, var(--color-page-bg) 25%, transparent), transparent)]` or Tailwind gradient syntax with `var()`.

4. **Content:** `relative z-10` **without** opaque full-sheet `bg-*` on the wrapper (see shared rule below).

**Shared content rule (2.3.0 and 2.3.0a):** The **`relative z-10`** main wrapper must **not** use **opaque** `[background:var(--color-surface)]` / `[background:var(--color-page-bg)]` over the whole card - it **erases** the decorative `<img>`. Root may use `[background:var(--color-surface)]`; inner wrapper stays **transparent**. Per-row scrims using `[background:var(--color-surface)/80]` OK.

### 2.3.0a Default - full-module decorative atmosphere (`inset-0`, brief silent or full-bleed) — Theme-Aware

**Scope:** Same **class** as 2.3.0. Use **2.3.0a** when:

- The **user brief is silent** on atmosphere placement (**default** for this class - atmosphere behind **title, column headers, and rows** without a permanent gap), **or**
- The brief explicitly asks for **full-bleed** / **whole-card** backdrop.

Use **2.3.0** instead **only** when the brief **explicitly** wants **bottom-strip only**.

**Stack (Theme-Aware):**

1. Root: `relative overflow-hidden [background:var(--color-surface)]` (card surface - keep radius if any).
2. Decorative wrapper: `absolute inset-0 z-0 pointer-events-none` (**full card** - avoids the "cap above `h-80`" issue).
3. `<img>`: `h-full w-full object-cover`; inline **`object-position`** (often `center bottom` for tall sources) so the crop is not an empty slice.
4. **Readability overlay** (sibling `div`, `absolute inset-0`): **vertical** scrim using `var(--color-page-bg)`, e.g. `bg-gradient-to-b from-[color:var(--color-page-bg)]/92 via-[color:var(--color-page-bg)]/55 to-[color:var(--color-page-bg)]/15` - **tune stops** so **title + column labels + rows** all stay legible while the **lower/mid** card still shows texture. **One** attenuation path: **full-opacity `<img>`**; **no** `opacity-25` + strong overlay stack (2.3.1).
5. Content: `relative z-10`; **transparent** full wrapper `bg-*` (shared rule above). Optional **per-row** scrims (`[background:var(--color-surface)/80]`) are OK.

**Non-goals:** Do not use 2.3.0a for **primary-image** cards, chart-only views, or payloads **without** a decorative backdrop URL.

### 2.3.1 Decorative background visibility constraints (mandatory) — Theme-Aware

If you decide to render a decorative background image, it must be **visibly perceivable** while still secondary:

- Do not use tiny corner stamps as the only decorative rendering (e.g., `w-32 h-32` in one corner for a full card) unless the source explicitly asks for corner badge style.
- Prefer one of these placements:
  - full-card background layer, or
  - bottom-band / edge-overlay that spans most of card width.
- Decorative coverage should be meaningful (roughly >= 60% card width, or a full-width bottom band), not a small isolated patch.
- Keep readable contrast: prefer **full-opacity `<img>`** + **soft sibling gradient** (2.3.0a default scrim using `var(--color-page-bg)`, or 2.3.0 band overlay). Avoid both extremes:
  - too weak (nearly invisible - **invalid** for a chosen decorative layer),
  - too strong (competes with rows/text).
- For dense table/ranking cards, decorative image should stay behind content (`z` lower than data layer) and must not reduce table legibility.

Hard constraints (must satisfy all):

- Do not use tiny corner decorative stamp as the only background expression for a whole card.
- Do not use decorative opacity below `0.10` for the only decorative image layer (nearly invisible output is invalid).
- Do not combine both tiny area + ultra-low opacity as the final decorative solution.
- When a decorative bottom or full-card `<img>` layer exists: do **not** apply **opaque / high-opacity** `[background:var(--color-surface)]` (or any opaque background) on the **`relative z-10` main content wrapper** that spans the card - it **covers** the decorative layer and makes it **invisible** (see **shared content rule** under 2.3.0 / 2.3.0a).
- text/rows must remain high-contrast and unobstructed,
- reduce prominence with **one** readable mechanism by default: **soft gradient mask on a sibling `div`**, keeping the decorative `<img>` at **full opacity** unless 2.3.3 applies (already-faint source). **Do not** "stack" strong `<img>` opacity + strong overlay.
- avoid pushing key information below the fold.
- Do **not** stack **low `<img>` opacity** (roughly `opacity-25` / <= `0.35`) **with** a **strong gradient overlay** using `from-[color:var(--color-page-bg)]/50`-`/70`. That stack often reads as **solid** (decorative layer imperceptible). Prefer **full-opacity `<img>`** + **weak** gradient (`from ... /10`-`/25`), or **one** attenuation path only - not both strong.

Invalid pattern archetype (must avoid):

- a small corner layer with very low opacity that is technically present but visually imperceptible.

Preferred robust pattern for structured data cards:

- **Brief silent (default):** **2.3.0a** - `inset-0` decorative `<img>` + **vertical** scrim using `var(--color-page-bg)` so texture reaches **title + table header + rows** (no fixed **`h-80`** cap).
- **Brief explicitly wants bottom-strip only:** **2.3.0** - accept possible zone above the strip.
- **Brief asks "show more of the full frame":** **`object-contain`** on `<img>` where appropriate (2.3.5); still **no** strong double-attenuation.
- Keep information rows/content in a higher layer (`relative z-10`) above decorative layer.

Semantic default (mandatory):

- If payload contains dense structured information (table/list/comparison rows) and also includes an extra image that is not the primary factual carrier,
  treat that image as a decorative background candidate: **2.3.0a** when the brief is **silent** or asks for **full-bleed**; **2.3.0** **only** when the brief **explicitly** requests a **lower-only** strip. Never use a tiny corner patch as the only expression.

### 2.3.2 Decorative image decision procedure (mandatory)

Use this deterministic decision order:

1. Check whether the image is a factual carrier.
   - If yes (core content depends on seeing this image), classify as **primary image**.
2. If not factual-carrier, check whether the page is dense structured content (table/list/comparison rows >= 3).
   - If yes, classify as **decorative background image**.
3. If neither of the above, classify as **supporting image**.

For step (2), decorative background must use wide-layer placement (full-card or bottom-band), not corner-stamp placement.

### 2.3.3 Low-contrast / high-transparency image handling (mandatory)

Some decorative assets are intrinsically faint (high transparency, low contrast, near-white tones).
For these assets, use the following general strategy:

- Do not reduce visibility twice:
  - avoid lowering overall layer opacity again if the source image is already visually faint.
- Prefer **image + separate mask layer** over lowering image opacity:
  - keep decorative image reasonably visible,
  - control readability with an independent soft gradient/mask above or below content as needed.
- Avoid relying on non-standard utility classes for visibility-critical values.
  - if exact opacity is required, use explicit style value that is valid in plain CSS.
- If decorative image is still barely perceivable after safe layout/mask treatment, fallback to one of:
  - supporting image usage (small but clear),
  - or remove decorative rendering entirely.
  Never keep a "technically present but visually imperceptible" decorative layer.

### 2.3.4 Decorative band rendering compatibility (mandatory)

Some mobile WebViews (including embedded Chromium / ArkWeb-class engines) apply `mask-image` / `-webkit-mask-image` inconsistently on **`<img>`** elements. Tall decorative assets also crop poorly inside a short bottom band with default `object-cover` (often showing an empty / near-black region).

To keep outputs robust across hosts:

- Do **not** attach `mask-image` / `-webkit-mask-image` directly to a decorative **`<img>`**.
  - Put the image in a positioned wrapper; apply fades via a **separate sibling** `div` with a normal `linear-gradient` background (or mask on the wrapper `div`, not on the `<img>`).
- For decorative images in a **short horizontal band** (`object-cover` + fixed height):
  - set **`object-position` / `object-*` deliberately** so the band intersects a part of the bitmap that still reads as atmosphere (detail / mid-tone), not a random slice; **do not** assume `object-center` is always correct for extreme aspect ratios.
  - if the band is still visually empty after anchoring, **increase band height** (toward **`h-80`-`h-96`** / `min-h-[280px]`) - **tall source bitmaps need more band height** than `h-36`-`h-52` or most of the image stays off-canvas.
- Avoid stacking **PNG alpha** + **extra low opacity** on the same `<img>` (double attenuation). Prefer full-opacity image + separate overlay for readability.

### 2.3.5 High aspect-ratio decorative bitmaps (mandatory) — Theme-Aware

**Scope:** Same as 7.1 - only when a **decorative** (non-primary) photo layer is present. Does not relax data fidelity, table structure, or unrelated card types.

If the decorative URL is **portrait or otherwise much taller than wide**:

- **Baseline (brief silent):** **2.3.0a** (`inset-0` + vertical scrim using `var(--color-page-bg)`). If the brief forces a **bottom strip only**, use **2.3.0** with **`min-h-[60%] max-h-[90%]`** or **`h-96`** before ultra-short `h-36`-`h-64` crops.
- **`object-cover` inside only `h-36`-`h-64`** is a **last resort** for tall sources: too little vertical slice = weak atmosphere vs the file.
- **Show more of the frame** (when atmosphere should read closer to the opened asset): switch the `<img>` to **`object-contain`** (same band height or slightly taller), neutral fill in the band as needed - still one primary attenuation path (gradient), not stacked crushing.
- Never combine **strong gradient** + **low `<img>` opacity** unless the bitmap is **globally very bright**; otherwise the layer reads as missing. **Invalid pattern (parametric, any domain):** decorative `<img>` at `opacity` ~`0.2`-`0.35` **plus** overlay `from ... /50+` - usually **imperceptible**.

## 3) Single-image mode

When payload semantically represents one main image card:

- Root should be a single image-focused container.
- Include one main `<img>` that fills container.
- If there is a related link, bind click interaction through `data-interactions` `openUrl` action (not privileged direct navigation).

## 4) Visualization/chart mode

If data clearly represents trends/comparisons/proportions (time series, grouped metrics, etc.), chart representation is preferred.

For this H5 pipeline:

- When the system message includes **ECharts chart specification** (`09-chart-generation-echarts.md`), follow it for chart-type decisions, mobile layout (grid/legend/tooltip), and safe `option` JSON.
- You may use a chart library only if it can run safely in generated page context (e.g. ECharts from trusted CDN per that spec).
- If chart library is not practical in current output, use a clean table/list fallback.
- Do not render the same dataset twice (e.g., both full chart and duplicated full list of the same records) unless user explicitly requests dual view.
- Avoid complex inline JS transforms that reduce robustness.

## 5) State-driven visual binding

When an element has state fields (`disabled`, `isActive`, status flags):

- State effects must be scoped to the corresponding element, not unrelated siblings/parents.
- Disabled interactive elements should show both visual disabled style and non-interactive behavior.
- Prefer declarative class/style branching with minimal, readable logic.

## 6) Data fidelity safeguards

- Preserve source field semantics and values.
- Do not fabricate factual content (scores, dates, names, links).
- If data is missing/inconsistent, show graceful empty/unknown states instead of fake data.
- Do **not** add **primary CTAs** (full-width buttons, sticky footers, "add..." / "new" / "learn more") when the payload and same-message brief **do not** define that action or label - that is **speculative chrome**, not layout (see `02-input-handling.md` 1).

## 7) Information-first layout priority (mandatory)

For structured datasets (rankings, tables, multi-row comparisons):

- Prioritize information density and scanability: title/header + table/list rows must be the visual focus.
- Do not allocate large prime-space blocks to non-informational decoration.
- If both structured rows and a decorative image exist, rows win; decorative image must be demoted to background or minor accent.
- Default rule of thumb: first screen should expose meaningful data, not mostly empty image area.

## 8) Final output self-check (mandatory before finishing)

Before emitting final HTML, run this checklist mentally and revise if any item fails:

0. **Source binding:** no button/link/copy whose text or target is **not** implied by payload fields or the user's explicit brief (no phantom action rows).
1. **Role check**: every image has one explicit role (primary/supporting/decorative).
2. **Structure check**: for dense table/list cards, first screen shows meaningful rows/metrics (not decoration-dominant area).
3. **Decorative visibility check** (if decorative image exists):
   - not tiny corner-only patch,
   - effective visibility is perceivable (not near-invisible),
   - coverage is meaningful (wide layer / bottom band),
   - foreground text remains readable.
4. **Faint-asset check**:
   - if source decorative image is already faint, avoid additional global opacity reduction,
   - use independent mask strategy for readability control,
   - if still not perceivable, degrade to supporting image or remove decoration.
5. **Host-compat check** (decorative band):
   - no `mask-image` on `<img>`; fades use overlay `div` or wrapper-only effects,
   - bottom-band decorative `<img>` uses anchored `object-position` when `object-cover` is used.
6. **Theme color check**:
   - all backgrounds, text colors, borders, and overlays use theme variable tokens from 05.1 (`var(--color-page-bg)`, `var(--color-surface)`, `var(--color-elevated)`, `var(--color-border)`, `var(--color-text-*)`, `var(--color-accent)`) — not hardcoded hex values,
   - no raw hex colors (`#0A0A0A`, `#1A1A1A`, `#333333`, etc.) appear in class or style attributes; use `var(--color-*)` instead,
   - decorative overlay gradients use `from-[color:var(--color-page-bg)]/...` (not hardcoded `#0A0A0A`),
   - decorative root container uses `[background:var(--color-surface)]` (not hardcoded `bg-[#1A1A1A]`).
7. **Failure policy**: if any check fails, regenerate layout structure first; do not keep the current layout and only tweak small class values.
