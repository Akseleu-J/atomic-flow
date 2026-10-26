"""Strict wire contract for measured identifier and source-span checks."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Status = Literal["not_assessed", "passed", "failed"]


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class CitationSpanReport(_ReportModel):
    node_id: str
    path: str
    source_digest: str
    status: Status = "not_assessed"
    lines: str | None = None
    reason: str | None = None
    start_line: StrictInt | None = None
    end_line: StrictInt | None = None
    quote: str | None = None

    @model_validator(mode="after")
    def coherent_span(self):
        if self.status == "passed":
            if (self.start_line is None or self.end_line is None
                    or self.start_line < 1 or self.end_line < self.start_line
                    or self.quote is None
                    or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.source_digest) is None
                    or not self.node_id or not self.path):
                raise ValueError("passed citation requires a current source span")
        elif self.status == "failed" and not self.reason:
            raise ValueError("failed citation requires a reason")
        return self


class CitationSpanChecks(_ReportModel):
    status: Status = "not_assessed"
    checked: StrictInt = Field(default=0, ge=0)
    items: list[CitationSpanReport] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_counts(self):
        checked = sum(item.status != "not_assessed" for item in self.items)
        status = ("failed" if any(item.status == "failed" for item in self.items)
                  else "passed" if any(item.status == "passed" for item in self.items)
                  else "not_assessed")
        if self.checked != checked or self.status != status:
            raise ValueError("citation count and status must match measured items")
        return self


class ContextOmission(_ReportModel):
    node_id: str
    reason: str


class ContextCoverage(_ReportModel):
    status: Literal["not_assessed", "partial", "shown_sources_complete"]
    repository_completeness: Literal["not_assessed"] = "not_assessed"
    shown_nodes: StrictInt = Field(ge=0)
    omitted_or_partial: list[ContextOmission] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_coverage(self):
        expected = ("partial" if self.omitted_or_partial else
                    "shown_sources_complete" if self.shown_nodes else "not_assessed")
        if self.status != expected:
            raise ValueError("context coverage must match observed nodes and omissions")
        return self


class VerificationReport(_ReportModel):
    schema_version: Literal[1] = 1
    method: Literal["identifier_presence"] = "identifier_presence"
    status: Status = "not_assessed"
    checked: StrictInt = Field(default=0, ge=0)
    passed: StrictInt = Field(default=0, ge=0)
    failed: StrictInt = Field(default=0, ge=0)
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    semantic_correctness: Literal["not_assessed"] = "not_assessed"
    citation_spans: CitationSpanChecks = Field(default_factory=CitationSpanChecks)
    omissions: list[str] = Field(default_factory=list)
    limits: str = "Identifier presence and source spans do not measure semantic correctness."
    context_coverage: ContextCoverage | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version_type(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def reject_boolean_score(cls, value):
        if isinstance(value, bool):
            raise ValueError("score must be a number or null, not a boolean")
        return value

    @model_validator(mode="after")
    def coherent_measurement(self):
        if self.checked != self.passed + self.failed:
            raise ValueError("checked must equal passed plus failed")
        if self.checked == 0:
            if self.status != "not_assessed" or self.score is not None:
                raise ValueError("zero checks require not_assessed and a null score")
        else:
            expected = "failed" if self.failed else "passed"
            if self.status != expected or self.score != round(self.passed / self.checked, 3):
                raise ValueError("status and score must match measured checks")
        return self
