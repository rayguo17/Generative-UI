"""
Debug CLI for the agentic UI generation pipeline.

Run individual steps, inspect prompts/responses, and diagnose issues.

Usage:
  # Run the full pipeline
  python debug_cli.py -m "weather dashboard with temperature and humidity"

  # Run only the classify step
  python debug_cli.py -m "weather dashboard" --step classify

  # Run classify + plan
  python debug_cli.py -m "weather dashboard" --step classify --step plan

  # Run from a file
  python debug_cli.py -f request.txt --step generate

  # Read from stdin
  echo '{"title":"Hello"}' | python debug_cli.py --stdin

  # Dry run: show prompts without calling LLM
  python debug_cli.py -m "weather dashboard" --dry-run

  # Verbose: show full prompts and responses
  python debug_cli.py -m "weather dashboard" --verbose

  # Save all interactions to a file
  python debug_cli.py -m "weather dashboard" --output debug_output.md

  # Test connectivity to LLM
  python debug_cli.py --test-connection
"""

from __future__ import annotations

import argparse
import asyncio
import io as _io
import json
import sys
import time
from pathlib import Path

# Ensure app package is importable
_src = Path(__file__).resolve().parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from app.config import load_config, AppConfig, LlmConfig
from app.prompts.loader import PromptLoader
from app.utils.token_counter import count_tokens
from app.shared.llm_client import LlmClient, TokenBudgetExceededError
from app.utils.llm_logger import LlmInteractionLogger, create_session_id

# Fix Windows terminal encoding for box-drawing / emoji characters
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ── Terminal colors ────────────────────────────────────────────────────

class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


# ── Helpers ────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    print(f"\n{c('━' * 60, Colors.BLUE)}")
    print(c(f"  {title}", Colors.BOLD + Colors.BLUE))
    print(f"{c('━' * 60, Colors.BLUE)}\n")


def print_section(title: str) -> None:
    print(f"\n{c(title, Colors.CYAN + Colors.BOLD)}")
    print(f"{c('─' * 50, Colors.DIM)}")


def print_token_info(system_prompt: str, user_prompt: str, budget: int | None = None) -> None:
    sys_tokens = count_tokens(system_prompt)
    usr_tokens = count_tokens(user_prompt)
    total = sys_tokens + usr_tokens
    print(f"  {c('System prompt:', Colors.DIM)} {sys_tokens} tokens, {len(system_prompt)} chars")
    print(f"  {c('User prompt:', Colors.DIM)}   {usr_tokens} tokens, {len(user_prompt)} chars")
    print(f"  {c('Total input:', Colors.DIM)}   {c(str(total), Colors.BOLD)} tokens")
    if budget:
        remaining = budget - total - 1500  # reserve for output
        status = c("OK", Colors.GREEN) if remaining > 0 else c("OVER BUDGET", Colors.RED)
        print(f"  {c('Budget:', Colors.DIM)}       {budget} tokens → {remaining} remaining for output [{status}]")


def print_response(raw: str, max_len: int = 2000) -> None:
    if not raw:
        print(c("  ⚠️  EMPTY RESPONSE — model returned nothing", Colors.RED + Colors.BOLD))
        return
    display = raw if len(raw) <= max_len else raw[:max_len] + f"\n... ({len(raw) - max_len} more chars)"
    print(f"  {c('Response:', Colors.DIM)} {len(raw)} chars, ~{count_tokens(raw)} tokens")
    print(c(f"  ┌{'─' * 55}", Colors.DIM))
    for line in display.split("\n")[:40]:
        print(f"  {c('│', Colors.DIM)} {line}")
    print(c(f"  └{'─' * 55}", Colors.DIM))


def print_json_result(data: dict) -> None:
    print(f"  {c('Parsed JSON:', Colors.DIM)}")
    print(c(f"  ┌{'─' * 55}", Colors.DIM))
    for line in json.dumps(data, ensure_ascii=False, indent=2).split("\n")[:30]:
        print(f"  {c('│', Colors.DIM)} {line}")
    print(c(f"  └{'─' * 55}", Colors.DIM))


# ── Connection test ────────────────────────────────────────────────────

