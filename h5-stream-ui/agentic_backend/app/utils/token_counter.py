"""
Token counting for context-window budget enforcement.

Uses a simple estimation strategy (char/4 ≈ tokens for English, char/2 for CJK)
with an optional tiktoken backend if available.
"""

from __future__ import annotations

import re

# Try to use tiktoken for accurate counting; fall back to estimation
try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
    # cl100k_base is the encoding used by GPT-4 and most modern OpenAI models
    _DEFAULT_ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    _DEFAULT_ENCODING = None

# CJK character range detection
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯]")


class TokenCounter:
    """Estimates token count for a string, with optional tiktoken precision."""

    def __init__(self, model: str | None = None):
        self._model = model
        self._encoding = None
        if _TIKTOKEN_AVAILABLE and model:
            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding = _DEFAULT_ENCODING
        elif _TIKTOKEN_AVAILABLE:
            self._encoding = _DEFAULT_ENCODING

    def count(self, text: str) -> int:
        """Estimate token count for the given text."""
        if not text:
            return 0

        if self._encoding is not None:
            return len(self._encoding.encode(text))

        # Fallback: heuristic estimation
        return self._estimate(text)

    @staticmethod
    def _estimate(text: str) -> int:
        """Heuristic token estimation: ~4 chars/token for Latin, ~2 chars/token for CJK."""
        cjk_chars = len(_CJK_RE.findall(text))
        latin_chars = len(text) - cjk_chars
        # CJK characters are typically 1-2 tokens each in most tokenizers
        # Latin text averages ~4 characters per token
        return max(1, (cjk_chars // 2) + (latin_chars // 4))

    def fits(self, text: str, max_tokens: int) -> bool:
        """Check if text fits within max_tokens."""
        return self.count(text) <= max_tokens


# Singleton for convenience
_default_counter = TokenCounter()


def count_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count for text."""
    if model:
        return TokenCounter(model).count(text)
    return _default_counter.count(text)


def fits_in_budget(text: str, max_tokens: int) -> bool:
    """Check if text fits within a token budget."""
    return _default_counter.fits(text, max_tokens)
