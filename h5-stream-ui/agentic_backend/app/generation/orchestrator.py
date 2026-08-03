"""
Generation Orchestrator — drives the Plan → Generate pipeline.

Simplified 2-pass agentic UI generation:
  0. (auto) summarise       — if input >70% of 4K budget, recursively summarise
  1. plan                   → LayoutPlan (component tree + data bindings + harness validation)
  2. generate               → HTML fragment (streamed to frontend)

The orchestrator can invoke a utility summarisation agent when the user
input exceeds ~70% of the 4K context window. The full original is saved
to ContextStore so the plan agent can search for specific details later.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable, Optional, TYPE_CHECKING

from app.config import AppConfig
from app.generation.llm_client import GenerationLlmClient
from app.generation.plan import create_layout_plan
from app.generation.generate import generate_html_stream, generate_html
from app.generation.composer import GenerationComposer
from app.prompts.loader import PromptLoader
from app.shared.llm_client import TokenBudgetExceededError
from app.utils.context_store import ContextStore
from app.utils.token_counter import count_tokens
from app.utils.plan_metrics import PlanMetricsRecorder

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

SseCallback = Callable[[str, str, str], Awaitable[None]]

# Separator for log readability
SEP = "-" * 60


class GenerationOrchestrator:
    """Orchestrates the Plan → Generate pipeline with optional summarisation."""

    def __init__(
        self,
        config: AppConfig,
        prompt_loader: PromptLoader,
        context_store: ContextStore | None = None,
    ):
        self.config = config
        self.prompt_loader = prompt_loader
        self.context_store = context_store or ContextStore(
            Path(__file__).resolve().parent.parent.parent / "context_store"
        )
        self._plan_metrics = PlanMetricsRecorder(
            Path(__file__).resolve().parent.parent / "logs"
        )
        self._steps_executed: list[str] = []
        self._total_tokens = 0
        self._was_summarised = False
        self._session_id = ""

    async def generate(
        self,
        query: str,
        *,
        override_model: str | None = None,
        override_base_url: str | None = None,
        override_api_key: str | None = None,
        sse_callback: SseCallback | None = None,
        interaction_logger: Optional["LlmInteractionLogger"] = None,
        session_id: str = "",
    ) -> str:
        """Run the pipeline and return the final HTML."""
        self._steps_executed = []
        self._was_summarised = False
        self._session_id = session_id
        start_time = time.monotonic()

        llm = GenerationLlmClient(
            self.config,
            override_model=override_model,
            override_base_url=override_base_url,
            override_api_key=override_api_key,
        )

        # ── Pass 0: Summarise (if needed) ─────────────────────────
        working_query = query
        try:
            from app.utils.summarizer import summarize_if_needed
            from app.utils.token_counter import count_tokens

            input_tokens = count_tokens(query)
            threshold = int(self.config.token_budget * 0.50)

            if input_tokens > threshold:
                await self._emit(sse_callback, "phase_start", "", "summarize",
                                 f"Indexing input ({input_tokens} tokens > {threshold})...")
                if interaction_logger:
                    llm.set_logger(interaction_logger, "summarize")

                working_query, was_summarised = await summarize_if_needed(
                    query,
                    token_budget=self.config.token_budget,
                    config=self.config,
                    context_store=self.context_store,
                    session_id=session_id,
                    interaction_logger=interaction_logger,
                )
                self._was_summarised = was_summarised
                self._steps_executed.append(
                    f"summarize ({input_tokens} → {count_tokens(working_query)} tokens)"
                )
                await self._emit(sse_callback, "phase_end", "", "summarize")
        except Exception as e:
            logger.warning("Summarisation failed, proceeding with original input: %s", e)
            working_query = query

        # ── Pass 1: Plan ──────────────────────────────────────────
        await self._emit(sse_callback, "phase_start", "", "plan",
                         "Planning the layout...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "plan")

        # Count and log tokens before the plan call
        plan_system = self.prompt_loader.load_for_step("plan")
        effective_query = working_query
        if self._was_summarised and session_id:
            effective_query = _augment_query_with_context_note(working_query, session_id)
        plan_user = f"## Task\nAnalyze this user request and create a layout plan.\n\n## User Request\n{effective_query[:1500]}"
        self._log_step_tokens("plan", plan_system, plan_user, sse_callback)

        plan = None
        try:
            plan = await create_layout_plan(
                effective_query, llm, self.prompt_loader,
                metrics=self._plan_metrics, session_id=session_id,
            )
            self._steps_executed.append("plan")
        except TokenBudgetExceededError:
            logger.error("Plan: token budget exceeded")
            plan = _fallback_plan()
            self._steps_executed.append("plan (fallback)")
        except Exception as e:
            logger.error("Plan failed: %s", e)
            plan = _fallback_plan()
            self._steps_executed.append("plan (error)")

        await self._emit(sse_callback, "phase_end", "", "plan")

        # ── Pass 2: Compose (two-agent generation pipeline) ────────
        await self._emit(sse_callback, "phase_start", "", "generate",
                         "Generating HTML...")
        if interaction_logger:
            llm.set_logger(interaction_logger, "generate")

        # Count and log tokens before the generate calls
        needs_charts = plan.get("needs_charts", False) if plan else False
        needs_interactions = plan.get("needs_interactions", False) if plan else False

        # Log token budgets for page_generate and component_generate steps
        pg_system = self.prompt_loader.load_for_step("page_generate")
        pg_user = f"Plan: {json.dumps(plan, ensure_ascii=False)[:800]}"
        self._log_step_tokens("page_generate (shell)", pg_system, pg_user, sse_callback)

        cg_system = self.prompt_loader.load_for_step("component_generate")
        self._log_step_tokens("component_generate (per-section)", cg_system,
                              f"Generated per section from plan with {len(plan.get('sections', [])) if plan else 0} sections",
                              sse_callback)

        html = ""
        try:
            composer = GenerationComposer(self.config, self.prompt_loader, self.context_store)
            html = await composer.compose(
                plan=plan,
                working_query=working_query,
                llm=llm,
                session_id=session_id,
                sse_callback=sse_callback,
                interaction_logger=interaction_logger,
            )
            self._steps_executed.append("generate (composer)")
        except TokenBudgetExceededError:
            logger.error("Generate: token budget exceeded — trying legacy fallback")
            try:
                html = await generate_html(
                    working_query, _fallback_plan(), llm, self.prompt_loader,
                )
                await self._emit(sse_callback, "token", html, "generate")
                self._steps_executed.append("generate (legacy fallback)")
            except Exception:
                html = _fallback_html()
                await self._emit(sse_callback, "token", html, "generate")
                self._steps_executed.append("generate (hard fallback)")
        except Exception as e:
            logger.error("Composer failed: %s — trying legacy generate", e)
            try:
                gen_system = self.prompt_loader.load_for_step(
                    "generate", needs_charts=needs_charts, needs_interactions=needs_interactions,
                )
                gen_user = (
                    f"## Task\nGenerate HTML from the layout plan.\n\n"
                    f"## Data\n{working_query[:1200]}\n\n"
                    f"## Plan\n{json.dumps(plan, ensure_ascii=False)[:800]}"
                )
                self._log_step_tokens("generate (legacy)", gen_system, gen_user, sse_callback)

                async for token in generate_html_stream(
                    working_query, plan, llm, self.prompt_loader,
                ):
                    html += token
                    await self._emit(sse_callback, "token", token, "generate")
                self._steps_executed.append("generate (legacy)")
            except Exception as e2:
                logger.error("Legacy generate also failed: %s", e2)
                html = _fallback_html()
                await self._emit(sse_callback, "token", html, "generate")
                self._steps_executed.append("generate (hard fallback)")

        await self._emit(sse_callback, "phase_end", "", "generate")

        self._total_tokens = llm.total_tokens_used
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info("Generation complete: steps=%s, tokens=%d, time=%.0fms",
                     self._steps_executed, self._total_tokens, elapsed)

        # Print plan metrics summary
        self._plan_metrics.print_summary()

        return html

    @property
    def steps_executed(self) -> list[str]:
        return self._steps_executed

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def was_summarised(self) -> bool:
        return self._was_summarised

    def _log_step_tokens(
        self,
        step: str,
        system_prompt: str,
        user_prompt: str,
        sse_callback: SseCallback | None = None,
    ) -> tuple[int, int, int]:
        """Count and log system + user prompt tokens before an LLM call.

        Returns (system_tokens, user_tokens, total_tokens).
        """
        sys_tokens = count_tokens(system_prompt)
        usr_tokens = count_tokens(user_prompt)
        total = sys_tokens + usr_tokens
        remaining = self.config.token_budget - total
        reserve = self.config.output_reserve
        available_for_output = remaining - reserve

        logger.info(
            "%s\n  STEP: %s — Token Budget\n"
            "  System prompt:  %5d tokens  (%d chars)\n"
            "  User prompt:    %5d tokens  (%d chars)\n"
            "  ─────────────────────────────────\n"
            "  Total input:    %5d tokens\n"
            "  Budget:         %5d tokens\n"
            "  Output reserve: %5d tokens\n"
            "  Available:      %5d tokens  %s\n"
            "%s",
            SEP, step,
            sys_tokens, len(system_prompt),
            usr_tokens, len(user_prompt),
            total,
            self.config.token_budget,
            reserve,
            max(available_for_output, 0),
            "OK" if available_for_output > 0 else "OVER BUDGET",
            SEP,
        )

        # Emit to frontend
        if sse_callback:
            asyncio.create_task(sse_callback(
                "token_budget", "", step,
                f"sys={sys_tokens} usr={usr_tokens} total={total}/{self.config.token_budget} "
                f"avail={max(available_for_output, 0)}",
            ))

        return sys_tokens, usr_tokens, total

    async def _emit(
        self, callback: SseCallback | None,
        event_type: str, content: str, phase: str = "", message: str = "",
    ) -> None:
        if callback:
            await callback(event_type, content, phase, message)


def _augment_query_with_context_note(index_text: str, session_id: str) -> str:
    """Append a note about the context store so the plan agent can look up details."""
    return (
        index_text
        + f"\n\n---\n"
        f"> 📁 **Full input saved to context store** (session `{session_id}`). "
        f"The above is a structural index only — use context store search to retrieve "
        f"specific details (scenic spot descriptions, prices, URLs, dates, numbers) "
        f"when you need them for the card."
    )


# ── Fallback helpers ────────────────────────────────────────────

def _fallback_plan() -> dict:
    return {
        "card_type": "simple_card",
        "sections": [{
            "section_type": "header", "data_bindings": [],
            "layout_direction": "vertical", "visual_priority": 0,
            "is_repeatable": False, "grid_columns": None,
        }, {
            "section_type": "text_block", "data_bindings": [],
            "layout_direction": "vertical", "visual_priority": 1,
            "is_repeatable": False, "grid_columns": None,
        }],
        "data_summary": {}, "interaction_intents": [],
        "style_preferences": {
            "accent_color": "#0A59F7", "card_radius": "20px",
            "spacing_scale": "normal", "harmony_mode": False,
        },
        "needs_charts": False, "needs_pagination": False,
        "needs_interactions": False, "estimated_complexity": "low",
    }


def _fallback_html() -> str:
    return (
        '<div class="w-full rounded-[20px] overflow-hidden bg-white p-4">'
        '<div class="text-base font-medium text-gray-900">Content unavailable</div>'
        '<div class="text-sm text-gray-500 mt-2">Please try again with a more specific prompt.</div>'
        '</div>'
    )
