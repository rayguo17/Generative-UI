"""Render a generated card fragment onto a fixed-pixel surface and screenshot it.

Grid units map at 75px per cell (4x6 → 300×450). Playwright is optional:
missing install or a render failure logs a warning and returns None so
card generation still succeeds.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.generation.intent_classifier import _SURFACE_RE
from app.utils.llm_logger import create_session_id

logger = logging.getLogger(__name__)

CELL_PX = 75
DEFAULT_SURFACE = (300, 300)  # 4x4 fallback
_DEVICE_SCALE = 2
_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"


def surface_pixels(surface_size: str | None) -> tuple[int, int]:
    """Map a grid size like ``'4x6'`` to CSS pixels.

    Missing / unparseable → 300×300 (4x4). ``2x4`` is 150×300 (order is not swapped).
    """
    if not surface_size:
        return DEFAULT_SURFACE
    m = _SURFACE_RE.search(str(surface_size))
    if not m:
        return DEFAULT_SURFACE
    return int(m.group(1)) * CELL_PX, int(m.group(2)) * CELL_PX


def wrap_card_html(html_fragment: str, width: int, height: int, theme: str | None = None) -> str:
    """Wrap a card fragment in the page shell, sized to the surface.

    Args:
        theme: theme name to set on <html data-theme="...">. If None, the
            prefix's default (no data-theme attribute) is used — the host
            or genui-widgets CDN decides the theme.
    """
    prefix_path = _ASSETS_DIR / "page_shell_prefix.html"
    suffix_path = _ASSETS_DIR / "page_shell_suffix.html"
    prefix = prefix_path.read_text(encoding="utf-8") if prefix_path.is_file() else (
        "<!doctype html><html><head></head><body><div id=\"root\">"
    )
    suffix = suffix_path.read_text(encoding="utf-8") if suffix_path.is_file() else (
        "</div></body></html>"
    )

    # Inject data-theme if specified
    if theme:
        prefix = prefix.replace(
            '<html lang="zh-CN">',
            f'<html lang="zh-CN" data-theme="{theme}">',
            1,
        )

    surface = (
        f'<div id="card-surface" style="width:{width}px;height:{height}px;overflow:hidden">'
        f"{html_fragment}"
        f"</div>"
    )
    return prefix + surface + suffix


async def screenshot_card(
    html_fragment: str,
    surface_size: str | None,
    output_dir: Path,
    *,
    stem: str | None = None,
    theme: str | None = None,
) -> Path | None:
    """Wrap fragment, render at surface size, write PNG. None on failure.

    Args:
        theme: theme name for <html data-theme="...">. None = no theme attribute.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning(
            "playwright not installed — skip card screenshot. "
            "pip install playwright && python -m playwright install chromium"
        )
        return None

    width, height = surface_pixels(surface_size)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"card_screenshot_{create_session_id()}"
    png_path = output_dir / f"{stem}.png"
    wrapped = wrap_card_html(html_fragment, width, height, theme=theme)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".html", prefix=f"{stem}_", delete=False, encoding="utf-8", mode="w",
        ) as tmp:
            tmp.write(wrapped)
            tmp_path = Path(tmp.name)

        file_url = tmp_path.resolve().as_uri()
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=_DEVICE_SCALE,
                )
                page = await context.new_page()
                await page.goto(file_url, wait_until="networkidle", timeout=30000)
                if await page.locator("[data-echarts]").count() > 0:
                    try:
                        await page.wait_for_selector("[data-echarts] canvas", timeout=5000)
                    except Exception:
                        logger.warning("card screenshot: echarts canvas did not appear in time")
                    await page.wait_for_timeout(500)
                await page.locator("#card-surface").screenshot(path=str(png_path))
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("card screenshot failed: %s", e)
        return None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    logger.info("card screenshot saved: %s (%dx%d css, scale=%d)",
                png_path, width, height, _DEVICE_SCALE)
    return png_path
