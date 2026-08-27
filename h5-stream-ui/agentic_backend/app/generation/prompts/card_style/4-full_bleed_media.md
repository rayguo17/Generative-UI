### 4. `full_bleed_media`
A photo or map covers the whole card; a dark vertical scrim guarantees white-text legibility. The image must carry information (a map, a place) — never decoration.

```html
<div class="relative rounded-[20px] overflow-hidden w-full h-full">
  <img src="MAP_URL" class="absolute inset-0 w-full h-full object-cover"/>
  <div class="absolute inset-0 bg-gradient-to-b from-black/40 to-black/50"></div>
  <div class="relative z-10 p-4 text-white text-sm font-medium">Traffic · WA-99, Seattle</div>
</div>
```