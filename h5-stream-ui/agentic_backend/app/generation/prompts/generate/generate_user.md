## Task
Generate a complete H5 HTML fragment based on the layout plan below. Output ONLY the raw HTML fragment — start with '<', no markdown fences, no explanations.

## Layout Plan
```json
{plan_json}
```

## Data to Render
```
{data_context}
```


## Critical Rules (must follow exactly)
1. First character MUST be '<' — start the root element immediately
2. Single root element — typically <div class="...">
3. NO markdown fences (```), NO JSON wrappers, NO preamble or commentary
4. NO <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link> tags
5. Use Tailwind utility classes for ALL styling (host provides Tailwind CDN)
6. For rows: use flex with flex-1 min-w-0 on main content, shrink-0 on fixed elements
7. Use truncate or line-clamp-2 for text overflow
8. Root: w-full, rounded-[20px], overflow-hidden
9. Single primary accent color unless data warrants more
10. Use data-interactions='{{"onClick":[{{"type":"...","params":{{...}}}}]}}' for any interactive elements
11. For images: classify as primary (main <img>), supporting (small inline), or decorative (absolute inset-0 background layer)
12. Render ALL array items — never sample just the first one
13. No fabricated content — every visible string must come from the data
14. Output ONLY the HTML — no text before or after