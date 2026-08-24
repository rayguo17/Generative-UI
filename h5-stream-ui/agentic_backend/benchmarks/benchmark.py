#!/usr/bin/env python
"""
End-to-end Benchmark Tool for the Agentic UI Generation Pipeline.

Runs the full pipeline (plan -> research -> page shell -> components -> assemble)
with configurable parameters, collects per-step metrics, and saves structured results.

Loosely coupled: only imports 4 stable public modules (config, prompts.loader,
context_store, orchestrator). Never imports researcher/composer/content_retriever/etc.

Usage:
  # Single run with inline query
  python benchmarks/benchmark.py --query "Make a one day travel itinerary to hangzhou"

  # Run with JSON config (reproducible)
  python benchmarks/benchmark.py --config travel_plan.json

  # CLI args override config
  python benchmarks/benchmark.py --config default.json --token-budget 8000

  # Multiple configs in one invocation (sequential, independent)
  python benchmarks/benchmark.py --config a.json --config b.json --config c.json

  # Compare all results in the output dir
  python benchmarks/benchmark.py --compare

  # Compare specific runs
  python benchmarks/benchmark.py --compare bench_001 bench_002
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# Resolve project paths relative to this file so the script runs from any CWD.
_BENCH_DIR = Path(__file__).resolve().parent            # .../agentic_backend/benchmarks
_BACKEND_DIR = _BENCH_DIR.parent                        # .../agentic_backend (app package root)
_CONFIGS_DIR = _BENCH_DIR / "benchmark_configs"         # bundled benchmark configs
_RESULTS_DIR = _BENCH_DIR / "results"                   # benchmark output

# Ensure the app package is importable (backend dir on sys.path)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _resolve_config_path(cfg_path: str) -> Path:
    """Resolve a config path against CWD, then the bundled configs dir.

    Lets users write `--config travel_plan.json` or
    `--config benchmark_configs/travel_plan.json` from any working directory.
    """
    p = Path(cfg_path)
    if p.is_file():
        return p
    rel = p.as_posix()
    if rel.startswith("benchmark_configs/"):
        rel = rel[len("benchmark_configs/"):]
    candidate = _CONFIGS_DIR / rel
    if candidate.is_file():
        return candidate
    # Fall back to the original path so from_json() raises a clear error
    return p

# ── Only stable public modules ───────────────────────────────────────
from app.config import AppConfig, LlmConfig, load_config
from app.prompts.loader import PromptLoader
from app.utils.context_store import ContextStore
from app.generation.orchestrator import GenerationOrchestrator
from app.utils.llm_logger import LlmInteractionLogger, create_session_id


# ════════════════════════════════════════════════════════════════════
# BenchmarkConfig — all tunable parameters per run
# ════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkConfig:
    """All parameters that can be changed per benchmark run."""
    query: str = ""
    model: str = ""                        # single model for all stages (if models not set)
    models: dict = None                   # per-stage: {"plan": "...", "research": "...", "page_shell": "...", "components": "..."}
    base_url: str = ""
    api_key: str = ""
    token_budget: int = 0       # 0 = use env default
    temperature: float = -1     # -1 = use env default
    output_reserve: int = 0     # 0 = use env default
    plan_file: str = ""
    no_think_enabled: bool = True
    no_think_directive: str = "/no_think"
    parallel: bool = False       # run research + page shell concurrently, then components concurrently
    output_dir: str = str(_RESULTS_DIR)
    session_id: str = ""        # auto-generated if empty
    tag: str = ""               # human-readable label for comparison

    @classmethod
    def from_json(cls, path: str | Path) -> "BenchmarkConfig":
        """Load config from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def override_from_args(self, args: argparse.Namespace) -> "BenchmarkConfig":
        """Override config fields from CLI args (only non-None/non-default values)."""
        if args.query:
            self.query = args.query
        if args.model:
            self.model = args.model
        if args.base_url:
            self.base_url = args.base_url
        if args.api_key:
            self.api_key = args.api_key
        if args.token_budget > 0:
            self.token_budget = args.token_budget
        if args.temperature >= 0:
            self.temperature = args.temperature
        if args.output_reserve > 0:
            self.output_reserve = args.output_reserve
        if args.plan_file:
            self.plan_file = args.plan_file
        if args.no_think is not None:
            self.no_think_enabled = args.no_think
        if args.tag:
            self.tag = args.tag
        if args.parallel:
            self.parallel = True
        return self


# ════════════════════════════════════════════════════════════════════
# Run a single benchmark
# ════════════════════════════════════════════════════════════════════