async def test_connection(config: AppConfig, interaction_logger: LlmInteractionLogger | None = None) -> bool:
    """Test connectivity to the local LLM endpoint."""
    print_header("Testing LLM Connection")

    llm = LlmClient(
        LlmConfig(
            base_url=config.local.base_url,
            api_key=config.local.api_key,
            model=config.local.model,
        ),
        token_budget=None,
        supports_json_mode=False,
        interaction_logger=interaction_logger,
        log_label="test:simple",
    )

    print(f"  Model:   {c(config.local.model, Colors.BOLD)}")
    print(f"  Base URL: {c(config.local.base_url, Colors.BOLD)}")
    print()

    # Test 1: Simple completion
    print(c("  [1/3] Testing simple completion...", Colors.YELLOW))
    try:
        t0 = time.monotonic()
        result = await llm.generate(
            system_prompt="You are a helpful assistant. Reply with ONLY the word 'OK'.",
            user_prompt="Say OK",
            temperature=0.1,
            max_tokens=200,  # Thinking models need headroom even for trivial responses
        )
        elapsed = (time.monotonic() - t0) * 1000
        if result.strip():
            print(f"  {c('✓', Colors.GREEN)} Response: '{result.strip()}' ({elapsed:.0f}ms)")
        else:
            print(f"  {c('✗', Colors.RED)} Empty response ({elapsed:.0f}ms)")
            print(f"  {c('→ Check: Is Ollama running? Is the model pulled?', Colors.YELLOW)}")
            print(f"  {c('→ Try: ollama pull ' + config.local.model, Colors.YELLOW)}")
            return False
    except Exception as e:
        print(f"  {c('✗', Colors.RED)} Failed: {e}")
        print(f"  {c('→ Is the base URL correct? Is the server reachable?', Colors.YELLOW)}")
        return False

    # Test 2: JSON generation
    print(c("  [2/3] Testing JSON generation...", Colors.YELLOW))
    try:
        result = await llm.generate_json(
            system_prompt="You are a JSON API. Reply ONLY with valid JSON.",
            user_prompt='Return: {"status": "ok", "value": 42}',
            max_tokens=500,  # Thinking models need headroom
        )
        if result:
            print(f"  {c('✓', Colors.GREEN)} Parsed: {json.dumps(result)}")
        else:
            print(f"  {c('✗', Colors.RED)} Empty response (model may need a system prompt tweak)")
            print(f"  {c('→ This is likely why classify/plan return empty', Colors.YELLOW)}")
            return False
    except Exception as e:
        print(f"  {c('✗', Colors.RED)} Failed: {e}")
        return False

    # Test 3: Token budget check
    print(c("  [3/3] Testing token budget enforcement...", Colors.YELLOW))
    try:
        test_budget = 100  # Very small budget
        small_llm = LlmClient(
            LlmConfig(base_url=config.local.base_url, api_key=config.local.api_key, model=config.local.model),
            token_budget=test_budget,
            supports_json_mode=False,
        )
        long_prompt = "test " * 200  # ~600 tokens, should exceed budget
        try:
            await small_llm.generate(system_prompt=long_prompt, user_prompt="hi", max_tokens=10)
            print(f"  {c('⚠', Colors.YELLOW)} Budget enforcement did not trigger (prompt may be smaller than expected)")
        except TokenBudgetExceededError:
            print(f"  {c('✓', Colors.GREEN)} Budget enforcement works correctly")
    except Exception as e:
        print(f"  {c('⚠', Colors.YELLOW)} Budget test: {e}")

    print(f"\n{c('Connection test complete.', Colors.GREEN)}")
    return True


# ── Step runners ───────────────────────────────────────────────────────

