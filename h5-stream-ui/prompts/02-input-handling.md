# Input handling rules

The user message is a **single request**: instructions and raw data may appear together (e.g. a short brief, then a pasted JSON blob).

1. **Treat the data in the user message as authoritative** for facts (names, numbers, URLs, dates). Do not invent entities that are not supported by that content unless the user explicitly asks for placeholders (e.g., “use lorem ipsum”).
   - **Bind visible UI to sources (mandatory):** Any **user-visible string** and any **`<img src>` / `openUrl` / primary CTA label** must be traceable to **(a)** a value in the structured payload (or computed **deterministically** from it, e.g. `list.length` → “共 5 个” only when that matches the array), **or (b)** explicit text in the **same-message** instruction brief. **Do not** add “product polish” that the payload does not carry: extra **footer / full-width primary buttons** (“添加闹钟”, “新建”, “了解更多”, “立即查看”), **FABs**, or other **phantom actions** when neither data nor brief defines that control or copy. Read-only lists stay read-only unless interactions are specified.
   - **Exceptions** (only these): allowed **empty/unknown** states for missing fields; **inline decorative SVG** (no external URL); and **image fallbacks** per `08-special-data-processing.md` §2.2 when a declared image URL is invalid—not invented marketing assets.
2. **JSON**:
   - Preserve meaning; reorganize visually.
   - Prefer safe rendering: escape or avoid executing untrusted strings as code.
   - If a field looks like HTML, still prefer showing it as **text** unless the user explicitly requests HTML rendering.
3. **Text**:
   - Detect structure heuristically (headings, lists, code blocks, key:value lines).
   - If the text is extremely long, use progressive disclosure (details/summary, tabs, sections) **without** losing access to the full content (e.g., collapsible sections).
4. **Missing / inconsistent data**:
   - Show graceful empty states (“No items”, “Unknown field”) rather than crashing layouts.
5. **Security mindset**:
   - Assume payload may be untrusted. Avoid `eval`, dynamic `Function`, and inline handlers like `onclick="..."`.
   - For user interactions, prefer declarative `data-interactions` DSL and event delegation (`addEventListener`) in a `<script>` block.
   - For navigation intent, encode it in `data-interactions` (`openUrl`) instead of hard-wiring privileged navigation logic.
