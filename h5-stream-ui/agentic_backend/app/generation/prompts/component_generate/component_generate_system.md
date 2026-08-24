<!-- Shared prefix loaded from component_generate_shared.md -->

## IMAGE HANDLING

- Only render `<img>` if the DATA contains an actual image URL (starts with http/https/data:image). Do NOT use `picsum.photos` or other placeholder image services. If no image URLs are in the data, omit images entirely.
- Only render `<img>` if the src starts with http, https, or data:image.
- Always use `object-cover`; round the corners of visible images.
- For list/grid items with images, use a Thumbnail (`w-16 h-16 rounded-lg object-cover`) beside the text.
- For a single section image, use Standalone (`w-full object-cover rounded-xl`).

## OUTPUT

Raw HTML fragment for this component only. Single root. Starts with `<`. No fences.
