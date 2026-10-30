"""One redaction boundary for model-facing and persisted command/tool output.

This masks recognized credential forms, not arbitrary or encoded secret data.
Raw byte protocols must retain framing and sanitize their decoded public views.
"""
from __future__ import annotations

import re
from typing import Any

from atomic_flow.activation.redact import redact

_PRIVATE_FIELD = re.compile(r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?token|private[_-]?key)")


def sanitize_output(value: Any) -> Any:
    """Copy JSON-like output while masking string values and credential fields."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if isinstance(key, str) and _PRIVATE_FIELD.fullmatch(key)
                      and isinstance(item, str) else sanitize_output(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_output(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_output(item) for item in value)
    return value
