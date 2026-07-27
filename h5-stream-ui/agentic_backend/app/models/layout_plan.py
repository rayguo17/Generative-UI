"""
Layout plan models — the intermediate representation between analysis and HTML generation.

The LayoutPlan is a compact structured description of the UI to generate.
It serves as a "compression" of design intent, allowing the HTML generation
step to have more context window budget for actual HTML output.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CardType(StrEnum):
    """High-level classification of the card being generated."""
    SIMPLE_CARD = "simple_card"        # Single info card (weather, profile, etc.)
    DATA_TABLE = "data_table"           # Tabular data display
    DASHBOARD = "dashboard"             # Multi-metric overview
    FORM = "form"                       # Input form
    LIST_DETAIL = "list_detail"         # List with detail expansion
    CHART_VIEW = "chart_view"           # Chart/visualization primary
    MULTI_SECTION = "multi_section"     # Composite card with multiple distinct sections


class SectionType(StrEnum):
    """Types of visual sections that can appear in the card."""
    HEADER = "header"                   # Card title/identity area
    HERO_IMAGE = "hero_image"           # Primary image
    METRICS_GRID = "metrics_grid"       # KPI/metric cards in a grid
    DATA_TABLE = "data_table"           # Tabular data
    CHART_AREA = "chart_area"           # Chart container
    CARD_LIST = "card_list"             # Repeating card items
    FORM_FIELDS = "form_fields"         # Form inputs
    TEXT_BLOCK = "text_block"           # Prose/long text
    BUTTON_GROUP = "button_group"       # Action buttons row
    FOOTER = "footer"                   # Bottom metadata/actions


class DataBinding(BaseModel):
    """Maps a data field path to a visual role in the UI."""
    field_path: str = Field(
        ...,
        description="JSON path to the data field, e.g. 'items[].title' or 'summary.total'",
    )
    visual_role: str = Field(
        ...,
        description="Semantic role in the UI, e.g. 'card_title', 'metric_value', 'row_label'",
    )
    fallback: Optional[str] = Field(
        default=None,
        description="Default text if field is missing/null",
    )


class InteractionIntent(BaseModel):
    """Declarative interaction specification anchored to a UI element."""
    trigger_element: str = Field(
        ...,
        description="Which UI element triggers this, e.g. 'row_button', 'card_root'",
    )
    action_type: Literal["openUrl", "setPage", "updateData"] = Field(
        ...,
        description="DSL action type",
    )
    params_source: str = Field(
        ...,
        description="Field path providing action parameters, e.g. 'items[].link'",
    )
    condition: Optional[str] = Field(
        default=None,
        description="Condition for including this interaction, e.g. 'if field exists'",
    )


class LayoutSection(BaseModel):
    """A single visual section in the card layout."""
    section_type: SectionType
    data_bindings: list[DataBinding] = Field(default_factory=list)
    layout_direction: Literal["horizontal", "vertical", "grid"] = "vertical"
    grid_columns: Optional[int] = Field(default=None, ge=1, le=4)
    visual_priority: int = Field(default=0, description="Lower = rendered first / more prominent")
    is_repeatable: bool = Field(default=False, description="True if iterates over array data")


class StylePreferences(BaseModel):
    """Visual style choices for the card."""
    accent_color: str = "#0A59F7"
    card_radius: str = "20px"
    spacing_scale: Literal["compact", "normal", "relaxed"] = "normal"
    harmony_mode: bool = Field(default=False, description="Apply HarmonyOS card styling spec")


class LayoutPlan(BaseModel):
    """Complete layout plan — the intermediate representation for HTML generation."""
    card_type: CardType
    sections: list[LayoutSection] = Field(
        default_factory=list,
        description="Ordered top-to-bottom sections of the card",
    )
    data_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted data schema and sample values",
    )
    interaction_intents: list[InteractionIntent] = Field(default_factory=list)
    style_preferences: StylePreferences = Field(default_factory=StylePreferences)
    needs_charts: bool = False
    needs_pagination: bool = False
    needs_interactions: bool = False
    estimated_complexity: Literal["low", "medium", "high"] = "low"
