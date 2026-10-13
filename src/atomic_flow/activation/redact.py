"""Mask credentials before source text is sent to a model provider."""

from __future__ import annotations

import re

_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_TOKENS = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}\b"
)
_LITERAL = re.compile(
    r"""(?i)(\b(?:api[_-]?key|secret|token|password|passwd)(?!\w*_env)\w*\s*[:=]\s*)(['"])[^'"\s]{8,}\2"""
)


def _blank(match: re.Match[str]) -> str:
    return "[REDACTED]" + "\n" * match.group(0).count("\n")


def redact(text: str) -> str:
    text = _BLOCK.sub(_blank, text)
    text = _TOKENS.sub("[REDACTED]", text)
    return _LITERAL.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}", text)
