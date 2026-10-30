"""Host checks for shown identifiers and current citation spans.

Presence is bounded evidence, not semantic truth or repository completeness.
The model critic remains a separate opinion rather than an independent oracle.
"""

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


def grounding_score(unknown: list[str], checked: int) -> float | None:
    """Identifier presence only; an empty denominator is not successful proof."""
    if type(checked) is not int or checked < 0 or len(unknown) > checked:
        raise ValueError("invalid identifier verification denominator")
    return None if checked == 0 else round(1 - len(unknown) / checked, 3)


def verification_report(unknown: list[str], checked: int, *, dropped_citations=0, retrieval_weak=False) -> dict:
    score = grounding_score(unknown, checked)
    return {"schema_version": 1, "method": "identifier_presence", "status":
            "not_assessed" if checked == 0 else ("failed" if unknown else "passed"),
            "checked": checked, "passed": checked - len(unknown), "failed": len(unknown), "score": score,
            "semantic_correctness": "not_assessed",
            "citation_spans": {"status": "not_assessed", "checked": 0},
            "omissions": (["weak_retrieval"] if retrieval_weak else []) +
                         (["invalid_node_citations"] if dropped_citations else []),
            "limits": "Checks backticked identifier presence in shown context, not claim truth or semantic completeness."}


def citation_span(citation, node, *, current_hash: str, shown_complete: bool, source=None) -> dict:
    """Validate explicit line coordinates only against current complete evidence.

    A name-only citation remains not_assessed for line-span verification. A
    summary/signature or truncated rendering cannot prove an arbitrary line.
    """
    report = {"node_id": node.node_id, "path": node.path, "source_digest": current_hash,
              "status": "not_assessed", "lines": citation.lines}
    if citation.lines is None:
        return report
    match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", citation.lines.strip())
    from atomic_flow.agents.source import digest
    if (not match or not shown_complete or not current_hash or source is None or digest(source) != current_hash
            or (node.content_hash and node.content_hash != current_hash)):
        return {**report, "status": "failed", "reason": "unavailable or stale complete source span"}
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    if not node.start_line <= start <= end <= node.end_line:
        return {**report, "status": "failed", "reason": "line span outside cited current node"}
    from atomic_flow.activation.redact import redact
    lines = source.decode("utf-8-sig").splitlines(keepends=True)
    if end > len(lines):
        return {**report, "status": "failed", "reason": "line span exceeds current source"}
    return {**report, "status": "passed", "start_line": start, "end_line": end,
            "quote": redact("".join(lines[start - 1:end]))}