async def run_classify(config: AppConfig, prompt_loader: PromptLoader, query: str,
                       verbose: bool = False, dry_run: bool = False,
                       interaction_logger: LlmInteractionLogger | None = None):
    """Run only the classify step."""
    print_header("Pass 1: CLASSIFY (Intent Analysis)")

    from app.generation.analyze import analyze_user_request, _extract_data_preview

    system_prompt = prompt_loader.load_for_step("classify")

    # Build user prompt (same as analyze.py)
    data_preview = _extract_data_preview(query, max_chars=800)
    user_prompt = f"""## Task
Analyze this user request for H5 card generation. Classify the intent, extract data fields, and determine which modules are needed.

## User Request
{query[:1200]}

## Data Preview
{data_preview}

## Output
Return a JSON object following this exact schema:
{{
  "intent": "card" | "dashboard" | "list" | "form" | "chart" | "custom",
  "summary": "one-sentence description",
  "data_fields": [{{"name": "...", "type": "...", "path": "$...", "sample_value": "..."}}],
  "needed_modules": ["interaction", "chart", "image", "pagination"],
  "complexity": 1-5,
  "has_interactions": true/false,
  "has_images": true/false,
  "data_is_tabular": true/false
}}

Rules:
- intent: "card" for single info cards, "dashboard" for multi-metric, "list" for tables/lists, "form" for inputs, "chart" for visualizations, "custom" for free-form
- needed_modules: include "interaction" if the user mentions buttons/links/navigation/pagination, "chart" if data is numeric trends, "image" if image URLs are present, "pagination" if list is long
- has_interactions: true if user mentions any click/tap/navigate action
- data_is_tabular: true if data has repeating rows/items
- complexity: 1=simple text card, 3=multi-section card, 5=complex dashboard with interactions"""

    print_token_info(system_prompt, user_prompt, budget=config.token_budget)

    if verbose:
        print_section("System Prompt")
        print(system_prompt[:3000])
        print_section("User Prompt")
        print(user_prompt[:3000])

    if dry_run:
        print(f"\n{c('  [DRY RUN] Skipping LLM call', Colors.YELLOW)}")
        return {"intent": "card", "summary": "Dry run", "data_fields": [], "needed_modules": [],
                "complexity": 1, "has_interactions": False, "has_images": False, "data_is_tabular": False}

    # Make the LLM call with raw response inspection
    llm = LlmClient(
        LlmConfig(base_url=config.local.base_url, api_key=config.local.api_key, model=config.local.model),
        token_budget=config.token_budget,
        supports_json_mode=False,
        interaction_logger=interaction_logger,
        log_label="classify",
    )

    print(f"\n{c('  Calling LLM...', Colors.YELLOW)}")
    t0 = time.monotonic()

    # Try generate_json first, but fall back to raw text if empty
    result = await llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=512,
    )

    elapsed = (time.monotonic() - t0) * 1000

    if result:
        print(f"\n{c(f'  ✓ Got JSON response ({elapsed:.0f}ms)', Colors.GREEN)}")
        print_json_result(result)
    else:
        print(f"\n{c(f'  ✗ generate_json returned empty ({elapsed:.0f}ms)', Colors.RED)}")
        print(c('  → Trying raw text generation to see what model actually returns...', Colors.YELLOW))

        # Fallback: raw text generation to inspect the model's actual output
        try:
            raw = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=512,
                json_mode=False,
            )
            elapsed2 = (time.monotonic() - t0) * 1000
            if raw:
                print(f"\n{c(f'  Raw response ({elapsed2:.0f}ms total):', Colors.YELLOW)}")
                print_response(raw[:1000])
                print(f"\n{c('  → Model IS responding but not in JSON format. Check prompt instructions.', Colors.YELLOW)}")
            else:
                print(f"\n{c(f'  ✗ Raw generation ALSO returned empty ({elapsed2:.0f}ms)', Colors.RED)}")
                print(f"  {c('→ Possible causes:', Colors.BOLD)}")
                print(f"    1. Model '{config.local.model}' not pulled in Ollama")
                print(f"    2. Ollama server not running or wrong port")
                print(f"    3. Model's context window smaller than the prompt")
                print(f"    4. Model crashed or OOM on this prompt")
                result = {"intent": "card", "summary": "Empty response fallback"}
        except Exception as e:
            print(f"\n{c(f'  ✗ Raw generation failed: {e}', Colors.RED)}")
            result = {"intent": "card", "summary": f"Error: {e}"}

    return result


