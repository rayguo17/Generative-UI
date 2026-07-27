# Interaction DSL v1 (safe bridge mode)

Use a structured interaction DSL inspired by existing card interaction specs.

## 1) Where to bind

- Bind interaction config on interactive elements using `data-interactions`.
- The value must be valid JSON string (double-quoted JSON), for example:
  `<button data-interactions='{"onClick":[{"type":"openUrl","params":{"url":"https://example.com"}}]}'>`

## 2) DSL shape

Top-level keys:

- `onClick`: array of interaction entries
- `onAppear`: array of interaction entries (optional)
- `onDisappear`: array of interaction entries (optional)

Each entry:

```json
{ "type": "openUrl", "params": { "url": "https://example.com" } }
```

Supported action types in v1:

- `openUrl`
  - params:
    - `url` (required, absolute https url)
    - `target` (optional, `_blank` by default)
- `setPage` (host-supported local pagination switch)
  - params:
    - `group` (required, string; pagination group id, e.g. `emp`)
    - `page` (optional, integer >= 1)
    - `delta` (optional, integer; e.g. `-1` / `+1` for prev/next)
  - note:
    - provide either `page` or `delta` (prefer explicit `page` for numbered buttons)
    - host toggles visibility of `[data-page-group="<group>"][data-page]`
    - host also updates `[data-page-btn-group="<group>"][data-page-btn]` active state and optional `[data-page-indicator-group="<group>"]`
- `updateData` (optional, for local UI state hints)
  - params:
    - `data`: array of `{ "key": "path", "value": "any" }`

## 3) Critical implementation constraints

- Do not rely on direct top navigation from generated page.
- Do not rely on inline `onclick` handlers.
- Do not use `javascript:` URLs.
- Keep generated interactions declarative via `data-interactions`.
- For click navigation, prefer semantic clickable elements (`button`, `a`, card container with role/button semantics).

## 4) Runtime expectation

The host app runs the page in a sandboxed iframe and listens to interaction intents.
Generated page should declare intent in `data-interactions`; host decides whether to execute.

## 5) Pagination authoring (with `setPage`)

- If `onClick` is `[]` or missing entries, the **host does nothing** on click — the control will not “change page” or update data.
- **Do not** put `data-interactions='{"onClick":[]}'` on buttons as a placeholder.
- For interactive pagination in this host, use `setPage` + data attributes:

```html
<div data-page-group="emp" data-page="1">…rows page 1…</div>
<div data-page-group="emp" data-page="2" hidden>…rows page 2…</div>
<div data-page-group="emp" data-page="3" hidden>…rows page 3…</div>

<button data-page-btn-group="emp" data-page-btn="1"
  data-interactions='{"onClick":[{"type":"setPage","params":{"group":"emp","page":1}}]}'>1</button>
<button data-page-btn-group="emp" data-page-btn="2"
  data-interactions='{"onClick":[{"type":"setPage","params":{"group":"emp","page":2}}]}'>2</button>
<button data-page-btn-group="emp" data-page-btn="3"
  data-interactions='{"onClick":[{"type":"setPage","params":{"group":"emp","page":3}}]}'>3</button>

<button data-interactions='{"onClick":[{"type":"setPage","params":{"group":"emp","delta":-1}}]}'>上一页</button>
<button data-interactions='{"onClick":[{"type":"setPage","params":{"group":"emp","delta":1}}]}'>下一页</button>
```

- Optional indicator node:
  `<span data-page-indicator-group="emp" data-page-total="3">1 / 3 页</span>`
