# Output format — HTML fragment in host shell (primary)

The runtime **injects** a fixed page shell: `<!DOCTYPE>`, `<html>`, `<head>` (charset, viewport, Tailwind CDN), and an empty `<div id="root">`. Your output must **not** repeat that shell.

## Primary shape (required)

Output **only** a **single-root HTML fragment** — start directly with the root element, e.g. `<div class="...">...</div>`.

- The **first character** of your answer must be `<`.
- **No extra text at all** before or after HTML: no preamble, no explanations, no “Let me…”, no markdown fences, no code block markers, no comments outside the HTML fragment, no trailing notes.
- **Forbidden** in the fragment (host already provides them):  
  `<!DOCTYPE>`, `<html>`, `<head>`, `<body>`, `<meta>`, `<template>`, `<link>`, `<style>`, `<script>`, Tailwind CDN tags.
- **Allowed**: normal **body-like** markup under one outer root, **inline `style`**, **Tailwind `class`** (Tailwind is in the shell).
- **JavaScript**: no `<script>` in the fragment. Use `data-interactions` for clicks (see interaction DSL). Charts: tables / HTML+CSS unless product allows otherwise.
- **Streaming**: open the root tag immediately; never emit analysis text.

## Adaptive layout hard constraints (single output for multiple device widths)

The same generated fragment must adapt to narrow and wide containers (phone/fold/tablet) without per-device branches.

- Root container should be **fluid first**: prefer `w-full`; avoid hard-coding tiny max width as a universal default.
- Do **not** lock the entire card to `max-w-[420px]` unless the user explicitly asks for a fixed narrow card.
- For horizontal rows, always use responsive-safe structure:
  - row: `flex`
  - main text/content: `flex-1 min-w-0` (or equivalent)
  - fixed visual/action block: `shrink-0`
  - tags/attributes that may overflow: `flex-wrap`
- Text must use truncation/clamp (`truncate`, `line-clamp-*`) so wider/narrower containers both remain stable.

### Negative example (forbidden default pattern)

Do **not** use a globally narrow root as default output, such as:

```html
<div class="w-full max-w-[420px] mx-auto ...">...</div>
```

This pattern is only acceptable when the user explicitly requests a fixed narrow phone card.  
Default behavior must remain fluid and adaptive across larger containers.

## Strict prohibition

Do **not** output JSON wrappers such as `{"html":"..."}`.  
Do **not** output anything except raw HTML fragment text.

### Invalid examples (must never appear)

- `Let me analyze this...<div ...>`
- ```html ... ```
- `{"html":"<div ...>...</div>"}`

## Why

- Preview mounts as soon as a well-formed prefix of the fragment exists.
- Plain `<div…` streams immediately without waiting for JSON structure.
