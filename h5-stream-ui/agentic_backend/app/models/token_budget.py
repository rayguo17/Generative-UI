"""
Token budget management for 4K context-window enforcement.

Each generation pass gets a budget allocation. The TokenBudget class
tracks usage and enforces limits to ensure local LLM calls fit in the
constrained context window.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PromptAllocation:
    """Defines which prompt files to load for a generation step."""
    step_name: str
    prompt_files: list[str]  # filenames like "01-role-and-task.md"
    target_condensed_tokens: int  # max tokens for condensed system prompt
    conditional_files: dict[str, list[str]] = field(default_factory=dict)
    # e.g. {"needs_charts": ["09-chart-generation-echarts.md"]}


@dataclass
class TokenBudget:
    """Tracks token usage for a single LLM call within the generation pipeline."""
    max_total: int = 4000
    output_reserve: int = 1500  # Minimum tokens reserved for model output

    # Tracked usage
    system_prompt_tokens: int = 0
    user_prompt_tokens: int = 0

    @property
    def available_for_prompt(self) -> int:
        """Maximum tokens available for system + user prompts combined."""
        return self.max_total - self.output_reserve

    @property
    def used(self) -> int:
        """Total tokens used so far (system + user)."""
        return self.system_prompt_tokens + self.user_prompt_tokens

    @property
    def remaining_for_prompt(self) -> int:
        """Remaining tokens available for additional prompt content."""
        return max(0, self.available_for_prompt - self.used)

    @property
    def remaining_total(self) -> int:
        """Remaining tokens before hitting the hard limit."""
        return max(0, self.max_total - self.used)

    def can_fit(self, additional_tokens: int) -> bool:
        """Check if additional tokens fit within the budget."""
        needed = self.used + additional_tokens + self.output_reserve
        return needed <= self.max_total

    def add_system(self, tokens: int) -> bool:
        """Add tokens to the system prompt budget. Returns False if it doesn't fit."""
        if self.can_fit(tokens):
            self.system_prompt_tokens += tokens
            return True
        return False

    def add_user(self, tokens: int) -> bool:
        """Add tokens to the user prompt budget. Returns False if it doesn't fit."""
        if self.can_fit(tokens):
            self.user_prompt_tokens += tokens
            return True
        return False

    def snapshot(self) -> dict:
        """Return a snapshot of the current budget state."""
        return {
            "max_total": self.max_total,
            "output_reserve": self.output_reserve,
            "system_prompt_tokens": self.system_prompt_tokens,
            "user_prompt_tokens": self.user_prompt_tokens,
            "used": self.used,
            "remaining": self.remaining_total,
        }
