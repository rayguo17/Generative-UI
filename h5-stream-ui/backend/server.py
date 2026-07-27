from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import aiohttp
from openai import AsyncOpenAI

from pydantic import BaseModel, Field, field_validator, model_validator

from prompt_loader import build_user_message, load_system_prompt

load_dotenv()

logger = logging.getLogger("h5-stream-ui")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="H5 Stream UI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateBody(BaseModel):
    """One text field: natural-language instructions plus optional pasted JSON or prose data."""

    query: str = Field(default="", description="Full user request (instructions + data in one block)")
    model: str | None = Field(
        default=None,
        description="Chat model id (OpenAI-compatible). Empty = server OPENAI_MODEL.",
    )
    base_url: str | None = Field(
        default=None,
        description="Optional provider base URL. Empty = server OPENAI_BASE_URL.",
    )
    api_key: str | None = Field(
        default=None,
        description="Optional provider API key. Empty = server OPENAI_API_KEY.",
    )

    @field_validator("model", mode="before")
    @classmethod
    def _normalize_model(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("model must be a string")
        s = v.strip()
        if not s:
            return None
        if len(s) > 128 or "\n" in s or "\r" in s:
            raise ValueError("invalid model")
        return s

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("base_url must be a string")
        s = v.strip()
        if not s:
            return None
        if len(s) > 512 or "\n" in s or "\r" in s:
            raise ValueError("invalid base_url")
        return s

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("api_key must be a string")
        s = v.strip()
        if not s:
            return None
        if len(s) > 512 or "\n" in s or "\r" in s:
            raise ValueError("invalid api_key")
        return s

    @model_validator(mode="after")
    def _non_empty(self) -> GenerateBody:
        if not (self.query or "").strip():
            raise ValueError("query must not be empty")
        return self


def _client(*, api_key: str, base_url: str | None) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key or "missing-key", base_url=base_url)


async def _stream_chat(
    *, system: str, user: str, model: str, api_key: str, base_url: str | None
) -> AsyncIterator[str]:
    client = _client(api_key=api_key, base_url=base_url)
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for event in stream:
        # Usage appears in the final chunk (with finish_reason="stop")
        if getattr(event, "usage", None):
            u = event.usage
            logger.info(
                "stream_chat USAGE prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                getattr(u, "prompt_tokens", None),
                getattr(u, "completion_tokens", None),
                getattr(u, "total_tokens", None),
            )
        choice = event.choices[0] if event.choices else None
        if not choice:
            continue
        delta = choice.delta
        if not delta:
            continue
        text = getattr(delta, "content", None)
        if isinstance(text, str) and text:
            yield text
            continue
        # Some OpenAI-compatible providers emit non-standard text fields.
        # Intentionally ignore reasoning_content to avoid exposing model reasoning.
        alt_text = getattr(delta, "text", None)
        if isinstance(alt_text, str) and alt_text:
            yield alt_text
            continue
        # Keep-alive signal for providers that emit only reasoning deltas first.
        # Empty string is consumed by event_stream as "upstream responded", but not forwarded.
        reasoning_text = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning_text, str) and reasoning_text:
            yield ""


def _hex_prefix(data: bytes, n: int = 10000) -> str:
    """First *n* bytes as hex + safe-ascii, for spotting control / binary bytes."""
    if not data:
        return "(empty)"
    chunk = data[:n]
    hex_part = chunk.hex(" ").upper()
    ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    suffix = "…" if len(data) > n else ""
    return f"{hex_part}  |{ascii_part}|{suffix}"


