"""Bounded host-selected static workflows using the ordinary tool dispatcher."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _bounded_outcome(step, result, record) -> dict:
    from atomic_flow.agents.result import ToolResult
    summary = ToolResult.from_payload(result).summary()
    # The manifest digest binds all arguments. Avoid copying potentially large
    # source/argument data repeatedly into the report and journal summary.
    audit = {key: value for key, value in record.items() if key not in {"args", "result_summary"}}
    audit["args_sha256"] = hashlib.sha256(_encoded(step.args)).hexdigest()
    output = {"step": step.id, "tool": step.tool, "result": summary, "audit": audit}
    raw = _encoded(output)
    if len(raw) > 16384:
        compact = {key: value for key, value in summary.items() if len(_encoded(value)) <= 1024}
        if isinstance(summary.get("error"), str):
            compact["error"] = summary["error"][:512]
        output["result"] = {**compact, "summary_truncated": True, "summary_sha256": hashlib.sha256(raw).hexdigest()}
    return output


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    tool: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(default=1, strict=True)
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    steps: tuple[WorkflowStep, ...] = Field(min_length=1, max_length=32)

    @field_validator("version")
    @classmethod
    def known_version(cls, value):
        if value != 1:
            raise ValueError("unsupported workflow version")
        return value

    @model_validator(mode="after")
    def bounded_manifest(self):
        seen = set()
        for step in self.steps:
            if step.id in seen or len(step.depends_on) != len(set(step.depends_on)) or not set(step.depends_on) <= seen:
                raise ValueError("workflow requires unique IDs and dependencies on earlier steps only")
            seen.add(step.id)
        if len(_encoded(self.model_dump(mode="json"))) > 65536:
            raise ValueError("workflow manifest exceeds 65536 bytes")
        return self

    def digest(self) -> str:
        # Revalidation catches mutations of nested dicts in an otherwise frozen model.
        validated = Workflow.model_validate(self.model_dump(mode="json"))
        return hashlib.sha256(_encoded(validated.model_dump(mode="json"))).hexdigest()


def run_workflow(workflow: Workflow | dict, registry, ctx, *, allowed_tools: list[str] | tuple[str, ...],
                 role: str = "", expected_digest: str, approved: bool = False) -> dict:
    """No project auto-discovery, interpolation, resume or action retry.

    Entire static manifest and argument schemas are validated before the first
    action; every action still passes current policy, approval and cancellation.
    """
    from atomic_flow.agents.loop import execute_tool

    if not approved:
        raise PermissionError("workflow execution requires explicit host selection and approval")
    definition = Workflow.model_validate(workflow.model_dump(mode="json") if isinstance(workflow, Workflow) else workflow)
    digest = definition.digest()
    if digest != expected_digest:
        raise ValueError("workflow manifest differs from approved digest")
    for step in definition.steps:
        spec = registry.specification(step.tool)
        if spec is None or step.tool not in allowed_tools:
            raise PermissionError("workflow includes an unavailable or role-disallowed tool")
        spec.args_schema.model_validate(step.args)
        if "write" in spec.contract.effects and not ctx.edits_enabled:
            raise PermissionError("workflow requires explicitly enabled edits")
    outcomes = []
    effects = False
    for step in definition.steps:
        ctx.cancel_check()
        call_id = "workflow_" + uuid.uuid4().hex
        call = {"tool": step.tool, "args": step.args}
        if ctx.journal_event is not None:
            ctx.journal_event("tool_request", {"role": role, "call": call, "workflow_digest": digest,
                                               "workflow_step": step.id}, call_id=call_id)
        try:
            result, record = execute_tool(call, registry, ctx, allowed_tools=allowed_tools, role=role)
        except Exception as exc:
            # Preserve completed outcomes and leave durable intent unresolved.
            # Cancellation after send/write cannot safely be treated as no-op.
            spec = registry.specification(step.tool)
            exc.workflow_report = {"name": definition.name, "digest": digest, "status": "interrupted",
                "steps": outcomes, "pending_step": step.id, "pending_call_id": call_id,
                "side_effects_may_have_occurred": effects or bool(set(spec.contract.effects) & {"write", "execute"}),
                "replay_performed": False}
            raise
        # Reports retain audit proof, never unrestricted source/result bodies.
        effects = effects or bool(result.get("side_effects_may_have_occurred"))
        entry = _bounded_outcome(step, result, record)
        outcomes.append(entry)
        if ctx.journal_event is not None:
            ctx.journal_event("tool_result", {"role": role, "call": record, "result": entry["result"],
                "workflow_digest": digest, "workflow_step": step.id}, call_id=call_id, outcome=result.get("outcome"))
        if result.get("outcome") != "success":
            return {"name": definition.name, "digest": digest, "status": "stopped", "steps": outcomes,
                    "side_effects_may_have_occurred": effects, "replay_performed": False}
    return {"name": definition.name, "digest": digest, "status": "completed", "steps": outcomes,
            "side_effects_may_have_occurred": effects, "replay_performed": False}
