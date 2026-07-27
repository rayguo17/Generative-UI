"""
Pass 1 — Analyze: Intent classification and data extraction.

This is the first and lightest pass (~500-800 tokens total).
It classifies the user's intent, extracts data fields, and determines
which generation modules (interactions, charts, etc.) are needed.

Output: dict matching the AnalysisResult schema.
"""

from __future__ import annotations

import json
import logging
import re

from app.generation.llm_client import GenerationLlmClient
from app.prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

# Schema description injected into the system prompt for JSON mode
ANALYSIS_JSON_SCHEMA = """{
  "intent": "card" | "dashboard" | "list" | "form" | "chart" | "custom",
  "summary": "one-sentence description of what to build",
  "data_fields": [
    {"name": "field_name", "type": "string|number|boolean|array|object|image_url", "path": "$.field.path", "sample_value": "example"}
  ],
  "needed_modules": ["interaction", "chart", "image", "pagination"],
  "complexity": 1-5,
  "has_interactions": true/false,
  "has_images": true/false,
  "data_is_tabular": true/false
}"""


async def analyze_user_request(
    query: str,
    llm: GenerationLlmClient,
    prompt_loader: PromptLoader,
) -> dict:
    """Classify the user's intent and extract data structure.

    Args:
        query: Raw user prompt (instructions + data).
        llm: Local LLM client.
        prompt_loader: Prompt loader for condensed system prompts.

    Returns:
        Dict matching AnalysisResult schema.
    """
    system_prompt = prompt_loader.load_for_step("classify")

    # Extract a preview of the data (truncate if too long)
    data_preview = _extract_data_preview(query, max_chars=800)

    user_prompt = f"""## Task
Analyze this user request for H5 card generation. Classify the intent, extract data fields, and determine which modules are needed.

## User Request
{query[:1200]}

## Data Preview
{data_preview}

## Output
Return a JSON object following this exact schema:
{ANALYSIS_JSON_SCHEMA}

Rules:
- intent: "card" for single info cards, "dashboard" for multi-metric, "list" for tables/lists, "form" for inputs, "chart" for visualizations, "custom" for free-form
- needed_modules: include "interaction" if the user mentions buttons/links/navigation/pagination, "chart" if data is numeric trends, "image" if image URLs are present, "pagination" if list is long
- has_interactions: true if user mentions any click/tap/navigate action
- data_is_tabular: true if data has repeating rows/items
- complexity: 1=simple text card, 3=multi-section card, 5=complex dashboard with interactions"""

    result = await llm.generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_name="classify",
        max_tokens=4096,   # High for thinking models (qwen3 etc.)
    )

    # Ensure required fields
    result.setdefault("intent", "card")
    result.setdefault("summary", "Information card")
    result.setdefault("data_fields", [])
    result.setdefault("needed_modules", [])
    result.setdefault("complexity", 2)
    result.setdefault("has_interactions", False)
    result.setdefault("has_images", False)
    result.setdefault("data_is_tabular", False)

    logger.info("Analyze result: intent=%s, complexity=%d, modules=%s",
                 result.get("intent"), result.get("complexity"), result.get("needed_modules"))

    return result


def _extract_data_preview(query: str, max_chars: int = 800) -> str:
    """Extract a preview of structured data from the user query.

    Prioritizes JSON blocks, then key:value patterns, then first N chars.
    """
    # Try to find JSON blocks
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', query, re.DOTALL)
    if json_match:
        json_str = json_match.group(0)
        if len(json_str) <= max_chars:
            return json_str
        # Truncate JSON intelligently
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                preview = {}
                for k, v in list(data.items())[:10]:
                    if isinstance(v, (list, dict)):
                        preview[k] = f"[{_type_name(v)}: {_size_hint(v)}]"
                    else:
                        preview[k] = v
                return json.dumps(preview, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
        return json_str[:max_chars] + "..."

    # Try to find arrays
    array_match = re.search(r'\[.*?\]', query, re.DOTALL)
    if array_match:
        return array_match.group(0)[:max_chars]

    # Fallback: first N chars
    return query[:max_chars]


def _type_name(value) -> str:
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _size_hint(value) -> str:
    if isinstance(value, (list, dict)):
        return f"{len(value)} items"
    return ""
