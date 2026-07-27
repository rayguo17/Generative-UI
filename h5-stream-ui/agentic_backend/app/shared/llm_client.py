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

    # ── Properties ─────────────────────────────────────────────────

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens_used

    # ── Logger wiring ──────────────────────────────────────────────

    def set_logger(self, interaction_logger: "LlmInteractionLogger", label: str = "") -> None:
        self._interaction_logger = interaction_logger
        self._log_label = label

    def _log_call(self, system_prompt: str, user_prompt: str, response: str,
                  input_tokens: int = 0, output_tokens: int = 0,
                  status: str = "success", error_message: str = "", duration_ms: float = 0.0) -> None:
        if not self._interaction_logger:
            return
        if self._is_cloud:
            self._interaction_logger.log_cloud_call(
                dimension=self._log_label, model=self.model,
                system_prompt=system_prompt, user_prompt=user_prompt,
                response=response, input_tokens=input_tokens,
                output_tokens=output_tokens, status=status,
                error_message=error_message, duration_ms=duration_ms,
            )
        else:
            self._interaction_logger.log_local_call(
                step_name=self._log_label, model=self.model,
                system_prompt=system_prompt, user_prompt=user_prompt,
                response=response, input_tokens=input_tokens,
                output_tokens=output_tokens, status=status,
                error_message=error_message, duration_ms=duration_ms,
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

        last_error: Exception | None = None
        last_error_msg = ""
        for attempt in range(MAX_RETRIES + 1):
            try:
                if stream_callback:
                    content = await self._stream_and_collect(kwargs, stream_callback)
                else:
                    response = await self.client.chat.completions.create(**kwargs)
                    content = response.choices[0].message.content or ""
                    self._total_tokens_used += (
                        response.usage.total_tokens if response.usage else input_tokens
                    )

                # Strip thinking tags from non-streaming output
                stripped = self._strip_thinking(content) if not stream_callback else content

                output_tokens = self.token_counter.count(stripped)
                self._log_call(system_prompt, user_prompt, stripped,
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="success", duration_ms=(time.monotonic() - t_start) * 1000)
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

        stream = await self.client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens, stream=True,
        )

        collected: list[str] = []
        stream_error: str = ""
        try:
            async for event in stream:
                choice = event.choices[0] if event.choices else None
                if not choice:
                    continue
                delta = choice.delta
                if delta and delta.content:
                    collected.append(delta.content)
                    yield delta.content
        except Exception as e:
            stream_error = str(e)
            raise
        finally:
            response_text = "".join(collected)
            output_tokens = self.token_counter.count(response_text)
            elapsed_ms = (time.monotonic() - t_start) * 1000
            if stream_error:
                self._log_call(system_prompt, user_prompt,
                               response_text[:2000] if response_text else "",
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="error", error_message=stream_error, duration_ms=elapsed_ms)
            else:
                self._log_call(system_prompt, user_prompt, response_text,
                               input_tokens=input_tokens, output_tokens=output_tokens,
                               status="success", duration_ms=elapsed_ms)

    async def _stream_and_collect(
        self, kwargs: dict[str, Any], callback: Callable[[str], Awaitable[None]],
    ) -> str:
        kwargs["stream"] = True
        stream = await self.client.chat.completions.create(**kwargs)
        collected: list[str] = []
        async for event in stream:
            choice = event.choices[0] if event.choices else None
            if not choice:
                continue
            delta = choice.delta
            if delta and delta.content:
                collected.append(delta.content)
                await callback(delta.content)
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
