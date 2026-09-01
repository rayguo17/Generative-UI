"""
Agent B — Component Generator.

Generates HTML for ONE individual section/component at a time. Receives
a focused context package: section spec + retrieved data.
Produces a self-contained HTML fragment that replaces a placeholder in the
page shell.

Each call is independent — components can be generated sequentially or in
parallel batches (though local Ollama typically handles one at a time).

After generation, image URLs in the HTML are checked for reachability.
If any are unreachable, the component is regenerated once with a note
to omit the broken images.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────

import os

_IMAGE_CHECK_ENABLED = os.getenv("COMPONENT_IMAGE_CHECK_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
_IMAGE_CHECK_TIMEOUT = float(os.getenv("COMPONENT_IMAGE_CHECK_TIMEOUT", "5"))
_IMAGE_CHECK_MAX_RETRIES = int(os.getenv("COMPONENT_IMAGE_CHECK_MAX_RETRIES", "1"))

# ── JSON extraction + repair for widget_section_echarts ──────────────

def _strip_thinking(content: str) -> str:
    """Strip Qwen3 thinking tags (Unicode smart quotes variant)."""
    content = content.replace("\u201c\u201d\u201d\u201d", "")
    content = re.sub(r'^imd\s*', '', content)
    content = re.sub(r'^/no_think\s*', '', content)
    content = re.sub(r'^```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```\s*$', '', content)
    return content.strip()


def _repair_json(s: str) -> str:
    """Attempt to fix common LLM JSON mistakes."""
    s = s.strip()
    while "}}" in s:
        s = s.replace("}}", "}")
    s = s.replace('},"{', '},{')
    s = re.sub(r"'([^']*)'", r'"\1"', s)
    s = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', s)
    s = re.sub(r',\s*([}\]])', r'\1', s)
    return s


def _extract_json_brace_match(text: str, start_pos: int = 0) -> tuple[str | None, int]:
    """Find and extract a JSON object using brace depth counting."""
    first = text.find("{", start_pos)
    if first == -1:
        return None, -1
    depth = 0
    in_string = False
    escape = False
    for i in range(first, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[first:i+1], i+1
    return None, -1


def _extract_echarts_option(raw: str) -> str | None:
    """Extract and repair an ECharts option JSON from raw LLM output."""
    stripped = _strip_thinking(raw)
    pos = 0
    while True:
        json_str, next_pos = _extract_json_brace_match(stripped, pos)
        if json_str is None:
            break
        repaired = _repair_json(json_str)
        try:
            spec = json.loads(repaired)
            if isinstance(spec, dict) and ("series" in spec or "xAxis" in spec):
                return repaired
        except json.JSONDecodeError:
            pass
        pos = next_pos
    return None


def _wrap_widget_section_echarts_json(raw: str, idx: int) -> str:
    """Wrap a widget_section_echarts LLM output (JSON) into an HTML div with data-echarts."""
    json_str = _extract_echarts_option(raw)
    if json_str:
        return f'<div class="w-full h-64 bg-surface rounded-[20px] p-4" data-echarts=\'{json_str}\'></div>'
    logger.warning("widget_section_echarts [%d]: failed to extract valid JSON, wrapping raw", idx)
    escaped = raw.replace("<", "&lt;").replace(">", "&gt;")[:500]
    return f'<div class="w-full h-64 bg-surface rounded-[20px] p-4 flex items-center justify-center text-secondary text-sm">Chart data unavailable</div>'


# ── Image reachability check ──────────────────────────────────────────

_IMG_SRC_RE = re.compile(
    r'<img[^>]*\ssrc\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_IMG_TAG_RE = re.compile(
    r'<img[^>]*/?>',
    re.IGNORECASE,
)


def _extract_image_urls(html: str) -> list[str]:
    """Extract all http(s) image URLs from <img> tags in *html*.

    Skips ``data:`` URIs (cannot be checked). Returns only URLs that
    start with http:// or https:// — relative/bare-word src values
    are handled separately by _strip_invalid_img_srcs.
    """
    urls = _IMG_SRC_RE.findall(html)
    return [u for u in urls if u.startswith(("http://", "https://"))]


def _strip_invalid_img_tags(html: str) -> str:
    """Remove <img> tags with invalid src values.

    Strips <img> tags where src is:
    - A bare word (e.g., "cloudy", "thunder") — not a URL
    - A relative path without extension (e.g., "images/icon")
    - Empty or missing src
    Keeps <img> tags with http(s):// or data: URIs.
    """
    def _is_valid_src(src: str) -> bool:
        if not src or not src.strip():
            return False
        src = src.strip()
        if src.startswith(("http://", "https://", "data:")):
            return True
        return False

    # Find all <img> tags and remove ones with invalid src
    def _replace_img(match):
        img_tag = match.group(0)
        src_match = re.search(r'src="([^"]*)"', img_tag) or re.search(r"src='([^']*)'", img_tag)
        if src_match:
            if _is_valid_src(src_match.group(1)):
                return img_tag  # Keep valid
            else:
                logger.info("Stripping <img> with invalid src: %s", src_match.group(1)[:50])
                return ""  # Remove invalid
        return ""  # No src at all — remove

    return _IMG_TAG_RE.sub(_replace_img, html)


def _find_invalid_img_srcs(html: str) -> list[str]:
    """Find all invalid src values in <img> tags (for retry feedback).

    Returns a list of the invalid src strings (e.g., ["cloudy", "thunder"]).
    """
    invalid: list[str] = []
    for match in _IMG_TAG_RE.finditer(html):
        img_tag = match.group(0)
        src_match = re.search(r'src="([^"]*)"', img_tag) or re.search(r"src='([^']*)'", img_tag)
        if src_match:
            src = src_match.group(1).strip()
            if not src.startswith(("http://", "https://", "data:")):
                invalid.append(src)
        else:
            invalid.append("(missing src)")
    return invalid


def _build_image_retry_note_v2(invalid_srcs: list[str], failed_urls: list[str]) -> str:
    """Build a combined retry note for invalid src values and unreachable URLs."""
    lines = ["", "## IMAGE ISSUES — FIX BEFORE REGENERATING"]

    if invalid_srcs:
        lines.append("")
        lines.append("### Invalid src values (NOT valid URLs):")
        lines.append("The following <img> src values are NOT valid image URLs. They are bare")
        lines.append("words or relative paths. REMOVE these <img> tags entirely or replace")
        lines.append("with a real https:// URL from the provided DATA.")
        lines.append("")
        for src in invalid_srcs:
            lines.append(f"- src=\"{src}\" — this is not a URL")
        lines.append("")
        lines.append("Rules:")
        lines.append("- src MUST start with https:// and be an actual image URL from the DATA")
        lines.append("- NEVER use bare words like \"cloudy\" or \"sunny\" as src values")
        lines.append("- If the data has NO image URL, OMIT images entirely — no placeholders")

    if failed_urls:
        lines.append("")
        lines.append("### Unreachable image URLs:")
        lines.append("The following image URLs are unreachable or not images. Do NOT include")
        lines.append("any <img> tags for these URLs.")
        lines.append("")
        for url in failed_urls:
            lines.append(f"- {url}")

    lines.append("")
    lines.append("Regenerate the component without these broken images.")
    return "\n".join(lines)


# Domains that are never valid image sources
_BAD_IMAGE_DOMAINS = frozenset({
    "example.com", "example.org", "example.net",
    "picsum.photos", "placehold.co", "placekitten.com",
    "via.placeholder.com", "dummyimage.com", "fakeimg.pl",
})


def _is_obviously_bad_url(url: str) -> bool:
    """Quick check for URLs that are definitely not real images."""
    url_lower = url.lower()
    for domain in _BAD_IMAGE_DOMAINS:
        if domain in url_lower:
            return True
    return False


async def _check_image_reachable(
    session: "aiohttp.ClientSession",
    url: str,
    timeout: float = _IMAGE_CHECK_TIMEOUT,
) -> bool:
    """Check whether *url* points to a reachable image resource.

    Strategy:
      1. Quick blocklist check (example.com, picsum.photos, etc.)
      2. HEAD request — check status AND Content-Type is image/*
      3. If HEAD rejected (405), GET with Range — check Content-Type
      4. Any 2xx/3xx with image Content-Type → reachable; else unreachable
    """
    import aiohttp

    # Quick blocklist — skip network check entirely
    if _is_obviously_bad_url(url):
        logger.warning("Image URL blocked (known non-image domain): %s", url)
        return False

    client_timeout = aiohttp.ClientTimeout(total=timeout)

    try:
        async with session.head(
            url, timeout=client_timeout, allow_redirects=True
        ) as resp:
            if resp.status < 400:
                # Check Content-Type is actually an image
                content_type = resp.headers.get("Content-Type", "")
                if content_type.startswith("image/"):
                    return True
                # Status 200 but not an image (e.g., HTML page from example.com)
                logger.warning(
                    "Image URL returned non-image Content-Type: %s (status=%d, type=%s)",
                    url, resp.status, content_type,
                )
                return False
            if resp.status != 405:
                return False
    except Exception:
        pass

    # Fallback: GET with Range header (1 byte only)
    try:
        headers = {"Range": "bytes=0-1"}
        async with session.get(
            url,
            headers=headers,
            timeout=client_timeout,
            allow_redirects=True,
        ) as resp:
            if resp.status >= 400:
                return False
            content_type = resp.headers.get("Content-Type", "")
            if content_type.startswith("image/"):
                return True
            logger.warning(
                "Image URL returned non-image Content-Type (GET): %s (type=%s)",
                url, content_type,
            )
            return False
    except Exception:
        return False


async def _check_images(html: str) -> tuple[bool, list[str]]:
    """Check all image URLs in *html* for reachability.

    Returns ``(all_reachable, failed_urls)``.  If *html* contains no
    external image URLs, returns ``(True, [])`` immediately.
    """
    import aiohttp

    urls = _extract_image_urls(html)
    if not urls:
        return True, []

    # Deduplicate (same image may appear multiple times)
    unique_urls = list(dict.fromkeys(urls))

    async with aiohttp.ClientSession() as session:
        tasks = [
            _check_image_reachable(session, url) for url in unique_urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    failed: list[str] = []
    for url, result in zip(unique_urls, results):
        reachable = result if isinstance(result, bool) else False
        if not reachable:
            failed.append(url)
            logger.warning("Image URL unreachable: %s", url)

    return len(failed) == 0, failed


def _build_image_retry_note(failed_urls: list[str]) -> str:
    """Build a user-prompt note telling the LLM to omit broken images."""
    lines = ["", "## IMAGE URLS TO REMOVE"]
    lines.append(
        "The following image URLs are unreachable. Do NOT include any <img> "
        "tags for these URLs. If removing them leaves the component with no "
        "images, use a text-only layout (no placeholder images)."
    )
    lines.append("")
    for url in failed_urls:
        lines.append(f"- {url}")
    lines.append("")
    lines.append("Regenerate the component without these images.")
    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────

async def generate_component(
    section_context: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    interaction_logger: "LlmInteractionLogger | None" = None,
    image_check_enabled: bool = _IMAGE_CHECK_ENABLED,
) -> str:
    """Generate HTML for a single component/section.

    Args:
        section_context: Dict with keys:
            - index: section index in the plan
            - spec: section spec dict (widget, title, desc, data_needed, etc.)
            - data: retrieved data dict (field_path → value mappings)
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.
        interaction_logger: Optional logger for LLM interactions.
        image_check_enabled: If True, check image URLs in the generated
            HTML and retry once if any are unreachable.

    Returns:
        HTML fragment string for this component.
    """
    spec = section_context.get("spec", {})
    data = section_context.get("data", {})
    idx = section_context.get("index", 0)

    widget = spec.get("widget", "body_list")
    system_prompt = prompt_loader.load_component_system(widget)

    layout_direction = spec.get("layout_direction", "vertical")
    grid_columns = spec.get("grid_columns")
    visual_priority = spec.get("visual_priority", idx)

    # Format retrieved data — if it's a dict, pretty-print; otherwise use as string
    if isinstance(data, dict):
        retrieved_data = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        retrieved_data = str(data)

    user_prompt = prompt_loader.load_raw("component_generate/component_user.md").format(
        retrieved_data=retrieved_data,
    )

    # Truncate retrieved data if very long (>1500 chars)
    if len(retrieved_data) > 1500:
        user_prompt = user_prompt.replace(
            retrieved_data,
            retrieved_data[:1500] + "\n... (truncated, see context store for full data)"
        )

    if interaction_logger:
        llm.set_logger(interaction_logger, f"component_{idx}_{widget}")

    logger.info("Component Generator [%d:%s]: system=%d chars, user=%d chars, data_keys=%d",
                 idx, widget, len(system_prompt), len(user_prompt),
                 len(data) if isinstance(data, dict) else 0)

    html = await llm.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name=f"component_{idx}",
        max_tokens=4096,
        log_label=f"component_{idx}_{widget}",
    )

    # widget_section_echarts: LLM outputs JSON, wrap it in a data-echarts div
    if widget == "widget_section_echarts":
        return _wrap_widget_section_echarts_json(html, idx)

    # Basic validation
    if html and not html.strip().startswith("<"):
        logger.warning("Component [%d] does not start with '<', wrapping", idx)
        html = f'<div>{html}</div>'

    # ── Image validation + retry (gated by image_check_enabled) ─────
    if image_check_enabled:
        # Check for invalid src values (bare words, empty) and unreachable URLs
        invalid_srcs = _find_invalid_img_srcs(html)
        stripped_html = _strip_invalid_img_tags(html)

        failed_urls: list[str] = []
        if stripped_html:
            _, failed_urls = await _check_images(stripped_html)

        # If either issue found, retry with combined feedback
        if invalid_srcs or failed_urls:
            logger.warning(
                "Component [%d:%s]: %d invalid src values, %d unreachable URLs — regenerating",
                idx, widget, len(invalid_srcs), len(failed_urls),
            )

            retry_prompt = user_prompt + _build_image_retry_note_v2(invalid_srcs, failed_urls)

            retry_html = await llm.generate_text(
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                step_name=f"component_{idx}_img_retry",
                max_tokens=4096,
                log_label=f"component_{idx}_{widget}",
            )

            if retry_html and retry_html.strip().startswith("<"):
                html = _strip_invalid_img_tags(retry_html)  # Strip retry output too
            elif retry_html:
                html = _strip_invalid_img_tags(f'<div>{retry_html}</div>')
            else:
                html = stripped_html  # Retry failed — use stripped original
        else:
            html = stripped_html  # No issues — use stripped html

    return html


async def generate_echarts_option(
    section_context: dict,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
    *,
    interaction_logger: "LlmInteractionLogger | None" = None,
    log_label: str | None = None,
) -> str | None:
    """Generate a compact ECharts option JSON for one card chart section.

    Unlike generate_component(widget=widget_section_echarts), this does NOT
    wrap the JSON in a new div — the card HTML agent already emitted a sized
    empty ``data-echarts`` slot. Returns repaired JSON or None on failure.
    """
    spec = section_context.get("spec", {}) or {}
    data = section_context.get("data", {})
    name = spec.get("name") or section_context.get("index", "chart")
    components = [c for c in (spec.get("components") or []) if isinstance(c, str)]
    label = log_label or f"card_chart_{name}"

    echarts_system = prompt_loader.load_raw(
        "component_generate/component_generate_widget_section_echarts_system.md"
    )
    overlay = prompt_loader.load_raw(
        "component_generate/component_generate_card_echarts_system.md"
    )
    system_prompt = f"{echarts_system}\n\n{overlay}"

    if isinstance(data, dict):
        retrieved_data = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        retrieved_data = str(data)

    user_prompt = (
        f"chart_components: {json.dumps(components, ensure_ascii=False)}\n"
        f"section: {name}\n\n"
        + prompt_loader.load_raw("component_generate/component_user.md").format(
            retrieved_data=retrieved_data,
        )
    )

    if interaction_logger:
        llm.set_logger(interaction_logger, label)

    logger.info(
        "Card echarts [%s]: system=%d chars, user=%d chars, components=%s",
        name, len(system_prompt), len(user_prompt), components,
    )

    try:
        raw = await llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            step_name=label,
            max_tokens=2048,
            log_label=label,
        )
    except Exception as e:
        logger.error("Card echarts [%s] LLM call failed: %s", name, e)
        return None

    json_str = _extract_echarts_option(raw)
    if not json_str:
        logger.warning("Card echarts [%s]: failed to extract valid JSON", name)
        return None
    return json_str
