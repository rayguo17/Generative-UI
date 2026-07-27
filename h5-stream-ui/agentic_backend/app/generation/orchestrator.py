"""
Generation Orchestrator — drives the RPGR (Route-Plan-Generate-Refine) pipeline.

This is the main entry point for Workflow 1. It coordinates the 4-pass
agentic UI generation, managing token budgets, error handling, and
streaming output to the frontend.

Pipeline:
  1. classify → AnalysisResult (intent + data extraction)
  2. plan     → LayoutPlan (component tree + bindings)
  3. generate → HTML fragment (streamed to frontend)
  4. refine   → Corrected HTML (self-check pass)

Each pass fits within the 4K context window of the local LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Callable, Awaitable, Optional, TYPE_CHECKING

from app.config import AppConfig
from app.generation.llm_client import GenerationLlmClient
from app.generation.analyze import analyze_user_request
from app.generation.plan import create_layout_plan
from app.generation.generate import generate_html_stream, generate_html
from app.generation.refine import refine_html
from app.prompts.loader import PromptLoader
from app.shared.llm_client import TokenBudgetExceededError

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

# SSE event helpers
SseCallback = Callable[[str, str, str], Awaitable[None]]
# async def sse_callback(event_type: str, content: str, phase: str) -> None: ...


class GenerationOrchestrator:
    """Orchestrates the multi-pass agentic UI generation pipeline."""

    def __init__(self, config: AppConfig, prompt_loader: PromptLoader):
        self.config = config
        self.prompt_loader = prompt_loader
        self._steps_executed: list[str] = []
        self._total_tokens = 0

    async def generate(
        self,
        query: str,
        *,
        override_model: str | None = None,
        override_base_url: str | None = None,
        override_api_key: str | None = None,
        sse_callback: SseCallback | None = None,
        interaction_logger: Optional["LlmInteractionLogger"] = None,
    ) -> str:
        """Run the full generation pipeline and return the final HTML.

        Args:
            query: Raw user prompt (instructions + data).
            override_model: Override local LLM model.
            override_base_url: Override local LLM base URL.
            override_api_key: Override local LLM API key.
            sse_callback: Optional callback for SSE progress events.
                          Called as: await sse_callback(event_type, content, phase)
            interaction_logger: Optional logger for LLM interaction traces.

        Returns:
            Final HTML fragment string.
        """
        self._steps_executed = []
        start_time = time.monotonic()

        # Initialize LLM client with logger
        llm = GenerationLlmClient(
            self.config,
            override_model=override_model,
            override_base_url=override_base_url,
            override_api_key=override_api_key,
        )

        # ── Pass 1: Analyze ──
        await self._emit(sse_callback, "phase_start", "", "classify",
                         "Analyzing your request...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "classify")
        try:
            analysis = await analyze_user_request(query, llm, self.prompt_loader)
            self._steps_executed.append("classify")
        except TokenBudgetExceededError as e:
            logger.error("Classify: token budget exceeded: %s", e)
            # Fall through with minimal analysis
            analysis = {
                "intent": "card", "summary": "Information card",
                "data_fields": [], "needed_modules": [],
                "complexity": 2, "has_interactions": False,
                "has_images": False, "data_is_tabular": False,
            }
            self._steps_executed.append("classify (fallback)")
        except Exception as e:
            logger.error("Classify failed: %s", e)
            analysis = {
                "intent": "card", "summary": "Information card",
                "data_fields": [], "needed_modules": [],
                "complexity": 2, "has_interactions": False,
                "has_images": False, "data_is_tabular": False,
            }
            self._steps_executed.append("classify (error)")
        await self._emit(sse_callback, "phase_end", "", "classify")

        # ── Pass 2: Plan ──
        await self._emit(sse_callback, "phase_start", "", "plan",
                         "Planning the layout...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "plan")
        try:
            plan = await create_layout_plan(query, analysis, llm, self.prompt_loader)
            self._steps_executed.append("plan")
        except TokenBudgetExceededError as e:
            logger.error("Plan: token budget exceeded: %s", e)
            plan = _fallback_plan(analysis)
            self._steps_executed.append("plan (fallback)")
        except Exception as e:
            logger.error("Plan failed: %s", e)
            plan = _fallback_plan(analysis)
            self._steps_executed.append("plan (error)")
        await self._emit(sse_callback, "phase_end", "", "plan")

        # ── Pass 3: Generate (streamed) ──
        await self._emit(sse_callback, "phase_start", "", "generate",
                         "Generating HTML...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "generate")
        html = ""
        try:
            async for token in generate_html_stream(query, plan, llm, self.prompt_loader):
                html += token
                await self._emit(sse_callback, "token", token, "generate")
            self._steps_executed.append("generate")
        except TokenBudgetExceededError as e:
            logger.error("Generate: token budget exceeded: %s", e)
            # Try generating without streaming (non-streaming may have better error handling)
            try:
                html = await generate_html(query, _fallback_plan(analysis), llm, self.prompt_loader)
                await self._emit(sse_callback, "token", html, "generate")
                self._steps_executed.append("generate (non-streaming fallback)")
            except Exception:
                html = _fallback_html(plan)
                await self._emit(sse_callback, "token", html, "generate")
                self._steps_executed.append("generate (hard fallback)")
        except Exception as e:
            logger.error("Generate failed: %s", e)
            html = _fallback_html(plan)
            await self._emit(sse_callback, "token", html, "generate")
            self._steps_executed.append("generate (error)")
        await self._emit(sse_callback, "phase_end", "", "generate")

        # ── Pass 4: Refine ──
        await self._emit(sse_callback, "phase_start", "", "refine",
                         "Refining and checking output...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "refine")
        try:
            refined = await refine_html(html, plan, llm, self.prompt_loader)
            if refined and refined != html:
                # If refined HTML is different, stream the diff tokens
                html = refined
                await self._emit(sse_callback, "token", refined, "refine")
            self._steps_executed.append("refine")
        except TokenBudgetExceededError:
            logger.warning("Refine: token budget exceeded, keeping original HTML")
            self._steps_executed.append("refine (skipped — budget)")
        except Exception as e:
            logger.warning("Refine failed: %s, keeping original HTML", e)
            self._steps_executed.append("refine (error)")
        await self._emit(sse_callback, "phase_end", "", "refine")

        self._total_tokens = llm.total_tokens_used
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info("Generation complete: steps=%s, tokens=%d, time=%.0fms",
                     self._steps_executed, self._total_tokens, elapsed)

        return html

    @property
    def steps_executed(self) -> list[str]:
        return self._steps_executed

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    async def _emit(
        self,
        callback: SseCallback | None,
        event_type: str,
        content: str,
        phase: str = "",
        message: str = "",
    ) -> None:
        """Emit an SSE event via the callback if provided."""
        if callback:
            await callback(event_type, content, phase, message)


def _fallback_plan(analysis: dict) -> dict:
    """Generate a minimal layout plan when planning fails."""
    intent = analysis.get("intent", "card")
    return {
        "card_type": "simple_card",
        "sections": [
            {
                "section_type": "header",
                "data_bindings": [],
                "layout_direction": "vertical",
                "visual_priority": 0,
                "is_repeatable": False,
            },
            {
                "section_type": "text_block",
                "data_bindings": [],
                "layout_direction": "vertical",
                "visual_priority": 1,
                "is_repeatable": False,
            },
        ],
        "data_summary": {},
        "interaction_intents": [],
        "style_preferences": {
            "accent_color": "#0A59F7",
            "card_radius": "20px",
            "spacing_scale": "normal",
            "harmony_mode": False,
        },
        "needs_charts": False,
        "needs_pagination": False,
        "needs_interactions": analysis.get("has_interactions", False),
        "estimated_complexity": "low",
    }


def _fallback_html(plan: dict) -> str:
    """Generate minimal fallback HTML when generation fails."""
    title = "Card"
    if plan.get("sections"):
        first = plan["sections"][0]
        bindings = first.get("data_bindings", [])
        if bindings:
            title = bindings[0].get("fallback", "Card")
    return (
        f'<div class="w-full rounded-[20px] overflow-hidden bg-white p-4">'
        f'<div class="text-base font-medium text-gray-900">{title}</div>'
        f'<div class="text-sm text-gray-500 mt-2">Content could not be generated. '
        f'Please try again with a more specific prompt.</div>'
        f'</div>'
    )