async def run_plan(config: AppConfig, prompt_loader: PromptLoader, query: str,
                   analysis: dict, verbose: bool = False, dry_run: bool = False,
                   interaction_logger: LlmInteractionLogger | None = None):
    """Run only the plan step."""
    print_header("Pass 2: PLAN (Layout Planning)")

    from app.generation.plan import create_layout_plan

    system_prompt = prompt_loader.load_for_step("plan")

    # Reconstruct the user prompt (same as plan.py)
    data_context = _build_data_context(query, analysis)
    user_prompt = f"""## Task
Create a detailed layout plan for an H5 card based on the analysis below.

## Analysis
- Intent: {analysis.get('summary', 'Information card')}
- Type: {analysis.get('intent', 'card')}
- Complexity: {analysis.get('complexity', 2)}/5
- Has interactions: {analysis.get('has_interactions', False)}
- Has images: {analysis.get('has_images', False)}
- Is tabular: {analysis.get('data_is_tabular', False)}
- Needed modules: {', '.join(analysis.get('needed_modules', [])) or 'none'}

## Data Context
{data_context}

## User's Original Request
{query[:800]}

## Output
Return a JSON object with: card_type, sections (array), data_summary, interaction_intents, style_preferences, needs_charts, needs_pagination, needs_interactions, estimated_complexity."""

    print_token_info(system_prompt, user_prompt, budget=config.token_budget)

    if verbose:
        print_section("System Prompt")
        print(system_prompt[:3000])
        print_section("User Prompt")
        print(user_prompt[:3000])

    if dry_run:
        print(f"\n{c('  [DRY RUN] Skipping LLM call', Colors.YELLOW)}")
        return {"card_type": "simple_card", "sections": [], "data_summary": {}}

    llm = LlmClient(
        LlmConfig(base_url=config.local.base_url, api_key=config.local.api_key, model=config.local.model),
        token_budget=config.token_budget,
        supports_json_mode=False,
        interaction_logger=interaction_logger,
        log_label="plan",
    )

    print(f"\n{c('  Calling LLM...', Colors.YELLOW)}")
    t0 = time.monotonic()
    result = await llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=1024,
    )
    elapsed = (time.monotonic() - t0) * 1000

    if result:
        print(f"\n{c(f'  ✓ Got JSON response ({elapsed:.0f}ms)', Colors.GREEN)}")
        print_json_result(result)
    else:
        print(f"\n{c(f'  ✗ Empty response ({elapsed:.0f}ms)', Colors.RED)}")
        # Fall back to raw text
        try:
            llm._log_label = "plan (raw fallback)"
            raw = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                max_tokens=1024,
                json_mode=False,
            )
            if raw:
                print(f"\n{c('  Raw response:', Colors.YELLOW)}")
                print_response(raw[:1000])
            else:
                print(f"  {c('→ Model returned empty for plan step too', Colors.RED)}")
        except Exception as e:
            print(f"  {c(f'→ Failed: {e}', Colors.RED)}")

    return result


async def run_generate(config: AppConfig, prompt_loader: PromptLoader, query: str,
                       plan: dict, verbose: bool = False, dry_run: bool = False,
                       interaction_logger: LlmInteractionLogger | None = None):
    """Run only the generate step."""
    print_header("Pass 3: GENERATE (HTML Fragment)")

    from app.generation.generate import generate_html
    from app.generation.llm_client import GenerationLlmClient

    system_prompt = prompt_loader.load_for_step(
        "generate",
        needs_charts=plan.get("needs_charts", False),
        needs_interactions=plan.get("needs_interactions", False),
    )

    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    user_prompt = f"""## Task
Generate a complete H5 HTML fragment based on the layout plan below. Output ONLY the raw HTML fragment — start with '<', no markdown fences, no explanations.

## Layout Plan
```json
{plan_json}
```

## Data to Render
```
{query[:1200]}
```

## Original User Request
{query[:400]}

## Critical Rules
1. First character MUST be '<'
2. Single root element
3. NO markdown fences, NO JSON wrappers, NO preamble
4. NO <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link>
5. Use Tailwind utility classes
6. Output ONLY the HTML — nothing else"""

    print_token_info(system_prompt, user_prompt, budget=config.token_budget)

    if verbose:
        print_section("System Prompt")
        print(system_prompt[:3000])
        print_section("User Prompt")
        print(user_prompt[:2000])

    if dry_run:
        print(f"\n{c('  [DRY RUN] Skipping LLM call', Colors.YELLOW)}")
        return "<div>Dry run</div>"

    llm = LlmClient(
        LlmConfig(base_url=config.local.base_url, api_key=config.local.api_key, model=config.local.model),
        token_budget=config.token_budget,
        supports_json_mode=False,
        interaction_logger=interaction_logger,
        log_label="generate",
    )

    print(f"\n{c('  Calling LLM (streaming)...', Colors.YELLOW)}")
    t0 = time.monotonic()
    collected: list[str] = []

    try:
        async for token in llm.generate_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=2048,
        ):
            collected.append(token)
            # Print progress dots
            if len(collected) % 10 == 0:
                print(".", end="", flush=True)
    except Exception as e:
        print(f"\n{c(f'  ✗ Stream failed: {e}', Colors.RED)}")

        # Fall back to non-streaming
        try:
            llm._log_label = "generate (non-streaming fallback)"
            raw = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4,
                max_tokens=2048,
            )
            if raw:
                collected = [raw]
                print(c("  → Got response via non-streaming", Colors.YELLOW))
        except Exception as e2:
            print(f"{c(f'  ✗ Non-streaming also failed: {e2}', Colors.RED)}")

    html = "".join(collected)
    elapsed = (time.monotonic() - t0) * 1000

    if html:
        print(f"\n\n{c(f'  ✓ Got HTML ({elapsed:.0f}ms, {len(html)} chars)', Colors.GREEN)}")
        print_response(html, max_len=1500)
    else:
        print(f"\n\n{c(f'  ✗ Empty HTML response ({elapsed:.0f}ms)', Colors.RED)}")

    return html


