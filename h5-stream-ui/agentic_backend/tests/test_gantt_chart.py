"""Unit tests for the LlmInteractionLogger Gantt chart generation."""
import sys
import tempfile
import re
from pathlib import Path

# Ensure app package is importable
base = Path(__file__).resolve().parent.parent
if str(base) not in sys.path:
    sys.path.insert(0, str(base))

from app.utils.llm_logger import LlmInteractionLogger


def test_gantt_chart_has_correct_format():
    """The Gantt chart should use HH:mm:ss format, not Unix timestamps."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_format"
    logger = LlmInteractionLogger(log_dir, "test_gantt_format", "test")
    logger.log_local_call("plan", "qwen3:8b", "s", "u", "r", duration_ms=5000)
    logger.log_local_call("research_lead", "qwen3:8b", "s", "u", "r", duration_ms=12000)
    logger.log_local_call("component_0_lead", "qwen3:8b", "s", "u", "r", duration_ms=8000)
    path = logger.finalize(total_duration_ms=25000)

    content = path.read_text(encoding="utf-8")
    assert "```mermaid" in content
    assert "gantt" in content
    assert "dateFormat HH:mm:ss" in content
    assert "axisFormat %M:%S" in content
    print("PASS: test_gantt_chart_has_correct_format")


def test_gantt_chart_sequential_timestamps():
    """Each step's start time should be where the previous step ended (sequential)."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_seq"
    logger = LlmInteractionLogger(log_dir, "test_gantt_seq", "test")
    logger.log_local_call("plan", "qwen3:8b", "s", "u", "r", duration_ms=5000)
    logger.log_local_call("research", "qwen3:8b", "s", "u", "r", duration_ms=12000)
    logger.log_local_call("component_0", "qwen3:8b", "s", "u", "r", duration_ms=8000)
    path = logger.finalize(total_duration_ms=25000)

    content = path.read_text(encoding="utf-8")
    # Extract the gantt task lines with timestamps
    gantt_lines = [l for l in content.splitlines()
                   if ":" in l and ", " in l and "(" in l and "s)" in l]

    assert len(gantt_lines) == 3, f"Expected 3 gantt task lines, got {len(gantt_lines)}"

    # Parse start/end times from each line
    times = []
    for line in gantt_lines:
        match = re.search(r':\w+,\s*(\d{2}:\d{2}:\d{2}),\s*(\d{2}:\d{2}:\d{2})', line)
        assert match, f"Could not parse timestamps from: {line}"
        start = match.group(1)
        end = match.group(2)
        times.append((start, end))

    # Step 0: starts at 00:00:00, ends at 00:00:05 (5s)
    assert times[0] == ("00:00:00", "00:00:05"), f"Step 0 times wrong: {times[0]}"
    # Step 1: starts at 00:00:05 (where step 0 ended), ends at 00:00:17 (5+12=17s)
    assert times[1] == ("00:00:05", "00:00:17"), f"Step 1 times wrong: {times[1]}"
    # Step 2: starts at 00:00:17 (where step 1 ended), ends at 00:00:25 (5+12+8=25s)
    assert times[2] == ("00:00:17", "00:00:25"), f"Step 2 times wrong: {times[2]}"

    # Verify each step's start == previous step's end (sequential)
    for i in range(1, len(times)):
        assert times[i][0] == times[i-1][1], \
            f"Step {i} start ({times[i][0]}) != step {i-1} end ({times[i-1][1]})"

    print("PASS: test_gantt_chart_sequential_timestamps")


