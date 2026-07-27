"""
API request/response models for the FastAPI server.

Matches the frontend's expected contract:
- POST /api/generate with {query, model?, base_url?, api_key?}
- SSE stream with {type: "token", content: "..."} events
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.verification import VerificationReport


class GenerateRequest(BaseModel):
    """Request body for POST /api/generate."""
    query: str = Field(
        ...,
        min_length=1,
        description="User prompt: instructions and data (JSON or prose) in one string",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the local LLM model name",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Override the local LLM base URL",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Override the local LLM API key",
    )
    enable_verification: bool = Field(
        default=True,
        description="Whether to run cloud LLM verification after generation",
    )


class GenerateResponse(BaseModel):
    """Complete response for non-streaming generation (debug/testing)."""
    html: str = Field(..., description="Final HTML fragment")
    verification_report: Optional[VerificationReport] = None
    steps_executed: list[str] = Field(default_factory=list)
    total_tokens_used: int = 0
    generation_time_ms: float = 0.0


class VerifyRequest(BaseModel):
    """Request body for POST /api/verify (standalone verification)."""
    html: str = Field(..., description="HTML fragment to verify")
    user_query: str = Field(..., description="Original user prompt for data fidelity checks")


class VerifyResponse(BaseModel):
    """Response for standalone verification."""
    report: VerificationReport
    is_valid: bool


class SseEvent(BaseModel):
    """An SSE event emitted during generation."""
    type: str  # "token", "phase_start", "phase_end", "done", "error"
    content: str = ""
    phase: str = ""  # "classify", "plan", "generate", "refine", "verify"
    message: str = ""
    report: Optional[VerificationReport] = None
