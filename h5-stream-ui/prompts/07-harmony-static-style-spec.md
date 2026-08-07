# Harmony static style specification — card components (Theme-Aware)

This specification defines the visual style for **card‑style components** (ranking cards, product cards, info cards, etc.) that appear **inside** the page sections described by `05.1-core-design-principles.md`.  
It does **not** describe the overall page structure; that is governed by `05.1`.  

When the model outputs a full page, the root is a `<section>` hierarchy (per 05.1), and any card-like content block **inside** a section should follow the rules below.  
If the user explicitly asks for a standalone card fragment (not a full page), this specification applies directly to the root container.

All rules are **MUST** unless stated otherwise.  
Always comply with the **allowed CSS subset** defined in `10-css-and-html-subsets-categories.md`.  
Properties like `backdrop-filter`, `mask-image`, and `scroll-behavior` are **banned** — use gradient overlays and native overflow instead.

**Color tokens:** All colors MUST use the theme variable tokens from `05.1-core-design-principles.md` (see the Theme System section). Never use raw hex values.

---

## 1) Card container

- A card uses a **single root container** with:
  - `width: 100%`
  - a sensible `max-width` (default **`max-w-2xl`**; avoid `max-w-[420px]` unless the user specifically requests a narrow card)
  - `rounded-[20px] overflow-hidden`
  - `[background:var(--color-surface)]` (surface, distinct from page `[background:var(--color-page-bg)]`)
- The container is structured as a **flex column** (`flex flex-col` or equivalent).
- Content lives in an explicit inner wrapper that is scroll-safe (`overflow-auto` when content can be long).
- Whitespace separation only; **do not add decorative section dividers** unless the data contains distinct semantic groups.

**Scope rule — rounded corners belong to cards only:** The `rounded-[20px]` (or any large border-radius) defined in this spec applies **exclusively** to genuine card containers. Do **not** apply `rounded-[20px]`, `rounded-2xl`, or `rounded-xl` to:
  - Flex row containers (`flex`, `flex-wrap`)
  - List items (`<li>`, `divide-y` parents)
  - Divider lines or spacer blocks
  - Semantic `<section>` elements (these are page-level structure, not cards)

  Small inline elements (chips, tags, badges, buttons) may use `rounded-md` (6 px) at most — never card-level rounding. If a layout uses border separators (`divide-y`, `divide-x`, `border`) to structure content, it is **not** a card and must **not** receive card-level rounding.

## 2) Responsive row primitives

Every horizontal row (cover+text, left-right info, button groups, metric rows) **must** use:

| Role | Tailwind classes |
|------|------------------|
| Parent | `flex` |
| Main content | `flex-1 min-w-0` |
| Fixed side element | `shrink-0` |
| Multi‑tag / attribute overflow | `flex-wrap` |

Align with the content’s semantics: `items-center` for compact rows, `items-start` when a row contains multi‑line text.  
These primitives are documented in `04-tailwind-and-stack.md` and are **mandatory** for adaptive width.

---

## 3) Spacing rhythm

- Baseline: **4 px grid** (`4`, `8`, `12`, `16`, …).
- Typical gaps: `gap-2` (8 px) or `gap-3` (12 px) between sibling blocks.
- Keep spacing consistent; avoid one‑off values that break the rhythm.

---

## 4) Typography

Apply the global typography system from `05.1-core-design-principles.md` (font stack, size hierarchy, colour prominence).  All text inside a card must comply with that system.  Cards add no additional typography rules beyond the baseline.

---

## 5) Card header (optional)

- Render a card header **only** when the source data supplies explicit app‑identity metadata (icon + app name).
- If identity fields are missing: **do not** invent a header, and **do not** promote other fields into one.
- A “more” action (e.g. «更多») must correspond to a real data intent; never fabricate it.

---

## 6) Button matrix (Theme-Aware)

Use three semantic types:

| Type | Description | Example classes |
|------|-------------|-----------------|
| **Primary** | Highest‑priority CTA | `[background:var(--color-accent)]` + `text-white` |
| **Filled‑secondary** | Normal secondary action | `[background:var(--color-elevated)]` + `[color:var(--color-accent)]` |
| **Text** | Low‑emphasis action | no background, `[color:var(--color-accent)]` or warning text only |

Sizes:

- Small control: height ~`28 px` (`h-7`)
- Large control: height ~`40 px` (`h-10`)
- No fixed width unless the user explicitly requires it.

Disabled state: visibly muted (`opacity-50` + `pointer-events-none`).

