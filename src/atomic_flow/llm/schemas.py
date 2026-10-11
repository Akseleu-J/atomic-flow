"""Validated data exchanged between the LLM roles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryIntent(StrictModel):
    task_type: Literal["explain", "diagnose", "review", "trace", "refactor", "other"] = "other"
    target_scope: list[str] = Field(default_factory=list)
    focus_terms: list[str] = Field(default_factory=list)
    answer_format: str = "explanation"


class Citation(StrictModel):
    node_id: str
    what: str = Field(min_length=1)


class Answer(StrictModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    missing_context: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class AnswerCritique(StrictModel):
    verdict: Literal["ok", "needs_more_context", "needs_retry", "wrong_answer"]
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_node_ids: list[str] = Field(default_factory=list)
    feedback: str = ""
    citation_coverage: float = Field(default=0, ge=0, le=1)


class ContextPlan(StrictModel):
    node_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class AnswerResult(StrictModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    missing_context: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    mode_used: Literal["simple", "medium", "hard"]
    roles_invoked: list[str]
    prompts_source: dict[str, str]
    attempts: int = 1
    ledger_id: str
    artifacts_path: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    backend_used: str
    model_used: str
    verdict: str = "unverified"
    citation_coverage: float = 0
