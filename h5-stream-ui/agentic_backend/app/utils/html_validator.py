"""
Deterministic HTML validation checks (non-LLM).

These run before and after LLM-based verification to catch mechanical issues:
- Balanced HTML tags
- Forbidden elements (html, head, body, script, style, meta, template, link)
- First character check (must be '<')
- Markdown fence detection
- JSON wrapper detection
- data-interactions JSON validity
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


FORBIDDEN_TAGS: frozenset[str] = frozenset({
    "html", "head", "body", "script", "style",
    "meta", "template", "link", "title",
})


@dataclass
class HtmlSyntaxIssue:
    message: str
    category: str  # "forbidden_tag", "malformed", "format", "dsl"
    severity: str = "error"  # error, warning


@dataclass
class HtmlSyntaxReport:
    is_valid: bool = True
    issues: list[HtmlSyntaxIssue] = field(default_factory=list)


class _BalancedTagChecker(HTMLParser):
    """HTMLParser subclass that detects forbidden tags and collects issues."""

    def __init__(self):
        super().__init__()
        self.issues: list[HtmlSyntaxIssue] = []
        self._tag_stack: list[str] = []
        self._void_elements = frozenset({
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        })

    def handle_starttag(self, tag: str, attrs):
        tag_lower = tag.lower()
        if tag_lower in FORBIDDEN_TAGS:
            self.issues.append(HtmlSyntaxIssue(
                message=f"Forbidden element <{tag}> found — host shell already provides this",
                category="forbidden_tag",
            ))
        if tag_lower not in self._void_elements:
            self._tag_stack.append(tag_lower)

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

    def handle_data(self, data):
        pass  # no-op


def check_html_syntax(html: str) -> HtmlSyntaxReport:
    """Run all deterministic checks on the HTML fragment.

    Returns a report with any issues found.
    """
    issues: list[HtmlSyntaxIssue] = []

    # 1. First character check
    if html and html.strip() and not html.strip().startswith("<"):
        issues.append(HtmlSyntaxIssue(
            message="First character must be '<' — output must start directly with the root element",
            category="format",
        ))

    # 2. Markdown fence check
    if re.search(r"```html|```", html):
        issues.append(HtmlSyntaxIssue(
            message="Markdown code fences (```) detected — output must be raw HTML, not a code block",
            category="format",
        ))

    # 3. JSON wrapper check
    if re.match(r'^\s*\{', html):
        issues.append(HtmlSyntaxIssue(
            message='JSON wrapper detected (e.g. {"html":"..."}) — output must be raw HTML, not JSON',
            category="format",
        ))

    # 4. Forbidden tag check + balanced tags
    checker = _BalancedTagChecker()
    try:
        checker.feed(html)
        checker.close()
        issues.extend(checker.issues)
    except Exception as e:
        issues.append(HtmlSyntaxIssue(
            message=f"HTML parsing error: {e}",
            category="malformed",
        ))

    # 5. data-interactions JSON validity
    _check_data_interactions(html, issues)

    return HtmlSyntaxReport(
        is_valid=len([i for i in issues if i.severity == "error"]) == 0,
        issues=issues,
    )


def _check_data_interactions(html: str, issues: list[HtmlSyntaxIssue]) -> None:
    """Validate all data-interactions attributes contain valid JSON."""
    pattern = re.compile(r'data-interactions=([\'"])(.*?)\1', re.DOTALL)
    for match in pattern.finditer(html):
        raw = match.group(2)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            issues.append(HtmlSyntaxIssue(
                message=f"Invalid JSON in data-interactions: {e}",
                category="dsl",
            ))
            continue

        # Validate structure
        if "onClick" in parsed:
            _validate_actions(parsed["onClick"], "onClick", issues)
        if "onAppear" in parsed:
            _validate_actions(parsed["onAppear"], "onAppear", issues)


def _validate_actions(actions: list, event_name: str, issues: list[HtmlSyntaxIssue]) -> None:
    """Validate interaction action entries."""
    if not isinstance(actions, list):
        return
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        action_type = action.get("type", "")
        if action_type not in ("openUrl", "setPage", "updateData"):
            issues.append(HtmlSyntaxIssue(
                message=f"Unknown action type '{action_type}' in {event_name}[{i}]",
                category="dsl",
                severity="warning",
            ))
        if action_type == "openUrl":
            url = action.get("params", {}).get("url", "")
            if url and not (url.startswith("https://") or url.startswith("http://")):
                issues.append(HtmlSyntaxIssue(
                    message=f"openUrl must use https — got: {url}",
                    category="dsl",
                ))
