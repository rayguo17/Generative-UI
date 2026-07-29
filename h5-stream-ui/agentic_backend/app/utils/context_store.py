"""
Context Store — file-based storage for detailed user input.

When the orchestrator summarises long input, the full original is saved here.
The plan agent can search it later to retrieve specific details (URLs, names,
descriptions) that were necessarily omitted from the summary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ContextStore:
    """Stores and retrieves detailed user input by session ID."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Write ────────────────────────────────────────────────────

    def save(self, session_id: str, full_text: str, metadata: dict | None = None) -> Path:
        """Save the full original user input for a session.

        Returns the path to the saved file.
        """
        meta = metadata or {}
        meta.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
        meta.setdefault("char_count", len(full_text))

        file_path = self.base_dir / f"{session_id}_full.md"
        lines = [
            f"# Original User Input — {session_id}",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Saved** | {meta['saved_at']} |",
            f"| **Characters** | {meta['char_count']} |",
        ]
        for k, v in meta.items():
            if k not in ("saved_at", "char_count"):
                lines.append(f"| **{k}** | {v} |")

        lines += [
            "",
            "---",
            "",
            full_text,
        ]
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return file_path

    # ── Read / search ────────────────────────────────────────────

    def load(self, session_id: str) -> Optional[str]:
        """Load the full original input for a session."""
        path = self.base_dir / f"{session_id}_full.md"
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        # Strip the header (everything before ---)
        sep = text.find("\n---\n")
        if sep > 0:
            return text[sep + 5:].strip()
        return text

    def search(
        self,
        session_id: str,
        query: str,
        max_snippets: int = 5,
    ) -> list[dict]:
        """Search the full input for specific content.

        Used by the plan agent to find details omitted from the summary.

        Returns a list of dicts with `heading` and `snippet` keys.
        """
        full = self.load(session_id)
        if not full:
            return []

        results: list[dict] = []
        query_lower = query.lower()

        # Split into logical blocks (headings, paragraphs, list items)
        blocks = re.split(r'\n(?=## |\n(?:[-*]|\d+\.) )', full)

        for block in blocks:
            if query_lower in block.lower():
                # Extract a sensible heading
                lines = block.strip().split("\n")
                heading = lines[0].strip("# ")[:80] if lines else ""

                # Snippet: first ~300 chars, with query highlighted
                snippet = block.strip()[:400]

                results.append({
                    "heading": heading,
                    "snippet": snippet,
                    "match_at": block.lower().find(query_lower),
                })

                if len(results) >= max_snippets:
                    break

        # Sort by best match (position in block)
        results.sort(key=lambda r: r.get("match_at", 9999))
        return results

    def extract_urls(self, session_id: str) -> list[str]:
        """Extract all image/video URLs from the full input."""
        full = self.load(session_id)
        if not full:
            return []

        url_pattern = re.compile(
            r'https?://[^\s<>"\')\]]+\.(?:jpg|jpeg|png|gif|webp|svg|mp4|webm|mov)'
            r'|https?://[^\s<>"\')\]]+(?:youtube\.com|youtu\.be)[^\s<>"\')\]]*'
            r'|https?://[^\s<>"\')\]]*',
            re.IGNORECASE,
        )
        return list(set(match.group(0) for match in url_pattern.finditer(full)))

    def extract_sections(self, session_id: str) -> list[dict]:
        """Extract structured sections (headings + content) for LLM lookup."""
        full = self.load(session_id)
        if not full:
            return []

        sections: list[dict] = []
        # Match markdown headings and their content
        pattern = re.compile(r'^(#{1,3})\s+(.+?)$\n(.*?)(?=^#{1,3}\s|\Z)', re.MULTILINE | re.DOTALL)
        for match in pattern.finditer(full):
            level = len(match.group(1))
            title = match.group(2).strip()
            body = match.group(3).strip()[:500]
            sections.append({
                "level": level,
                "title": title,
                "body": body,
            })
        return sections