async def run_refine(config: AppConfig, prompt_loader: PromptLoader, html: str,
                     plan: dict, verbose: bool = False, dry_run: bool = False,
                     interaction_logger: LlmInteractionLogger | None = None):
    """Run only the refine step."""
    print_header("Pass 4: REFINE (Self-Check)")

    from app.generation.refine import refine_html, _summarize_plan, _truncate_html_for_context

    system_prompt = prompt_loader.load_for_step("refine")
    plan_summary = _summarize_plan(plan)
    html_fragment = _truncate_html_for_context(html, max_chars=1500)

    user_prompt = f"""## Task
Review and fix the generated HTML fragment below. Focus on rule violations, not redesign.

## Generated HTML
```html
{html_fragment}
```

## Layout Plan (for reference)
```json
{plan_summary}
```

## Fix Instructions
1. Check for forbidden tags: <html>, <head>, <body>, <script>, <style>, <meta>, <template>, <link>
2. Check for markdown fences or JSON wrappers
3. Verify every visible string/URL comes from the data
4. Check responsive primitives: flex rows need min-w-0 on main content, shrink-0 on fixed items
5. Verify data-interactions JSON is valid double-quoted JSON
6. Check image placement: primary as visible <img>, decorative as absolute background layer

## Output
Return the CORRECTED HTML fragment. If no fixes needed, return the original HTML unchanged.
Output ONLY the HTML — start with '<', no markdown, no commentary."""

    print_token_info(system_prompt, user_prompt, budget=config.token_budget)

    if verbose:
        print_section("System Prompt")
        print(system_prompt[:2000])

    if dry_run:
        print(f"\n{c('  [DRY RUN] Skipping LLM call', Colors.YELLOW)}")
        return html

    llm = LlmClient(
        LlmConfig(base_url=config.local.base_url, api_key=config.local.api_key, model=config.local.model),
        token_budget=config.token_budget,
        supports_json_mode=False,
        interaction_logger=interaction_logger,
        log_label="refine",
    )

    print(f"\n{c('  Calling LLM...', Colors.YELLOW)}")
    t0 = time.monotonic()
    try:
        refined = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
            max_tokens=2048,
        )
    except Exception as e:
        print(f"{c(f'  ✗ Failed: {e}', Colors.RED)}")
        refined = ""

    elapsed = (time.monotonic() - t0) * 1000

    if refined:
        print(f"\n{c(f'  ✓ Got refined HTML ({elapsed:.0f}ms, {len(refined)} chars)', Colors.GREEN)}")
        if refined.strip() != html.strip():
            print(c("  → HTML was modified by refine step", Colors.YELLOW))
        else:
            print(c("  → HTML unchanged (no issues found)", Colors.DIM))
        print_response(refined, max_len=1500)
    else:
        print(f"\n{c(f'  ✗ Empty response ({elapsed:.0f}ms)', Colors.RED)}")
        refined = html

    return refined


