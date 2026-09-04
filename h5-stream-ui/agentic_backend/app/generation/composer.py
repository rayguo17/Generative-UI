"""
Generation Composer — programmatic orchestrator for the two-agent generation pipeline.

Replaces the old monolithic generate step. Responsibilities:
  1. Parse the plan: extract sections, style preferences, card_type
  2. Retrieve data per section: search context store for matching data values
  3. Call Agent A (Page Structure Generator): generate HTML shell with placeholders
  4. Call Agent B (Component Generator) per section: generate component HTML
  5. Assemble: replace placeholders with generated component HTML
  6. Stream: yield final assembled HTML to the SSE callback

All composition logic is PROGRAMMATIC — no LLM calls in this module itself.
LLM calls happen in page_generator, component_generator, and content_retriever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Awaitable

from app.config import AppConfig
from app.generation.llm_client import GenerationLlmClient
from app.generation.page_generator import generate_page_shell
from app.generation.component_generator import generate_component, generate_echarts_option
from app.generation.card_generator import generate_card, _normalize_sections_data
from app.generation.card_charts import (
    chart_sections,
    fill_card_charts,
    inject_chart_theme,
)
from app.generation.card_screenshot import screenshot_card
from app.generation.generate import generate_html  # fallback
from app.prompts.loader import PromptLoader
from app.utils.token_counter import count_tokens

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

# Regex to find placeholder markers in the page shell.
# Matches a single comment: <!-- COMP_PLACEHOLDER:N:type -->
# No closing tag needed — the composer replaces each marker with component HTML.
PLACEHOLDER_RE = re.compile(
    r'<!-- COMP_PLACEHOLDER:(?:section_)?(\d+):(\w+) -->'
)

# Maximum number of components with NO placeholder match before we fall back
MAX_ASSEMBLY_FAILURES = 2

SseCallback = Callable[[str, str, str, str], Awaitable[None]]


class GenerationComposer:
    """Programmatic coordinator for the two-agent generation pipeline."""

    def __init__(
        self,
        config: AppConfig,
        prompt_loader: PromptLoader,
    ):
        self.config = config
        self.prompt_loader = prompt_loader
        self._total_llm_calls = 0
        self.last_screenshot_path: Path | None = None

    async def compose(
        self,
        plan: dict,
        working_query: str,
        llm: GenerationLlmClient,
        sections_data: dict[int, dict] | None = None,
        sse_callback: SseCallback | None = None,
        interaction_logger: "LlmInteractionLogger | None" = None,
    ) -> str:
        """Run the two-agent generation pipeline.

        Args:
            plan: Layout plan dict from the plan step.
            working_query: The working query (original or indexed).
            llm: Local LLM client.
            session_id: Session ID for context store lookup.
            sse_callback: Optional SSE callback for progress events.
            interaction_logger: Optional interaction logger.

        Returns:
            Complete assembled HTML string.
        """
        start_time = time.monotonic()
        sections = plan.get("sections", [])
        plan_data_summary = plan.get("data_summary", {})

        if not sections:
            logger.warning("Plan has no sections — falling back to legacy generate")
            return await generate_html(working_query, plan, llm, self.prompt_loader)

        await self._emit(sse_callback, "phase_start", "", "generate",
                         f"Generating page ({len(sections)} sections)...")

        # ── Step 1: Use pre-gathered data from the researcher ──
        if sections_data is None:
            sections_data = {}

        section_contexts = []
        for i, section in enumerate(sections):
            raw = sections_data.get(f"{i}", sections_data.get(i, {}))
            # Extract the raw text from the researcher's output
            # (fields_text for single_lookup, items_text for search_all/iterate_days)
            if isinstance(raw, dict):
                data = raw.get("fields_text") or raw.get("items_text") or raw
            else:
                data = raw
            section_contexts.append({
                "index": i,
                "spec": section,
                "data": data,
            })

        # ── Step 2: Generate page shell (Agent A) ───────────────
        await self._emit(sse_callback, "phase_start", "", "page_shell",
                         "Generating page structure...")

        try:
            shell_html = await generate_page_shell(
                plan, llm, self.prompt_loader,
                interaction_logger=interaction_logger,
                log_label="page_generate",
            )
            self._total_llm_calls += 1
        except Exception as e:
            logger.error("Page shell generation failed: %s — falling back", e)
            return await generate_html(working_query, plan, llm, self.prompt_loader)

        await self._emit(sse_callback, "phase_end", "", "page_shell")

        # Verify shell has placeholders
        placeholder_count = len(PLACEHOLDER_RE.findall(shell_html))
        if placeholder_count == 0:
            logger.warning("Page shell has no placeholders — falling back to legacy generate")
            return await generate_html(working_query, plan, llm, self.prompt_loader)

        logger.info("Page shell: %d placeholders for %d sections",
                     placeholder_count, len(sections))

        # ── Step 3: Generate each component (Agent B) ───────────
        await self._emit(sse_callback, "phase_start", "", "components",
                         f"Generating {len(section_contexts)} components...")

        components: dict[int, str] = {}  # section_index → html
        for ctx in section_contexts:
            idx = ctx["index"]
            widget = ctx["spec"].get("widget", "body_block")
            await self._emit(sse_callback, "phase_progress", "", "components",
                             f"Component {idx+1}/{len(section_contexts)}: {widget}")

            try:
                component_html = await generate_component(
                    ctx, llm, self.prompt_loader,
                    interaction_logger=interaction_logger,
                    image_check_enabled=self.config.component_image_check_enabled,
                )
                components[idx] = component_html
                self._total_llm_calls += 1
            except Exception as e:
                logger.error("Component [%d:%s] generation failed: %s", idx, widget, e)
                # Insert a fallback placeholder for this component
                components[idx] = (
                    f'<div class="px-4 py-3 text-gray-500 text-sm">'
                    f'{widget} — generation failed</div>'
                )

        await self._emit(sse_callback, "phase_end", "", "components")

        # ── Step 4: Assemble ────────────────────────────────────
        await self._emit(sse_callback, "phase_progress", "", "assemble",
                         "Assembling final HTML...")

        final_html = self._assemble(shell_html, components)

        # ── Step 5: Final validation ────────────────────────────
        if not final_html or not final_html.strip().startswith("<"):
            logger.warning("Assembly produced invalid HTML — falling back to legacy generate")
            return await generate_html(working_query, plan, llm, self.prompt_loader)

        elapsed = (time.monotonic() - start_time) * 1000
        logger.info("Composer: %d sections, %d LLM calls, %.0fms, %d chars output",
                     len(sections), self._total_llm_calls, elapsed, len(final_html))

        # Stream the assembled HTML to the frontend
        if sse_callback:
            await sse_callback("token", final_html, "generate", "")

        await self._emit(sse_callback, "phase_end", "", "generate")

        return final_html

    async def compose_card(
        self,
        plan: dict,
        sections_data: "list[dict] | dict | None",
        llm: GenerationLlmClient,
        *,
        interaction_logger: "LlmInteractionLogger | None" = None,
        sse_callback: SseCallback | None = None,
        output_dir: Path | None = None,
        screenshot_stem: str | None = None,
    ) -> str:
        """Assemble a card: HTML agent generates final fragment (charts included).

        The card_generate LLM produces the complete HTML including chart JSON
        directly in data-echarts attributes. No separate echarts LLM call.
        If output_dir is set, screenshot the filled card at plan surface size.
        """
        start_time = time.monotonic()

        await self._emit(
            sse_callback, "phase_start", "", "generate",
            "Generating card...",
        )

        html = await generate_card(
            plan, sections_data, llm, self.prompt_loader,
            interaction_logger=interaction_logger,
            log_label="card_generate",
        )
        self._total_llm_calls += 1

        self.last_screenshot_path = None
        if output_dir is not None:
            self.last_screenshot_path = await screenshot_card(
                html,
                plan.get("surface_size"),
                Path(output_dir),
                stem=screenshot_stem,
                theme=plan.get("style_theme"),
            )

        elapsed = (time.monotonic() - start_time) * 1000
        logger.info(
            "Composer.compose_card: %d LLM calls, %.0fms, %d chars",
            self._total_llm_calls, elapsed, len(html),
        )

        await self._emit(sse_callback, "phase_end", "", "generate")

        if sse_callback:
            await sse_callback("token", html, "generate", "")
        await self._emit(sse_callback, "phase_end", "", "generate")
        return html

    # ── Assembly logic ──────────────────────────────────────────

    def _assemble(self, shell_html: str, components: dict[int, str]) -> str:
        """Replace placeholders in the page shell with generated component HTML.

        Args:
            shell_html: HTML shell string with COMP_PLACEHOLDER markers.
            components: Dict mapping section_index → component HTML fragment.

        Returns:
            Assembled HTML with all placeholders replaced.
        """
        result = shell_html

        # Find all placeholders and replace them
        def replace_placeholder(match: re.Match) -> str:
            idx_str = match.group(1)
            widget = match.group(2)
            idx = int(idx_str)

            if idx in components:
                replacement = components[idx]
                logger.debug("Assemble: replacing section_%d:%s (%d chars)",
                             idx, widget, len(replacement))
                return replacement
            else:
                logger.warning("Assemble: no component for section_%d:%s, keeping placeholder", idx, widget)
                return match.group(0)

        result = PLACEHOLDER_RE.sub(replace_placeholder, result)

        # Check for unreplaced placeholders
        remaining = PLACEHOLDER_RE.findall(result)
        if remaining:
            logger.warning("Assemble: %d unreplaced placeholders remain: %s",
                           len(remaining),
                           [(r[0], r[1]) for r in remaining])

        return result

    # ── Helpers ─────────────────────────────────────────────────

    @property
    def total_llm_calls(self) -> int:
        return self._total_llm_calls

    async def _emit(
        self,
        callback: SseCallback | None,
        event_type: str,
        content: str,
        phase: str = "",
        message: str = "",
    ) -> None:
        if callback:
            await callback(event_type, content, phase, message)
