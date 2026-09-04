"""Unit tests for theme handling in the card plan pipeline.

Tests:
- VALID_THEMES and DEFAULT_THEME are loaded from CDN (or fallback)
- parse_card_plan_jsonl extracts theme from the style line
- validate_card_plan validates theme, falls back to default
- _fallback_card_plan includes style_theme
"""
import sys
from pathlib import Path

base = Path(__file__).resolve().parent.parent
if str(base) not in sys.path:
    sys.path.insert(0, str(base))

from app.generation.card_planner import (
    VALID_THEMES,
    DEFAULT_THEME,
    parse_card_plan_jsonl,
    validate_card_plan,
    _fallback_card_plan,
)


def test_valid_themes_loaded():
    """VALID_THEMES should contain all 6 theme names."""
    expected = {"dark", "ocean", "forest", "gold", "modern-saas", "modern-saas-light"}
    assert VALID_THEMES == expected, f"VALID_THEMES mismatch: {VALID_THEMES}"
    print("PASS: VALID_THEMES loaded from CDN")


def test_default_theme():
    """DEFAULT_THEME should be modern-saas-light."""
    assert DEFAULT_THEME == "modern-saas-light"
    print("PASS: DEFAULT_THEME is modern-saas-light")


def test_parse_extracts_theme():
    """Parser should extract theme from the style line."""
    jsonl = '{"style": {"template": "dark_data_tile", "theme": "dark", "desc": "test"}}'
    plan, errors = parse_card_plan_jsonl(jsonl)
    assert errors == [], f"Unexpected parse errors: {errors}"
    assert plan["style_theme"] == "dark", f"Expected 'dark', got '{plan['style_theme']}'"
    print("PASS: parser extracts theme from style line")


def test_parse_defaults_theme_when_missing():
    """Parser should default to DEFAULT_THEME when theme is missing from style line."""
    jsonl = '{"style": {"template": "neutral_minimal", "desc": "no theme"}}'
    plan, errors = parse_card_plan_jsonl(jsonl)
    assert errors == [], f"Unexpected parse errors: {errors}"
    assert plan["style_theme"] == DEFAULT_THEME, \
        f"Expected default '{DEFAULT_THEME}', got '{plan['style_theme']}'"
    print("PASS: parser defaults theme when missing")


def test_validate_accepts_valid_theme():
    """Validator should accept a valid theme."""
    plan, _ = parse_card_plan_jsonl(
        '{"style": {"template": "dark_data_tile", "theme": "ocean", "desc": "test"}}'
    )
    validated = validate_card_plan(plan)
    assert validated["style_theme"] == "ocean"
    print("PASS: validator accepts valid theme 'ocean'")


def test_validate_rejects_invalid_theme():
    """Validator should fall back to DEFAULT_THEME for an invalid theme."""
    plan, _ = parse_card_plan_jsonl(
        '{"style": {"template": "dark_data_tile", "theme": "nonexistent", "desc": "bad"}}'
    )
    validated = validate_card_plan(plan)
    assert validated["style_theme"] == DEFAULT_THEME, \
        f"Expected fallback '{DEFAULT_THEME}', got '{validated['style_theme']}'"
    print("PASS: validator falls back for invalid theme")


def test_validate_defaults_when_no_style_line():
    """Validator should set DEFAULT_THEME when no style line is present."""
    plan, _ = parse_card_plan_jsonl('{"topic": "general", "intent": "test"}')
    validated = validate_card_plan(plan)
    assert validated["style_theme"] == DEFAULT_THEME
    print("PASS: validator defaults theme when no style line")


def test_fallback_plan_has_theme():
    """_fallback_card_plan should include style_theme."""
    plan = _fallback_card_plan()
    assert "style_theme" in plan, "Fallback plan missing style_theme"
    assert plan["style_theme"] == DEFAULT_THEME
    print("PASS: fallback plan includes style_theme")


def test_all_themes_round_trip():
    """All themes from VALID_THEMES should survive parse -> validate round-trip."""
    for name in VALID_THEMES:
        jsonl = f'{{"style": {{"template": "neutral_minimal", "theme": "{name}", "desc": "test"}}}}'
        plan, errors = parse_card_plan_jsonl(jsonl)
        assert errors == [], f"Parse errors for theme '{name}': {errors}"
        validated = validate_card_plan(plan)
        assert validated["style_theme"] == name, \
            f"Theme '{name}' didn't survive round-trip, got '{validated['style_theme']}'"
    print(f"PASS: all {len(VALID_THEMES)} themes survive parse -> validate")


if __name__ == "__main__":
    test_themes_json_loads()
    test_valid_themes_loaded()
    test_default_theme()
    test_parse_extracts_theme()
    test_parse_defaults_theme_when_missing()
    test_validate_accepts_valid_theme()
    test_validate_rejects_invalid_theme()
    test_validate_defaults_when_no_style_line()
    test_fallback_plan_has_theme()
    test_all_themes_round_trip()
    print("\nAll theme tests passed!")
