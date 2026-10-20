"""The v2 writing doctrine: a short, plain, byte-stable system block.

It replaces ~4,100 tokens of v1 rule blocks. It is deliberately nearly content-free:
the register comes from the creator's own transcripts (exemplar turns), not from
rules, so the doctrine must not turn into a new house voice. Budget-capped (a test
enforces it) and version-hashed; every v2 script carries `doctrineVersion` so results
can be segmented by doctrine.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("doctrine.md")
MAX_TOKENS = 500            # ~4 chars per token


@lru_cache(maxsize=1)
def text() -> str:
    return _PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def version() -> str:
    return "wd-" + hashlib.sha1(text().encode("utf-8")).hexdigest()[:10]


def approx_tokens(s: str) -> int:
    return (len(s) + 3) // 4