# ── Quality analysis ─────────────────────────────────────────────────

import re as _re

# Allowed theme utility classes (anything else is "invalid")
_VALID_CLASS_RE = _re.compile(
    r'^(bg-page|bg-surface|bg-elevated|border-default|'
    r'text-heading|text-primary|text-secondary|text-tertiary|'
    r'bg-accent|text-accent|border-accent|'
    r'bg-success|text-success|bg-warning|text-warning|'
    r'bg-error|text-error|bg-info|text-info)$'
)

# Invalid color patterns: bg-white, text-gray-*, dark:bg-gray-*, text-blue-*, etc.
_INVALID_COLOR_RE = _re.compile(
    r'\b('
    r'bg-(?:white|black|gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-?\d*'
    r'|text-(?:white|black|gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-?\d*'
    r'|dark:(?:bg|text)-(?:white|black|gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-?\d*'
    r'|\[color:var\('
    r')',
    _re.IGNORECASE,
)


def _parse_log_steps(log_path: Path) -> list[dict]:
    """Parse the interaction log to extract per-step response text.

    Returns a list of dicts: ``{step, response}``.
    """
    if not log_path or not log_path.is_file():
        return []

    text = log_path.read_text(encoding="utf-8")
    steps: list[dict] = []

    # Match: ### #N — Step: {step_name} ... Response (stripped)</b> ... ```...\n{content}\n```
    for m in _re.finditer(
        r'### #\d+ \u2014 Step: (.+?)(?:\s+\S+)?\s*\n.*?Response \(stripped\)</b>.*?```[a-z]*\n(.*?)```',
        text,
        _re.DOTALL,
    ):
        step_name = m.group(1).strip()
        response = m.group(2)
        steps.append({"step": step_name, "response": response})

    return steps


def _count_na(text: str) -> int:
    """Count 'N/A' occurrences (case-insensitive, word-boundary)."""
    return len(_re.findall(r'\bN/?A\b', text, _re.IGNORECASE))


def _count_picsum(text: str) -> int:
    """Count picsum.photos URL occurrences."""
    return len(_re.findall(r'picsum\.photos', text, _re.IGNORECASE))


def _find_invalid_classes(html: str) -> list[str]:
    """Find CSS classes that violate the theme palette rules."""
    # Extract all class="..." values
    class_attrs = _re.findall(r'class\s*=\s*"([^"]*)"', html)
    invalid: list[str] = []
    for cls_attr in class_attrs:
        for token in cls_attr.split():
            if _INVALID_COLOR_RE.match(token):
                invalid.append(token)
    return invalid


def _count_inline_styles(html: str) -> int:
    """Count style="..." attributes (should be minimal)."""
    return len(_re.findall(r'\sstyle\s*=\s*"', html, _re.IGNORECASE))


def _analyze_quality(html: str, log_path: Path) -> dict:
    """Analyze the generated HTML and interaction log for quality issues.

    Checks:
      - N/A values in research step responses (per step)
      - picsum.photos placeholder images in component output and final HTML
      - Invalid CSS classes (bg-white, text-gray-*, etc.) in final HTML
      - Inline style attributes (should be minimal)
      - Broken/unreachable image URLs (from component image check)

    Returns a dict with totals and per-step breakdown.
    """
    steps = _parse_log_steps(log_path)

    per_step_issues: list[dict] = []
    total_na = 0
    total_picsum_in_steps = 0

    for s in steps:
        step = s["step"]
        resp = s["response"]

        na_count = _count_na(resp)
        picsum_count = _count_picsum(resp)
        invalid_classes = _find_invalid_classes(resp)

        issues: dict = {"step": step}
        if na_count:
            issues["na_count"] = na_count
            total_na += na_count
        if picsum_count:
            issues["picsum_count"] = picsum_count
            total_picsum_in_steps += picsum_count
        if invalid_classes:
            issues["invalid_classes"] = invalid_classes

        if len(issues) > 1:  # has issues beyond just "step"
            per_step_issues.append(issues)

    # Final HTML analysis
    picsum_in_html = _count_picsum(html)
    invalid_in_html = _find_invalid_classes(html)
    inline_styles = _count_inline_styles(html)

    # Image URLs in final HTML
    img_urls = _re.findall(r'<img[^>]*\ssrc\s*=\s*["\']([^"\']+)["\']', html, _re.IGNORECASE)
    external_imgs = [u for u in img_urls if u.startswith(("http://", "https://"))]

    return {
        "na_count_total": total_na,
        "picsum_count_total": total_picsum_in_steps + picsum_in_html,
        "picsum_in_html": picsum_in_html,
        "invalid_classes_in_html": invalid_in_html,
        "invalid_classes_count": len(invalid_in_html),
        "inline_style_count": inline_styles,
        "image_count": len(img_urls),
        "external_image_count": len(external_imgs),
        "per_step": per_step_issues,
    }


