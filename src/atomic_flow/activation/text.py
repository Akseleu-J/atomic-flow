"""Code-aware tokenization: identifier splitting, stop words, light stemming."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\W\d_]+", re.UNICODE)
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STOP = frozenset("""
a an the is are was were be been being to of in on for and or but if then than as at by with from into
it its this that these those there here how what which who whom whose where when why does do did done
can could will would should may might must shall i me my you your we our they their he she his her
not no yes only also just any all each some such very more most about over under up down out off so
use used using exact explain show tell give find make makes made want need please
def class self cls none true false return async await import lambda pass yield raise try except
finally while elif else del global nonlocal assert
""".split())
_STEM_SUFFIXES = ("ization", "ations", "ation", "ables", "able", "ments", "ment", "ness", "ings",
                  "ing", "ers", "er", "ies", "es", "ed", "s", "ly")
SYNONYMS: dict[str, set[str]] = {
    "gradient": {"grad", "grads", "bwd", "backward", "dhu", "dav", "vjp"},
    "backward": {"bwd", "back", "grad", "gradient", "vjp"},
    "forward": {"fwd", "fw"},
    "state": {"h0", "h", "hidden", "carry", "h_pre", "h_next", "hstate"},
    "chunked": {"chunk", "chunks", "chunking"},
    "chunk": {"chunked", "chunks", "chunking"},
    "kernel": {"body", "pallas"},
    "combine": {"comb", "merge", "scan"},
    "decay": {"gc", "g_decay"},
    "inverse": {"inv", "solve"},
    "score": {"scores", "logits"},
    "layer": {"block", "module"},
}


def split_identifier(token: str) -> list[str]:
    """'is_refundable' -> ['is', 'refundable'];  'parseJSONFile' -> ['parse', 'json', 'file']."""
    parts: list[str] = []
    for chunk in token.split("_"):
        parts.extend(p.lower() for p in _CAMEL.findall(chunk))
    return parts


def stem(word: str) -> str:
    """Deliberately crude: both sides of a match go through the same function, so it only has
    to be *consistent* (refund/refunds/refundable -> refund), not linguistically correct."""
    if len(word) <= 4 or not word.isascii():
        return word
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def _keep(word: str) -> bool:
    return (len(word) > 1 and word not in STOP) and (not word.isdigit() or len(word) >= 2)


def terms(text: str) -> list[str]:
    """Index/query terms. Identifiers contribute BOTH the whole name and its stemmed parts, so a
    query for `refundable` finds `is_refundable` and a query for `refund_message` finds the exact symbol."""
    out: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group()
        if token.isascii():
            parts = split_identifier(token)
            if len(parts) > 1:
                out.append(token.lower())
            out.extend(stem(p) for p in parts if _keep(p))
        else:
            word = token.lower()
            if _keep(word):
                out.append(word)
    expanded: list[str] = []
    for term in out:
        expanded.append(term)
        for synonym in SYNONYMS.get(term, set()) | SYNONYMS.get(stem(term), set()):
            expanded.append(synonym)
            expanded.append(stem(synonym))
    return expanded


def raw_terms(text: str) -> set[str]:
    """Unstemmed lowercase tokens (whole identifiers and their parts) for exact-name priors."""
    result: set[str] = set()
    for match in _TOKEN.finditer(text):
        token = match.group()
        result.add(token.lower())
        result.update(split_identifier(token))
    return result


def has_non_latin_letters(text: str) -> bool:
    return any(ch.isalpha() and not ch.isascii() for ch in text)
