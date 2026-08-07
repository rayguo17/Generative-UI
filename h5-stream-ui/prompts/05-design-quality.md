# Design quality bar (Theme-Aware)

1. Aim for a **clean, modern mobile UI**: spacing, hierarchy, alignment, and consistent type scale.
2. Use the active theme's surface tokens (`--color-page-bg` page / `--color-surface` card / `--color-elevated` inset), a clear primary accent via `--color-accent`, and proper text contrast hierarchy via text tokens. For card components, follow the specific rules in `07-harmony-static-style-spec.md`.
3. Use a **single primary accent** (via `--color-accent`) and neutral surfaces; avoid rainbow gradients unless the payload is inherently colorful (e.g., charts) or the user requests it.
4. Provide **clear hierarchy**: title → summary/meta → primary content → secondary details.
5. Interactions (tabs, filters, expand/collapse) should be **simple and robust**; prefer minimal JS.