# ════════════════════════════════════════════════════════════════════
# Run benchmark
# ════════════════════════════════════════════════════════════════════

async def run_benchmark(config: BenchmarkConfig) -> dict:
    """Run the pipeline with the given config and collect metrics.

    Returns a results dict with all metrics + file paths.
    """
    if not config.query:
        raise ValueError("Query is required (set 'query' in config or use --query)")

    # Suppress "Event loop is closed" errors from httpx/OpenAI async cleanup
    import asyncio as _asyncio
    _loop = _asyncio.get_event_loop()
    _orig_handler = _loop.get_exception_handler()
    def _suppress_httpx_cleanup(loop, context):
        exc = context.get("exception")
        if exc and "Event loop is closed" in str(exc):
            return  # Suppress — cosmetic error from AsyncOpenAI connection pool cleanup
        loop.default_exception_handler(context)
    _loop.set_exception_handler(_suppress_httpx_cleanup)

    # Build AppConfig first to know the effective token budget
    base_config = load_config()
    if config.token_budget > 0:
        base_config.token_budget = config.token_budget
    if config.temperature >= 0:
        base_config.temperature = config.temperature
    if config.output_reserve > 0:
        base_config.output_reserve = config.output_reserve
    base_config.no_think_enabled = config.no_think_enabled
    base_config.no_think_directive = config.no_think_directive

    model_name = config.model or base_config.local.model
    # Use tag in the session_id if provided, otherwise include model + parallel
    if config.tag:
        session_id = config.session_id or f"bench_{int(time.time())}_{config.tag}"
    else:
        parallel_tag = "_parallel" if config.parallel else ""
        session_id = config.session_id or f"bench_{int(time.time())}_{model_name}{parallel_tag}_{base_config.token_budget}tok"
    output_dir = Path(config.output_dir) / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Build pipeline components (public API only) ───────────────
    prompt_loader = PromptLoader(
        condensed_dir=base_config.condensed_prompts_dir,
        full_prompts_dir=base_config.prompts_dir,
    )
    context_store = ContextStore(_BACKEND_DIR / "context_store")
    logger = LlmInteractionLogger(
        log_dir=output_dir,
        session_id=session_id,
        user_query=config.query,
    )

    orchestrator = GenerationOrchestrator(
        config=base_config,
        prompt_loader=prompt_loader,
    )

    # ── Run the pipeline ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Benchmark: {session_id}")
    print(f"  Tag: {config.tag or '(none)'}")
    print(f"  Query: {config.query[:80]}{'...' if len(config.query) > 80 else ''}")
    if config.models:
        print(f"  Models: plan={config.models.get('plan', config.model or 'default')}")
        print(f"         research={config.models.get('research', config.model or 'default')}")
        print(f"         page_shell={config.models.get('page_shell', config.model or 'default')}")
        print(f"         components={config.models.get('components', config.model or 'default')}")
    else:
        print(f"  Model: {config.model or base_config.local.model}")
    print(f"  Token Budget: {base_config.token_budget}")
    print(f"  Parallel: {config.parallel}")
    print(f"  Plan File: {config.plan_file or '(none — plan will run)'}")
    print(f"{'='*60}\n")

    # ── Live progress callback ───────────────────────────────────
    pipeline_start = time.monotonic()

    async def progress_callback(event_type: str, content: str, phase: str, message: str = ""):
        elapsed = time.monotonic() - pipeline_start
        if event_type == "phase_start":
            print(f"  [{elapsed:7.1f}s] >> START  {phase} - {message}")
        elif event_type == "phase_progress":
            print(f"  [{elapsed:7.1f}s]    ...    {phase} - {message}")
        elif event_type == "phase_end":
            print(f"  [{elapsed:7.1f}s] << DONE   {phase}")
        elif event_type == "token":
            pass
        elif event_type == "error":
            print(f"  [{elapsed:7.1f}s] !! ERROR  {message}")
        sys.stdout.flush()

    # Suppress thinking-token warnings (they interleave with progress output in parallel mode)
    import logging as _logging
    _llm_logger = _logging.getLogger("app.shared.llm_client")
    _orig_level = _llm_logger.level
    _llm_logger.setLevel(_logging.ERROR)  # suppress WARNING-level thinking messages

    t_start = time.monotonic()

    html = await orchestrator.generate(
        query=config.query,
        override_model=config.model or None,
        override_base_url=config.base_url or None,
        override_api_key=config.api_key or None,
        sse_callback=progress_callback,
        interaction_logger=logger,
        session_id=session_id,
        plan_file=config.plan_file or None,
        models=config.models or None,
        parallel=config.parallel,
    )

    elapsed = time.monotonic() - t_start

    template_path = _BENCH_DIR / "chat_content.html"
    if template_path.is_file():
        template = template_path.read_text(encoding="utf-8")
        html = template.replace("{content}", html)

    # ── Finalize the log ──────────────────────────────────────────
    log_path = logger.finalize(
        total_duration_ms=elapsed * 1000,
        steps_executed=orchestrator.steps_executed,
    )

    # ── Collect metrics ───────────────────────────────────────────
    _llm_logger.setLevel(_orig_level)  # restore logging level

    per_step = []
    for step_name, dur_ms in logger._call_durations:
        per_step.append({
            "step": step_name,
            "duration_s": round(dur_ms / 1000, 1),
        })

    results = {
        "session_id": session_id,
        "tag": config.tag,
        "config": {
            "query": config.query,
            "model": config.model or base_config.local.model,
            "models": config.models,
            "token_budget": base_config.token_budget,
            "temperature": base_config.temperature,
            "plan_file": config.plan_file,
            "no_think_enabled": config.no_think_enabled,
            "parallel": config.parallel,
        },
        "metrics": {
            "total_duration_s": round(elapsed, 1),
            "total_tokens": orchestrator.total_tokens,
            "llm_call_count": logger._call_index,
            "steps_executed": orchestrator.steps_executed,
            "was_summarised": getattr(orchestrator, 'was_summarised', False),
            "html_length": len(html),
            "html_starts_with_tag": html.strip().startswith("<"),
        },
        "quality": _analyze_quality(html, log_path),
        "per_step": per_step,
        "files": {
            "results_json": str(output_dir / "results.json"),
            "html": str(output_dir / "output.html"),
            "log": str(log_path),
        },
    }

    # ── Save results ──────────────────────────────────────────────
    results_path = output_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    html_path = output_dir / "output.html"
    html_path.write_text(html, encoding="utf-8")

    # ── Print summary ─────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  Benchmark Complete: {session_id}")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Tokens: {orchestrator.total_tokens}")
    print(f"  LLM Calls: {logger._call_index}")
    print(f"  Steps: {', '.join(orchestrator.steps_executed)}")
    print(f"  HTML: {len(html)} chars")

    # Quality summary
    q = results.get("quality", {})
    if q:
        print(f"\n  Quality:")
        print(f"    N/A values:        {q.get('na_count_total', 0)}")
        print(f"    Picsum images:     {q.get('picsum_count_total', 0)}")
        print(f"    Invalid classes:   {q.get('invalid_classes_count', 0)}")
        print(f"    Inline styles:     {q.get('inline_style_count', 0)}")
        print(f"    Images in HTML:    {q.get('image_count', 0)} ({q.get('external_image_count', 0)} external)")
        for s in q.get("per_step", []):
            parts = []
            if "na_count" in s:
                parts.append(f"N/A={s['na_count']}")
            if "picsum_count" in s:
                parts.append(f"picsum={s['picsum_count']}")
            if "invalid_classes" in s:
                parts.append(f"bad_classes={len(s['invalid_classes'])}")
            print(f"    {s['step']}: {', '.join(parts)}")

    print(f"  Results: {results_path}")
    print(f"  Log: {log_path}")
    print(f"{'─'*60}\n")

    return results


