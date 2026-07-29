"""
Prompt registry — maps each generation/verification step to the prompt files it needs.

Simplified for the Plan → Generate pipeline.
"""

from __future__ import annotations

from app.models.token_budget import PromptAllocation


class PromptRegistry:
    """Maps (step_name) → prompt file allocation with token budgets."""

    # ── Local LLM Generation Steps (condensed prompts) ──

    STEP_ALLOCATIONS: dict[str, PromptAllocation] = {
        "plan": PromptAllocation(
            step_name="plan",
            prompt_files=["plan_system.md"],
            target_condensed_tokens=1200,
        ),

        "generate": PromptAllocation(
            step_name="generate",
            prompt_files=["generate_system.md"],
            target_condensed_tokens=1800,
            conditional_files={
                "needs_charts": [],
                "needs_interactions": [],
            },
        ),

        # ── Two-agent generation pipeline ──

        "page_generate": PromptAllocation(
            step_name="page_generate",
            prompt_files=["page_generate_system.md"],
            target_condensed_tokens=800,
        ),

        "component_generate": PromptAllocation(
            step_name="component_generate",
            prompt_files=["component_generate_system.md"],
            target_condensed_tokens=1200,
        ),

        "content_retrieve": PromptAllocation(
            step_name="content_retrieve",
            prompt_files=["content_retrieve_system.md"],
            target_condensed_tokens=400,
        ),
    }

    # ── Cloud LLM Verification Steps (full original prompts) ──

    VERIFICATION_ALLOCATIONS: dict[str, list[str]] = {
        "syntax": ["03-output-format.md"],
        "style": [
            "04-tailwind-and-stack.md",
            "05-design-quality.md",
            "07-harmony-static-style-spec.md",
        ],
        "data_fidelity": [
            "02-input-handling.md",
            "08-special-data-processing.md",
        ],
        "interaction": ["06-interaction-dsl-v1.md"],
        "aggregate": ["01-role-and-task.md"],
    }

    @classmethod
    def get_generation_allocation(cls, step_name: str) -> PromptAllocation:
        if step_name not in cls.STEP_ALLOCATIONS:
            raise KeyError(
                f"Unknown generation step: {step_name}. "
                f"Available: {list(cls.STEP_ALLOCATIONS.keys())}"
            )
        return cls.STEP_ALLOCATIONS[step_name]

    @classmethod
    def get_verification_files(cls, dimension: str) -> list[str]:
        if dimension not in cls.VERIFICATION_ALLOCATIONS:
            raise KeyError(
                f"Unknown verification dimension: {dimension}. "
                f"Available: {list(cls.VERIFICATION_ALLOCATIONS.keys())}"
            )
        return cls.VERIFICATION_ALLOCATIONS[dimension]

    @classmethod
    def get_all_verification_files(cls) -> list[str]:
        seen: set[str] = set()
        for files in cls.VERIFICATION_ALLOCATIONS.values():
            for f in files:
                seen.add(f)
        return sorted(seen)

    @classmethod
    def get_conditional_files(
        cls, step_name: str, *, needs_charts: bool = False, needs_interactions: bool = False,
    ) -> list[str]:
        allocation = cls.get_generation_allocation(step_name)
        extra: list[str] = []
        if needs_charts and "needs_charts" in allocation.conditional_files:
            extra.extend(allocation.conditional_files["needs_charts"])
        if needs_interactions and "needs_interactions" in allocation.conditional_files:
            extra.extend(allocation.conditional_files["needs_interactions"])
        return extra
