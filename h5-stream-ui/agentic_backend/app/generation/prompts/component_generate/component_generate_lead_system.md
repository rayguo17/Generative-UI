<!-- Shared prefix loaded from component_generate_shared.md -->

## HERO BANNER SECTION (MUST)

If there are a specified image given by the user to describe this component, the component should start with a centered image or a full width image with
Afterwards, it should contains the summary in a text paragraph format. The summary should be provided by the user side and if it is too long you need to summarize and paraphrase it yourself. The text should be concise, within 1 statement, it serves as an introduction, summary, or preface to entice the reader to read more about the page.

Example of a result are as the following.
```
<p class="text-sm text-secondary mb-4">Description</p>
<!-- Include image here ONLY if the data contains an image URL -->
```

The description should use the described format. No changes of text format is permitted.

## IMAGE HANDLING (lead: Standalone or Decorative)

The lead should use the following image format.

**Standalone (hero) image** — a visible full-width image AFTER the summary paragraph it (should be text-on-top).
  - Example of styling: `<img class="w-full object-cover rounded-xl mb-4">`.

General rules:
- **No fabrication**: only use image URLs that appear in the provided DATA. If the data has NO image URL, **DO NOT** put any image at all. NEVER invent or guess a URL.
- **Always `object-cover`**; round the corners of images.
