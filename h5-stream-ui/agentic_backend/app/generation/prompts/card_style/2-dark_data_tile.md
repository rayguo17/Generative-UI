### 2. `dark_data_tile`
A near-black tile for data-dense finance/metrics. White headings; deltas in semantic hues (gain `emerald-400`, loss `red-400`, caution `amber-500`). Sparklines stroke the semantic hue.

```html
<div class="rounded-[20px] w-full h-full p-4 flex flex-col bg-neutral-900 text-white">
  <div class="text-sm font-medium">BIDU</div>
  <div class="text-5xl font-light tabular-nums">108.42</div>
  <p class="text-sm text-emerald-400">+1.23 (+1.15%)</p>
</div>
```