"""Unit tests for _extract_json_lines and parse_plan_jsonl in app.generation.plan.

Covers: standard JSONL, pretty-printed multi-line JSON, space-separated
objects, commentary between objects, braces inside string values, escaped
quotes, markdown-fenced blocks, thinking tags, and edge cases.
"""
import sys
from pathlib import Path

base = Path(__file__).resolve().parent.parent
if str(base) not in sys.path:
    sys.path.insert(0, str(base))

from app.generation.plan import _extract_json_lines, parse_plan_jsonl


# ── Standard JSONL ────────────────────────────────────────────────────

def test_standard_jsonl():
    """One complete JSON object per line — the ideal format."""
    text = (
        '{"topic": "travel_plan", "intent": "One-day Hangzhou trip"}\n'
        '{"section": 0, "title": "Overview", "widget": "lead", "desc": "Hero", "data": "name", "research": "none", "repeatable": false, "est_count": null}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"topic"' in lines[0]
    assert '"section"' in lines[1]
    print("PASS: test_standard_jsonl")


def test_jsonl_with_nested_braces():
    """Standard JSONL where one object has nested braces (global block)."""
    text = (
        '{"topic": "travel_plan", "intent": "Trip"}\n'
        '{"global": {"desc": "A card.", "card_type": "multi_section"}}\n'
        '{"section": 0, "title": "Overview", "widget": "lead", "desc": "Hero", "data": "name", "research": "none", "repeatable": false, "est_count": null}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 3
    assert '"global"' in lines[1]
    assert '"desc": "A card."' in lines[1]
    print("PASS: test_jsonl_with_nested_braces")


# ── Pretty-printed multi-line JSON (the main bug case) ────────────────

def test_pretty_printed_multiline():
    """Weaker models output each field on its own line — must still parse."""
    text = (
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "Create a one-day travel itinerary"\n'
        '} {\n'
        '  "global": {\n'
        '    "desc": "A compact card.",\n'
        '    "card_type": "multi_section"\n'
        '  }\n'
        '} {\n'
        '  "section": 0,\n'
        '  "title": "Trip Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero section",\n'
        '  "data": "destination name (text)",\n'
        '  "research": "single_lookup",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 3, f"Expected 3 objects, got {len(lines)}: {lines}"
    assert '"topic"' in lines[0]
    assert '"global"' in lines[1]
    assert '"section": 0' in lines[2]
    print("PASS: test_pretty_printed_multiline")


def test_pretty_printed_newlines_between_objects():
    """Pretty-printed JSON with blank lines between objects."""
    text = (
        '{\n'
        '  "topic": "general",\n'
        '  "intent": "Test"\n'
        '}\n'
        '\n'
        '{\n'
        '  "section": 0,\n'
        '  "title": "Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero",\n'
        '  "data": "name",\n'
        '  "research": "none",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"topic"' in lines[0]
    assert '"section"' in lines[1]
    print("PASS: test_pretty_printed_newlines_between_objects")


# ── Space-separated objects on same line ──────────────────────────────

def test_space_separated_objects():
    """Objects separated by `} {` on the same line (Qwen3-4B pattern)."""
    text = '{"topic": "general", "intent": "A"} {"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}'
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"topic"' in lines[0]
    assert '"section"' in lines[1]
    print("PASS: test_space_separated_objects")


def test_multiple_objects_on_one_line():
    """Three compact objects on a single line."""
    text = '{"topic": "general", "intent": "A"} {"global": {"desc": "d", "card_type": "multi_section"}} {"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}'
    lines = _extract_json_lines(text)
    assert len(lines) == 3
    print("PASS: test_multiple_objects_on_one_line")


# ── Commentary / mixed content ────────────────────────────────────────

def test_commentary_between_objects():
    """Text between objects should be silently skipped."""
    text = (
        'Here is the plan:\n'
        '\n'
        '{"topic": "general", "intent": "Test"}\n'
        '\n'
        'Let me add sections:\n'
        '{"section": 0, "title": "Hello", "widget": "lead", "desc": "Hi", "data": "title", "research": "none", "repeatable": false, "est_count": null}\n'
        'That is all.'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"topic"' in lines[0]
    assert '"section"' in lines[1]
    print("PASS: test_commentary_between_objects")


# ── Braces inside string values ────────────────────────────────────────

def test_braces_inside_string_values():
    """Braces inside quoted strings must not affect depth tracking."""
    text = (
        '{"topic": "general", "intent": "Show { and } symbols"}\n'
        '{"section": 0, "title": "Test", "widget": "lead", "desc": "Use {curly} braces", "data": "field with } char", "research": "none", "repeatable": false, "est_count": null}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2, f"Expected 2 objects, got {len(lines)}: {lines}"
    assert '"Show { and } symbols"' in lines[0]
    assert '"Use {curly} braces"' in lines[1]
    assert '"field with } char"' in lines[1]
    print("PASS: test_braces_inside_string_values")


def test_escaped_quotes_inside_strings():
    """Escaped double-quotes inside strings should not end the string."""
    text = (
        '{"topic": "general", "intent": "Say \\"hello\\""}\n'
        '{"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"Say \\"hello\\""' in lines[0]
    print("PASS: test_escaped_quotes_inside_strings")


def test_backslash_inside_string():
    """A backslash before a character that is not a quote should be handled."""
    text = '{"topic": "general", "intent": "Path C:\\\\folder"}'
    lines = _extract_json_lines(text)
    assert len(lines) == 1
    assert 'C:\\\\folder' in lines[0]
    print("PASS: test_backslash_inside_string")


# ── Markdown-fenced blocks ─────────────────────────────────────────────

def test_markdown_fenced_jsonl():
    """JSONL inside a ```jsonl fence should be extracted."""
    text = (
        '```jsonl\n'
        '{"topic": "general", "intent": "A"}\n'
        '{"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}\n'
        '```'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    print("PASS: test_markdown_fenced_jsonl")


def test_markdown_fenced_pretty_json():
    """Pretty-printed JSON inside a ```json fence should be extracted."""
    text = (
        '```json\n'
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "Fenced"\n'
        '}\n'
        '{\n'
        '  "section": 0,\n'
        '  "title": "Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero",\n'
        '  "data": "name",\n'
        '  "research": "none",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}\n'
        '```'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2, f"Expected 2 objects, got {len(lines)}"
    assert '"topic"' in lines[0]
    assert '"section"' in lines[1]
    print("PASS: test_markdown_fenced_pretty_json")


# ── Thinking tags ──────────────────────────────────────────────────────

def test_thinking_tags_with_jsonl():
    """Thinking tags should be stripped before extraction."""
    text = (
        '<think>\n'
        'Let me plan this out.\n'
        '</think>\n'
        '\n'
        '{"topic": "travel_plan", "intent": "With thinking"}\n'
        '{"section": 0, "title": "Overview", "widget": "lead", "desc": "Hero", "data": "name", "research": "none", "repeatable": false, "est_count": null}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    assert '"travel_plan"' in lines[0]
    print("PASS: test_thinking_tags_with_jsonl")


def test_thinking_tags_with_pretty_json():
    """Thinking tags + pretty-printed JSON should both be handled."""
    text = (
        '<think>\n'
        'I should output JSONL.\n'
        '</think>\n'
        '\n'
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "With thinking"\n'
        '}\n'
        '{\n'
        '  "section": 0,\n'
        '  "title": "Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero",\n'
        '  "data": "name",\n'
        '  "research": "none",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}'
    )
    lines = _extract_json_lines(text)
    assert len(lines) == 2
    print("PASS: test_thinking_tags_with_pretty_json")


# ── Edge cases ────────────────────────────────────────────────────────

def test_empty_input():
    """Empty string should return an empty list."""
    assert _extract_json_lines("") == []
    print("PASS: test_empty_input")


def test_whitespace_only():
    """Whitespace-only input should return an empty list."""
    assert _extract_json_lines("   \n\n  \t  \n") == []
    print("PASS: test_whitespace_only")


def test_no_braces():
    """Text without any braces should return an empty list."""
    assert _extract_json_lines("Hello world\nNo JSON here") == []
    print("PASS: test_no_braces")


def test_unclosed_object():
    """An object with no closing brace should not produce a line."""
    text = '{"topic": "general", "intent": "unclosed'
    lines = _extract_json_lines(text)
    # The brace scanner never sees depth return to 0, so nothing is emitted
    assert len(lines) == 0
    print("PASS: test_unclosed_object")


# ── Integration: parse_plan_jsonl ─────────────────────────────────────

def test_parse_plan_jsonl_pretty_printed():
    """parse_plan_jsonl should handle pretty-printed multi-line JSON end-to-end."""
    text = (
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "Create a one-day travel itinerary for Hangzhou"\n'
        '} {\n'
        '  "global": {\n'
        '    "desc": "A compact travel itinerary card.",\n'
        '    "card_type": "multi_section"\n'
        '  }\n'
        '} {\n'
        '  "section": 0,\n'
        '  "title": "Trip Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero section",\n'
        '  "data": "destination name (text)",\n'
        '  "research": "single_lookup",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert errors == [], f"Expected no errors, got: {errors}"
    assert plan["topic"] == "travel_plan"
    assert plan["intent"] == "Create a one-day travel itinerary for Hangzhou"
    assert plan["global_desc"] == "A compact travel itinerary card."
    assert plan["card_type"] == "multi_section"
    assert len(plan["sections"]) == 1
    assert plan["sections"][0]["title"] == "Trip Overview"
    assert plan["sections"][0]["widget"] == "lead"
    assert plan["sections"][0]["research_strategy"] == "single_lookup"
    print("PASS: test_parse_plan_jsonl_pretty_printed")


def test_parse_plan_jsonl_standard_jsonl():
    """parse_plan_jsonl with standard one-object-per-line JSONL."""
    text = (
        '{"topic": "travel_plan", "intent": "One-day Hangzhou trip"}\n'
        '{"global": {"desc": "A card.", "card_type": "multi_section"}}\n'
        '{"section": 0, "title": "Overview", "widget": "lead", "desc": "Hero", "data": "name", "research": "none", "repeatable": false, "est_count": null}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert errors == []
    assert plan["topic"] == "travel_plan"
    assert len(plan["sections"]) == 1
    print("PASS: test_parse_plan_jsonl_standard_jsonl")


def test_parse_plan_jsonl_braces_in_strings():
    """parse_plan_jsonl should preserve braces inside string values."""
    text = (
        '{"topic": "general", "intent": "Show { and } symbols"}\n'
        '{"section": 0, "title": "Test", "widget": "lead", "desc": "Use {curly} braces", "data": "field with } char", "research": "none", "repeatable": false, "est_count": null}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert errors == []
    assert plan["intent"] == "Show { and } symbols"
    assert plan["sections"][0]["desc"] == "Use {curly} braces"
    assert plan["sections"][0]["data_needed"] == "field with } char"
    print("PASS: test_parse_plan_jsonl_braces_in_strings")


def test_parse_plan_jsonl_multiple_sections_pretty():
    """parse_plan_jsonl with multiple pretty-printed section objects."""
    text = (
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "Trip"\n'
        '}\n'
        '{\n'
        '  "section": 0,\n'
        '  "title": "Lead",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero",\n'
        '  "data": "name",\n'
        '  "research": "none",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '}\n'
        '{\n'
        '  "section": 1,\n'
        '  "title": "Spots",\n'
        '  "widget": "body_grid",\n'
        '  "desc": "Grid",\n'
        '  "data": "spots",\n'
        '  "research": "search_all",\n'
        '  "repeatable": false,\n'
        '  "est_count": 4\n'
        '}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert errors == [], f"Expected no errors, got: {errors}"
    assert len(plan["sections"]) == 2
    assert plan["sections"][0]["widget"] == "lead"
    assert plan["sections"][1]["widget"] == "body_grid"
    assert plan["sections"][1]["est_count"] == 4
    print("PASS: test_parse_plan_jsonl_multiple_sections_pretty")


def test_parse_plan_jsonl_rejects_garbage_line():
    """A line that is not valid JSON should produce a parse error, not crash."""
    text = (
        '{"topic": "general", "intent": "Test"}\n'
        'This is not JSON\n'
        '{"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}'
    )
    plan, errors = parse_plan_jsonl(text)
    # "This is not JSON" has no braces so it's skipped — no error
    # But if it had braces, it would produce an error
    assert plan["topic"] == "general"
    assert len(plan["sections"]) == 1
    print("PASS: test_parse_plan_jsonl_rejects_garbage_line")


def test_parse_plan_jsonl_malformed_object_with_braces():
    """An object that has braces but invalid JSON should produce a parse error."""
    text = (
        '{"topic": "general", "intent": "Test"}\n'
        '{bad json}\n'
        '{"section": 0, "title": "T", "widget": "lead", "desc": "D", "data": "n", "research": "none", "repeatable": false, "est_count": null}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert len(errors) == 1
    assert "unparseable" in errors[0]
    assert plan["topic"] == "general"
    assert len(plan["sections"]) == 1
    print("PASS: test_parse_plan_jsonl_malformed_object_with_braces")


# ── Real-world benchmark sample ───────────────────────────────────────

def test_benchmark_qwen3_4b_pretty_output():
    """Exact pretty-printed output from Qwen3-4B benchmark (bench_1786698621)."""
    text = (
        '{\n'
        '  "topic": "travel_plan",\n'
        '  "intent": "Create a one-day travel itinerary for Hangzhou with scenic spots, daily schedule, and transportation guide"\n'
        '} {\n'
        '  "global": {\n'
        '    "desc": "A compact travel itinerary card for a one-day trip to Hangzhou. Starts with a hero lead showcasing the destination and trip highlights, then presents a grid of top sights, a chronological timeline of the day\'s plan, and a list of nearby parking options.",\n'
        '    "card_type": "multi_section"\n'
        '  }\n'
        '} {\n'
        '  "section": 0,\n'
        '  "title": "Trip Overview",\n'
        '  "widget": "lead",\n'
        '  "desc": "Hero section displaying destination name, date, weather preview, and a 2-3 sentence trip summary",\n'
        '  "data": "destination_name (text), date (text), weather_forecast (text), trip_summary (text, 2-3 sentences), hero_image_url (url)",\n'
        '  "research": "single_lookup",\n'
        '  "repeatable": false,\n'
        '  "est_count": null\n'
        '} {\n'
        '  "section": 1,\n'
        '  "title": "Top Scenic Spots",\n'
        '  "widget": "body_grid",\n'
        '  "desc": "2-column grid of top 4 scenic spots featuring images, names, and brief descriptions",\n'
        '  "data": "spot_name (text), image_url (url), short_description (text), estimated_time (text)",\n'
        '  "research": "search_all",\n'
        '  "repeatable": false,\n'
        '  "est_count": 4\n'
        '} {\n'
        '  "section": 2,\n'
        '  "title": "Daily Itinerary",\n'
        '  "widget": "body_timeline",\n'
        '  "desc": "Hourly breakdown of the day\'s plan with time blocks, activities, locations, and optional notes",\n'
        '  "data": "time_slot (text), activity (text), location (text), additional_notes (text, optional)",\n'
        '  "research": "iterate_days",\n'
        '  "repeatable": true,\n'
        '  "est_count": null\n'
        '} {\n'
        '  "section": 3,\n'
        '  "title": "Transportation Guide",\n'
        '  "widget": "body_list",\n'
        '  "desc": "Simple list of nearby parking lots with name, address, fee details, and distance to major landmarks",\n'
        '  "data": "parking_lot_name (text), address (text), hourly_rate (text), distance_to_attractions (text)",\n'
        '  "research": "search_all",\n'
        '  "repeatable": true,\n'
        '  "est_count": null\n'
        '}'
    )
    plan, errors = parse_plan_jsonl(text)
    assert errors == [], f"Expected no errors, got: {errors}"
    assert plan["topic"] == "travel_plan"
    assert plan["intent"] == "Create a one-day travel itinerary for Hangzhou with scenic spots, daily schedule, and transportation guide"
    assert plan["global_desc"].startswith("A compact travel itinerary card")
    assert len(plan["sections"]) == 4
    assert plan["sections"][0]["widget"] == "lead"
    assert plan["sections"][1]["widget"] == "body_grid"
    assert plan["sections"][1]["est_count"] == 4
    assert plan["sections"][2]["widget"] == "body_timeline"
    assert plan["sections"][2]["is_repeatable"] == True
    assert plan["sections"][3]["widget"] == "body_list"
    print("PASS: test_benchmark_qwen3_4b_pretty_output")


# ── Runner ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_standard_jsonl()
    test_jsonl_with_nested_braces()
    test_pretty_printed_multiline()
    test_pretty_printed_newlines_between_objects()
    test_space_separated_objects()
    test_multiple_objects_on_one_line()
    test_commentary_between_objects()
    test_braces_inside_string_values()
    test_escaped_quotes_inside_strings()
    test_backslash_inside_string()
    test_markdown_fenced_jsonl()
    test_markdown_fenced_pretty_json()
    test_thinking_tags_with_jsonl()
    test_thinking_tags_with_pretty_json()
    test_empty_input()
    test_whitespace_only()
    test_no_braces()
    test_unclosed_object()
    test_parse_plan_jsonl_pretty_printed()
    test_parse_plan_jsonl_standard_jsonl()
    test_parse_plan_jsonl_braces_in_strings()
    test_parse_plan_jsonl_multiple_sections_pretty()
    test_parse_plan_jsonl_rejects_garbage_line()
    test_parse_plan_jsonl_malformed_object_with_braces()
    test_benchmark_qwen3_4b_pretty_output()
    print("\nAll _extract_json_lines tests passed!")
