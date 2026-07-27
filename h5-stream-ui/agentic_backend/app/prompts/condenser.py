"""
Prompt condensation — extracts imperative rules from full prompt files to fit within
the 4K token budget of the local LLM.

Strategy:
1. Read the original markdown prompt file
2. Extract all lines containing MUST/mandatory/required/forbidden/Do not/Never
3. Keep code blocks with exact class names, color values, DSL syntax
4. Drop narrative exposition, examples, rationale ("Why" sections)
5. Format as dense bullet list with rule references
6. If still over target token budget, drop lower-priority rules (warnings before errors)
"""

from __future__ import annotations

import re
from pathlib import Path

from app.utils.token_counter import count_tokens


class PromptCondenser:
    """Condenses prompt files to fit within token budgets."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._cache: dict[str, str] = {}

    def condense(self, filename: str, target_tokens: int | None = None) -> str:
        """Load a condensed prompt file.

        If a pre-condensed version exists in generation/prompts/, use it directly.
        These are hand-crafted condensed versions of the original prompts.

        Args:
            filename: The condensed prompt filename (e.g. "analyze_system.md").
            target_tokens: If provided, further trim to fit within this budget.

        Returns:
            The condensed prompt text.
        """
        cache_key = f"{filename}:{target_tokens}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        filepath = self.base_dir / filename
        if not filepath.is_file():
            raise FileNotFoundError(f"Condensed prompt not found: {filepath}")

        text = filepath.read_text(encoding="utf-8").strip()

        if target_tokens is not None:
            current = count_tokens(text)
            if current > target_tokens:
                text = self._trim_to_budget(text, target_tokens)

        self._cache[cache_key] = text
        return text

    @staticmethod
    def condense_full_prompt(original_path: Path, target_tokens: int) -> str:
        """Condense an original full prompt file to fit within target_tokens.

        This is used when building condensed prompts from the original files.
        Applies rule extraction heuristics.

        Args:
            original_path: Path to the original markdown prompt file.
            target_tokens: Maximum token count for the condensed output.

        Returns:
            Condensed prompt text.
        """
        if not original_path.is_file():
            return ""

        text = original_path.read_text(encoding="utf-8").strip()
        lines = text.split("\n")

        # Priority extraction patterns (ordered by importance)
        priority_patterns = [
            # Priority 1: Explicit mandatory/forbidden rules
            (re.compile(r'(?:MUST|must not|must|必|必须|禁止)', re.IGNORECASE), 1),
            # Priority 2: Strong directives
            (re.compile(r'(?:Do not|Never|Always|ensure|require)', re.IGNORECASE), 2),
            # Priority 3: Code blocks (class names, values, DSL syntax)
            (re.compile(r'^```|^`[^`]+`$|`[^`]+`'), 3),
            # Priority 4: Key specifications (colors, sizes, fonts)
            (re.compile(r'(?:#(?:[0-9a-fA-F]{3}){1,2}\b|(?:\d+)px|HarmonyOS|rounded-\[)', re.IGNORECASE), 4),
        ]

        scored_lines: list[tuple[int, int, str]] = []  # (priority, line_index, line)
        in_code_block = False

        for i, line in enumerate(lines):
            # Track code block state
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                scored_lines.append((1, i, line))  # Always keep code block markers
                continue

            if in_code_block:
                scored_lines.append((1, i, line))  # Always keep code block content
                continue

            # Score the line
            best_priority = 5  # Default (lowest priority)
            for pattern, priority in priority_patterns:
                if pattern.search(line):
                    best_priority = min(best_priority, priority)
                    if best_priority == 1:
                        break  # Already highest priority

            scored_lines.append((best_priority, i, line))

        # Build condensed output: include priority 1-3 lines, skip priority 4-5
        # Start with most important, add until we hit the token budget
        condensed_lines: list[str] = []
        current_tokens = 0

        for priority in [1, 2, 3]:
            for p, _, line in scored_lines:
                if p == priority:
                    condensed_lines.append(line)
                    # Re-check token budget every few lines
                    if len(condensed_lines) % 10 == 0:
                        current_tokens = count_tokens("\n".join(condensed_lines))
                        if current_tokens > target_tokens:
                            break
            if current_tokens > target_tokens:
                break

        result = "\n".join(condensed_lines)
        tokens = count_tokens(result)

        # If still over budget, trim line by line from the end
        if tokens > target_tokens:
            while condensed_lines and count_tokens("\n".join(condensed_lines)) > target_tokens:
                condensed_lines.pop()

        return "\n".join(condensed_lines).strip()

    @staticmethod
    def _trim_to_budget(text: str, target_tokens: int) -> str:
        """Aggressively trim text to fit within target_tokens."""
        lines = text.split("\n")
        while lines and count_tokens("\n".join(lines)) > target_tokens:
            # Remove the last non-empty, non-critical line
            removed = False
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                # Don't remove lines with key syntax
                if line and not line.startswith("#") and "`" not in line:
                    lines.pop(i)
                    removed = True
                    break
            if not removed:
                # If nothing else, remove last line
                lines.pop()
        return "\n".join(lines)
