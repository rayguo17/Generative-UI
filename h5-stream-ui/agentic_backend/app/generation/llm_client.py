"""
Local LLM client specialized for the generation workflow.

Wraps the shared LlmClient with generation-specific conveniences:
- Token budget validation per step
- JSON mode for structured outputs (classify, plan)
- Streaming for HTML generation
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from app.config import AppConfig, LlmConfig
from app.shared.llm_client import LlmClient, TokenBudgetExceededError

logger = logging.getLogger(__name__)


class GenerationLlmClient:
    """Local LLM client for UI generation steps."""

    def __init__(self, config: AppConfig, override_model: str | None = None,
                 override_base_url: str | None = None, override_api_key: str | None = None,
                 thinking_enabled: bool = False):
        llm_config = LlmConfig(
            base_url=override_base_url or config.local.base_url,
            api_key=override_api_key or config.local.api_key,
            model=override_model or config.local.model,
        )
        self._client = LlmClient(
            llm_config,
            token_budget=config.token_budget,
            supports_json_mode=False,  # Ollama/local models don't support response_format
            thinking_enabled=thinking_enabled,
        )
        self._config = config

    def set_logger(self, interaction_logger, label: str = "") -> None:
        """Set the interaction logger, propagated to the underlying client."""
        self._client._interaction_logger = interaction_logger
        self._client._log_label = label

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        step_name: str = "unknown",
        max_tokens: int = 4096,
    ) -> dict:
        """Generate structured JSON output (for classify and plan steps).

        The high default max_tokens (4096) accounts for thinking models
        that burn output tokens on <think> blocks before producing JSON.

        Validates the token budget before calling the LLM.
        """
        input_tokens = self._client.estimate_input_tokens(system_prompt, user_prompt)
        logger.info(
            "Step '%s': input=%d tokens, budget=%d",
            step_name, input_tokens, self._client.token_budget or 0,
        )

        if self._client.token_budget and input_tokens > self._client.token_budget:
            raise TokenBudgetExceededError(
                used=input_tokens,
                budget=self._client.token_budget,
            )

        return await self._client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,  # Lower temp for structured output
            max_tokens=max_tokens,
        )

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        step_name: str = "unknown",
        max_tokens: int = 4096,
    ) -> str:
        """Generate plain text output (for refine step).
        High default accounts for thinking-model overhead."""
        input_tokens = self._client.estimate_input_tokens(system_prompt, user_prompt)
        logger.info(
            "Step '%s': input=%d tokens, budget=%d",
            step_name, input_tokens, self._client.token_budget or 0,
        )

        if self._client.token_budget and input_tokens > self._client.token_budget:
            raise TokenBudgetExceededError(
                used=input_tokens,
                budget=self._client.token_budget,
            )

        return await self._client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self._config.temperature,
            max_tokens=max_tokens,
        )

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        step_name: str = "unknown",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Generate text with streaming (for the HTML generate step).
        High default accounts for thinking-model overhead."""
        input_tokens = self._client.estimate_input_tokens(system_prompt, user_prompt)
        logger.info(
            "Step '%s': input=%d tokens, budget=%d",
            step_name, input_tokens, self._client.token_budget or 0,
        )

        if self._client.token_budget and input_tokens > self._client.token_budget:
            raise TokenBudgetExceededError(
                used=input_tokens,
                budget=self._client.token_budget,
            )

        async for token in self._client.generate_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=self._config.temperature,
            max_tokens=max_tokens,
        ):
            yield token

    @property
    def total_tokens_used(self) -> int:
        return self._client.total_tokens_used
