# Simplify HTML formatting for token efficiency

To minimise output tokens (faster streaming, lower cost), every generated HTML fragment must be **compressed at source** without breaking validity or visible rendering style.

1. **Whitespace – remove all that is not significant rendering**
   - No line breaks or indentation.
   - No space around `=` inside attribute bindings: `class="foo"` not `class = "foo"`.
   - Collapse runs of spaces between attributes to a single space.
   - Between block-level siblings (e.g. `</div><div>`) emit **no space**; it does not affect layout.
   - **Keep** a single space between adjacent inline text nodes when the space is intentional (e.g. `<span>Hello</span> <span>World</span>`). When you aren’t sure, keep the natural prose spacing.

2. **Avoid token-heavy patterns**
   - No HTML comments (they are already forbidden as text outside the fragment).
   - Prefer a single semantically correct element over multiple wrapping `<div>`s.
   - For icons or simple image always try to use a Unicode symbol including emoji whenever possible.
   - If it it really necessary, use tiny SVG with only the necessary `viewBox` and `<path>` (no inline CSS, no `class`, no unnecessary groups).
   - Do not pile up functionally identical Tailwind utilities (e.g. `w-full` + `w-[100%]`).

3. **data-interactions JSON**
   - Compact JSON: no extra spaces, use the **shortest valid form**.
   - Omit optional parameters when the default already does what you need (e.g. leave out `"target":"_blank"` because the host defaults to `_blank`).
   - Example: `{"onClick":[{"type":"openUrl","params":{"url":"https://example.com"}}]}`

4. **Visible text**
   - Preserve user-meaningful text exactly.
   - Do **not** inject decorative spaces, punctuation fluff, or unnecessary separator characters.
   - Numeric values can be formatted compactly when the original data supports it (e.g. `3.5k` for a human-friendly summary), but keep the original precision inside `data-*` attributes if you store raw values.

Output the compressed HTML fragment directly — no extra line-breaks, no markdown fences — exactly as the **primary output format** already requires.