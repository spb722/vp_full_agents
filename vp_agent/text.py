from __future__ import annotations

import re
from collections import Counter

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(text: object) -> list[str]:
    return TOKEN_RE.findall(str(text).lower())


def token_counter(*parts: object) -> Counter[str]:
    counter: Counter[str] = Counter()
    for part in parts:
        counter.update(tokens(part))
    return counter


def phrase_text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(phrase_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(phrase_text(v) for v in value)
    return "" if value is None else str(value)