async def _stream_chat_debug(
    *, system: str, user: str, model: str, api_key: str, base_url: str | None
) -> AsyncIterator[str]:
    """Raw HTTP streaming with per‑chunk byte‑level logging.

    Set env ``STREAM_DEBUG=1`` to use this instead of ``_stream_chat``.
    """
    url = f"{base_url.rstrip('/')}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    logger.info("DEBUG url=%s", url)
    logger.info("DEBUG request_body_size=%s chars", len(json.dumps(request_body, ensure_ascii=False)))

    timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url,
            json=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            logger.info(
                "DEBUG response status=%s content_type=%s headers=%s",
                resp.status,
                resp.headers.get("Content-Type", "-"),
                {k: v for k, v in resp.headers.items()},
            )

            chunk_no = 0
            event_no = 0
            buffer = b""
            t_last_chunk = time.perf_counter()

            async for chunk in resp.content.iter_any():
                chunk_no += 1
                t_now = time.perf_counter()
                gap_ms = (t_now - t_last_chunk) * 1000.0
                t_last_chunk = t_now

                buf_before = len(buffer)
                buffer += chunk

                # Count non-UTF-8-continuation bytes 0x80‑0xFF that aren't valid
                # lead bytes — quick heuristic to flag binary-looking data.
                suspicious = [b for b in chunk if b < 0x20 and b not in (0x0A, 0x0D)]

                logger.info(
                    "DEBUG chunk#%-4d size=%-5d gap_ms=%7.1f buf_before=%-5d buf_after=%-5d "
                    "suspicious_ctrl=%d hex_first_40=%s",
                    chunk_no,
                    len(chunk),
                    gap_ms,
                    buf_before,
                    len(buffer),
                    len(suspicious),
                    _hex_prefix(chunk),
                )

                # Split on \n\n (SSE event boundary)
                while True:
                    idx = buffer.find(b"\n\n")
                    if idx < 0:
                        logger.info("DEBUG cannot find next line")
                        break
                    raw_event = buffer[:idx]
                    buffer = buffer[idx + 2:]

                    event_no += 1
                    logger.info(
                        "DEBUG   → event#%-4d raw_bytes=%-5d hex_first_40=%s",
                        event_no,
                        len(raw_event),
                        _hex_prefix(raw_event),
                    )

                    t_decode_start = time.perf_counter()
                    try:
                        line_str = raw_event.decode("utf-8", errors="replace")
                    except Exception as dec_err:
                        logger.info(
                            "DEBUG   → event#%-4d DECODE_EXCEPTION type=%s msg=%s",
                            event_no,
                            type(dec_err).__name__,
                            str(dec_err),
                        )
                        continue
                    t_decode_end = time.perf_counter()
                    decode_ms = (t_decode_end - t_decode_start) * 1000.0
                    if decode_ms > 5:
                        logger.info(
                            "DEBUG   → event#%-4d decode_ms=%.1f (SLOW)",
                            event_no,
                            decode_ms,
                        )

                    logger.info(
                        "DEBUG   → event#%-4d str_len=%-5d repr(first_250)=%s",
                        event_no,
                        len(line_str),
                        repr(line_str[:250]),
                    )

                    if not line_str.startswith("data:"):
                        continue
                    data_str = line_str[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        logger.info("DEBUG   → event#%-4d [DONE] or empty data", event_no)
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.info(
                            "DEBUG   → event#%-4d JSON_PARSE_FAIL raw_data_str=%s",
                            event_no,
                            repr(data_str[:500]),
                        )
                        continue
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            logger.info(
                                "DEBUG   → event#%-4d YIELD content=%s",
                                event_no,
                                repr(content),
                            )
                            yield content
                    # Usage appears in the final chunk (with finish_reason="stop")
                    usage = data.get("usage")
                    if usage:
                        logger.info(
                            "DEBUG   → event#%-4d USAGE prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                            event_no,
                            usage.get("prompt_tokens"),
                            usage.get("completion_tokens"),
                            usage.get("total_tokens"),
                        )
                    else:
                        logger.info("No usage field")

            # Drain any remaining data (last event may not end with \n\n)
            if buffer.strip():
                event_no += 1
                logger.info(
                    "DEBUG   → event#%-4d (final_no_nl) raw_bytes=%-5d hex_first_40=%s",
                    event_no,
                    len(buffer),
                    _hex_prefix(buffer),
                )
                line_str = buffer.decode("utf-8", errors="replace")
                logger.info(
                    "DEBUG   → event#%-4d (final_no_nl) str_len=%-5d repr(first_250)=%s",
                    event_no,
                    len(line_str),
                    repr(line_str[:250]),
                )

            logger.info("DEBUG stream ended — chunks=%d events=%d remaining_buffer=%d",
                        chunk_no, event_no, len(buffer))


def _stream_chat_enabled() -> bool:
    return os.getenv("STREAM_DEBUG", "0").strip() in ("1", "true", "yes", "on")


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _timing_enabled() -> bool:
    return os.getenv("STREAM_TIMING_LOG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _first_token_timeout_sec() -> float:
    raw = os.getenv("FIRST_TOKEN_TIMEOUT_SEC", "45").strip()
    try:
        val = float(raw)
    except ValueError:
        return 45.0
    return max(1.0, val)


def _total_stream_timeout_sec() -> float:
    raw = os.getenv("TOTAL_STREAM_TIMEOUT_SEC", "180").strip()
    try:
        val = float(raw)
    except ValueError:
        return 180.0
    return max(5.0, val)


# 与 chat_renderer 的 extract 逻辑一致：模型常以 table/ul 做榜单，仅放行 div 会导致永不吐 token、预览全空。
_ALLOWED_ROOT_TAGS: tuple[str, ...] = (
    "div",
    "main",
    "section",
    "article",
    "table",
    "ul",
    "ol",
    "nav",
    "header",
    "figure",
)


def _is_html_tag_boundary(ch: str) -> bool:
    return ch in (" ", "\t", "\r", "\n", ">", "/")


def _find_whitelisted_root_start(text: str) -> int:
    """Return first index of allowed HTML root tag; -1 if not found yet."""
    lower = text.lower()
    i = 0
    while True:
        i = lower.find("<", i)
        if i < 0:
            return -1
        for tag in _ALLOWED_ROOT_TAGS:
            prefix = f"<{tag}"
            if not lower.startswith(prefix, i):
                continue
            end = i + len(prefix)
            if end >= len(lower) or _is_html_tag_boundary(lower[end]):
                return i
        i += 1


class _ThinkTagFilter:
    """Stream filter: strip <think ...>...</think> blocks across chunk boundaries."""

    def __init__(self) -> None:
        self._pending = ""
        self._in_think = False

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        text = self._pending + chunk
        self._pending = ""
        out_parts: list[str] = []
        i = 0

        while i < len(text):
            lower_text = text.lower()
            if self._in_think:
                close_idx = lower_text.find("</think>", i)
                if close_idx < 0:
                    # Keep a small suffix so "</think>" can be matched when split across chunks.
                    keep = min(7, len(text) - i)
                    self._pending = text[len(text) - keep :]
                    return "".join(out_parts)
                i = close_idx + len("</think>")
                self._in_think = False
                continue

            open_idx = lower_text.find("<think", i)
            if open_idx < 0:
                tail_hold = self._tail_hold_len(text)
                if tail_hold > 0:
                    out_parts.append(text[i : len(text) - tail_hold])
                    self._pending = text[len(text) - tail_hold :]
                else:
                    out_parts.append(text[i:])
                return "".join(out_parts)

            out_parts.append(text[i:open_idx])
            tag_end = text.find(">", open_idx)
            if tag_end < 0:
                self._pending = text[open_idx:]
                return "".join(out_parts)
            is_self_closing = text[max(open_idx, tag_end - 1)] == "/"
            i = tag_end + 1
            if not is_self_closing:
                self._in_think = True

        return "".join(out_parts)

    @staticmethod
    def _tail_hold_len(text: str) -> int:
        """Keep partial tag prefixes at the end for next chunk."""
        max_hold = 0
        probes = ("<think", "</think>")
        for probe in probes:
            upper = min(len(probe) - 1, len(text))
            for n in range(1, upper + 1):
                if text.endswith(probe[:n]):
                    max_hold = max(max_hold, n)
        return max_hold


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate")
async def generate(body: GenerateBody) -> StreamingResponse:
    resolved_api_key = body.api_key or os.getenv("OPENAI_API_KEY", "")
    resolved_base_url = body.base_url or (os.getenv("OPENAI_BASE_URL") or None)
    if not resolved_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set")

    t0 = time.perf_counter()
    q = body.query.strip()
    t_after_payload = time.perf_counter()
    system = load_system_prompt()
    t_after_system = time.perf_counter()
    user = build_user_message(content=q)
    t_after_user = time.perf_counter()
    model_name = body.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def event_stream() -> AsyncIterator[str]:
        t_stream_enter = time.perf_counter()
        t_after_start_event: float | None = None
        first_upstream_delta_at: float | None = None
        first_html_sse_at: float | None = None
        last_emit_at: float | None = None
        html_started = False
        preface_buffer = ""
        think_filter = _ThinkTagFilter()
        chunk_count = 0
        char_count = 0
        err: Exception | None = None
        first_token_timeout_sec = _first_token_timeout_sec()
        total_timeout_sec = _total_stream_timeout_sec()
        if _stream_chat_enabled():
            logger.info("STREAM_DEBUG=1 — using _stream_chat_debug")
            chat_fn = _stream_chat_debug
        else:
            chat_fn = _stream_chat
        stream_iter = chat_fn(
            system=system,
            user=user,
            model=model_name,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        ).__aiter__()
        try:
            yield _sse({"type": "start"})
            t_after_start_event = time.perf_counter()
            while True:
                now = time.perf_counter()
                elapsed_sec = now - t_stream_enter
                remaining_total_sec = total_timeout_sec - elapsed_sec
                if remaining_total_sec <= 0:
                    raise TimeoutError(
                        f"stream timeout: exceeded {total_timeout_sec:.0f}s without completion"
                    )
                if first_upstream_delta_at is None:
                    wait_timeout_sec = min(first_token_timeout_sec, remaining_total_sec)
                else:
                    wait_timeout_sec = min(30.0, remaining_total_sec)
                try:
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=wait_timeout_sec)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as timeout_exc:
                    if first_upstream_delta_at is None:
                        raise TimeoutError(
                            f"first token timeout: no upstream delta within {first_token_timeout_sec:.0f}s"
                        ) from timeout_exc
                    raise TimeoutError(
                        f"stream stalled: no delta for {wait_timeout_sec:.0f}s (total limit {total_timeout_sec:.0f}s)"
                    ) from timeout_exc
                now = time.perf_counter()
                if last_emit_at is None or (now - last_emit_at) >= 1.0:
                    yield _sse({"type": "ping"})
                    last_emit_at = now
                if first_upstream_delta_at is None:
                    first_upstream_delta_at = time.perf_counter()
                if chunk:
                    chunk = think_filter.feed(chunk)
                    if not chunk:
                        continue
                    if not html_started:
                        preface_buffer += chunk
                        start_idx = _find_whitelisted_root_start(preface_buffer)
                        if start_idx < 0:
                            continue
                        html_started = True
                        chunk = preface_buffer[start_idx:]
                        preface_buffer = ""
                    if first_html_sse_at is None:
                        first_html_sse_at = time.perf_counter()
                    chunk_count += 1
                    char_count += len(chunk)
                    yield _sse({"type": "token", "content": chunk})
                    last_emit_at = time.perf_counter()
            yield _sse({"type": "done"})
        except Exception as e:  # noqa: BLE001 — surface provider errors to client
            err = e
            yield _sse({"type": "error", "message": str(e)})
        finally:
            if _timing_enabled():
                t_end = time.perf_counter()
                prep_ms = (t_after_user - t0) * 1000.0
                payload_ms = (t_after_payload - t0) * 1000.0
                system_ms = (t_after_system - t_after_payload) * 1000.0
                user_ms = (t_after_user - t_after_system) * 1000.0
                queue_to_stream_ms = (t_stream_enter - t_after_user) * 1000.0
                start_event_ms = (
                    (t_after_start_event - t_stream_enter) * 1000.0
                    if t_after_start_event is not None
                    else -1.0
                )
                upstream_first_delta_ms = (
                    (first_upstream_delta_at - t_stream_enter) * 1000.0
                    if first_upstream_delta_at is not None
                    else None
                )
                first_html_sse_ms = (
                    (first_html_sse_at - t_stream_enter) * 1000.0
                    if first_html_sse_at is not None
                    else None
                )
                first_html_from_request_ms = (
                    (first_html_sse_at - t0) * 1000.0 if first_html_sse_at is not None else None
                )
                preface_gap_ms = (
                    (first_html_sse_at - first_upstream_delta_at) * 1000.0
                    if first_html_sse_at is not None and first_upstream_delta_at is not None
                    else None
                )
                total_ms = (t_end - t0) * 1000.0
                logger.info(
                    "stream_timing model=%s chars_sys=%s chars_user=%s "
                    "prep_ms=%.1f (payload=%.1f system_prompt=%.1f user_msg=%.1f) "
                    "queue_to_stream_ms=%.1f sse_start_emit_ms=%.1f "
                    "upstream_first_delta_ms=%s first_html_sse_ms=%s first_html_from_request_ms=%s "
                    "preface_gap_ms=%s chunks=%s out_chars=%s total_ms=%.1f err=%s",
                    model_name,
                    len(system),
                    len(user),
                    prep_ms,
                    payload_ms,
                    system_ms,
                    user_ms,
                    queue_to_stream_ms,
                    start_event_ms,
                    f"{upstream_first_delta_ms:.1f}"
                    if upstream_first_delta_ms is not None
                    else "n/a",
                    f"{first_html_sse_ms:.1f}" if first_html_sse_ms is not None else "n/a",
                    f"{first_html_from_request_ms:.1f}"
                    if first_html_from_request_ms is not None
                    else "n/a",
                    f"{preface_gap_ms:.1f}" if preface_gap_ms is not None else "n/a",
                    chunk_count,
                    char_count,
                    total_ms,
                    type(err).__name__ if err else "none",
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("server:app", host=host, port=port, reload=True)
