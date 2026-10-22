"""Task: durable multi-step job with plan, status, and resumable state.

Persisted as one JSON file per task under ~/.atomic_flow/tasks/<id>.json.
Writes go through tmp+replace so a crashed process never leaves a half-written task.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from atomic_flow.config import user_dir
from atomic_flow.state import TASK_SCHEMA_VERSION, StateError, migrate_task

StepStatus = Literal["pending", "running", "done", "failed"]
TaskStatus = Literal["running", "paused", "done", "failed"]


@dataclass
class Step:
    index: int
    description: str
    tools_needed: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    verify_with: str = "py_compile"
    status: StepStatus = "pending"
    result_summary: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    request_id: str = ""


@dataclass
class Task:
    id: str
    query: str
    project: str
    steps: list[Step]
    current_index: int = 0
    status: TaskStatus = "running"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    tree_fingerprint: str = ""
    budget_spent: float = 0.0
    error: str = ""
    rationale: str = ""
    replan_count: int = 0
    changeset_id: str = ""
    plan_ready: bool = True
    budget_events: list[dict] = field(default_factory=list)
    schema_version: int = TASK_SCHEMA_VERSION
    completion_report: dict = field(default_factory=dict)

    @classmethod
    def new(cls, query: str, project: Path, steps: list[Step], rationale: str = "") -> Task:
        return cls(
            id="task_" + uuid.uuid4().hex[:12],
            query=query,
            project=str(project),
            steps=steps,
            rationale=rationale,
        )


def tasks_dir() -> Path:
    return user_dir() / "tasks"


def save(task: Task) -> Path:
    _validate_id(task.id)
    data = asdict(_decode(asdict(task), task.id))
    directory = tasks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{task.id}.json"
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > 16 * 1024 * 1024:
        raise StateError("task state exceeds 16 MiB; no state was written")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return target


def load(task_id: str) -> Task:
    _validate_id(task_id)
    path = tasks_dir() / f"{task_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such task: {task_id}")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise StateError("task state exceeds 16 MiB; inspect its file before resuming")
    try:
        return _decode(json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_constant), task_id)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StateError(f"invalid task JSON for {task_id}; restore a known-good state before resuming") from exc


def _invalid_constant(value: str):
    raise StateError(f"invalid task JSON constant {value}; finite numbers are required before resuming")


def _decode(raw: object, task_id: str) -> Task:
    data = migrate_task(raw)
    if data.get("id") != task_id:
        raise StateError("task id does not match its file")
    for name in ("id", "query", "project", "tree_fingerprint", "error", "rationale", "changeset_id"):
        if name in data and not isinstance(data[name], str):
            raise StateError(f"invalid task state: {name} must be text")
    for name in ("current_index", "replan_count"):
        if name in data and (type(data[name]) is not int or data[name] < 0):
            raise StateError(f"invalid task state: {name} must be a nonnegative integer")
    for name in ("created_at", "finished_at", "budget_spent"):
        value = data.get(name)
        if name in data and value is None and name != "finished_at":
            raise StateError(f"invalid task state: {name} cannot be null")
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise StateError(f"invalid task state: {name} must be a finite nonnegative number")
    if "status" in data and data["status"] not in ("running", "paused", "done", "failed"):
        raise StateError("invalid task status")
    if "plan_ready" in data and type(data["plan_ready"]) is not bool:
        raise StateError("invalid task state: plan_ready must be boolean")
    if "budget_events" in data and (not isinstance(data["budget_events"], list)
                                     or any(not isinstance(e, dict) for e in data["budget_events"])):
        raise StateError("invalid task state: budget_events must be a list of objects")
    if "completion_report" in data and not isinstance(data["completion_report"], dict):
        raise StateError("invalid task state: completion_report must be an object")
    raw_steps = data.pop("steps", [])
    if not isinstance(raw_steps, list):
        raise StateError("invalid task state: steps must be a list")
    steps = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise StateError("invalid task step: expected an object")
        for name in ("description", "expected_outcome", "verify_with", "result_summary", "request_id"):
            if name in raw_step and not isinstance(raw_step[name], str):
                raise StateError(f"invalid task step: {name} must be text")
        if type(raw_step.get("index")) is not int or raw_step["index"] < 0:
            raise StateError("invalid task step index")
        if raw_step.get("status", "pending") not in ("pending", "running", "done", "failed"):
            raise StateError("invalid task step status")
        tools = raw_step.get("tools_needed", [])
        if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
            raise StateError("invalid task step tools_needed")
        for name in ("started_at", "finished_at"):
            value = raw_step.get(name)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise StateError(f"invalid task step: {name} must be a finite nonnegative number")
        try:
            steps.append(Step(**raw_step))
        except TypeError as exc:
            raise StateError("invalid task step fields; inspect saved state before resuming") from exc
    try:
        return Task(steps=steps, **data)
    except TypeError as exc:
        raise StateError("invalid task fields; inspect saved state before resuming") from exc


def _validate_id(task_id: str):
    if not re.fullmatch(r"task_[a-f0-9]{12}", task_id):
        raise ValueError("invalid task id")


def list_tasks() -> list[Task]:
    directory = tasks_dir()
    if not directory.is_dir():
        return []
    out: list[Task] = []
    for path in sorted(directory.glob("task_*.json")):
        try:
            out.append(load(path.stem))
        except Exception:
            continue
    return sorted(out, key=lambda task: task.created_at, reverse=True)
