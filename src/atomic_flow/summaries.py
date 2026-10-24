"""Bounded, deterministic context notes. These never authorize or prove actions."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atomic_flow.activation.redact import redact

MAX_SUMMARY_BYTES = 32768
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|secret|api[_-]?key|token|[a-z][a-z0-9_]*_(?:token|secret|password|api_key))\s*[:=]\s*)"
    r'''(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)'''
)


def clean_context_text(value: str) -> str:
    """Known credential formats plus explicit credential assignments in notes."""
    return _CREDENTIAL_ASSIGNMENT.sub(lambda match: match.group(1) + "[REDACTED]", redact(value))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("context date must use ISO 8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("context dates require an explicit timezone")
    return parsed.astimezone(UTC).isoformat()


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    ref: str = Field(min_length=1, max_length=1024)
    origin: str = Field(default="host_observation", min_length=1, max_length=128)
    date: str = Field(default_factory=utc_now)
    scope: str = Field(min_length=1, max_length=2048)

    @field_validator("ref", "origin", "scope")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return clean_context_text(value)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return _date(value)


class StructuredSummary(BaseModel):
    """Caller-supplied facts, never automatic conclusions from model prose.

    ``evidence_refs`` are provenance pointers only. No reference is opened and
    no source coverage is granted by constructing, loading or rendering a note.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    goal: str = Field(default="", max_length=8000)
    constraints: list[str] = Field(default_factory=list, max_length=32)
    decisions: list[str] = Field(default_factory=list, max_length=32)
    open_questions: list[str] = Field(default_factory=list, max_length=32)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    origin: str = Field(default="host_observation", min_length=1, max_length=128)
    date: str = Field(default_factory=utc_now)
    scope: str = Field(min_length=1, max_length=2048)
    trust: Literal["data_only"] = "data_only"
    permissions_granted: Literal[False] = False
    read_evidence: Literal[False] = False

    @field_validator("schema_version", mode="before")
    @classmethod
    def valid_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported context summary schema")
        return value

    @field_validator("permissions_granted", "read_evidence", mode="before")
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("context summary is data only")
        return value

    @field_validator("goal", "origin", "scope")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return clean_context_text(value)

    @field_validator("constraints", "decisions", "open_questions")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item.encode("utf-8")) > 2048 for item in value):
            raise ValueError("context items must be nonempty and at most 2048 bytes")
        return [clean_context_text(item) for item in value]

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return _date(value)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json().encode("utf-8")) > MAX_SUMMARY_BYTES:
            raise ValueError("context summary exceeds byte limit")
        return self


def build_summary(**fields) -> StructuredSummary:
    """Validate explicit host fields; no extraction or inferred decisions."""
    return StructuredSummary.model_validate(fields)


def render_summary(summary: StructuredSummary | dict) -> str:
    # Revalidate copied/mutated objects as well; frozen models still contain lists.
    raw = summary.model_dump() if isinstance(summary, StructuredSummary) else summary
    checked = StructuredSummary.model_validate(raw)
    return json.dumps(checked.model_dump(), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