async def run_verify(config: AppConfig, prompt_loader: PromptLoader, html: str, query: str,
                     interaction_logger: LlmInteractionLogger | None = None):
    """Run verification on generated HTML."""
    print_header("VERIFICATION (Cloud LLM)")

    from app.verification.verifier import Verifier

    verifier = Verifier(config, prompt_loader)

    print(f"  Cloud model: {c(config.cloud.model, Colors.BOLD)}")
    print(f"  HTML length: {len(html)} chars")
    print(f"\n{c('  Running verification...', Colors.YELLOW)}")
    t0 = time.monotonic()

    try:
        report = await verifier.verify(html=html, user_query=query,
                                         interaction_logger=interaction_logger)
        elapsed = (time.monotonic() - t0) * 1000

        emoji = "✅" if report.overall_pass else "❌"
        print(f"\n  {emoji} {c(f'Verification: {report.summary}', Colors.BOLD)}")
        print(f"  {c(f'Completed in {elapsed:.0f}ms', Colors.DIM)}")

        if report.dimensions:
            print(f"\n{c('  Dimension Results:', Colors.CYAN)}")
            for dim in report.dimensions:
                status = c("✓", Colors.GREEN) if dim.passed else c("✗", Colors.RED)
                print(f"    {status} {dim.dimension.value}: {len(dim.violations)} violations")

        if report.critical_fixes_needed:
            print(f"\n{c('  Critical Fixes Needed:', Colors.RED)}")
            for fix in report.critical_fixes_needed[:5]:
                print(f"    - {fix}")

        return report
    except Exception as e:
        print(f"\n{c(f'  ✗ Verification failed: {e}', Colors.RED)}")
        return None


# ── Helpers ────────────────────────────────────────────────────────────

def _build_data_context(query: str, analysis: dict) -> str:
    """Build a compact data context string from the analysis (same as plan.py)."""
    fields = analysis.get("data_fields", [])
    if not fields:
        return query[:600]
    lines = ["Data fields found:"]
    for f in fields[:20]:
        name = f.get("name", "?")
        ftype = f.get("type", "string")
        sample = f.get("sample_value", "")
        path = f.get("path", "$")
        sample_str = f" = {json.dumps(sample, ensure_ascii=False)}" if sample else ""
        lines.append(f"  {name} ({ftype}) @ {path}{sample_str}")
    return "\n".join(lines)


# ── Main CLI ───────────────────────────────────────────────────────────

