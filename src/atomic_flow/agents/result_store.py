"""Request-scoped storage for redacted tool output, never an action replay log."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any

from atomic_flow.activation.redact import redact
from atomic_flow.agents.result import ToolResult

_SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|authorization|api[_-]?key|(?:^|_)token(?:$|_))")
_RESULT_ID = re.compile(r"[0-9a-f]{32}")


class ResultStoreError(ValueError):
    """A result is unavailable, corrupt, or outside storage bounds."""


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 24:
        raise ResultStoreError("result nesting exceeds 24 levels")
    if isinstance(value, str):
        return redact(value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {redact(key): "[REDACTED]" if _SECRET_KEY.search(key) else _clean(item, depth + 1)
                for key, item in value.items()}
    raise ResultStoreError("result must contain finite JSON values with text keys")


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class LargeResultStore:
    """Keep bounded results for one live request, with opaque unguessable IDs.

    ``root`` must be a host-private artifact directory, not a project path. Each
    instance creates a private child and accepts only IDs it issued itself.
    Results are retained for the run: capacity exhaustion fails explicitly and
    never silently evicts an earlier page. This is not a cross-session archive.
    """

    def __init__(self, root: Path, *, max_result_bytes: int = 1_048_576,
                 max_total_bytes: int = 8_388_608, max_results: int = 64):
        if not 256 <= max_result_bytes <= max_total_bytes <= 67_108_864 or not 1 <= max_results <= 1024:
            raise ValueError("invalid result storage limits")
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.directory = root.resolve() / f"results-{uuid.uuid4().hex}"
        self.directory.mkdir(mode=0o700)
        self.max_result_bytes = max_result_bytes
        self.max_total_bytes = max_total_bytes
        self.max_results = max_results
        self._entries: dict[str, dict[str, Any]] = {}
        self._total_bytes = 0

    def put(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ResultStoreError("stored result must be an object")
        cleaned = _clean(payload)
        outcome = cleaned.get("outcome")
        if outcome is not None and (not isinstance(outcome, str) or outcome not in {"success", "error", "denied"}):
            raise ResultStoreError("invalid original result outcome")
        result = ToolResult.from_payload(cleaned, outcome=outcome)
        cleaned = result.model_payload()
        result_id = uuid.uuid4().hex
        data = _encode({"schema_version": 1, "result_id": result_id, "payload": cleaned})
        if len(data) > self.max_result_bytes:
            raise ResultStoreError("result exceeds stored result byte limit")
        if len(self._entries) >= self.max_results or self._total_bytes + len(data) > self.max_total_bytes:
            raise ResultStoreError("request result storage capacity exhausted")
        checksum = hashlib.sha256(data).hexdigest()
        path = self.directory / f"{result_id}.json"
        try:
            with path.open("xb") as stream:
                if os.name != "nt":
                    os.chmod(path, 0o600)
                stream.write(data)
                stream.flush()
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise ResultStoreError("could not persist result") from exc
        self._entries[result_id] = {"sha256": checksum, "bytes": len(data)}
        self._total_bytes += len(data)
        # Bounded proof stays available without promoting data retrieval into a
        # second successful command/edit. The complete proof remains in storage.
        summary = result.summary()
        preview = {key: item for key, item in summary.items() if len(_encode(item)) <= 2048}
        omitted = sorted(set(summary) - set(preview))
        return {**preview, "result_id": result_id, "result_sha256": checksum,
                "stored_bytes": len(data), "history_body_omitted": True,
                "omitted_proof_fields": omitted,
                "history_notice": "Body retained in this request's result store; use read_result for JSON pages."}

    def read(self, result_id: str, offset: int = 0, limit: int = 4096) -> dict[str, Any]:
        if not isinstance(result_id, str) or not _RESULT_ID.fullmatch(result_id) or result_id not in self._entries:
            raise ResultStoreError("result ID is unavailable in this request")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16384:
            raise ResultStoreError("invalid result page range")
        entry = self._entries[result_id]
        path = self.directory / f"{result_id}.json"
        try:
            if self.directory.is_symlink() or path.is_symlink():
                raise ResultStoreError("stored result may not be a symbolic link")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ResultStoreError("stored result is not a regular file")
                data = stream.read(self.max_result_bytes + 1)
        except OSError as exc:
            raise ResultStoreError("stored result is unavailable") from exc
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ResultStoreError("stored result checksum mismatch")
        try:
            envelope = json.loads(data)
            if (envelope.get("schema_version") != 1 or envelope.get("result_id") != result_id
                    or not isinstance(envelope.get("payload"), dict)):
                raise ResultStoreError("invalid stored result schema")
            original = envelope["payload"]
            rendered = _encode(original).decode("utf-8")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ResultStoreError("invalid stored result schema") from exc
        if offset > len(rendered):
            raise ResultStoreError("result offset exceeds character length")
        end = min(offset + limit, len(rendered))
        verification = original["verification"]
        verification_omitted = len(_encode(verification)) > 4096
        return {"result_id": result_id, "result_sha256": entry["sha256"], "data": rendered[offset:end],
                "encoding": "json-utf8", "offset_unit": "unicode_characters", "offset": offset,
                "end": end, "total": len(rendered), "truncated": end < len(rendered),
                "next_offset": end if end < len(rendered) else None,
                "original_outcome": original["outcome"],
                "original_verification": {} if verification_omitted else verification,
                "original_verification_omitted": verification_omitted,
                "data_only": True, "notice": "Stored data retrieval; no original action was executed or reverified."}
