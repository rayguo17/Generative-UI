"""
FastAPI server for the agentic UI generation & verification system.

Endpoints:
  POST /api/generate     — Streaming H5 UI generation (frontend contract)
  POST /api/verify       — Standalone verification of HTML fragments
  GET  /health           — Health check

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
    SseEvent,
    VerifyRequest,
    VerifyResponse,
)
from app.models.verification import VerificationReport
from app.prompts.loader import PromptLoader
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

        # Start generation in background (with logger)
        gen_task = asyncio.create_task(
            orchestrator.generate(
                query=request.query,
                override_model=request.model,
                override_base_url=request.base_url,
                override_api_key=request.api_key,
                sse_callback=sse_callback,
                interaction_logger=llm_logger,
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
        verification_report = None
        if request.enable_verification and html:
            try:
                verifier = Verifier(config, prompt_loader)
                verification_report = await verifier.verify(
                    html=html, user_query=request.query,
                    interaction_logger=llm_logger,
                )
                report_dict = json.loads(verification_report.model_dump_json())
                yield await _sse_yield({"type": "verification", "report": report_dict})

                # Fix loop
                for fix_iter in range(config.max_fix_iterations):
                    if verification_report.overall_pass:
                        break
                    logger.info("Fix iteration %d/%d", fix_iter + 1, config.max_fix_iterations)

                    fixes = "\n".join(
                        f"- {f}" for f in verification_report.critical_fixes_needed[:5]
                    )
                    fix_query = f"{request.query}\n\n## CRITICAL: Fix these issues:\n{fixes}"
                    fix_html = await orchestrator.generate(
                        query=fix_query, sse_callback=sse_callback,
                        interaction_logger=llm_logger,
                    )
                    if fix_html and len(fix_html) > len(html) * 0.5:
                        html = fix_html
                        yield await _sse_yield({"type": "token", "content": fix_html})

                    verification_report = await verifier.verify(
                        html=html, user_query=request.query,
                        interaction_logger=llm_logger,
                    )
                    report_dict = json.loads(verification_report.model_dump_json())
                    yield await _sse_yield({"type": "verification", "report": report_dict})
            except Exception as e:
                logger.error("Verification error: %s", e)
                yield await _sse_yield({"type": "verification_error", "message": str(e)})

        # Done
        elapsed = (time.monotonic() - generation_start) * 1000
        steps = orchestrator.steps_executed if hasattr(orchestrator, 'steps_executed') else []
        tokens = orchestrator.total_tokens if hasattr(orchestrator, 'total_tokens') else 0

        # Finalize the interaction log
        verif_passed = verification_report.overall_pass if verification_report else None
        log_path = llm_logger.finalize(
            total_duration_ms=elapsed,
            steps_executed=steps,
            verification_passed=verif_passed,
        )

        done_data = {
            "type": "done",
            "steps": steps,
            "tokens": tokens,
            "time_ms": round(elapsed),
            "session_id": session_id,
            "log_file": str(log_path),
        }
        if verification_report:
            score = max(0, 100 - (verification_report.error_count * 20 + verification_report.warning_count * 5))
            done_data["verification"] = {
                "passed": verification_report.overall_pass,
                "score": score,
                "issues": verification_report.total_violations,
            }
        yield await _sse_yield(done_data)

        logger.info("Complete: %.0fms, %s, log → %s",
                     elapsed,
                     "PASS" if (verification_report and verification_report.overall_pass) else "N/A",
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


@app.post("/api/generate/plan-only")
async def generate_plan_only(request: GenerateRequest):
    """Debug endpoint: return the analysis + layout plan only."""
    from app.generation.llm_client import GenerationLlmClient
    from app.generation.analyze import analyze_user_request
    from app.generation.plan import create_layout_plan

    llm = GenerationLlmClient(
        config,
        override_model=request.model,
        override_base_url=request.base_url,
        override_api_key=request.api_key,
    )
    analysis = await analyze_user_request(request.query, llm, prompt_loader)
    plan = await create_layout_plan(request.query, analysis, llm, prompt_loader)
    return {"analysis": analysis, "plan": plan}


# ── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
