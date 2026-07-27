"""
Configuration management for local and cloud LLM clients.

Environment variables:
  LOCAL_LLM_BASE_URL  — base URL for the local LLM (default: http://localhost:11434/v1)
  LOCAL_LLM_API_KEY   — API key for the local LLM (default: "ollama")
  LOCAL_LLM_MODEL     — model name for the local LLM (default: "qwen2.5:7b")
  CLOUD_LLM_BASE_URL  — base URL for the cloud LLM (default: https://api.openai.com/v1)
  CLOUD_LLM_API_KEY   — API key for the cloud LLM
  CLOUD_LLM_MODEL     — model name for the cloud LLM (default: "gpt-4o")
  MAX_FIX_ITERATIONS  — max re-generation attempts after verification failure (default: 2)
  TOKEN_BUDGET        — max context window for local LLM (default: 4000)
  OUTPUT_RESERVE      — tokens reserved for LLM output in each pass (default: 1500)
  TEMPERATURE         — generation temperature (default: 0.4)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from agentic_backend directory (and parent directories)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)
load_dotenv()  # also try cwd


@dataclass
class LlmConfig:
    base_url: str
    api_key: str
    model: str


@dataclass
class AppConfig:
    local: LlmConfig
    cloud: LlmConfig
    token_budget: int = 4000
    output_reserve: int = 1500
    max_fix_iterations: int = 2
    temperature: float = 0.4

    # Paths
    @property
    def prompts_dir(self) -> Path:
        """Original prompt files directory (shared with existing backend)."""
        return Path(__file__).resolve().parent.parent.parent / "prompts"

    @property
    def condensed_prompts_dir(self) -> Path:
        """Condensed prompts for local LLM generation passes."""
        return Path(__file__).resolve().parent / "generation" / "prompts"


def load_config() -> AppConfig:
    """Load configuration from environment variables with sensible defaults."""
    return AppConfig(
        local=LlmConfig(
            base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11435/v1"),
            api_key=os.getenv("LOCAL_LLM_API_KEY", "ollama"),
            model=os.getenv("LOCAL_LLM_MODEL", "qwen3:8b"),
        ),
        cloud=LlmConfig(
            base_url=os.getenv("CLOUD_LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=os.getenv("CLOUD_LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            model=os.getenv("CLOUD_LLM_MODEL", "deepseek-v4-pro"),
        ),
        token_budget=int(os.getenv("TOKEN_BUDGET", "4000")),
        output_reserve=int(os.getenv("OUTPUT_RESERVE", "1500")),
        max_fix_iterations=int(os.getenv("MAX_FIX_ITERATIONS", "2")),
        temperature=float(os.getenv("TEMPERATURE", "0.4")),
    )
