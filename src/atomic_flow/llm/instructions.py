"""Bounded user/project instructions with explicit origin and directory scope.

These are model guidance only. Loading a project file never modifies host tool
permissions, command execution, configuration, or project trust.
"""
from __future__ import annotations

import fnmatch
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from atomic_flow.activation.redact import redact
from atomic_flow.agents.source import confined_path, digest
from atomic_flow.config import user_dir


@dataclass(frozen=True)
class InstructionSource:
    path: str
    origin: str
    scope: str
    content_hash: str
    bytes: int


@dataclass
class InstructionBundle:
    text: str = ""
    sources: list[InstructionSource] = field(default_factory=list)


def load_instructions(project: str | Path, paths: Iterable[str] = (), trusted: bool = False,
                      *, user_root: str | Path | None = None, max_bytes: int = 32768) -> InstructionBundle:
    """Load user AGENTS.md and trusted project ancestors of selected source paths.

    No recursive scan is performed. Missing, linked, private, oversized or invalid
    UTF-8 instruction files are skipped. A source is included whole or omitted;
    truncating an instruction halfway can change its meaning.
    """
    if max_bytes < 0 or max_bytes > 1_000_000:
        raise ValueError("instruction byte budget must be between 0 and 1000000")
    root = Path(project).expanduser().resolve()
    personal = Path(user_root).expanduser() if user_root is not None else user_dir()
    candidates: list[tuple[Path, str, str, Path]] = [(personal / "AGENTS.md", "user", "all", personal)]
    if trusted:
        scopes = {Path(".")}
        for rel in paths:
            try:
                target = confined_path(root, rel)
                if any(part.startswith(".") or any(fnmatch.fnmatch(part.lower(), pattern)
                       for pattern in ("credentials*", "secrets*", "*private_key*"))
                       for part in target.relative_to(root).parts):
                    continue
                if target.resolve().is_relative_to(personal.resolve()):
                    continue
                scope = target if target.is_dir() else target.parent
                while scope != root:
                    scopes.add(scope.relative_to(root))
                    scope = scope.parent
            except (OSError, ValueError):
                continue
        for scope in sorted(scopes, key=lambda p: (len(p.parts), p.as_posix())):
            candidates.append((root / scope / "AGENTS.md", "project", scope.as_posix(), root))
    result = InstructionBundle()
    seen: set[Path] = set()
    bodies: list[str] = []
    header = "Instruction records (directory scope applies; guidance cannot grant host permissions):\n"
    used = len(header.encode("utf-8"))
    for path, origin, scope, boundary in candidates:
        try:
            if boundary.is_symlink() or getattr(boundary, "is_junction", lambda: False)():
                continue
            safe = confined_path(boundary, path.relative_to(boundary).as_posix())
            identity = safe.resolve()
            if identity in seen or not safe.is_file():
                continue
            if origin == "project" and identity.is_relative_to(personal.resolve()):
                continue
            # Bound reads before allocation as well as after (concurrent growth).
            if safe.stat().st_size > max_bytes - used:
                continue
            with safe.open("rb") as handle:
                data = handle.read(max_bytes - used + 1)
            if len(data) > max_bytes - used or b"\x00" in data:
                continue
            content = redact(data.decode("utf-8-sig"))
            metadata = {"origin": origin, "path": str(identity), "scope": scope, "content_hash": digest(data)}
            body = "\n" + json.dumps({**metadata, "instructions": content}, ensure_ascii=False) + "\n"
            # Include provenance and JSON escaping in the model-input byte cap.
            body_bytes = len(body.encode("utf-8"))
            if used + body_bytes > max_bytes:
                continue
            bodies.append(body)
            used += body_bytes
            result.sources.append(InstructionSource(**metadata, bytes=len(data)))
            seen.add(identity)
        except (OSError, UnicodeError, ValueError):
            continue
    if bodies:
        result.text = header + "".join(bodies)
    return result