# ════════════════════════════════════════════════════════════════════
# Compare runs
# ════════════════════════════════════════════════════════════════════

def compare_runs(output_dir: str = str(_RESULTS_DIR), session_ids: list[str] | None = None):
    """Load results.json files and print a comparison table."""
    base = Path(output_dir)

    if session_ids:
        result_files = [base / sid / "results.json" for sid in session_ids]
    else:
        result_files = sorted(base.glob("*/results.json"))

    if not result_files:
        print("No benchmark results found. Run a benchmark first.")
        return

    runs = []
    for f in result_files:
        if f.is_file():
            runs.append(json.loads(f.read_text(encoding="utf-8")))

    # ── Print comparison table ────────────────────────────────────
    tags = [r.get("tag") or r["session_id"] for r in runs]
    col_width = max(len(t) for t in tags) + 2

    print(f"\n{'='*60}")
    print(f"  Benchmark Comparison ({len(runs)} runs)")
    print(f"{'='*60}\n")

    def print_row(label: str, values: list[str], deltas: bool = False):
        row = f"| {label:<20} |"
        for i, v in enumerate(values):
            row += f" {v:>{col_width}} |"
            if deltas and i > 0:
                prev = values[i - 1].replace("s", "").replace(",", "")
                curr = v.replace("s", "").replace(",", "")
                try:
                    p, c = float(prev), float(curr)
                    if p > 0:
                        pct = ((c - p) / p) * 100
                        row += f" ({pct:+.0f}%)"
                except ValueError:
                    pass
        print(row)

    # Header
    sep = f"| {'Metric':<20} |"
    for t in tags:
        sep += f" {'':>{col_width}} |"
    print(sep)
    print("|" + "-" * 22 + "|" + "|".join(["" + "-" * (col_width + 2) + " " for _ in tags]))

    # Rows
    durations = [f"{r['metrics']['total_duration_s']:.0f}s" for r in runs]
    tokens = [f"{r['metrics']['total_tokens']:,}" for r in runs]
    calls = [str(r['metrics']['llm_call_count']) for r in runs]
    html_lens = [f"{r['metrics']['html_length']:,}" for r in runs]

    print_row("Tag", tags)
    print_row("Duration", durations, deltas=True)
    print_row("Tokens", tokens, deltas=True)
    print_row("LLM Calls", calls, deltas=True)
    print_row("HTML Length", html_lens, deltas=True)

    # Quality rows
    na_vals = [str(r.get("quality", {}).get("na_count_total", 0)) for r in runs]
    picsum_vals = [str(r.get("quality", {}).get("picsum_count_total", 0)) for r in runs]
    bad_classes = [str(r.get("quality", {}).get("invalid_classes_count", 0)) for r in runs]
    inline_st = [str(r.get("quality", {}).get("inline_style_count", 0)) for r in runs]

    print_row("N/A Count", na_vals)
    print_row("Picsum Images", picsum_vals)
    print_row("Invalid Classes", bad_classes)
    print_row("Inline Styles", inline_st)

    # Steps
    for r in runs:
        steps = r["metrics"].get("steps_executed", [])
        print(f"\n  {r.get('tag') or r['session_id']}: {', '.join(steps)}")

    # Per-step breakdown (optional)
    if len(runs) <= 3:
        print(f"\n{'─'*60}")
        print("  Per-step breakdown:")
        all_steps = set()
        for r in runs:
            for s in r.get("per_step", []):
                all_steps.add(s["step"])

        for step in sorted(all_steps):
            row = f"  {step:<30}"
            for r in runs:
                dur = next((s["duration_s"] for s in r.get("per_step", []) if s["step"] == step), None)
                row += f" {dur:>6.1f}s" if dur else f" {'—':>7}"
            print(row)

    print(f"\n{'='*60}\n")


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="End-to-end benchmark tool for the agentic UI generation pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single run with inline query
  python benchmarks/benchmark.py --query "Make a one day travel itinerary to hangzhou"

  # Run with JSON config
  python benchmarks/benchmark.py --config travel_plan.json

  # CLI args override config
  python benchmarks/benchmark.py --config default.json --token-budget 8000 --temperature 0.2

  # Multiple configs (sequential, independent)
  python benchmarks/benchmark.py --config a.json --config b.json --config c.json

  # Compare all results
  python benchmarks/benchmark.py --compare

  # Compare specific runs
  python benchmarks/benchmark.py --compare bench_001 bench_002
        """,
    )

    # Query / config
    parser.add_argument("--query", "-q", type=str, default="", help="User prompt to benchmark")
    parser.add_argument("--config", "-c", action="append", default=[], help="JSON config file (can repeat for multiple runs)")
    parser.add_argument("--configs", nargs="?", const=str(_CONFIGS_DIR), default="", help="Directory of JSON config files (loads all *.json); bare flag loads bundled benchmark_configs/")

    # LLM overrides
    parser.add_argument("--model", type=str, default="", help="Override LLM model name")
    parser.add_argument("--base-url", type=str, default="", help="Override LLM endpoint")
    parser.add_argument("--api-key", type=str, default="", help="Override LLM API key")

    # Pipeline tuning
    parser.add_argument("--token-budget", type=int, default=0, help="Override token budget (0 = env default)")
    parser.add_argument("--temperature", type=float, default=-1, help="Override temperature (-1 = env default)")
    parser.add_argument("--output-reserve", type=int, default=0, help="Override output reserve (0 = env default)")
    parser.add_argument("--plan-file", type=str, default="", help="Inject a pre-made plan JSON (skip plan step)")
    parser.add_argument("--no-think", action="store_true", default=None, help="Enable /no_think injection")
    parser.add_argument("--no-no-think", action="store_false", dest="no_think", help="Disable /no_think injection")
    parser.add_argument("--parallel", action="store_true", help="Run research + page shell in parallel, then components in parallel")
    parser.add_argument("--output-dir", type=str, default=str(_RESULTS_DIR), help="Output directory for results")
    parser.add_argument("--dry-run", action="store_true", help="Test config loading + LLM connectivity without running the pipeline")
    parser.add_argument("--rerun", type=int, default=1, help="Rerun each config N times and output a summary table")
    parser.add_argument("--warmup", type=int, default=0, help="Unmeasured warmup runs before measured reruns (loads Ollama models into memory so timings reflect steady-state)")
    parser.add_argument("--open", action="store_true", help="Open all output HTML files in Chromium after completion")
    parser.add_argument("--screenshot", action="store_true", help="Take full-page screenshots of all output HTML files using Playwright")
    parser.add_argument("--screenshot-width", type=int, default=375, help="Viewport width for screenshots (default: 375px mobile)")

    # Labeling
    parser.add_argument("--tag", type=str, default="", help="Human-readable label for this run (used in comparison)")

    # Comparison mode
    parser.add_argument("--compare", nargs="*", default=None, help="Compare results (no args = all; or pass session IDs)")

    args = parser.parse_args()

    # ── Compare mode ──────────────────────────────────────────────
    if args.compare is not None:
        session_ids = args.compare if args.compare else None
        compare_runs(output_dir=args.output_dir, session_ids=session_ids)
        return

    # ── Run mode ─────────────────────────────────────────────────
    if not args.config and not args.configs and not args.query:
        parser.error("Provide --query, --config, or --configs (or --compare)")

    configs: list[BenchmarkConfig] = []

    # Load individual config files (--config flag, can repeat)
    if args.config:
        for cfg_path in args.config:
            cfg = BenchmarkConfig.from_json(_resolve_config_path(cfg_path))
            cfg.override_from_args(args)
            configs.append(cfg)

    # Load all JSON configs from a directory (--configs flag)
    if args.configs:
        config_dir = Path(args.configs)
        if not config_dir.is_dir():
            # Try resolving against the bundled configs directory
            candidate = _CONFIGS_DIR / args.configs
            if candidate.is_dir():
                config_dir = candidate
        if not config_dir.is_dir():
            print(f"ERROR: --configs directory not found: {args.configs}")
            sys.exit(1)
        for cfg_path in sorted(config_dir.glob("*.json")):
            cfg = BenchmarkConfig.from_json(cfg_path)
            cfg.override_from_args(args)
            configs.append(cfg)

    # Inline query (if no configs were loaded)
    if not configs and args.query:
        cfg = BenchmarkConfig(query=args.query, output_dir=args.output_dir)
        cfg.override_from_args(args)
        configs.append(cfg)

    # Run each config sequentially (with rerun support)
    all_results = []
    rerun_summary = []  # [{tag, runs: [results...], avg_duration, min_duration, max_duration, avg_tokens}]
    
    for cfg in configs:
        if not cfg.output_dir:
            cfg.output_dir = args.output_dir

        # ── Warmup runs (not measured — load Ollama models into memory) ──
        warmup_count = max(args.warmup, 0)
        if warmup_count and not args.dry_run:
            for warmup_idx in range(warmup_count):
                warmup_cfg = BenchmarkConfig(**{**cfg.__dict__})
                warmup_cfg.session_id = ""  # fresh session per warmup
                warmup_cfg.tag = f"{cfg.tag or cfg.model or 'run'}_warmup{warmup_idx + 1}"
                print(f"\n  ▶ Warmup {warmup_idx + 1}/{warmup_count}: {warmup_cfg.tag} (not measured)")
                try:
                    asyncio.run(run_benchmark(warmup_cfg))
                except Exception as e:
                    print(f"  Warmup {warmup_idx + 1} failed (continuing to measured runs): {e}")

        config_runs = []
        rerun_count = max(args.rerun, 1)
        for run_idx in range(rerun_count):
            run_cfg = BenchmarkConfig(**{**cfg.__dict__})
            if rerun_count > 1:
                run_cfg.tag = f"{cfg.tag or cfg.model or 'run'}_{run_idx + 1}"
                run_cfg.session_id = ""  # auto-generate new session ID each rerun

            if args.dry_run:
                # Just validate: load config, check LLM connectivity, print setup
                base_config = load_config()
                if run_cfg.token_budget > 0:
                    base_config.token_budget = run_cfg.token_budget
                if run_cfg.temperature >= 0:
                    base_config.temperature = run_cfg.temperature
                model = run_cfg.model or (run_cfg.models.get("plan") if run_cfg.models else "") or base_config.local.model
                parallel_str = " [PARALLEL]" if run_cfg.parallel else ""
                print(f"  [{run_cfg.tag or model}]{parallel_str} model={model} budget={base_config.token_budget} "
                      f"no_think={run_cfg.no_think_enabled} plan_file={run_cfg.plan_file or '(none)'}")
                import requests
                try:
                    r = requests.get(
                        f"{base_config.local.base_url}/models",
                        headers={"Authorization": f"Bearer {base_config.local.api_key}"},
                        timeout=5,
                    )
                    if r.status_code == 200:
                        available = [m.get("id", "?") for m in r.json().get("data", [])]
                        found = model in available
                        status = "OK" if found else f"NOT FOUND (available: {', '.join(available[:5])})"
                        print(f"    LLM: {status}")
                    else:
                        print(f"    LLM: HTTP {r.status_code}")
                except Exception as e:
                    print(f"    LLM: UNREACHABLE ({e})")
                config_runs.append(None)
                continue

            try:
                results = asyncio.run(run_benchmark(run_cfg))
                all_results.append(results)
                config_runs.append(results)

                # Take screenshot immediately after this run (if enabled)
                if args.screenshot and results:
                    take_screenshots([results], args.output_dir,
                                     width=args.screenshot_width, open_immediately=True)
            except Exception as e:
                base_config = load_config()
                model = run_cfg.model or base_config.local.model
                base_url = run_cfg.base_url or base_config.local.base_url
                print(f"\n  ERROR: Benchmark failed for {run_cfg.tag or run_cfg.query[:40]}")
                print(f"    Model: {model}")
                print(f"    Base URL: {base_url}")
                print(f"    Endpoint: {base_url}/chat/completions")
                print(f"    Error: {e}")
                import traceback
                traceback.print_exc()
                config_runs.append(None)

        # Build rerun summary for this config — collect per-rerun rows + aggregate
        successful = [r for r in config_runs if r is not None]
        if successful and rerun_count > 1:
            base_tag = cfg.tag or cfg.model or "run"
            rows = []
            for i, r in enumerate(config_runs):
                tag = r["tag"] if (r is not None and r.get("tag")) else f"{base_tag}_{i + 1}"
                if r is not None:
                    m = r["metrics"]
                    rows.append({
                        "tag": tag, "ok": True,
                        "duration_s": m["total_duration_s"],
                        "tokens": m["total_tokens"],
                        "calls": m["llm_call_count"],
                        "html_length": m["html_length"],
                    })
                else:
                    rows.append({"tag": tag, "ok": False})
            durations = [row["duration_s"] for row in rows if row["ok"]]
            tokens = [row["tokens"] for row in rows if row["ok"]]
            calls = [row["calls"] for row in rows if row["ok"]]
            htmls = [row["html_length"] for row in rows if row["ok"]]
            rerun_summary.append({
                "config_tag": base_tag,
                "rows": rows,
                "avg_duration": sum(durations) / len(durations),
                "min_duration": min(durations),
                "max_duration": max(durations),
                "avg_tokens": sum(tokens) / len(tokens),
                "avg_calls": sum(calls) / len(calls),
                "avg_html": sum(htmls) / len(htmls),
            })

    # Print rerun summary table — one row per rerun, grouped by config
    if rerun_summary:
        print("\n" + "=" * 92)
        print("  Rerun Results (per run, grouped by config)")
        print("=" * 92 + "\n")
        print(f"  {'Tag':<42} {'Dur':>7} {'Tokens':>8} {'Calls':>6} {'HTML':>8}")
        print(f"  {'-' * 42} {'-' * 7} {'-' * 8} {'-' * 6} {'-' * 8}")
        for s in rerun_summary:
            for row in s["rows"]:
                if row["ok"]:
                    print(f"  {row['tag']:<42} {row['duration_s']:>6.1f}s {row['tokens']:>8} {row['calls']:>6} {row['html_length']:>8}")
                else:
                    print(f"  {row['tag']:<42} {'FAILED':>7}")
            print(f"  {'-' * 42} {'-' * 7} {'-' * 8} {'-' * 6} {'-' * 8}")
            print(f"  {s['config_tag'] + ' (avg)':<42} {s['avg_duration']:>6.1f}s {s['avg_tokens']:>8.0f} {s['avg_calls']:>6.0f} {s['avg_html']:>8.0f}   [{s['min_duration']:.1f}..{s['max_duration']:.1f}s]")
            print()

    # If multiple runs, print comparison
    if len(all_results) > 1 and not rerun_summary:
        print("\n" + "="*60)
        print("  Multiple runs completed — printing comparison")
        print("="*60 + "\n")
        compare_runs(
            output_dir=args.output_dir,
            session_ids=[r["session_id"] for r in all_results],
        )

    # Open all output HTML files in Chromium
    if args.open and all_results:
        open_outputs_in_browser(all_results, args.output_dir)

    # Screenshots are taken per-run (immediately after each run completes)


def take_screenshots(results: list[dict], output_dir: str, width: int = 375,
                     open_immediately: bool = True):
    """Take full-page screenshots of output HTML files using Playwright.

    When open_immediately=True, opens each screenshot in the default image
    viewer right after capture — useful for seeing results as runs complete.
    """
    from playwright.sync_api import sync_playwright
    import webbrowser

    html_files = []
    for r in results:
        html_path = r.get("files", {}).get("html", "")
        if html_path and Path(html_path).is_file():
            html_files.append(html_path)

    if not html_files:
        print("  No HTML files to screenshot.")
        return

    print(f"\n  Taking {len(html_files)} screenshots (width={width}px)...")

    screenshots = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": width, "height": 800},
                device_scale_factor=2,
            )
            page = context.new_page()

            for html_path in html_files:
                screenshot_path = html_path.replace(".html", ".png")
                try:
                    file_url = f"file:///{Path(html_path).resolve().as_posix()}"
                    page.goto(file_url, wait_until="networkidle", timeout=30000)
                    # Wait for ECharts to render
                    try:
                        page.wait_for_selector("[data-echarts]", timeout=5000)
                        page.wait_for_timeout(2000)  # extra time for chart animation
                    except Exception:
                        page.wait_for_timeout(1500)  # no charts, just wait for content

                    page.screenshot(path=screenshot_path, full_page=True)
                    screenshots.append(screenshot_path)
                    print(f"    OK: {Path(screenshot_path).name} ({width}px full-page)")

                    # Open immediately (per-run mode)
                    if open_immediately:
                        webbrowser.open(f"file:///{screenshot_path}")
                except Exception as e:
                    print(f"    FAIL: {Path(html_path).name} — {str(e)[:80]}")

            browser.close()
    except Exception as e:
        print(f"  Playwright error: {e}")

    # Open screenshots in default image viewer (batch mode — when not opened immediately)
    if screenshots and not open_immediately:
        print(f"\n  Opening {len(screenshots)} screenshots...")
        for screenshot_path in screenshots:
            webbrowser.open(f"file:///{screenshot_path}")

    print(f"  Done: {len(screenshots)} screenshots taken")


def open_outputs_in_browser(results: list[dict], output_dir: str):
    """Open all output HTML files in Chromium."""
    import subprocess
    import platform
    html_files = []
    for r in results:
        html_path = r.get("files", {}).get("html", "")
        if html_path and Path(html_path).is_file():
            html_files.append(html_path)

    if not html_files:
        print("  No HTML files to open.")
        return

    print(f"\n  Opening {len(html_files)} HTML files in Chromium...")

    # Try Chromium first, then Chrome, then default browser
    browser_paths = [
        "chromium",
        "chromium-browser",
        "chrome",
        "google-chrome",
        "C:\\Program Files\\Chromium\\Application\\chromium.exe",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Chromium\\Application\\chromium.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Users\\admin\\AppData\\Local\\Chromium\\Application\\chrome.exe",
        "C:\\Users\\admin\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Users\\admin\\AppData\\Local\\Chromium\\Application\\chromium.exe",
    ]

    browser_cmd = None
    for path in browser_paths:
        try:
            subprocess.run([path, "--version"], capture_output=True, timeout=3)
            browser_cmd = path
            break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if browser_cmd:
        for html_path in html_files:
            try:
                subprocess.Popen([browser_cmd, html_path])
            except Exception:
                pass  # Fallback to webbrowser
    else:
        # Fallback to webbrowser module
        import webbrowser
        for html_path in html_files:
            webbrowser.open(f"file:///{html_path}")

    print(f"  Opened {len(html_files)} files in browser")


if __name__ == "__main__":
    main()
