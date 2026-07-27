"""
Cloud LLM client — unlimited context window for verification.

Unlike the local LLM, this has no token budget enforcement.
It loads the FULL original prompts for comprehensive verification.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, TYPE_CHECKING

from app.config import AppConfig, LlmConfig
from app.shared.llm_client import LlmClient

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)


class CloudLlmClient:
    """Cloud LLM client for verification — no context window limit."""

    def __init__(self, config: AppConfig):
        llm_config = LlmConfig(
            base_url=config.cloud.base_url,
            api_key=config.cloud.api_key,
            model=config.cloud.model,
        )
        # No token budget — cloud LLM has large context
        self._client = LlmClient(llm_config, token_budget=None, is_cloud=True)
        self._config = config

    def set_logger(self, interaction_logger: "LlmInteractionLogger") -> None:
        """Set the interaction logger, propagated to the underlying client."""
        self._client._interaction_logger = interaction_logger

    async def verify(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        dimension: str = "unknown",
    ) -> dict[str, Any]:
        """Run a verification check and return structured JSON results.

        Args:
            system_prompt: Full verification rules (uncondensed).
            user_prompt: The HTML + check instructions.
            temperature: Low temp for consistent verification.
            max_tokens: Maximum output tokens.
            dimension: Which verification dimension (for logging).

        Returns:
            Parsed JSON verification result.
        """
        self._client._log_label = f"verify:{dimension}"
        result = await self._client.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result

    async def verify_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> str:
        """Run a verification check and return free-text results."""
        return await self._client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    def total_tokens_used(self) -> int:
        return self._client.total_tokens_used