def test_gantt_chart_total_in_title():
    """The chart title should show the total time."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_total"
    logger = LlmInteractionLogger(log_dir, "test_gantt_total", "test")
    logger.log_local_call("step1", "qwen3:8b", "s", "u", "r", duration_ms=30000)
    logger.log_local_call("step2", "qwen3:8b", "s", "u", "r", duration_ms=60000)
    path = logger.finalize(total_duration_ms=90000)

    content = path.read_text(encoding="utf-8")
    assert "90s total" in content, f"Title should show 90s total"
    print("PASS: test_gantt_chart_total_in_title")


def test_gantt_chart_sanitizes_names():
    """Step names with emoji/special chars should be sanitized for Mermaid."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_sanitize"
    logger = LlmInteractionLogger(log_dir, "test_gantt_sanitize", "test")
    logger.log_local_call("plan ✅", "qwen3:8b", "s", "u", "r", duration_ms=1000)
    path = logger.finalize(total_duration_ms=1000)

    content = path.read_text(encoding="utf-8")
    # The emoji should be stripped; the task line should have "plan" not "plan ✅"
    gantt_task_lines = [l for l in content.splitlines()
                        if ":plan" in l and "(" in l and "s)" in l]
    assert len(gantt_task_lines) == 1
    assert "✅" not in gantt_task_lines[0], f"Emoji not stripped: {gantt_task_lines[0]}"
    print("PASS: test_gantt_chart_sanitizes_names")


def test_gantt_chart_empty_durations():
    """If no calls were logged, no chart should be generated."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_empty"
    logger = LlmInteractionLogger(log_dir, "test_gantt_empty", "test")
    path = logger.finalize(total_duration_ms=0)

    content = path.read_text(encoding="utf-8")
    assert "```mermaid" not in content, "Should not have a chart with no calls"
    assert "Pipeline Timeline" not in content, "Should not have a timeline section with no calls"
    print("PASS: test_gantt_chart_empty_durations")


def test_gantt_chart_exact_output():
    """Print the exact chart text so it can be visually verified."""
    log_dir = Path(tempfile.gettempdir()) / "test_gantt_exact"
    logger = LlmInteractionLogger(log_dir, "test_gantt_exact", "test")
    logger.log_local_call("plan", "qwen3:8b", "s", "u", "r", duration_ms=5000)
    logger.log_local_call("research_lead", "qwen3:8b", "s", "u", "r", duration_ms=12000)
    logger.log_local_call("page_generate", "qwen3:8b", "s", "u", "r", duration_ms=3000)
    logger.log_local_call("component_0_lead", "qwen3:8b", "s", "u", "r", duration_ms=8000)
    path = logger.finalize(total_duration_ms=28000)

    content = path.read_text(encoding="utf-8")
    chart_start = content.find("```mermaid")
    chart_end = content.find("```", chart_start + 3)
    chart_text = content[chart_start:chart_end + 3]

    expected_lines = [
        "```mermaid",
        "gantt",
        "    title Time per Step (28s total)",
        "    dateFormat HH:mm:ss",
        "    axisFormat %M:%S",
        "",
        "    plan (5s) :plan, 00:00:00, 00:00:05",
        "    research_lead (12s) :research_lead, 00:00:05, 00:00:17",
        "    page_generate (3s) :page_generate, 00:00:17, 00:00:20",
        "    component_0_lead (8s) :component_0_lead, 00:00:20, 00:00:28",
        "```",
    ]

    actual_lines = chart_text.strip().splitlines()

    print("=== EXACT CHART OUTPUT ===")
    for line in actual_lines:
        print(f"  {line}")
    print("=== EXPECTED ===")
    for line in expected_lines:
        print(f"  {line}")
    print("=== LINE-BY-LINE COMPARISON ===")

    for i, (actual, expected) in enumerate(zip(actual_lines, expected_lines)):
        match = actual.strip() == expected.strip()
        status = "MATCH" if match else "DIFF"
        print(f"  [{status}] line {i+1}")
        if not match:
            print(f"    expected: {expected!r}")
            print(f"    actual:   {actual!r}")

    assert len(actual_lines) == len(expected_lines), \
        f"Line count mismatch: expected {len(expected_lines)}, got {len(actual_lines)}"

    for i, (actual, expected) in enumerate(zip(actual_lines, expected_lines)):
        assert actual.strip() == expected.strip(), \
            f"Line {i+1} mismatch:\n  expected: {expected!r}\n  actual:   {actual!r}"

    print("PASS: test_gantt_chart_exact_output")


if __name__ == "__main__":
    test_gantt_chart_has_correct_format()
    test_gantt_chart_sequential_timestamps()
    test_gantt_chart_total_in_title()
    test_gantt_chart_sanitizes_names()
    test_gantt_chart_empty_durations()
    test_gantt_chart_exact_output()
    print("\nAll Gantt chart tests passed!")

