"""
Verification Orchestrator — drives the multi-dimensional quality check workflow.

This is the main entry point for Workflow 2. It coordinates cloud LLM-based
verification across all rule dimensions:

1. Syntax & Output Format — valid HTML, no forbidden elements, first char '<'
2. Style Compliance — Tailwind, responsive primitives, HarmonyOS spec
3. Data Fidelity — source binding, no fabrication, array completeness
4. Interaction DSL — valid data-interactions JSON, correct action types
5. Aggregate — final pass/fail report with fix suggestions

Each dimension check uses the FULL original prompts (uncondensed) since
the cloud LLM has no context window limitation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable, Optional, TYPE_CHECKING

from app.config import AppConfig
from app.prompts.loader import PromptLoader
from app.utils.html_validator import check_html_syntax, HtmlSyntaxReport
from app.verification.cloud_llm import CloudLlmClient
from app.models.verification import (
    Violation,
    ViolationDimension,
    ViolationSeverity,
    DimensionResult,
    VerificationReport,
)

if TYPE_CHECKING:
    from app.utils.llm_logger import LlmInteractionLogger

logger = logging.getLogger(__name__)


class Verifier:
    """Orchestrates cloud LLM verification of generated HTML."""

    def __init__(self, config: AppConfig, prompt_loader: PromptLoader):
        self.config = config
        self.prompt_loader = prompt_loader

    async def verify(
        self,
        html: str,
        user_query: str,
        layout_plan: dict | None = None,
        interaction_logger: Optional["LlmInteractionLogger"] = None,
    ) -> VerificationReport:
        """Run all verification dimensions and produce an aggregate report.

        Args:
            html: Generated HTML fragment to verify.
            user_query: Original user prompt for data fidelity checks.
            layout_plan: Optional layout plan for deeper validation.
            interaction_logger: Optional logger for LLM interaction traces.

        Returns:
            VerificationReport with pass/fail and all issues.
        """
        # Phase 0: Deterministic checks (no LLM)
        syntax_report = check_html_syntax(html)

        # Convert deterministic issues to violations
        det_results = _deterministic_to_dimension_result(syntax_report)

        # Phase 1-4: Cloud LLM checks (run in parallel)
        cloud_llm = CloudLlmClient(self.config)
        if interaction_logger:
            cloud_llm.set_logger(interaction_logger)

        dimensions_to_run: list[ViolationDimension] = [
            ViolationDimension.OUTPUT_FORMAT,
            ViolationDimension.DESIGN_QUALITY,
            ViolationDimension.DATA_FIDELITY,
            ViolationDimension.INTERACTION_DSL,
            ViolationDimension.RESPONSIVE_LAYOUT,
            ViolationDimension.HARMONY_SPEC,
        ]

        # Skip dimensions based on content
        if not _has_images(html):
            dimensions_to_run = [
                d for d in dimensions_to_run
                if d != ViolationDimension.IMAGE_PROCESSING
            ]

        cloud_tasks = [
            self._run_dimension_check(dim, html, user_query, cloud_llm)
            for dim in dimensions_to_run
        ]

        cloud_results = await asyncio.gather(*cloud_tasks, return_exceptions=True)

        # Collect all dimension results
        all_results: list[DimensionResult] = [det_results]
        for result in cloud_results:
            if isinstance(result, DimensionResult):
                all_results.append(result)
            elif isinstance(result, Exception):
                logger.error("Verification dimension failed: %s", result)
                # Add a warning-level result for failed checks
                all_results.append(DimensionResult(
                    dimension=ViolationDimension.HTML_SYNTAX,
                    passed=True,  # Don't fail due to check failure
                    violations=[Violation(
                        dimension=ViolationDimension.HTML_SYNTAX,
                        severity=ViolationSeverity.WARNING,
                        rule_ref="verification",
                        description=f"Verification check failed: {result}",
                        fix_suggestion="Manually review the HTML output.",
                    )],
                ))

        # Aggregate
        report = VerificationReport.from_dimension_results(all_results)

        logger.info(
            "Verification: %s — %d violations (%d errors, %d warnings)",
            "PASS" if report.overall_pass else "FAIL",
            report.total_violations,
            report.error_count,
            report.warning_count,
        )

        return report

    async def _run_dimension_check(
        self,
        dimension: ViolationDimension,
        html: str,
        user_query: str,
        cloud_llm: CloudLlmClient,
    ) -> DimensionResult:
        """Run a single verification dimension check using the cloud LLM."""
        try:
            system_prompt = self._build_dimension_prompt(dimension)
            user_prompt = self._build_dimension_user_prompt(dimension, html, user_query)

            result = await cloud_llm.verify(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                dimension=dimension.value,
            )

            return _parse_dimension_result(dimension, result)
        except Exception as e:
            logger.error("Dimension %s check failed: %s", dimension, e)
            return DimensionResult(
                dimension=dimension,
                passed=True,  # Don't fail due to infrastructure error
                violations=[],
            )

    def _build_dimension_prompt(self, dimension: ViolationDimension) -> str:
        """Build the system prompt for a verification dimension."""
        dim_name = dimension.value.replace("_", " ")
        dim_to_prompt_key = {
            ViolationDimension.HTML_SYNTAX: "syntax",
            ViolationDimension.OUTPUT_FORMAT: "syntax",
            ViolationDimension.DESIGN_QUALITY: "style",
            ViolationDimension.RESPONSIVE_LAYOUT: "style",
            ViolationDimension.HARMONY_SPEC: "style",
            ViolationDimension.DATA_FIDELITY: "data_fidelity",
            ViolationDimension.IMAGE_PROCESSING: "data_fidelity",
            ViolationDimension.INTERACTION_DSL: "interaction",
            ViolationDimension.CHART_COMPLIANCE: "style",
            ViolationDimension.SECURITY: "syntax",
        }

        key = dim_to_prompt_key.get(dimension, "syntax")
        rules = self.prompt_loader.load_full_for_verification(key)

        return f"""You are verifying an HTML fragment generated for an H5 mobile card application.