Colours come from the global **Color Scheme** in `05.1-core-design-principles.md`.  Pick one accent hue as the page accent; buttons use that accent via `var(--color-accent)`.  Warning / destructive buttons use the Red accent (`--color-accent` set to `#E84026`, or use `[color:var(--color-error-text)]` if available).

---

## 7) Region backgrounds within a card (Theme-Aware)

- **Neutral info blocks**: use `[background:var(--color-elevated)]` — this provides a subtle differentiation from the card surface `[background:var(--color-surface)]`.
- **Semantic state blocks** (success / warning / error / info): use the Semantic state tints from `05.1` — background and text via theme tokens.  
  These are **exceptions** to the “one shade” rule and are permitted **only for small state indicators**, not for large decorative regions.
- **Never** use saturated solid backgrounds for large non‑primary regions.
- Region spacing remains on the 4‑px grid (`p-2`, `p-3`, `gap-2`).

### 7.1 Decorative image (background/band) — decision table (Theme-Aware)

**Scope:** Applies **only** when a card component intentionally includes a **non‑primary decorative `<img>`** (background atmosphere or bottom band).  
Cards without such a layer — plain tables, charts, text‑only — are **unchanged**.

**Decision procedure** (apply in order):

| # | Condition | Action |
|---|-----------|--------|
| 1 | The user’s brief explicitly asks for a **bottom‑only strip** | Use **§2.3.0** pattern: `absolute inset-x-0 bottom-0 h-72` to `h-96`, vertical gradient overlay using `var(--color-page-bg)`, content `relative z-10` (no opaque full‑sheet background). |
| 2 | The user’s brief is **silent** (or asks for full‑bleed) — **default** | Use **§2.3.0a** pattern: `absolute inset-0` decorative wrapper + vertical scrim using `var(--color-page-bg)` — tune for legibility. |
| 3 | The decorative source is tall (height > 1.25 × width) **and** a narrow band is forced | Switch the `<img>` to **`object-contain`** (not `object-cover`) to show more visual content. |
| 4 | Card content + title exceed ~60% of card height | Prefer `inset-0` (full cover, §2.3.0a) to avoid a “cap” above a short bottom band. |

**Hard constraints** (applies to all decorative cases):

- Use **one** attenuation path: **full‑opacity `<img>` + sibling gradient overlay**, **not** low `<img>` opacity **and** a strong gradient.
- The `z‑10` content wrapper must be **transparent** — no `[background:var(--color-surface)]` or `[background:var(--color-page-bg)]` over the full card, or the decorative image becomes invisible.
- **Never** apply `mask-image` or `-webkit-mask-image` directly on `<img>` (banned by host — see `10-css-html-subsets`).
- A tiny corner patch (e.g. `w-32 h-32` in one corner) is **not** a valid decorative layer for a whole card.
- After placing a decorative layer, run the **decorative visibility self‑check** from `08-special-data-processing.md` §8.

---

## 8) Shape consistency

- Rectangular elements (image chips, buttons, blocks) should use even‑pixel border radii and stay consistent within one card (`rounded-lg` → `8 px`, `rounded-xl` → `12 px`, `rounded-2xl` → `16 px`, `rounded-[20px]` for the card itself).
- Avoid mixing many unrelated corner styles.

---

## 9) Text overflow

Long text must **never** break the layout. Apply truncation contextually:

- Title: `truncate` or `line-clamp-2`
- Subtitle / meta / tag: `truncate`
- Button text: `whitespace-nowrap` first, then `truncate` if width is constrained

Missing `min-w-0` or `shrink-0` in a flex row is considered a layout error when combined with overflow.

---

## Tailwind mapping reference (Theme-Aware)

- Card container: `rounded-[20px] overflow-hidden p-3` (or `p-4` for more breathing room) with `[background:var(--color-surface)]`
- Elevated surface (inset blocks): `[background:var(--color-elevated)]`
- Borders / dividers: `[border-color:var(--color-border)]` / `divide-[color:var(--color-border)]`
- 4‑px rhythm: `gap-1`/`2`/`3`/`4`, `p-2`/`3`/`4`, `mb-2`/`3`
- Overflow helpers:
  - Single‑line: `truncate`
  - Two‑line: `line-clamp-2`
  - Three‑line: `line-clamp-3`
- Text hierarchy:
  - Heading: `[color:var(--color-text-heading)]`
  - Primary body: `[color:var(--color-text-primary)]`
  - Secondary / meta: `[color:var(--color-text-secondary)]`
  - Tertiary / disabled: `[color:var(--color-text-tertiary)]`
