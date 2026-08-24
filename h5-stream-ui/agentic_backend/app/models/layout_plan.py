"""
Layout plan models — the intermediate representation between planning and generation.

The refined LayoutPlan captures topic, intent, global structure, and per-section
widget assignments with data requirements. The researcher agent attaches gathered
data, and the composer agent uses section specs + data to generate HTML.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Topic(StrEnum):
    """High-level topic classification detected from user input."""
    TRAVEL_PLAN = "travel_plan"
    STOCK_ANALYSIS = "stock_analysis"
    WEATHER = "weather"
    PRODUCT_LISTING = "product_listing"
    GENERAL = "general"


class Widget(StrEnum):
    """Visual widget assigned to each section."""
    LEAD = "lead"
    BODY_LIST = "body_list"
    BODY_NUMBERED_LIST = "body_numbered_list"
    BODY_GRID = "body_grid"
    BODY_BLOCK = "body_block"
    BODY_CHIPS = "body_chips"
    BODY_TIMELINE = "body_timeline"
    BODY_CARDS = "body_cards"
    BODY_TABLE = "body_table"
    WIDGET_SECTION_ECHARTS = "widget_section_echarts"


class ResearchStrategy(StrEnum):
    """Strategy hint for the researcher agent."""
    SINGLE_LOOKUP = "single_lookup"
    SEARCH_ALL = "search_all"
    ITERATE_DAYS = "iterate_days"
    NONE = "none"


class CardType(StrEnum):
    """High-level classification of the card being generated."""
    SIMPLE_CARD = "simple_card"
    DATA_TABLE = "data_table"
    DASHBOARD = "dashboard"
    FORM = "form"
    LIST_DETAIL = "list_detail"
    CHART_VIEW = "chart_view"
    MULTI_SECTION = "multi_section"


class StylePreferences(BaseModel):
    """Visual style choices for the card."""
    accent_color: str = "#0A59F7"
    card_radius: str = "20px"
    spacing_scale: Literal["compact", "normal", "relaxed"] = "normal"
    harmony_mode: bool = Field(default=False, description="Apply HarmonyOS card styling spec")


class PlanSection(BaseModel):
    """A single content section in the plan."""
    index: int = Field(..., description="Sequential position, 0 = lead")
    title: str = Field(..., description="Human-readable section name")
    widget: str = Field(default="body_block", description="Widget from the 10-widget palette")
    desc: str = Field(default="", description="What this section displays and its role")
    data_needed: str = Field(default="", description="Natural language description of data fields needed")
    research_strategy: str = Field(default="none", description="Strategy hint for researcher agent")
    is_repeatable: bool = Field(default=False, description="True if iterates over array data")
    est_count: Optional[int] = Field(default=None, description="Estimated item count for repeatable sections")


class LayoutPlan(BaseModel):
    """Complete content plan — drives researcher → composer pipeline."""
    topic: str = Field(default="general", description="Detected topic category")
    intent: str = Field(default="", description="One-line summary of user intent")
    global_desc: str = Field(default="", description="One paragraph describing overall page structure")
    card_type: str = Field(default="multi_section", description="Card classification")
    sections: list[PlanSection] = Field(default_factory=list, description="Ordered content sections")
    style_preferences: StylePreferences = Field(default_factory=StylePreferences)

    # Legacy fields kept for downstream compatibility — set to sensible defaults
    data_summary: dict[str, Any] = Field(default_factory=dict)
    needs_charts: bool = False
    needs_pagination: bool = False
    needs_interactions: bool = False
    estimated_complexity: Literal["low", "medium", "high"] = "low"
