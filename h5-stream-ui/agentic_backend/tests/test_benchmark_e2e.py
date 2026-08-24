"""End-to-end benchmark test with a mock LLMClient (no Ollama required).

Exercises the full pipeline — config load -> plan -> page shell -> component
-> assemble — with GenerationLlmClient replaced by a canned-response mock, so
the pipeline can be tested without a running LLM server.
"""
import sys
import asyncio
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

base = Path(__file__).resolve().parent.parent
if str(base) not in sys.path:
    sys.path.insert(0, str(base))                 # app.*
if str(base / "benchmarks") not in sys.path:
    sys.path.insert(0, str(base / "benchmarks"))  # benchmark module


class MockGenerationLlmClient:
    """Canned-response LLM client. Returns per-step mock output.

    Replaces GenerationLlmClient in tests so the pipeline runs end-to-end
    without Ollama. ``_canned`` picks a response based on ``step_name``:
    plan -> JSONL plan; page_generate -> shell HTML with a placeholder;
    component -> component HTML.
    """

    def __init__(self, config, override_model=None, override_base_url=None, override_api_key=None):
        self.config = config
        self._label = ""
        # plan.py reads llm._client.model; orchestrator reads token_budget.
        self._client = types.SimpleNamespace(
            model=override_model or "mock-model",
            token_budget=config.token_budget,
            total_tokens_used=0,
            last_finish_reason="stop",
            last_thinking_tokens=0,
            estimate_input_tokens=lambda s, u: 100,
        )

    def set_logger(self, logger, label=""):
        # orchestrator wires a logger per client; the mock ignores it.
        self._label = label

    @property
    def total_tokens_used(self):
        return 0

    async def generate_text(self, system_prompt, user_prompt, *,
                             step_name="unknown", max_tokens=4096, log_label=None):
        return self._canned(step_name or log_label or self._label)

    async def generate_json(self, system_prompt, user_prompt, *,
                             step_name="unknown", max_tokens=4096, log_label=None):
        return {"fields_text": "mock: destination Hangzhou"}

    async def generate_stream(self, system_prompt, user_prompt, *,
                              temperature=0.4, max_tokens=2048, log_label=None):
        yield self._canned(log_label or "generate")

    @staticmethod
    def _canned(step):
        s = (step or "").lower()
        if "plan" in s:
            return (
                '{"topic": "travel_plan", "intent": "mock trip"}\n'
                '{"global": {"desc": "mock card", "card_type": "multi_section"}}\n'
                '{"section": 0, "title": "Overview", "widget": "lead", "desc": "hero", '
                '"data": "name", "research": "none", "repeatable": false, "est_count": null}'
            )
        if "page_generate" in s or "shell" in s:
            return ('<section class="px-5 mb-5">'
                    '<h1 class="text-heading">Mock</h1>'
                    '<!-- COMP_PLACEHOLDER:0:lead --></section>')
        if "component" in s:
            return '<div class="px-4 py-3">mock component</div>'
        return "mock"


def test_benchmark_e2e_mock_llm():
    """Sequential pipeline (parallel=False) end-to-end with the mock LLM."""
    from benchmark import run_benchmark, BenchmarkConfig

    tmp = tempfile.mkdtemp()
    cfg = BenchmarkConfig(
        query="mock query",
        model="mock-model",
        models=None,
        parallel=False,        # sequential path (deterministic)
        token_budget=4000,
        output_dir=tmp,
        tag="test_e2e",
    )
    with patch("app.generation.orchestrator.GenerationLlmClient", MockGenerationLlmClient):
        results = asyncio.run(run_benchmark(cfg))

    assert results is not None
    assert results["metrics"]["html_length"] > 0, f"html_length={results['metrics']['html_length']}"
    assert results["metrics"]["steps_executed"], "steps_executed is empty"

    html_path = Path(results["files"]["html"])
    assert html_path.is_file(), f"output html not written: {html_path}"
    html = html_path.read_text(encoding="utf-8")
    assert "mock component" in html, "mock component HTML missing from assembled output"
    assert "COMP_PLACEHOLDER" not in html, "unfilled placeholder left in output"
    print("PASS: test_benchmark_e2e_mock_llm")


if __name__ == "__main__":
    test_benchmark_e2e_mock_llm()
    print("\nAll benchmark e2e tests passed!")
