# CSS and HTML Subset Guidance


## Rationale

These restrictions exist to ensure that the resulting HTML would be generated correctly in the limited in-development browser environment.

## Rules

1. For HTML, only the tags listed below are permitted. Any tag not in this list may not be used.

```
<body>
<br>
<button>
<div>
<head>
<html>
<img>
<input>
<script>
<span>
<textarea>
<hr>
<label>
<svg>
<section>
<fieldset>
<legend>
<link>
<meta>
<template>
<p>
<table>
<caption>
<thead>
<colgroup>
<col>
<th>
<tbody>
<tr>
<td>
<tfoot>
<a>
<h1>
<h2>
<h3>
<h4>
<h5>
<h6>
<li>
<ol>
<ul>
<code>
<pre>
```


For example, `<img>` is allowed because it has consistent cross-browser rendering and is essential for displaying visual content, but `<select>` and `<video>` are not allowed due to lack of support. Use `<input>`, buttons, or custom-built components instead.

2. For typography, use `<span>` with Tailwind classes instead of semantic HTML tags. Use the following mappings:

| Instead of ... | Use ... |
|---|---|
| `<strong>` / `<b>` | `class="font-bold"` |
| `<em>` / `<i>` | `class="italic"` |
| `<u>` | `class="underline"` |
| `<s>` / `<del>` | `class="line-through"` |
| `<mark>` | `class="bg-yellow-200"` |
| `<small>` | `class="text-sm"` |
| `<sub>` | `class="align-sub text-sm"` |
| `<sup>` | `class="align-sup text-sm"` |


3. You are only allowed to use the tailwind classes that under the following categories.

```
align-content
align-items
align-self
animation
aspect-ratio
backface-visibility
background-attachment
background-clip
background-color
background-origin
background-position
background-repeat
background-size
background-image
block-size
border-collapse
border-color
border-spacing
border-style
box-sizing
caption-side
caret-color
clear
color
content
cursor
display
filter
filter: blur()
filter: brightness()
filter: contrast()
filter: grayscale()
filter: hue-rotate()
filter: invert()
filter: saturate()
filter: sepia()
flex
flex-basis
flex-direction
flex-grow
flex-shrink
flex-wrap
float
font-family
font-size
font-stretch
font-style
font-weight
gap
grid-auto-columns
grid-auto-flow
grid-auto-rows
grid-column
grid-row
grid-template-columns
grid-template-rows
height
inline-size
isolation
justify-content
justify-items
justify-self
letter-spacing
line-height
list-style-image
list-style-position
list-style-type
max-block-size
max-height
max-inline-size
max-width
min-block-size
min-height
min-inline-size
min-width
mix-blend-mode
object-fit
object-position
opacity
order
outline-color
outline-offset
outline-style
outline-width
overflow
overflow-wrap
padding
perspective
perspective-origin
place-content
place-items
place-self
pointer-events
position
rotate
scale
skew
table-layout
text-align
text-decoration-color
text-decoration-line
text-decoration-style
text-indent
text-overflow
text-shadow
text-transform
top / right / bottom / left
transform
transform-origin
transform-style
transition-behavior
transition-delay
transition-duration
transition-property
transition-timing-function
translate
vertical-align
visibility
white-space
width
will-change
word-break
z-index
border-radius
border-width
box-shadow
columns
line-clamp
margin
```

4. You are **not allowed under any circumstances** to use the tailwind classes in the following categories. **If there are any contradiction, this rule shall be enforced no matter what as using them in the system could cause massive error and consequence**.

```
accent-color
appearance
backdrop-filter
backdrop-filter: blur()
backdrop-filter: brightness()
backdrop-filter: contrast()
backdrop-filter: grayscale()
backdrop-filter: hue-rotate()
backdrop-filter: invert()
backdrop-filter: opacity()
backdrop-filter: saturate()
backdrop-filter: sepia()
background-blend-mode
box-decoration-break
break-after
break-before
break-inside
color-scheme
field-sizing
fill
filter: drop-shadow()
font-feature-settings
font-smoothing
font-variant-numeric
forced-color-adjust
hyphens
mask-clip
mask-composite
mask-image
mask-mode
mask-origin
mask-position
mask-repeat
mask-size
mask-type
overscroll-behavior
resize
scroll-behavior
scroll-margin
scroll-padding
scroll-snap-align
scroll-snap-stop
scroll-snap-type
stroke
stroke-width
text-decoration-thickness
text-underline-offset
text-wrap
touch-action
user-select
```

When you are considering using a Tailwind class, check whether its **underlying CSS property** matches any banned category from the list above. For example, `scheme-normal` ultimately compile to the `color-scheme` CSS property, so they fall under the `color-scheme` category and must not be used. Similarly, `drop-shadow-2xl` compiles to `filter: drop-shadow()`, so it is also banned.

## Conflict Resolution and Edge Cases

If a CSS category could plausibly fit under both the allowed list (section 3) and the banned list (section 4), the banned list always takes precedence. When you encounter a visual requirement that cannot be achieved using the allowed subset, do not use prohibited tags or classes under any circumstances. Instead, fall back to the closest allowed alternative by combining the allowed HTML elements with allowed tailwind classes and possibly javascript codes. If there are doubt of whether a class fall under a category or not, flag the uncertainty in a comment above the relevant code and proceed with the safest available alternative. If no viable alternative exists within the allowed subset, clearly explain what is missing and propose the closest approximation using only permitted constructs.

## Self-Validation Checklist

After generating HTML/CSS, verify:
1. Is every HTML tag in the allowed list? If not, replace it.
2. For every Tailwind class used, identify its CSS category. Does that category appear in the banned list (section 4)? If yes, remove it and use an alternative.
3. If stuck, use JavaScript or other tailwind classes as a fallback rather than prohibited classes.