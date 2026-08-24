<!-- Shared prefix loaded from component_generate_shared.md -->

## FOOTER COMPONENT STRUCTURE (MUST)

A bottom section with muted, low-emphasis text. Meta info only — source attribution, last-updated, links, copyright. No CTAs unless the plan specifies interactions.

```
<div class="flex flex-col gap-1 text-center pt-4 border-t border-default">
  <p class="text-xs text-tertiary">Source: visit-hangzhou.gov.cn</p>
  <p class="text-xs text-tertiary">Last updated: 2026-08</p>
</div>
```

- Muted text (10-12 px): `text-tertiary`.
- Optional `border-t border-default` top separator.
- `text-center` or right-aligned.
- No card wrapper, no rounded corners, no background.
- Meta info only — no fabricated content.

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- Footer typically has no images — skip unless the data explicitly provides one.

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
