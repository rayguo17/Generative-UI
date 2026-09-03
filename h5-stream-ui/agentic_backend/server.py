"""
FastAPI server for the agentic UI generation & verification system.

Endpoints:
  POST /api/classify-intent         — Pipeline entry point: route query to card vs page pipeline
  POST /api/generate                — Streaming H5 UI generation (frontend contract)
  POST /api/generate/plan-only      — Debug: page layout plan only
  POST /api/generate/card-plan-only — Debug: intent classify + card layout plan only
  POST /api/generate/card           — Debug: classify + card plan + final HTML fragment
  POST /api/verify                  — Standalone verification of HTML fragments
  GET  /health                      — Health check

Matches the frontend's expected SSE contract:
  {type: "token", content: "..."} for HTML streaming
  {type: "done"} when complete
  {type: "error", message: "..."} on failure
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import AsyncIterator

# Ensure app package is importable
_src = Path(__file__).resolve().parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import load_config, AppConfig
from app.models.api_models import (
    GenerateRequest,
    IntentClassificationResponse,
    SseEvent,
    VerifyRequest,
    VerifyResponse,
)
from app.models.verification import VerificationReport
from app.prompts.loader import PromptLoader
from app.generation.card_planner import create_card_plan
from app.generation.composer import GenerationComposer
from app.generation.intent_classifier import classify_intent
from app.generation.llm_client import GenerationLlmClient
from app.generation.orchestrator import GenerationOrchestrator
from app.verification.verifier import Verifier
from app.utils.llm_logger import LlmInteractionLogger, create_session_id

# ── Setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("server")

config: AppConfig = load_config()

# Directory for LLM interaction logs
LLM_LOG_DIR = (Path(__file__).resolve().parent / "logs").as_posix()

prompt_loader = PromptLoader(
    condensed_dir=config.condensed_prompts_dir,
    full_prompts_dir=config.prompts_dir,
)

app = FastAPI(
    title="Agentic H5 UI Generator",
    description="Multi-pass agentic UI generation with cloud-based verification",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ────────────────────────────────────────────────────────────

async def _sse_yield(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "local_model": config.local.model,
        "cloud_model": config.cloud.model,
        "token_budget": config.token_budget,
    }


@app.post("/api/generate")
async def generate(request: GenerateRequest):
    """Generate an H5 UI from a user prompt with streaming SSE response.

    This is the main endpoint that the frontend calls. It streams
    HTML tokens as SSE events, plus phase progress events.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")

    logger.info("Generate: query=%d chars, verify=%s",
                 len(request.query), request.enable_verification)

    async def event_stream() -> AsyncIterator[str]:
        """Generate the SSE event stream with progress, tokens, and verification."""
        generation_start = time.monotonic()

        # ── Create interaction log for this session ──
        session_id = create_session_id()
        llm_logger = LlmInteractionLogger(
            log_dir=Path(LLM_LOG_DIR),
            session_id=session_id,
            user_query=request.query,
        )
        logger.info("Session %s: log → %s", session_id, llm_logger._file_path)

        # Collect events from orchestrator via callback queue
        event_queue: asyncio.Queue[dict] = asyncio.Queue()
        html_parts: list[str] = []

        async def sse_callback(ev_type: str, content: str, phase: str, message: str = ""):
            await event_queue.put({
                "type": ev_type, "content": content,
                "phase": phase, "message": message,
            })

        orchestrator = GenerationOrchestrator(config, prompt_loader)

        # Start generation in background (with logger + session id)
        gen_task = asyncio.create_task(
            orchestrator.generate(
                query=request.query,
                override_model=request.model,
                override_base_url=request.base_url,
                override_api_key=request.api_key,
                sse_callback=sse_callback,
                interaction_logger=llm_logger,
                session_id=session_id,
            )
        )

        gen_done = False
        html = ""

        # Stream events as they arrive
        while not gen_done or not event_queue.empty():
            try:
                ev = await asyncio.wait_for(event_queue.get(), timeout=0.1)

                if ev["type"] == "token":
                    html_parts.append(ev["content"])
                    yield await _sse_yield({"type": "token", "content": ev["content"]})
                elif ev["type"] == "phase_start":
                    yield await _sse_yield({
                        "type": "phase_start", "phase": ev["phase"],
                        "message": ev.get("message", ""),
                    })
                elif ev["type"] == "phase_end":
                    yield await _sse_yield({"type": "phase_end", "phase": ev["phase"]})
            except asyncio.TimeoutError:
                pass

            if gen_task.done() and not gen_done:
                gen_done = True
                try:
                    html = "".join(html_parts)
                    if not html:
                        html = gen_task.result()
                except Exception as e:
                    logger.error("Generation failed: %s", e)
                    yield await _sse_yield({"type": "error", "message": str(e)})
                    yield await _sse_yield({"type": "done"})
                    return

        # ── Verification ──
        # verification_report = None
        # if request.enable_verification and html:
        #     try:
        #         verifier = Verifier(config, prompt_loader)
        #         verification_report = await verifier.verify(
        #             html=html, user_query=request.query,
        #             interaction_logger=llm_logger,
        #         )
        #         report_dict = json.loads(verification_report.model_dump_json())
        #         yield await _sse_yield({"type": "verification", "report": report_dict})

        #         # Fix loop
        #         for fix_iter in range(config.max_fix_iterations):
        #             if verification_report.overall_pass:
        #                 break
        #             logger.info("Fix iteration %d/%d", fix_iter + 1, config.max_fix_iterations)

        #             fixes = "\n".join(
        #                 f"- {f}" for f in verification_report.critical_fixes_needed[:5]
        #             )
        #             fix_query = f"{request.query}\n\n## CRITICAL: Fix these issues:\n{fixes}"
        #             fix_html = await orchestrator.generate(
        #                 query=fix_query, sse_callback=sse_callback,
        #                 interaction_logger=llm_logger,
        #             )
        #             if fix_html and len(fix_html) > len(html) * 0.5:
        #                 html = fix_html
        #                 yield await _sse_yield({"type": "token", "content": fix_html})

        #             verification_report = await verifier.verify(
        #                 html=html, user_query=request.query,
        #                 interaction_logger=llm_logger,
        #             )
        #             report_dict = json.loads(verification_report.model_dump_json())
        #             yield await _sse_yield({"type": "verification", "report": report_dict})
        #     except Exception as e:
        #         logger.error("Verification error: %s", e)
        #         yield await _sse_yield({"type": "verification_error", "message": str(e)})

        # Done
        elapsed = (time.monotonic() - generation_start) * 1000
        steps = orchestrator.steps_executed if hasattr(orchestrator, 'steps_executed') else []
        tokens = orchestrator.total_tokens if hasattr(orchestrator, 'total_tokens') else 0

        # Finalize the interaction log
        # verif_passed = verification_report.overall_pass if verification_report else None
        log_path = llm_logger.finalize(
            total_duration_ms=elapsed,
            steps_executed=steps,
            # verification_passed=verif_passed,
        )

        done_data = {
            "type": "done",
            "steps": steps,
            "tokens": tokens,
            "time_ms": round(elapsed),
            "session_id": session_id,
            "log_file": str(log_path),
        }
        # if verification_report:
        #     score = max(0, 100 - (verification_report.error_count * 20 + verification_report.warning_count * 5))
        #     done_data["verification"] = {
        #         "passed": verification_report.overall_pass,
        #         "score": score,
        #         "issues": verification_report.total_violations,
        #     }
        yield await _sse_yield(done_data)

        logger.info("Complete: %.0fms, log → %s",
                     elapsed,
                    #  "PASS" if (verification_report and verification_report.overall_pass) else "N/A",
                     log_path)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/verify", response_model=VerifyResponse)
