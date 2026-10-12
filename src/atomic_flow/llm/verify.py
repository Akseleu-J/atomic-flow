"""Deterministic grounding checks. A critic that is the same model as the answerer is a self-grader;
these checks cost nothing and cannot be talked out of a verdict."""

from __future__ import annotations

import builtins
import keyword
import re

_BACKTICK = re.compile(r"`([^`\n]{1,80})`")
_IDENT_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALLOWED = set(dir(builtins)) | set(keyword.kwlist) | {"self", "cls", "args", "kwargs"}


def unknown_identifiers(answer_text: str, context: str) -> tuple[list[str], int]:
    """Identifiers the answer puts in backticks that never occur in the supplied context.

    Returns ``(unknown, checked)``. A backticked name absent from every line the model was shown is
    either hallucinated or comes from outside knowledge - both deserve a second look.
    """
    known = set(_WORD.findall(context))
    unknown: list[str] = []
    checked = 0
    seen: set[str] = set()
    for match in _BACKTICK.finditer(answer_text):
        raw = match.group(1).strip()
        if not _IDENT_PATH.match(raw) or raw in seen:
            continue
        seen.add(raw)
        parts = raw.removesuffix("()").split(".")
        if all(p in _ALLOWED for p in parts):
            continue
        checked += 1
        if not all(p in known or p in _ALLOWED for p in parts):
            unknown.append(raw)
    return unknown, checked


def grounding_score(unknown: list[str], checked: int) -> float:
    return 1.0 if checked == 0 else round(1 - len(unknown) / checked, 3)