async def main_async(args: argparse.Namespace) -> None:
    config = load_config()
    prompt_loader = PromptLoader(
        condensed_dir=config.condensed_prompts_dir,
        full_prompts_dir=config.prompts_dir,
    )

    # Resolve query
    if args.message:
        query = args.message
    elif args.input_file:
        query = Path(args.input_file).read_text(encoding="utf-8")
    elif args.stdin:
        query = sys.stdin.read()
    else:
        query = ""

    query = query.strip()
    if not query and not args.test_connection:
        print(c("Error: No query provided. Use -m, -f, or --stdin.", Colors.RED))
        sys.exit(1)

    dry_run = args.dry_run

    # Create interaction logger (always, unless dry-run)
    if args.output:
        # Use user-specified path: --output path/to/my_log.md
        out_path = Path(args.output)
        log_dir = out_path.parent if out_path.parent != Path(".") else (Path(__file__).resolve().parent / "logs")
        session_id = out_path.stem  # "my_log" from "my_log.md"
        log_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_dir = Path(__file__).resolve().parent / "logs"
        session_id = f"debug_{create_session_id()}"

    interaction_logger = LlmInteractionLogger(log_dir, session_id, query) if not dry_run else None
    if not dry_run:
        print(f"  Log:    {c(str(interaction_logger._file_path), Colors.DIM)}")

    # Test connection mode
    if args.test_connection:
        ok = await test_connection(config, interaction_logger=interaction_logger)
        if interaction_logger:
            log_path = interaction_logger.finalize(
                steps_executed=["test-connection"],
                verification_passed=None,
            )
            print(f"\n  Log saved: {c(str(log_path), Colors.GREEN)}")
        sys.exit(0 if ok else 1)

    verbose = args.verbose

    # Determine which steps to run
    all_steps = {"classify", "plan", "generate", "refine", "verify"}
    if args.step:
        steps = set(args.step)
        invalid = steps - all_steps
        if invalid:
            print(c(f"Error: Invalid step(s): {invalid}. Valid: {all_steps}", Colors.RED))
            sys.exit(1)
    else:
        steps = all_steps

    print_header(f"Debug Pipeline: {', '.join(sorted(steps))}")
    print(f"  Model:  {c(config.local.model, Colors.BOLD)} @ {config.local.base_url}")
    print(f"  Query:  {c(query[:100] + ('...' if len(query) > 100 else ''), Colors.BOLD)}")
    print(f"  Budget: {c(str(config.token_budget), Colors.BOLD)} tokens")
    print(f"  Mode:   {c('DRY RUN' if dry_run else 'LIVE', Colors.YELLOW if dry_run else Colors.GREEN)}")

    # State that accumulates across steps
    analysis = {}
    plan = {}
    html = ""
    verification_passed = None

    # ── Step: Classify ──
    if "classify" in steps:
        analysis = await run_classify(config, prompt_loader, query,
                                       verbose=verbose, dry_run=dry_run,
                                       interaction_logger=interaction_logger)
        if not analysis or not analysis.get("intent"):
            print(c("\n⚠️  Classify returned empty/incomplete result. Pipeline may fail.", Colors.YELLOW))

    # ── Step: Plan ──
    if "plan" in steps:
        if not analysis:
            analysis = {"intent": "card", "summary": "Unknown", "data_fields": [],
                        "needed_modules": [], "complexity": 1,
                        "has_interactions": False, "has_images": False, "data_is_tabular": False}
            print(c("\n⚠️  No analysis result available, using defaults for plan step.", Colors.YELLOW))
        plan = await run_plan(config, prompt_loader, query, analysis,
                               verbose=verbose, dry_run=dry_run,
                               interaction_logger=interaction_logger)

    # ── Step: Generate ──
    if "generate" in steps:
        if not plan:
            plan = {"card_type": "simple_card", "sections": [], "data_summary": {},
                    "needs_charts": False, "needs_pagination": False, "needs_interactions": False}
            print(c("\n⚠️  No plan available, using defaults for generate step.", Colors.YELLOW))
        html = await run_generate(config, prompt_loader, query, plan,
                                   verbose=verbose, dry_run=dry_run,
                                   interaction_logger=interaction_logger)

    # ── Step: Refine ──
    if "refine" in steps:
        if not html:
            html = "<div>No HTML generated</div>"
            print(c("\n⚠️  No HTML available for refine step.", Colors.YELLOW))
        if not plan:
            plan = {}
        refined = await run_refine(config, prompt_loader, html, plan,
                                    verbose=verbose, dry_run=dry_run,
                                    interaction_logger=interaction_logger)
        if refined:
            html = refined

    # ── Step: Verify ──
    if "verify" in steps:
        if not html:
            print(c("\n⚠️  No HTML to verify. Run generate first.", Colors.YELLOW))
        else:
            report = await run_verify(config, prompt_loader, html, query,
                                      interaction_logger=interaction_logger)
            if report:
                verification_passed = report.overall_pass

    # ── Summary ──
    print_header("Pipeline Complete")
    print(f"  Steps executed: {c(', '.join(sorted(steps)), Colors.BOLD)}")
    if html:
        print(f"  Final HTML: {c(str(len(html)), Colors.BOLD)} chars")
        print(f"  Starts with '<': {c('Yes' if html.strip().startswith('<') else 'NO ⚠️', Colors.GREEN if html.strip().startswith('<') else Colors.RED)}")

    # ── Finalize log ──
    if interaction_logger:
        log_path = interaction_logger.finalize(
            steps_executed=sorted(steps),
            verification_passed=verification_passed,
        )
        print(f"\n  {c('Interaction log:', Colors.DIM)} {c(str(log_path), Colors.GREEN)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Debug CLI for agentic UI generation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python debug_cli.py -m "weather dashboard card"
  python debug_cli.py -m "employee list with pagination" --step classify --step plan
  python debug_cli.py -m "chart of monthly sales" --verbose --step generate
  python debug_cli.py --test-connection
  python debug_cli.py -m "simple card" --dry-run
        """,
    )

    # Query source (mutually exclusive)
    query_group = parser.add_mutually_exclusive_group()
    query_group.add_argument("-m", "--message", help="User prompt (instructions + data)")
    query_group.add_argument("-f", "--input-file", type=str, help="Read query from file")
    query_group.add_argument("--stdin", action="store_true", help="Read query from stdin")

    # Step selection
    parser.add_argument(
        "--step", action="append",
        choices=["classify", "plan", "generate", "refine", "verify"],
        help="Run only this step (can be repeated). Default: all steps.",
    )

    # Modes
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prompts without calling LLM")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show full system prompts and user prompts")
    parser.add_argument("--test-connection", action="store_true",
                        help="Test connectivity to local LLM and exit")

    # Output
    parser.add_argument("--output", "-o", type=str,
                        help="Path for the interaction log file (default: logs/debug_<timestamp>.md)")

    args = parser.parse_args()

    if not args.test_connection:
        n = sum(x is not None for x in (args.message, args.input_file)) + (1 if args.stdin else 0)
        if n != 1:
            parser.error("Provide exactly one of: --message/-m, --input-file/-f, --stdin")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
