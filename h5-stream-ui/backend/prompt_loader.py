from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Built-in product default: which prompt files to skip when assembling system message.
# Adjust only here (or edit prompts/*.md) when shipping behavior changes — not via env/UI.
#
# - Empty set = load **all** `prompts/*.md`, including `09-chart-generation-echarts.md`
#   (full chart typing + ECharts mobile rules). Use this when可视化是主能力.
# - To shave TTFT only: e.g. `frozenset({"09-chart-generation-echarts.md"})`
#   — model can still draw charts from general knowledge + `08` §4, but less consistent.
SYSTEM_PROMPT_EXCLUDE_FILES: frozenset[str] = frozenset({
    # "01-role-and-task.md",
    # "02-input-handling.md",
    # "03-output-format.md",
    # "04-tailwind-and-stack.md",
    # "05-design-quality.md",
    # "05.1-core-design-principles.md",
    "06-interaction-dsl-v1.md",
    "07-harmony-static-style-spec.md",
    "08-special-data-processing.md",
    "09-chart-generation-echarts.md",
    "10-css-and-html-subsets-categories.md",
    "11-simplified-output.md",
    "12-scenario-specific.md"
})


def prompts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts"


def load_system_prompt(*, extra_paths: list[Path] | None = None) -> str:
    excludes = SYSTEM_PROMPT_EXCLUDE_FILES
    base = prompts_dir()
    files: list[Path] = sorted(
        [p for p in base.glob("*.md") if p.is_file() and p.name not in excludes],
        key=lambda p: p.name,
    )
    if extra_paths:
        for p in extra_paths:
            if p.is_file() and p.name not in excludes:
                files.append(p)
        files = sorted({p.resolve(): p for p in files}.values(), key=lambda p: p.name)
    sig = tuple((str(p.resolve()), p.stat().st_mtime_ns) for p in files)
    return _load_system_prompt_cached(sig)


@lru_cache(maxsize=32)
def _load_system_prompt_cached(sig: tuple[tuple[str, int], ...]) -> str:
    parts: list[str] = []
    for path_str, _mtime in sig:
        path = Path(path_str)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<!-- {path.name} -->\n{text}")
    return "\n\n".join(parts).strip()


def build_user_message(*, content: str) -> str:
    """Single user turn: instructions and raw data (JSON, text, mixed) in one block."""
    c = (content or "").strip()
    if not c:
        raise ValueError("content must not be empty")
    return "## User request\n" + c
