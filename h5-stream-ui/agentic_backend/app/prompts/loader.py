"""
Prompt loader — assembles prompts for each generation and verification step.

Uses PromptRegistry to determine which files to load, and PromptCondenser
to keep each prompt within the token budget.

Also provides access to the FULL original prompts for cloud LLM verification.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.prompts.registry import PromptRegistry
from app.prompts.condenser import PromptCondenser
from app.utils.token_counter import count_tokens


class PromptLoader:
    """Loads and assembles prompts for the generation and verification workflows."""

    def __init__(self, condensed_dir: Path, full_prompts_dir: Path):
        self.condensed_dir = Path(condensed_dir)
        self.full_prompts_dir = Path(full_prompts_dir)
        self.condenser = PromptCondenser(self.condensed_dir)

    # ── Local LLM: Condensed prompts ──

    def load_for_step(
        self,
        step_name: str,
        *,
        needs_charts: bool = False,
        needs_interactions: bool = False,
    ) -> str:
        """Load and assemble condensed prompts for a generation step.

        Args:
            step_name: One of 'plan', 'generate'.
            needs_charts: Whether to include chart generation rules.
            needs_interactions: Whether to include interaction DSL rules.

        Returns:
            Assembled system prompt string for the step.
        """
        allocation = PromptRegistry.get_generation_allocation(step_name)
        extra = PromptRegistry.get_conditional_files(
            step_name,
            needs_charts=needs_charts,
            needs_interactions=needs_interactions,
        )

        parts: list[str] = []

        # Load main prompt files
        for filename in allocation.prompt_files:
            try:
                tokens_per_file = max(100, allocation.target_condensed_tokens // len(allocation.prompt_files))
                text = self.condenser.condense(filename, target_tokens=tokens_per_file)
                if text:
                    parts.append(f"<!-- {filename} -->\n{text}")
            except FileNotFoundError:
                continue

        # Load conditional files
        for filename in extra:
            try:
                text = self.condenser.condense(filename)
                if text:
                    parts.append(f"<!-- {filename} -->\n{text}")
            except FileNotFoundError:
                continue

        return "\n\n".join(parts).strip()

    def load_full_system_prompt(self) -> str:
        """Load ALL condensed prompts combined — for when context allows."""
        all_steps = ["plan", "generate"]
        parts: list[str] = []
        for step in all_steps:
            allocation = PromptRegistry.get_generation_allocation(step)
            for filename in allocation.prompt_files:
                try:
                    text = self.condenser.condense(filename)
                    if text:
                        parts.append(f"<!-- {filename} -->\n{text}")
                except FileNotFoundError:
                    continue
        return "\n\n".join(parts).strip()

    def estimate_step_tokens(
        self,
        step_name: str,
        *,
        needs_charts: bool = False,
        needs_interactions: bool = False,
    ) -> int:
        """Estimate the token count for a step's system prompt without loading it."""
        prompt = self.load_for_step(
            step_name,
            needs_charts=needs_charts,
            needs_interactions=needs_interactions,
        )
        return count_tokens(prompt)

    # ── Cloud LLM: Full original prompts ──

    def load_full_for_verification(self, dimension: str) -> str:
        """Load the FULL (uncondensed) original prompts for a verification dimension.

        Args:
            dimension: One of 'syntax', 'style', 'data_fidelity', 'interaction', 'aggregate'.

        Returns:
            Assembled full system prompt for cloud LLM verification.
        """
        files = PromptRegistry.get_verification_files(dimension)
        parts: list[str] = []
        for filename in files:
            path = self.full_prompts_dir / filename
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(f"<!-- {filename} -->\n{text}")
        return "\n\n".join(parts).strip()

    def load_all_full_prompts(self) -> str:
        """Load ALL full original prompts combined — for comprehensive verification."""
        # Use the existing prompt_loader from the backend if available
        try:
            import sys
            backend_path = str(Path(__file__).resolve().parent.parent.parent.parent / "backend")
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)
            from prompt_loader import load_system_prompt
            return load_system_prompt()
        except ImportError:
            pass

        # Fallback: load all prompts directly
        parts: list[str] = []
        all_files = PromptRegistry.get_all_verification_files()
        all_files.append("01-role-and-task.md")  # Always include role definition
        for filename in sorted(set(all_files)):
            path = self.full_prompts_dir / filename
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(f"<!-- {filename} -->\n{text}")
        return "\n\n".join(parts).strip()

    def estimate_full_tokens(self) -> int:
        """Estimate total token count of all full prompts."""
        prompt = self.load_all_full_prompts()
        return count_tokens(prompt)
