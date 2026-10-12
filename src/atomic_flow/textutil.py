"""Small text helpers shared by the indexer and the context builder."""

from __future__ import annotations


def split_lines(text: str) -> list[str]:
    """Split like Python's tokenizer does (\\n, \\r\\n, \\r only).

    ``str.splitlines`` also splits on form feeds, \\x1c-\\x1e, \\x85 and U+2028/2029.
    ``ast`` does not, so using ``splitlines`` shifts every line number after such a
    character and makes the stored ranges point at the wrong code.
    """
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def estimate_tokens(text: str) -> int:
    """Cheap, slightly pessimistic token estimate.

    Uses UTF-8 bytes instead of characters so Cyrillic/CJK text (2-3 bytes per char and
    typically 1+ token per 2 chars) is not underestimated by 2x like ``len(text) // 4``.
    """
    return (len(text.encode("utf-8")) + 3) // 4
