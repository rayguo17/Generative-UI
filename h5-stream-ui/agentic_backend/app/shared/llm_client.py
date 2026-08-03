"""
Unified async LLM client supporting both local (4K constrained) and cloud (unlimited) backends.

Provides:
- Async generation with optional streaming
- Token budget validation before calls
- Structured output parsing (JSON mode)
- Retry with exponential backoff
- Optional interaction logging to markdown files
- Thinking-tag stripping for reasoning models (qwen3, deepseek-r1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Callable, Awaitable, Optional, TYPE_CHECKING

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.config import LlmConfig
from app.utils.token_counter import TokenCounter

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 1.5  # seconds


class TokenBudgetExceededError(Exception):
    """Raised when a prompt exceeds the token budget."""

    def __init__(self, used: int, budget: int):
        self.used = used
        self.budget = budget
        super().__init__(f"Token budget exceeded: {used}/{budget} tokens used")


# ── Response diagnostics (network-level observability) ──────────────

def _log_response_diagnostics(
    raw_content: str,
    finish_reason: str,
    max_tokens: int,
    model: str,
    api_prompt: int = 0,
    api_completion: int = 0,
    estimated_input: int = 0,
    reasoning_content: str = "",
) -> None:
    """Log detailed diagnostics about the raw API response.

    Called for every LLM call BEFORE think-tag stripping so we can see
    exactly what the model returned at the network level.

    Tracks both inline <think> tags AND native reasoning_content streaming
    field (used by some API providers to separate reasoning from content).
    """
    raw_len = len(raw_content)
    raw_tokens_est = int(raw_len / 4)  # rough heuristic
    finish_emoji = {"stop": "✅", "length": "⚠️", "content_filter": "🚫"}.get(finish_reason, "❓")

    # Detect inline <think> tags in content
    has_think_open = "<think" in raw_content.lower()
    has_think_close = "</think>" in raw_content.lower()
    think_complete = has_think_open and has_think_close
    think_incomplete = has_think_open and not has_think_close

    think_match = re.search(r'<think[^>]*>(.*?)(?:</think>|$)', raw_content,
                            re.IGNORECASE | re.DOTALL)
    think_chars = len(think_match.group(1)) if think_match else 0
    think_pct = (think_chars / max(raw_len, 1)) * 100

    # Native reasoning_content (separate field from content in streaming API)
    reasoning_len = len(reasoning_content)
    has_native_reasoning = reasoning_len > 0

    # Build the thinking/reasoning status line
    if has_native_reasoning:
        reasoning_status = (
            f"SEPARATE FIELD ({reasoning_len} chars, ~{int(reasoning_len / 4)} tokens)"
        )
    elif think_incomplete:
        reasoning_status = "INCOMPLETE (unclosed <think>)"
    elif think_complete:
        reasoning_status = "present (complete)"
    else:
        reasoning_status = "none"

    total_overhead = think_chars + reasoning_len
    total_output = raw_len + reasoning_len
    overhead_pct = (total_overhead / max(total_output, 1)) * 100

    logger.info(
        "─ RESPONSE DIAGNOSTICS ─────────────────────────────────\n"
        "  Model:            %s\n"
        "  Finish reason:    %s %s\n"
        "  Raw content:      %d chars  (~%d tokens)\n"
        "  Reasoning field:  %s\n"
        "  Max tokens req:   %d\n"
        "  API prompt tok:   %d  (our estimate: %d)\n"
        "  API compl tok:    %d\n"
        "  Thinking/overhead:%s %.0f%% of total output)",
        model,
        finish_reason, finish_emoji,
        raw_len, raw_tokens_est,
        reasoning_status,
        max_tokens,
        api_prompt, estimated_input,
        api_completion,
        " %.0f%%" % overhead_pct if overhead_pct > 0 else "",
        overhead_pct,
    )

    # Extra warnings
    if finish_reason == "length":
        logger.warning(
            "  ⚠️  OUTPUT TRUNCATED — model hit max_tokens=%d limit. "
            "Response may be incomplete.",
            max_tokens,
        )
    if think_incomplete:
        logger.warning(
            "  ⚠️  INCOMPLETE THINK BLOCK — <think> tag was never closed. "
            "Model probably ran out of tokens mid-reasoning. "
            "Consider increasing max_tokens (currently %d).",
            max_tokens,
        )
    if raw_len == 0:
        logger.error(
            "  ❌  EMPTY RAW RESPONSE — model returned zero content. "
            "This may indicate: model not loaded, prompt rejected, "
            "or immediate token exhaustion."
        )
    if think_pct > 70 and raw_len > 0:
        logger.warning(
            "  ⚠️  THINKING-DOMINANT — %.0f%% of output is reasoning tokens. "
            "Only ~%.0f chars remain for actual content.",
            think_pct, raw_len - think_chars,
        )

    # Log first/last 150 chars of raw content for quick inspection
    if raw_len > 0:
        preview = raw_content[:200].replace("\n", "\\n")
        if raw_len > 200:
            preview += f" … [{raw_len - 400} chars] … "
            preview += raw_content[-200:].replace("\n", "\\n")
        logger.info("  Raw preview: %s", preview)

    logger.info("─" * 60)


class LlmClient:
    """Async OpenAI-compatible LLM client with token tracking and optional logging."""

    def __init__(
        self,
        config: LlmConfig,
        token_budget: int | None = None,
        interaction_logger: Optional["LlmInteractionLogger"] = None,
        log_label: str = "",
        is_cloud: bool = False,
        supports_json_mode: bool = True,
        thinking_enabled: bool = True,
    ):
        self.config = config
        self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        self.model = config.model
        self.token_budget = token_budget  # None = unlimited (cloud mode)
        self.token_counter = TokenCounter(config.model)
        self._total_tokens_used = 0
        self._interaction_logger = interaction_logger
        self._log_label = log_label
        self._is_cloud = is_cloud
        self._supports_json_mode = supports_json_mode  # Ollama/local models often don't
        self._thinking_enabled = thinking_enabled  # Ollama think parameter (v0.5+)
        self._last_finish_reason: str = ""  # Set after each generate() call

    # ── Properties ─────────────────────────────────────────────────

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens_used

    @property
    def last_finish_reason(self) -> str:
        """The finish_reason from the most recent generate() call.
        'stop' = natural end, 'length' = truncated by max_tokens.
        """
        return self._last_finish_reason

    # ── Logger wiring ──────────────────────────────────────────────

    def set_logger(self, interaction_logger: "LlmInteractionLogger", label: str = "") -> None:
        self._interaction_logger = interaction_logger
        self._log_label = label

    def _log_call(self, system_prompt: str, user_prompt: str, response: str,
                  input_tokens: int = 0, output_tokens: int = 0,
                  status: str = "success", error_message: str = "", duration_ms: float = 0.0,
                  raw_response: str = "", finish_reason: str = "",
                  api_prompt_tokens: int = 0, api_completion_tokens: int = 0) -> None:
        if not self._interaction_logger:
            return
        if self._is_cloud:
            self._interaction_logger.log_cloud_call(
                dimension=self._log_label, model=self.model,
                system_prompt=system_prompt, user_prompt=user_prompt,
                response=response, input_tokens=input_tokens,
                output_tokens=output_tokens, status=status,
                error_message=error_message, duration_ms=duration_ms,
                raw_response=raw_response, finish_reason=finish_reason,
                api_prompt_tokens=api_prompt_tokens,
                api_completion_tokens=api_completion_tokens,
            )
        else:
            self._interaction_logger.log_local_call(
                step_name=self._log_label, model=self.model,
                system_prompt=system_prompt, user_prompt=user_prompt,
                response=response, input_tokens=input_tokens,
                output_tokens=output_tokens, status=status,
                error_message=error_message, duration_ms=duration_ms,
                raw_response=raw_response, finish_reason=finish_reason,
                api_prompt_tokens=api_prompt_tokens,
                api_completion_tokens=api_completion_tokens,
            )

    # ── Core generation ────────────────────────────────────────────

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        json_mode: bool = False,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Generate a completion, optionally streaming via callback."""
        t_start = time.monotonic()
        input_tokens = self.token_counter.count(system_prompt) + self.token_counter.count(user_prompt)

        if self.token_budget is not None:
            if input_tokens > self.token_budget:
                self._log_call(system_prompt, user_prompt, "",
                               input_tokens=input_tokens, output_tokens=0, status="error",
                               error_message=f"Token budget exceeded: {input_tokens}/{self.token_budget}")
                raise TokenBudgetExceededError(used=input_tokens, budget=self.token_budget)
            if input_tokens > self.token_budget * 0.9:
                logger.warning("Prompt uses %d/%d tokens (%.0f%% of budget)",
                               input_tokens, self.token_budget,
                               (input_tokens / self.token_budget) * 100)

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if json_mode and self._supports_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if not self._thinking_enabled:
            kwargs["reasoning_effort"] = "none"

        last_error: Exception | None = None
        last_error_msg = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                if stream_callback:
                    content = await self._stream_and_collect(kwargs, stream_callback)
                    finish_reason = "stream"
                    api_prompt = 0
                    api_completion = 0
                else:
                    response = await self.client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or ""
                    finish_reason = (response.choices[0].finish_reason or "unknown")
                    api_prompt = response.usage.prompt_tokens if response.usage else 0
                    api_completion = response.usage.completion_tokens if response.usage else 0
                    self._total_tokens_used += (
                        response.usage.total_tokens if response.usage else input_tokens
                    )

                # Store finish_reason so callers can check for truncation
                self._last_finish_reason = finish_reason

                # ── Diagnostic logging ──────────────────────────
                _log_response_diagnostics(
                    raw_content=content, finish_reason=finish_reason,
                    max_tokens=max_tokens, model=self.model,
                    api_prompt=api_prompt, api_completion=api_completion,
                    estimated_input=input_tokens,
                )

                # Strip thinking tags from non-streaming output
                stripped = self._strip_thinking(content) if not stream_callback else content

                output_tokens = self.token_counter.count(stripped)
                self._log_call(system_prompt, user_prompt, stripped,
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="success", duration_ms=(time.monotonic() - t_start) * 1000,
                               raw_response=content, finish_reason=finish_reason,
                               api_prompt_tokens=api_prompt,
                               api_completion_tokens=api_completion)
                return stripped

            except Exception as e:
                last_error = e
                last_error_msg = str(e)
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning("LLM call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                                   attempt + 1, MAX_RETRIES + 1, e, wait)
                    await asyncio.sleep(wait)
                else:
                    logger.error("LLM call failed after %d retries: %s", MAX_RETRIES + 1, e)

        self._log_call(system_prompt, user_prompt, f"[ERROR] {last_error_msg}",
                       input_tokens=input_tokens, output_tokens=0, status="error",
                       error_message=last_error_msg,
                       duration_ms=(time.monotonic() - t_start) * 1000)
        raise last_error  # type: ignore[misc]

    # ── JSON generation ────────────────────────────────────────────

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Generate a completion and parse it as JSON.

        High default max_tokens (4096) accounts for thinking models that burn
        output tokens on <think> blocks before producing actual JSON.
        """
        # For models without native JSON mode, add JSON instructions to the prompt
        effective_system = system_prompt
        if not self._supports_json_mode:
            effective_system = system_prompt + (
                "\n\n## OUTPUT FORMAT (MANDATORY)\n"
                "You MUST output ONLY a valid JSON object. No markdown, no commentary.\n"
                "Start your response with '{' and end with '}'. No ``` fences.\n"
            )

        raw = ""
        try:
            raw = await self.generate(
                system_prompt=effective_system, user_prompt=user_prompt,
                temperature=temperature, max_tokens=max_tokens,
                json_mode=self._supports_json_mode,
            )
        except Exception:
            raw = ""

        # Retry without json_mode if empty
        if not raw or not raw.strip():
            logger.warning("Empty JSON response from %s. Retrying without json_mode...", self.model)
            try:
                raw = await self.generate(
                    system_prompt=effective_system, user_prompt=user_prompt,
                    temperature=temperature, max_tokens=max_tokens, json_mode=False,
                )
            except Exception as e:
                logger.error("Retry also failed: %s", e)
                return {}

        if not raw or not raw.strip():
            logger.error("Both attempts returned empty content from %s", self.model)
            return {}

        # Strip thinking tags and parse
        stripped = self._strip_thinking(raw)
        if len(stripped) < len(raw) * 0.3:
            logger.warning("Response from %s was %d%% thinking. Model may have hit max_tokens.",
                           self.model,
                           int((len(raw) - len(stripped)) / max(len(raw), 1) * 100))

        # Try direct JSON parse
        try:
            return json.loads(stripped.strip())
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown fences
        fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', stripped, re.DOTALL)
        if fence:
            try:
                return json.loads(fence.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding any JSON object
        obj = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', stripped, re.DOTALL)
        if obj:
            try:
                return json.loads(obj.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning("Failed to parse JSON from LLM response (%d chars raw, %d stripped): %s",
                       len(raw), len(stripped), stripped[:300])
        return {}

    # ── Streaming generation ───────────────────────────────────────

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Generate a completion and yield tokens as they arrive. Logs after stream ends."""
        t_start = time.monotonic()
        input_tokens = self.token_counter.count(system_prompt) + self.token_counter.count(user_prompt)

        if self.token_budget is not None and input_tokens > self.token_budget:
            self._log_call(system_prompt, user_prompt, "",
                           input_tokens=input_tokens, output_tokens=0, status="error",
                           error_message=f"Token budget exceeded: {input_tokens}/{self.token_budget}")
            raise TokenBudgetExceededError(used=input_tokens, budget=self.token_budget)

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        stream_kwargs: dict[str, Any] = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": True,
        }
        if not self._thinking_enabled:
            stream_kwargs["reasoning_effort"] = "none"

        stream = await self.client.chat.completions.create(**stream_kwargs)

        collected: list[str] = []
        reasoning_collected: list[str] = []  # Native reasoning/thinking tokens (separate from content)
        stream_error: str = ""
        finish_reason = "stream"
        api_prompt = 0
        api_completion = 0
        try:
            async for event in stream:
                choice = event.choices[0] if event.choices else None
                if not choice:
                    continue
                # Capture finish_reason from the final chunk
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                # Native reasoning content — field name varies by provider:
                # Ollama uses "reasoning", OpenAI/others may use "reasoning_content"
                if delta:
                    reasoning_token = getattr(delta, 'reasoning', None) or getattr(delta, 'reasoning_content', None)
                    if reasoning_token:
                        reasoning_collected.append(reasoning_token)
                if delta and delta.content:
                    collected.append(delta.content)
                    yield delta.content
                # Capture usage from the final chunk if present
                if hasattr(event, 'usage') and event.usage:
                    api_prompt = event.usage.prompt_tokens or 0
                    api_completion = event.usage.completion_tokens or 0
        except Exception as e:
            stream_error = str(e)
            raise
        finally:
            response_text = "".join(collected)
            reasoning_text = "".join(reasoning_collected)

            # ── Diagnostic logging ──────────────────────────────
            _log_response_diagnostics(
                raw_content=response_text, finish_reason=finish_reason,
                max_tokens=max_tokens, model=self.model,
                api_prompt=api_prompt, api_completion=api_completion,
                estimated_input=input_tokens,
                reasoning_content=reasoning_text,
            )

            output_tokens = self.token_counter.count(response_text)
            elapsed_ms = (time.monotonic() - t_start) * 1000
            if stream_error:
                self._log_call(system_prompt, user_prompt,
                               response_text[:2000] if response_text else "",
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="error", error_message=stream_error, duration_ms=elapsed_ms,
                               raw_response=response_text, finish_reason=finish_reason)
            else:
                self._log_call(system_prompt, user_prompt, response_text,
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="success", duration_ms=elapsed_ms,
                               raw_response=response_text, finish_reason=finish_reason)

    async def _stream_and_collect(
        self, kwargs: dict[str, Any], callback: Callable[[str], Awaitable[None]],
    ) -> str:
        kwargs["stream"] = True
        stream = await self.client.chat.completions.create(**kwargs)
        collected: list[str] = []
        reasoning_collected: list[str] = []
        async for event in stream:
            choice = event.choices[0] if event.choices else None
            if not choice:
                continue
            delta = choice.delta
            # Native reasoning content — field name varies by provider
            if delta:
                reasoning_token = getattr(delta, 'reasoning', None) or getattr(delta, 'reasoning_content', None)
                if reasoning_token:
                    reasoning_collected.append(reasoning_token)
            if delta and delta.content:
                collected.append(delta.content)
                await callback(delta.content)

        # Log reasoning content if present
        reasoning_text = "".join(reasoning_collected)
        if reasoning_text:
            logger.info(
                "Stream reasoning: %d chars (~%d tokens) of native reasoning_content "
                "(separate from %d chars of content)",
                len(reasoning_text), int(len(reasoning_text) / 4),
                len("".join(collected)),
            )

        return "".join(collected)

    # ── Token estimation ───────────────────────────────────────────

    def estimate_input_tokens(self, system_prompt: str, user_prompt: str) -> int:
        """Estimate the total input tokens without making an API call."""
        return self.token_counter.count(system_prompt) + self.token_counter.count(user_prompt)

    # ── Thinking-tag stripping ─────────────────────────────────────

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip <think>...</think> blocks from reasoning-model output.

        Models like qwen3, deepseek-r1 wrap chain-of-thought in these tags.
        We remove them to get the actual output (JSON, HTML, etc.).
        """
        # Remove complete <think>...</think> blocks
        text = re.sub(r'<think[^>]*>.*?</think>', '', text, flags=re.IGNORECASE | re.DOTALL)
        # Remove  response... responseblocks
        text = re.sub(r'response.*?response', '', text, flags=re.IGNORECASE | re.DOTALL)
        # Handle unclosed <think> (output truncated by max_tokens)
        unclosed = re.search(r'<think[^>]*>', text, re.IGNORECASE)
        if unclosed:
            before = text[:unclosed.start()].strip()
            after = text[unclosed.end():].strip()
            # Keep after-content if it looks like real output
            if len(after) > 50 and ('{' in after or '<' in after):
                return before + "\n" + after
            return before if before else ""
        return text.strip()
