"""
LLM Interaction Logger — saves all LLM prompts and responses to markdown files.

Each generation session creates one markdown file containing:
- Session metadata (timestamp, user query, session ID)
- Every local LLM call: step name, model, system prompt, user prompt, response, token stats
- Every cloud LLM call: dimension, model, system prompt, user prompt, response, token stats
- A summary section at the end with overall statistics

Format is designed for human readability and debugging.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class LlmInteractionLogger:
    """Records all LLM interactions to a markdown file for a generation session."""

    def __init__(self, log_dir: Path, session_id: str, user_query: str, *, clock=None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._file_path = self.log_dir / f"{session_id}.md"
        self._clock = clock if clock is not None else time.monotonic  # injectable for deterministic tests
        self._call_index = 0
        self._total_local_calls = 0
        self._total_cloud_calls = 0
        self._total_tokens_spent = 0
        self._section_parts: list[str] = []
        self._call_records: list[dict] = []  # {step, start, end, duration_ms}
        self._pipeline_start = None  # set on first log call
        self._total_duration_ms = 0.0  # set by finalize()

        # Write header
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        safe_query = user_query[:300].replace("`", "'").replace("|", "/")
        header = f"""# LLM Interaction Log

**Session ID**: `{session_id}`
**Started**: {now}
**User Query**:
```
{safe_query}{"..." if len(user_query) > 300 else ""}
```

---

"""
        self._file_path.write_text(header, encoding="utf-8")

    # ── Public API ─────────────────────────────────────────────────

    def log_local_call(
        self,
        step_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        status: str = "success",
        error_message: str = "",
        duration_ms: float = 0.0,
        raw_response: str = "",
        finish_reason: str = "",
        api_prompt_tokens: int = 0,
        api_completion_tokens: int = 0,
    ) -> None:
        """Log a local LLM call (generation workflow)."""
        self._total_local_calls += 1
        self._total_tokens_spent += input_tokens + output_tokens
        self._call_index += 1
        self._record_call(step_name, duration_ms)

        section = self._build_call_section(
            call_index=self._call_index,
            title=f"Step: {step_name}",
            llm_type="Local LLM",
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
            is_json=self._is_json_response(response),
            raw_response=raw_response,
            finish_reason=finish_reason,
            api_prompt_tokens=api_prompt_tokens,
            api_completion_tokens=api_completion_tokens,
        )
        self._append(section)

    def log_cloud_call(
        self,
        dimension: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        status: str = "success",
        error_message: str = "",
        duration_ms: float = 0.0,
        raw_response: str = "",
        finish_reason: str = "",
        api_prompt_tokens: int = 0,
        api_completion_tokens: int = 0,
    ) -> None:
        """Log a cloud LLM call (verification workflow)."""
        self._total_cloud_calls += 1
        self._total_tokens_spent += input_tokens + output_tokens
        self._call_index += 1
        self._record_call(f"verify_{dimension}", duration_ms)

        section = self._build_call_section(
            call_index=self._call_index,
            title=f"Verify: {dimension}",
            llm_type="Cloud LLM",
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
            is_json=self._is_json_response(response),
            raw_response=raw_response,
            finish_reason=finish_reason,
            api_prompt_tokens=api_prompt_tokens,
            api_completion_tokens=api_completion_tokens,
        )
        self._append(section)

    def finalize(
        self,
        *,
        total_duration_ms: float = 0.0,
        steps_executed: list[str] | None = None,
        verification_passed: bool | None = None,
    ) -> Path:
        """Write the summary section and close the log file.

        Args:
            total_duration_ms: Actual wall-clock pipeline duration (not the
                sum of per-call durations, which overcounts in parallel mode).

        Returns the path to the completed log file.
        """
        self._total_duration_ms = total_duration_ms
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        summary = f"""

---

## 📊 Summary

| Metric | Value |
|--------|-------|
| **Completed** | {now} |
| **Total Duration** | {total_duration_ms / 1000:.1f}s |
| **Local LLM Calls** | {self._total_local_calls} |
| **Cloud LLM Calls** | {self._total_cloud_calls} |
| **Total Calls** | {self._call_index} |
| **Total Tokens** | ~{self._total_tokens_spent} |
"""
        if steps_executed:
            summary += f"| **Steps Executed** | {', '.join(steps_executed)} |\n"

        if verification_passed is not None:
            emoji = "✅ PASS" if verification_passed else "❌ FAIL"
            summary += f"| **Verification** | {emoji} |\n"

        # ── Gantt chart: time per step ──────────────────────────
        if self._call_records:
            chart = self._build_gantt_chart()
            summary += chart

        # ── Pie chart: time distribution by phase ─────────────
        if self._call_records:
            summary += self._build_pie_chart()

        summary += f"""
