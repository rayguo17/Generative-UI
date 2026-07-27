"""
Verification models — structured output from the cloud LLM verification workflow.

Each dimension check produces a DimensionResult with a list of Violations.
The aggregate VerificationReport combines all dimensions into a pass/fail decision.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class ViolationSeverity(StrEnum):
    """Severity of a verification violation."""
    ERROR = "error"        # Must be fixed — blocks pass
    WARNING = "warning"    # Should be fixed — does not block pass
    INFO = "info"          # Suggestion for improvement


class ViolationDimension(StrEnum):
    """Verification dimensions corresponding to prompt rule categories."""
    HTML_SYNTAX = "html_syntax"            # Valid HTML, balanced tags
    OUTPUT_FORMAT = "output_format"         # Fragment rules (no shell elements, first char <)
    DESIGN_QUALITY = "design_quality"       # Visual hierarchy, single accent, spacing
    DATA_FIDELITY = "data_fidelity"         # Source binding, no fabrication, array completeness
    INTERACTION_DSL = "interaction_dsl"     # data-interactions validity
    RESPONSIVE_LAYOUT = "responsive_layout" # flex-1/min-w-0/shrink-0, flex-wrap, truncation
    HARMONY_SPEC = "harmony_spec"           # HarmonyOS card rules (rounded-[20px], typography, buttons)
    IMAGE_PROCESSING = "image_processing"   # Image role classification, decorative rules
    CHART_COMPLIANCE = "chart_compliance"   # Chart type rules, mobile constraints
    SECURITY = "security"                   # No eval, no onclick, no javascript: URLs


class Violation(BaseModel):
    """A single violation found during verification."""
    dimension: ViolationDimension
    severity: ViolationSeverity
    rule_ref: str = Field(
        ...,
        description="Reference to the prompt rule, e.g. '03-output-format.md §1'",
    )
    description: str = Field(
        ...,
        description="Human-readable description of what is wrong",
    )
    location_hint: Optional[str] = Field(
        default=None,
        description="CSS selector or line-level hint to locate the issue",
    )
    fix_suggestion: str = Field(
        ...,
        description="Concrete, actionable fix instruction for the generation LLM",
    )


class DimensionResult(BaseModel):
    """Result of a single verification dimension check."""
    dimension: ViolationDimension
    passed: bool
    violations: list[Violation] = Field(default_factory=list)


class VerificationReport(BaseModel):
    """Aggregate verification report combining all dimension checks."""
    overall_pass: bool
    dimensions: list[DimensionResult] = Field(default_factory=list)
    total_violations: int = 0
    error_count: int = 0
    warning_count: int = 0
    critical_fixes_needed: list[str] = Field(
        default_factory=list,
        description="Top-level fix instructions, ordered by priority",
    )
    summary: str = ""

    @classmethod
    def from_dimension_results(cls, results: list[DimensionResult]) -> "VerificationReport":
        """Build an aggregate report from individual dimension results."""
        total = sum(len(r.violations) for r in results)
        errors = sum(
            len([v for v in r.violations if v.severity == ViolationSeverity.ERROR])
            for r in results
        )
        warnings = sum(
            len([v for v in r.violations if v.severity == ViolationSeverity.WARNING])
            for r in results
        )
        passed = all(r.passed for r in results)

        # Collect critical fixes (errors first, then warnings)
        critical = [
            v.fix_suggestion
            for r in results
            for v in r.violations
            if v.severity == ViolationSeverity.ERROR
        ]
        if not critical:
            critical = [
                v.fix_suggestion
                for r in results
                for v in r.violations
                if v.severity == ViolationSeverity.WARNING
            ][:3]

        summary_parts = [f"{'PASS' if passed else 'FAIL'} — "]
        summary_parts.append(f"{len(results)} dimensions checked, ")
        summary_parts.append(f"{total} violations ({errors} errors, {warnings} warnings)")

        return cls(
            overall_pass=passed,
            dimensions=results,
            total_violations=total,
            error_count=errors,
            warning_count=warnings,
            critical_fixes_needed=critical,
            summary="".join(summary_parts),
        )
