"""
Plan Quality Metrics — records every plan generation attempt for observability.

Writes JSONL (one JSON object per line) to logs/plan_metrics.jsonl so you can
analyse failure patterns over time with simple tools (grep, jq, pandas).

Tracks:
  - Every attempt (initial + regenerations)
  - Parse success/failure and the specific failure reasons
  - Whether regeneration fixed the issues
  - Token usage and duration per attempt
  - Which quality checks triggered most often
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class PlanMetricsRecorder:
    """Records every plan generation attempt to a JSONL file."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self.log_dir / "plan_metrics.jsonl"

    def record_attempt(
        self,
        *,
        session_id: str,
        attempt: int,
        success: bool,
        failure_reasons: list[str] | None = None,
        parse_failed: bool = False,
        regenerate_succeeded: bool | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: float = 0.0,
        card_type: str = "",
        section_count: int = 0,
        binding_count: int = 0,
        model: str = "",
        query_preview: str = "",
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id,
            "attempt": attempt,
            "success": success,
            "parse_failed": parse_failed,
            "failure_reasons": failure_reasons or [],
            "regenerate_succeeded": regenerate_succeeded,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": round(duration_ms),
            "card_type": card_type,
            "section_count": section_count,
            "binding_count": binding_count,
            "model": model,
            "query_preview": query_preview[:80],
        }

        with open(self._file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_summary(self, last_n: int = 100) -> dict:
        """Aggregate stats from the most recent N attempts."""
        entries = self._read_last_n(last_n)
        if not entries:
            return {"total": 0}

        total = len(entries)
        successes = sum(1 for e in entries if e.get("success"))
        parse_failures = sum(1 for e in entries if e.get("parse_failed"))
        retries = sum(1 for e in entries if e.get("attempt", 0) > 0)
        retry_successes = sum(
            1 for e in entries
            if e.get("attempt", 0) > 0 and e.get("regenerate_succeeded")
        )

        reason_counts: dict[str, int] = {}
        for e in entries:
            for reason in e.get("failure_reasons", []):
                # Normalise: use the error code if present
                code = reason.split(":")[0].strip() if ":" in reason else reason[:40]
                reason_counts[code] = reason_counts.get(code, 0) + 1

        avg_input = sum(e.get("input_tokens", 0) for e in entries) / max(total, 1)
        avg_output = sum(e.get("output_tokens", 0) for e in entries) / max(total, 1)
        avg_duration = sum(e.get("duration_ms", 0) for e in entries) / max(total, 1)

        return {
            "total_attempts": total,
            "success_rate": f"{(successes / total) * 100:.1f}%",
            "parse_failure_rate": f"{(parse_failures / total) * 100:.1f}%",
            "regenerations_triggered": retries,
            "regeneration_success_rate": (
                f"{(retry_successes / max(retries, 1)) * 100:.1f}%"
                if retries > 0 else "N/A"
            ),
            "top_failure_reasons": sorted(
                reason_counts.items(), key=lambda x: -x[1]
            )[:5],
            "avg_input_tokens": round(avg_input),
            "avg_output_tokens": round(avg_output),
            "avg_duration_ms": round(avg_duration),
        }

    def print_summary(self) -> None:
        """Log a human-readable summary."""
        s = self.get_summary()
        if s.get("total", 0) == 0:
            return
        logger.info(
            "PLAN METRICS (last %d attempts) | success=%s | parse_fail=%s | "
            "regens=%d (%s success) | avg_in=%dtok avg_out=%dtok avg=%dms | "
            "top_failures: %s",
            s.get("total_attempts", 0),
            s.get("success_rate", "N/A"),
            s.get("parse_failure_rate", "N/A"),
            s.get("regenerations_triggered", 0),
            s.get("regeneration_success_rate", "N/A"),
            s.get("avg_input_tokens", 0),
            s.get("avg_output_tokens", 0),
            s.get("avg_duration_ms", 0),
            s.get("top_failure_reasons", []),
        )

    def _read_last_n(self, n: int) -> list[dict]:
        if not self._file_path.is_file():
            return []
        entries: list[dict] = []
        with open(self._file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries[-n:] if len(entries) > n else entries
