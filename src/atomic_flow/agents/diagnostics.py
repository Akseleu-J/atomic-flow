"""Bounded quality diagnostics with project-confined, revision-bound locations."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from atomic_flow.agents.source import confined_path, digest

_LOCATION = re.compile(r"^(.+?):(\d+)(?::(\d+))?:\s*(?:(error|warning|note):\s*)?(.*)$")


def syntax_diagnostic(path: str, data: bytes, exc: SyntaxError) -> dict:
    return {"path": path, "line": exc.lineno or 1, "column": exc.offset or 1,
            "end_line": exc.end_lineno or exc.lineno or 1, "end_column": exc.end_offset or exc.offset or 1,
            "severity": "error", "message": exc.msg[:500], "code": "syntax", "source": "python_compile",
            "content_hash": digest(data)}


def quality_diagnostics(command: str, text: str, root: Path, hashes: dict[str, str]) -> dict:
    results = []
    stale = set()
    for line in text.splitlines()[:1000]:
        match = _LOCATION.match(line)
        if not match:
            continue
        raw, row, column, severity, message = match.groups()
        if int(row) < 1 or column is not None and int(column) < 1:
            continue
        raw = raw.replace("\\", "/")
        if raw.startswith("/workspace/"):
            raw = raw.removeprefix("/workspace/")
        else:
            candidate = Path(raw)
            if candidate.is_absolute():
                try:
                    raw = candidate.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
        if PurePosixPath(raw).is_absolute() or ":" in raw or ".." in PurePosixPath(raw).parts or raw not in hashes:
            continue
        try:
            if digest(confined_path(root, raw).read_bytes()) != hashes[raw]:
                stale.add(raw)
                continue
        except OSError:
            stale.add(raw)
            continue
        code = re.match(r"([A-Z]+\d{3,5})\s+", message)
        mypy_code = re.search(r"\[([a-z0-9-]+)\]$", message)
        results.append({"path": raw, "line": int(row), "column": int(column or 1), "severity": severity or "error",
                        "message": message[:1000], "code": code[1] if code else mypy_code[1] if mypy_code else "",
                        "source": command, "content_hash": hashes[raw]})
        if len(results) == 100:
            break
    return {"diagnostics": results, "diagnostics_limit": 100, "diagnostics_may_be_truncated": len(results) == 100,
            "stale_diagnostic_paths": sorted(stale), "diagnostic_basis": "Parsed bounded output tail; paths/hashes validated after the check."}
