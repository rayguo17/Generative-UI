"""
Prompt registry — maps each generation/verification step to the prompt files it needs.

The registry encodes the "context-window-aware routing" logic: each step gets
only the rules relevant to its task, keeping the total prompt under 4K tokens.

For local LLM steps: condensed prompt files are used (from generation/prompts/).
For cloud verification: full original prompt files are used (from prompts/).
"""

from __future__ import annotations

from app.models.token_budget import PromptAllocation


class PromptRegistry:
    """Maps (step_name, conditions) -> prompt file allocation with token budgets."""

    # ── Local LLM Generation Steps (condensed prompts) ──

    STEP_ALLOCATIONS: dict[str, PromptAllocation] = {
        "classify": PromptAllocation(
            step_name="classify",
            prompt_files=[
                "analyze_system.md",
            ],
            target_condensed_tokens=400,
        ),

        "plan": PromptAllocation(
            step_name="plan",
            prompt_files=[
                "plan_system.md",
            ],
            target_condensed_tokens=800,
        ),

        "generate": PromptAllocation(
            step_name="generate",
            prompt_files=[
                "generate_system.md",
            ],
            target_condensed_tokens=1800,
            conditional_files={
                "needs_charts": [],       # Chart rules already in generate_system if needed
                "needs_interactions": [],  # Interaction DSL already in generate_system if needed
            },
        ),

        "refine": PromptAllocation(
            step_name="refine",
            prompt_files=[
                "refine_system.md",
            ],
            target_condensed_tokens=600,
        ),

        # Targeted fix steps (used after verification finds issues)
        "fix_syntax": PromptAllocation(
            step_name="fix_syntax",
            prompt_files=[
                "generate_system.md",  # Contains output format rules
            ],
            target_condensed_tokens=300,
        ),

        "fix_style": PromptAllocation(
            step_name="fix_style",
            prompt_files=[
                "generate_system.md",  # Contains style rules
            ],
            target_condensed_tokens=500,
        ),

        "fix_data": PromptAllocation(
            step_name="fix_data",
            prompt_files=[
                "refine_system.md",  # Contains data fidelity rules
            ],
            target_condensed_tokens=400,
        ),

        "fix_interactions": PromptAllocation(
            step_name="fix_interactions",
            prompt_files=[
                "generate_system.md",  # Contains interaction DSL rules
            ],
            target_condensed_tokens=300,
        ),
    }

    # ── Cloud LLM Verification Steps (full original prompts) ──

    VERIFICATION_ALLOCATIONS: dict[str, list[str]] = {
        "syntax": [
            "03-output-format.md",
        ],
        "style": [
            "04-tailwind-and-stack.md",
            "05-design-quality.md",
            "07-harmony-static-style-spec.md",
        ],
        "data_fidelity": [
            "02-input-handling.md",
            "08-special-data-processing.md",
        ],
        "interaction": [
            "06-interaction-dsl-v1.md",
        ],
        "aggregate": [
            "01-role-and-task.md",
        ],
    }

    @classmethod
    def get_generation_allocation(cls, step_name: str) -> PromptAllocation:
        """Get the prompt allocation for a local LLM generation step."""
        if step_name not in cls.STEP_ALLOCATIONS:
            raise KeyError(f"Unknown generation step: {step_name}. "
                           f"Available: {list(cls.STEP_ALLOCATIONS.keys())}")
        return cls.STEP_ALLOCATIONS[step_name]

    @classmethod
    def get_verification_files(cls, dimension: str) -> list[str]:
        """Get the full prompt files needed for a verification dimension."""
        if dimension not in cls.VERIFICATION_ALLOCATIONS:
            raise KeyError(f"Unknown verification dimension: {dimension}. "
                           f"Available: {list(cls.VERIFICATION_ALLOCATIONS.keys())}")
        return cls.VERIFICATION_ALLOCATIONS[dimension]

    @classmethod
    def get_all_verification_files(cls) -> list[str]:
        """Get all unique prompt files used across verification dimensions."""
        seen: set[str] = set()
        for files in cls.VERIFICATION_ALLOCATIONS.values():
            for f in files:
                seen.add(f)
        return sorted(seen)

    @classmethod
    def get_conditional_files(
        cls, step_name: str, *, needs_charts: bool = False, needs_interactions: bool = False
    ) -> list[str]:
        """Get additional prompt files based on generation conditions."""
        allocation = cls.get_generation_allocation(step_name)
        extra: list[str] = []
        if needs_charts and "needs_charts" in allocation.conditional_files:
            extra.extend(allocation.conditional_files["needs_charts"])
        if needs_interactions and "needs_interactions" in allocation.conditional_files:
            extra.extend(allocation.conditional_files["needs_interactions"])
        return extra
