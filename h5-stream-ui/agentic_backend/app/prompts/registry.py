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
            prompt_files=["plan_system_refine.md"],
            target_condensed_tokens=2000,
        ),

        "generate": PromptAllocation(
            step_name="generate",
            prompt_files=["generate/generate_system.md"],
            target_condensed_tokens=1800,
            conditional_files={
                "needs_charts": [],
                "needs_interactions": [],
            },
        ),

        # ── Two-agent generation pipeline ──

        "page_generate": PromptAllocation(
            step_name="page_generate",
            prompt_files=["page_generate/page_generate_system.md"],
            target_condensed_tokens=1800,
        ),

        "component_generate": PromptAllocation(
            step_name="component_generate",
            prompt_files=["component_generate/component_generate_system.md"],
            target_condensed_tokens=2200,
        ),

        "content_retrieve": PromptAllocation(
            step_name="content_retrieve",
            prompt_files=["content_retrieve/content_retrieve_system.md"],
            target_condensed_tokens=1400,
        ),
    }

    # ── Widget taxonomy ──
    # The plan emits `widget` values for each section. Per-type prompt files are
    # component_generate/{widget}_system.md. All 10+1 widgets have dedicated prompts.
    KNOWN_WIDGETS: frozenset[str] = frozenset({
        "lead",
        "body_list",
        "body_numbered_list",
        "body_grid",
        "body_block",
        "body_chips",
        "body_timeline",
        "body_cards",
        "body_table",
        "widget_section_echarts",
        "footer",
    })

    # Explicit mapping: widget → per-type prompt file (for discoverability + validation).
    WIDGET_PROMPTS: dict[str, str] = {
        "lead":             "component_generate/component_generate_lead_system.md",
        "body_list":        "component_generate/component_generate_body_list_system.md",
        "body_numbered_list": "component_generate/component_generate_body_numbered_list_system.md",
        "body_grid":        "component_generate/component_generate_body_grid_system.md",
        "body_block":       "component_generate/component_generate_body_block_system.md",
        "body_chips":       "component_generate/component_generate_body_chips_system.md",
        "body_timeline":    "component_generate/component_generate_body_timeline_system.md",
        "body_cards":       "component_generate/component_generate_body_cards_system.md",
        "body_table":       "component_generate/component_generate_body_table_system.md",
        "widget_section_echarts": "component_generate/component_generate_widget_section_echarts_system.md",
        "footer":           "component_generate/component_generate_footer_system.md",
    }

    # Old plan section_type → new widget equivalents (backward compat with stale plans).
    # New widget values pass through unchanged via map_section_type.
    SECTION_TYPE_MAP: dict[str, str] = {
        "header": "lead",
        "hero_image": "lead",
        "metrics_grid": "body_grid",
        "card_list": "body_cards",
        "data_table": "body_table",
        "text_block": "body_block",
        "button_group": "body_chips",
        "form_fields": "body_block",
        "chart_area": "widget_section_echarts",
        "footer": "footer",
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

    @classmethod
    def map_section_type(cls, widget: str) -> str:
        """Map a widget/section_type to the per-type prompt name.

        - Old ``section_type`` values (header, metrics_grid, ...) are mapped to
          their new widget equivalents via ``SECTION_TYPE_MAP``.
        - New widget values (lead, body_list, body_grid, body_timeline,
          body_cards, body_table, widget_section_echarts, ...) pass through unchanged.
        - Unknown values pass through (load_component_system will try the
          per-type file and fall back to the general prompt if not found).
        Hyphens are normalised to underscores.
        """
        s = (widget or "").replace("-", "_").strip()
        return cls.SECTION_TYPE_MAP.get(s, s)