async def verify(request: VerifyRequest):
    """Standalone verification of an HTML fragment."""
    if not request.html.strip():
        raise HTTPException(status_code=400, detail="HTML must not be empty")

    verifier = Verifier(config, prompt_loader)
    report = await verifier.verify(
        html=request.html,
        user_query=request.user_query,
    )
    return VerifyResponse(report=report, is_valid=report.overall_pass)


@app.post("/api/classify-intent", response_model=IntentClassificationResponse)
async def classify_intent_route(request: GenerateRequest):
    """Pipeline entry point: classify the user query's intent.

    Decides whether the request should be handled by the card pipeline
    (compact UI card on a fixed display surface, e.g. "generate a 4x6 card
    for the weather report") or the long-form page pipeline (/api/generate).
    Downstream dispatch on the result is added later.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")

    logger.info("Classify-intent: query=%d chars", len(request.query))

    # Log the classification call like any other pipeline step
    session_id = create_session_id()
    llm_logger = LlmInteractionLogger(
        log_dir=Path(LLM_LOG_DIR),
        session_id=session_id,
        user_query=request.query,
    )

    llm = GenerationLlmClient(
        config,
        override_model=request.model,
        override_base_url=request.base_url,
        override_api_key=request.api_key,
    )
    llm.set_logger(llm_logger, label="intent_classify")

    start = time.monotonic()
    # classify_intent never raises for LLM/parse failures — it falls back to
    # "page" — so this surfaces only unexpected internal errors.
    try:
        result = await classify_intent(request.query, llm, prompt_loader)
    except Exception as e:
        logger.error("Intent classification failed: %s", e)
        llm_logger.finalize(
            total_duration_ms=(time.monotonic() - start) * 1000,
            steps_executed=["intent_classify"],
        )
        raise HTTPException(status_code=500, detail=f"Intent classification failed: {e}")

    elapsed = (time.monotonic() - start) * 1000
    log_path = llm_logger.finalize(
        total_duration_ms=elapsed,
        steps_executed=["intent_classify"],
    )

    return IntentClassificationResponse(
        **result.to_dict(),
        session_id=session_id,
        log_file=str(log_path),
    )


@app.post("/api/generate/plan-only")
async def generate_plan_only(request: GenerateRequest):
    """Debug endpoint: return the layout plan only."""
    from app.generation.plan import create_layout_plan

    llm = GenerationLlmClient(
        config,
        override_model=request.model,
        override_base_url=request.base_url,
        override_api_key=request.api_key,
    )
    plan = await create_layout_plan(request.query, llm, prompt_loader)
    return {"plan": plan}


@app.post("/api/generate/card-plan-only")
async def generate_card_plan_only(request: GenerateRequest):
    """Debug endpoint: classify intent, then return the card layout plan only.

    Previews the future card-pipeline dispatch flow: intent classification →
    card planner. No HTML is generated.
    """
    llm = GenerationLlmClient(
        config,
        override_model=request.model,
        override_base_url=request.base_url,
        override_api_key=request.api_key,
    )
    intent = await classify_intent(request.query, llm, prompt_loader)
    card_plan = await create_card_plan(
        request.query, llm, prompt_loader,
        intent_result=intent,
        plan_fail_mode=config.plan_fail_mode,
    )
    return {"intent": intent.to_dict(), "card_plan": card_plan}


@app.post("/api/generate/card")
async def generate_card_route(request: GenerateRequest):
    """Debug endpoint: classify → card plan → compose_card (HTML + echarts).

    All three steps share one session log. `compose_card` / `generate_card`
    never raise on LLM failure — they return a fallback fragment — so errors
    here surface only unexpected issues in classify/plan.
    """
    session_id = create_session_id()
    llm_logger = LlmInteractionLogger(
        log_dir=Path(LLM_LOG_DIR),
        session_id=session_id,
        user_query=request.query,
    )

    llm = GenerationLlmClient(
        config,
        override_model=request.model,
        override_base_url=request.base_url,
        override_api_key=request.api_key,
    )

    start = time.monotonic()
    composer = None
    try:
        llm.set_logger(llm_logger, label="intent_classify")
        intent = await classify_intent(request.query, llm, prompt_loader)
        llm.set_logger(llm_logger, label="card_plan")
        card_plan = await create_card_plan(
            request.query, llm, prompt_loader,
            intent_result=intent,
            plan_fail_mode=config.plan_fail_mode,
        )
        composer = GenerationComposer(config, prompt_loader)
        html = await composer.compose_card(
            card_plan, request.data, llm,
            interaction_logger=llm_logger,
            output_dir=Path("debug_output"),
            screenshot_stem=f"card_generate_output_{session_id}",
        )
    except Exception as e:
        logger.error("Card generation failed: %s", e)
        llm_logger.finalize(
            total_duration_ms=(time.monotonic() - start) * 1000,
            steps_executed=["intent_classify", "card_plan"],
        )
        raise HTTPException(status_code=500, detail=f"Card generation failed: {e}")

    log_path = llm_logger.finalize(
        total_duration_ms=(time.monotonic() - start) * 1000,
        steps_executed=["intent_classify", "card_plan", "card_generate"],
    )

    screenshot = composer.last_screenshot_path if composer else None
    return {
        "intent": intent.to_dict(),
        "card_plan": card_plan,
        "html": html,
        "screenshot": str(screenshot) if screenshot else None,
        "session_id": session_id,
        "log_file": str(log_path),
    }


# ── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=config.host, port=config.port, reload=True)
