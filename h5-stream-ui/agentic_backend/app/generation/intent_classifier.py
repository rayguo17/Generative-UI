"""
Intent Classifier — entry point of the generation pipeline.

Given the raw user query, decides which downstream pipeline should handle it:

  - "card"  — a compact UI card for a FIXED display surface (grid units such
              as 2x2 / 4x4 / 4x6), showing key application information or an
              interactive summary of search results at a glance. Routed to the
              card pipeline (card_planner → layout-aware generation, see
              ../../../layout-aware-agent).
  - "page"  — a long-form, multi-section report page. Routed to the existing
              Plan → Research → Compose pipeline (GenerationOrchestrator).

Single cheap LLM call returning JSON, following the same load-prompt →
call → validate → retry pattern as plan.py. Any persistent failure falls back
to "page" so the request can always continue down the existing pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

INTENT_CARD = "card"
INTENT_PAGE = "page"
VALID_INTENTS = frozenset({INTENT_CARD, INTENT_PAGE})

# System prompt file under app/generation/prompts/ (loaded verbatim)
PROMPT_FILE = "intent_classifier_system.md"

# 1 initial attempt + 1 retry on unparseable/invalid output
MAX_RETRIES = 1

# Matches grid sizes like 2x2, 4x6, "4 x 4", 2×4 (also tolerates "2*4")
_SURFACE_RE = re.compile(r"(\d{1,2})\s*[x×*]\s*(\d{1,2})")


@dataclass
class IntentResult:
    """Normalised routing decision produced by the intent classifier."""
    intent: str = INTENT_PAGE            # "card" | "page"
    surface_size: str | None = None      # e.g. "4x6" (grid units) — card only
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "surface_size": self.surface_size,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }


# ── Main entry point ─────────────────────────────────────────────────

async def classify_intent(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> IntentResult:
    """Classify the user query into a generation intent ("card" or "page").

    Never raises for LLM/parse failures — on persistent failure returns the
    "page" fallback so the request can proceed down the existing pipeline.
    (TokenBudgetExceededError is caught here as well.)
    """
    system_prompt = prompt_loader.load_raw(PROMPT_FILE)
    user_prompt = _build_user_prompt(query)

    for attempt in range(MAX_RETRIES + 1):
        label = f"intent_classify{'_retry' + str(attempt) if attempt else ''}"
        try:
            raw = await llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                step_name=label,
                max_tokens=2048,  # tiny output; headroom covers thinking leaks
                log_label=label,
            )
        except Exception as e:
            logger.error("Intent classify attempt %d failed: %s", attempt, e)
            raw = {}

        result = _validate(raw, query)
        if result is not None:
            logger.info(
                "Intent classified: %s (surface=%s, confidence=%.2f) — %s",
                result.intent, result.surface_size, result.confidence, result.reason,
            )
            return result

        logger.warning("Intent classify attempt %d: invalid output: %s",
                       attempt, str(raw)[:200])
        user_prompt = _build_user_prompt(
            query,
            feedback="Your previous output was missing or invalid. Output ONLY "
                     "the JSON object with keys: intent, surface_size, confidence, reason.",
        )

    logger.warning("Intent classification failed after %d attempts — defaulting to 'page'",
                   MAX_RETRIES + 1)
    return _fallback_result("classifier failure — defaulted to page")


# ── Validation & normalisation ────────────────────────────────────────

def _validate(raw: dict[str, Any], query: str) -> IntentResult | None:
    """Normalise the raw JSON into an IntentResult. Returns None if unusable."""
    if not isinstance(raw, dict) or not raw:
        return None

    intent = str(raw.get("intent", "")).strip().lower()
    if intent not in VALID_INTENTS:
        # Tolerate near-misses like "card_generation" / "report"
        if "card" in intent:
            intent = INTENT_CARD
        elif "page" in intent or "report" in intent:
            intent = INTENT_PAGE
        else:
            return None

    surface = _extract_surface_size(raw.get("surface_size"), query)

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reason = str(raw.get("reason", "")).strip()

    if intent == INTENT_PAGE:
        surface = None
    elif surface is None:
        # Card intent without a usable size is still valid — the Calibration
        # Agent resolves the real surface later — but it wasn't explicit.
        confidence = min(confidence, 0.7)

    return IntentResult(intent=intent, surface_size=surface,
                        confidence=confidence, reason=reason)


def _extract_surface_size(value: Any, query: str) -> str | None:
    """Normalise a surface size to "NxM".

    Prefers the model's surface_size field; falls back to scanning the raw
    query — the grid size is deterministic text the model only needs to copy.
    """
    candidates = [str(value)] if value is not None else []
    candidates.append(query)
    for text in candidates:
        m = _SURFACE_RE.search(text)
        if m:
            return f"{m.group(1)}x{m.group(2)}"
    return None


# ── Helpers ───────────────────────────────────────────────────────────

def _build_user_prompt(query: str, feedback: str | None = None) -> str:
    """Build the user prompt, optionally with retry feedback."""
    prompt = f"""## User Request
{query}

Classify this request. Output ONLY the JSON object."""

    if feedback:
        prompt += f"""

## ⚠️ PREVIOUS ATTEMPT HAD ISSUES — FIX THESE:
{feedback}"""

    return prompt


def _fallback_result(reason: str) -> IntentResult:
    """Safe default: route to the existing long-form page pipeline."""
    return IntentResult(intent=INTENT_PAGE, surface_size=None,
                        confidence=0.0, reason=reason)
