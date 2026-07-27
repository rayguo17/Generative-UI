"""
Stream HTML to stdout (OpenAI-compatible API).

Usage:
  python stream_cli.py -m "做成资讯卡片，数据如下：\\n\\n{\"title\":\"示例\"}"
  python stream_cli.py -f request.txt
  cat request.txt | python stream_cli.py --stdin
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from prompt_loader import build_user_message, load_system_prompt

load_dotenv()


def _read_stdin() -> str:
    return sys.stdin.read()


async def _run(*, content: str) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        raise SystemExit(2)
    base_url = os.getenv("OPENAI_BASE_URL") or None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    system = load_system_prompt()
    user = build_user_message(content=content)
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
        stream=True,
    )
    async for event in stream:
        choice = event.choices[0] if event.choices else None
        if not choice:
            continue
        delta = choice.delta
        if delta and delta.content:
            sys.stdout.write(delta.content)
            sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser(description="Stream H5 JSON+HTML fragment from LLM to stdout")
    p.add_argument(
        "-m",
        "--message",
        help="Full user request: instructions and data (JSON or prose) in one string",
    )
    p.add_argument("-f", "--input-file", type=Path, help="Read full user request from this file")
    p.add_argument("--stdin", action="store_true", help="Read full user request from stdin")
    args = p.parse_args()
    n = sum(x is not None for x in (args.message, args.input_file)) + (1 if args.stdin else 0)
    if n != 1:
        raise SystemExit("Provide exactly one of: --message / -m, --input-file / -f, --stdin")
    if args.message is not None:
        content = args.message
    elif args.input_file is not None:
        content = args.input_file.read_text(encoding="utf-8")
    else:
        content = _read_stdin()
    content = content.strip()
    if not content:
        raise SystemExit("User request is empty")
    asyncio.run(_run(content=content))


if __name__ == "__main__":
    main()
