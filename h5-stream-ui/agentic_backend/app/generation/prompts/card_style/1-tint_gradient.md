### 1. `tint_gradient`
A vertical, single-hue gradient matched to the entity's state (sunny → sky blue, night → deep slate, storm → dark slate). All text white; secondary text `white/80`. Minimalistic: the color carries the mood while the content stays sparse.

```html
<div class="rounded-[20px] w-full h-full p-4 flex flex-col justify-between bg-gradient-to-b from-sky-500 to-sky-700 text-white">
  <div class="text-sm font-medium">Hong Kong</div>
  <div class="text-6xl font-light leading-none tabular-nums">31°</div>
  <p class="text-sm text-white/80">Sunny · H:33° L:27°</p>
</div>
```
