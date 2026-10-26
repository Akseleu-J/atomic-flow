"""Validated data exchanged between the LLM roles.

Model OUTPUTS are parsed leniently (unknown keys ignored, bad enums coerced) because a strict
`extra="forbid"` turns one stray "reasoning" key into a failed, already-paid-for run. Internal
results (`AnswerResult`) stay strict.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atomic_flow.llm.verification import VerificationReport


class LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskType = Literal["explain", "diagnose", "review", "trace", "refactor", "modify", "other"]


class QueryIntent(LLMModel):
    task_type: TaskType = "other"
    target_scope: list[str] = Field(default_factory=list)
    focus_terms: list[str] = Field(default_factory=list)
    answer_format: str = "explanation"

    @field_validator("task_type", mode="before")
    @classmethod
    def _coerce_task(cls, value: Any) -> Any:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"rename", "add", "remove", "delete", "update", "fix", "edit", "change", "patch"}:
                return "modify"
            if v in TaskType.__args__:
                return v
        return "other"          # type: ignore[attr-defined]


class Citation(LLMModel):
    node_id: str
    what: str = ""
    lines: str | None = None                                             # e.g. "12-18" (optional, verifiable)


class Answer(LLMModel):
    text: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    missing_context: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, value: Any) -> Any:
        return value if value in {"high", "medium", "low"} else "medium"


Verdict = Literal["ok", "needs_more_context", "needs_retry", "wrong_answer"]


class AnswerCritique(LLMModel):
    verdict: Verdict
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_node_ids: list[str] = Field(default_factory=list)
    feedback: str = ""
    citation_coverage: float = 0.0

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, value: Any) -> Any:
        return value if value in Verdict.__args__ else "needs_retry"      # type: ignore[attr-defined]

    @field_validator("citation_coverage", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


class ContextPlan(LLMModel):
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
    artifacts_path: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None                  # sum of the calls that reported a cost
    cost_complete: bool = True                     # False -> some calls had no cost, cost_usd is a lower bound
    latency_ms: int = 0
    backend_used: str
    model_used: str
    verdict: str = "unverified"                    # critic verdict, downgraded by deterministic checks
    citation_coverage: float = 0.0                 # critic's estimate (model opinion)
    grounding: float | None = None                # nullable when no identifiers were checked; never semantic proof
    verification_report: VerificationReport = Field(default_factory=VerificationReport)
    unknown_identifiers: list[str] = Field(default_factory=list)
    dropped_citations: int = 0                     # citations to nodes that were not in the context
    retrieval_weak: bool = False
    warnings: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