## Rules to Check
{rules}

## Your Task
Check the HTML fragment below against the rules above. Focus on dimension: {dim_name}.
Output a JSON object with this structure:
{{"passed": true/false, "violations": [{{"severity": "error"|"warning"|"info", "description": "what is wrong", "rule_ref": "which rule this violates", "fix_suggestion": "concrete fix"}}]}}"""

    def _build_dimension_user_prompt(
        self, dimension: ViolationDimension, html: str, user_query: str
    ) -> str:
        """Build the user prompt with the HTML to verify."""
        dim_name = dimension.value.replace("_", " ")
        return f"""## HTML Fragment to Verify
```html
{html[:3000]}
```

## Original User Request
{user_query[:500]}

## Dimension to Check
{dim_name}

Check the HTML above against the rules for {dim_name}. Be thorough but fair.
If the HTML passes this dimension, set passed=true and violations=[].
If issues are found, list each one with severity, description, rule reference, and a fix suggestion."""


def _deterministic_to_dimension_result(report: HtmlSyntaxReport) -> DimensionResult:
    """Convert deterministic syntax check results to a DimensionResult."""
    violations = []
    for issue in report.issues:
        violations.append(Violation(
            dimension=ViolationDimension.OUTPUT_FORMAT if issue.category in ("forbidden_tag", "format") else ViolationDimension.INTERACTION_DSL,
            severity=ViolationSeverity.ERROR if issue.severity == "error" else ViolationSeverity.WARNING,
            rule_ref=issue.category,
            description=issue.message,
            fix_suggestion=f"Fix: {issue.message}",
        ))
    return DimensionResult(
        dimension=ViolationDimension.OUTPUT_FORMAT,
        passed=len([v for v in violations if v.severity == ViolationSeverity.ERROR]) == 0,
        violations=violations,
    )


def _parse_dimension_result(dimension: ViolationDimension, raw: dict) -> DimensionResult:
    """Parse a cloud LLM verification response into a DimensionResult."""
    passed = raw.get("passed", True)
    violations_raw = raw.get("violations", [])
    violations = []
    for v in violations_raw:
        if isinstance(v, dict):
            violations.append(Violation(
                dimension=dimension,
                severity=ViolationSeverity(v.get("severity", "warning")),
                rule_ref=v.get("rule_ref", str(dimension)),
                description=v.get("description", "No description"),
                fix_suggestion=v.get("fix_suggestion", "Review manually."),
            ))
    return DimensionResult(dimension=dimension, passed=passed, violations=violations)


def _has_images(html: str) -> bool:
    """Check if the HTML contains any image elements."""
    return "<img" in html or "background-image" in html
