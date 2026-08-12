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

    def __init__(self, log_dir: Path, session_id: str, user_query: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._file_path = self.log_dir / f"{session_id}.md"
        self._call_index = 0
        self._total_local_calls = 0
        self._total_cloud_calls = 0
        self._total_tokens_spent = 0
        self._section_parts: list[str] = []
        self._call_durations: list[tuple[str, float]] = []

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
        self._call_durations.append((step_name, duration_ms))

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
        self._call_durations.append((f"verify_{dimension}", duration_ms))

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

        Returns the path to the completed log file.
        """
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
        if self._call_durations:
            chart = self._build_gantt_chart()
            summary += chart

        summary += f"""
---

*Log file: `{self._file_path.name}`*
"""

        self._append(summary)
        return self._file_path

    # ── Internal ───────────────────────────────────────────────────

    def _build_gantt_chart(self) -> str:
        """Build a Mermaid Gantt chart showing the time per step."""
        import re as _re

        lines = [
            "\n## ⏱️ Pipeline Timeline\n",
            "```mermaid",
            f"gantt",
            f"    title Time per Step ({sum(d for _, d in self._call_durations) / 1000:.0f}s total)",
            f"    dateFormat X",
            f"    axisFormat %S s",
            "",
        ]

        cumulative_ms = 0
        for step_name, dur_ms in self._call_durations:
            # Sanitize step name for Mermaid (no emoji, no special chars)
            clean = _re.sub(r"[^\w\s]", "", step_name).strip().replace(" ", "_")
            dur_s = dur_ms / 1000
            start_ms = int(cumulative_ms)
            end_ms = int(cumulative_ms + dur_ms)
            cumulative_ms = end_ms
            # Mermaid Gantt: task_name :id, start, end (Unix ms with dateFormat X)
            lines.append(f"    {clean} ({dur_s:.0f}s) :{clean}, {start_ms}, {end_ms}")

        lines.append("```")
        lines.append("")
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