---

*Log file: `{self._file_path.name}`*
"""

        self._append(summary)
        return self._file_path

    # ── Internal ───────────────────────────────────────────────────

    def _record_call(self, step_name: str, duration_ms: float) -> None:
        """Record a call with actual start/end times for Gantt chart."""
        now = self._clock()
        start = now - (duration_ms / 1000)  # when the call started
        if self._pipeline_start is None or start < self._pipeline_start:
            self._pipeline_start = start
        self._call_records.append({
            "step": step_name,
            "start": start,
            "end": now,
            "duration_ms": duration_ms,
        })

    @property
    def _call_durations(self) -> list[tuple[str, float]]:
        """Backward-compatible property: returns [(step_name, duration_ms)]."""
        return [(r["step"], r["duration_ms"]) for r in self._call_records]

    def _build_gantt_chart(self) -> str:
        """Build a Mermaid Gantt chart showing actual start/end times per step."""
        from datetime import datetime, timedelta

        if not self._call_records:
            return ""

        # Find the pipeline start (earliest call start)
        t0 = min(r["start"] for r in self._call_records)

        # Use wall-clock duration from finalize() (falls back to sum of
        # durations when not set — backward compat for sequential callers).
        wall_clock_ms = getattr(self, "_total_duration_ms", 0) or sum(
            r["duration_ms"] for r in self._call_records
        )

        lines = [
            "\n## Pipeline Timeline\n",
            "```mermaid",
            "gantt",
            f"    title Time per Step ({wall_clock_ms / 1000:.0f}s total)",
            "    dateFormat HH:mm:ss",
            "    axisFormat %M:%S",
            "",
        ]

        start_epoch = datetime(2024, 1, 1, 0, 0, 0)

        for rec in self._call_records:
            clean = self._sanitize_gantt_name(rec["step"])
            dur_s = rec["duration_ms"] / 1000
            offset_start = rec["start"] - t0
            offset_end = rec["end"] - t0
            start_time = start_epoch + timedelta(seconds=offset_start)
            end_time = start_epoch + timedelta(seconds=offset_end)
            start_str = start_time.strftime("%H:%M:%S")
            end_str = end_time.strftime("%H:%M:%S")
            lines.append(f"    {clean} ({dur_s:.0f}s) :{clean}, {start_str}, {end_str}")

        lines.append("```")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_gantt_name(name: str) -> str:
        """Sanitize a step name for use as a Mermaid Gantt task label + id."""
        import re as _re
        clean = _re.sub(r"[^\w\s]", "", name).strip().replace(" ", "_")
        return clean[:30] if len(clean) > 30 else clean

    def _build_pie_chart(self) -> str:
        """Build a Mermaid pie chart showing time distribution by phase."""
        groups: dict[str, list[float]] = {}  # group_name -> [total_ms, count]
        for rec in self._call_records:
            key = rec["step"].split("_")[0].capitalize()
            if key not in groups:
                groups[key] = [0, 0]
            groups[key][0] += rec["duration_ms"]
            groups[key][1] += 1

        llm_total_ms = sum(d[0] for d in groups.values())
        wall_clock_ms = getattr(self, "_total_duration_ms", 0) or llm_total_ms

        lines = [
            "\n## Time Distribution\n", "```mermaid",
            f"pie title Time by Phase ({llm_total_ms / 1000:.0f}s LLM total)",
            "",
        ]

        for group, (dur_ms, count) in groups.items():
            if dur_ms > 0:
                dur_s = int(dur_ms / 1000)
                label = f"{group} ({count})" if count > 1 else group
                lines.append(f'    "{label}" : {dur_s}')

        lines += ["```", ""]

        if wall_clock_ms < llm_total_ms:
            lines.append(
                f"*Wall-clock: {wall_clock_ms / 1000:.0f}s "
                f"(parallel overlap saved "
                f"{(llm_total_ms - wall_clock_ms) / 1000:.0f}s)*\n"
            )

        return "\n".join(lines)

    def _build_call_section(
        self,
        call_index: int,
        title: str,
        llm_type: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response: str,
        input_tokens: int,
        output_tokens: int,
        status: str,
        error_message: str,
        duration_ms: float,
        is_json: bool,
        raw_response: str = "",
        finish_reason: str = "",
        api_prompt_tokens: int = 0,
        api_completion_tokens: int = 0,
    ) -> str:
        """Build a markdown section for a single LLM call."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        status_emoji = {"success": "✅", "error": "❌", "skipped": "⏭️"}.get(status, "⚠️")
        budget_note = ""
        if input_tokens > 0:
            budget_note = f" | Budget: {input_tokens} → {input_tokens + output_tokens} total"

        # Finish reason indicator
        finish_display = ""
        if finish_reason:
            finish_emoji = {"stop": "✅", "length": "⚠️ TRUNCATED", "content_filter": "🚫 FILTERED"}.get(
                finish_reason, finish_reason
            )
            finish_display = f" | Finish: {finish_emoji}"

        # Token accuracy note
        token_note = ""
        if api_prompt_tokens > 0:
            diff = api_prompt_tokens - input_tokens
            sign = "+" if diff > 0 else ""
            token_note = f"API: {api_prompt_tokens} prompt, {api_completion_tokens} completion"

        section = f"""

---

### #{call_index} — {title} {status_emoji}

| Field | Value |
|-------|-------|
| **Type** | {llm_type} |
| **Model** | `{model}` |
| **Timestamp** | {timestamp} |
| **Status** | {status_emoji} {status}{budget_note}{finish_display} |
| **Duration** | {duration_ms:.0f}ms |
| **Input Tokens** | ~{input_tokens} |
| **Output Tokens** | ~{output_tokens} |
"""

        if token_note:
            section += f"| **API Token Usage** | {token_note} |\n"

        if error_message:
            section += f"""
> ⚠️ **Error**: {error_message}
"""

        # System prompt
        section += f"""
<details>
<summary><b>📥 System Prompt</b> ({len(system_prompt)} chars)</summary>

{self._code_block(system_prompt, "markdown")}

</details>
"""

        # User prompt
        section += f"""
<details>
<summary><b>📥 User Prompt</b> ({len(user_prompt)} chars)</summary>

{self._code_block(user_prompt, "markdown")}

</details>
"""

        # Raw response (pre-stripping) — show when different from final response
        if raw_response and raw_response != response:
            raw_len = len(raw_response)
            stripped_len = len(response)
            thinking_pct = ((raw_len - stripped_len) / max(raw_len, 1)) * 100
            section += f"""
<details>
<summary><b>📤 Raw Response</b> ({raw_len} chars — before think-tag stripping, ~{thinking_pct:.0f}% thinking)</summary>

{self._code_block(raw_response, "")}

</details>
"""

        # Final response (after stripping)
        resp_lang = "json" if is_json else "html"
        section += f"""
<details>
<summary><b>📤 Response (stripped)</b> ({len(response)} chars)</summary>

{self._code_block(response, resp_lang)}

</details>
"""

        return section

    @staticmethod
    def _code_block(content: str, lang: str = "") -> str:
        """Wrap content in a markdown code block, escaping nested backticks."""
        # Escape triple backticks inside the content
        safe = content.replace("```", "\\`\\`\\`")
        # Limit length for readability (still expandable via details)
        if len(safe) > 8000:
            truncated_notice = f"\n\n*... (truncated from {len(safe)} chars — see full output in RAW panel)*"
            safe = safe[:8000] + truncated_notice
        return f"```{lang}\n{safe}\n```"

    @staticmethod
    def _is_json_response(text: str) -> bool:
        """Check if the response is JSON (for syntax highlighting)."""
        stripped = text.strip()
        return stripped.startswith("{") or stripped.startswith("[")

    def _append(self, text: str) -> None:
        """Append text to the log file."""
        with open(self._file_path, "a", encoding="utf-8") as f:
            f.write(text)


def create_session_id() -> str:
    """Create a unique session ID based on the current timestamp."""
    now = datetime.now()
    return now.strftime("%Y%m%d_%H%M%S") + f"_{int(time.monotonic() * 1000) % 1000:03d}"
