"""Task: durable multi-step job with plan, status, and resumable state.

Persisted as one JSON file per task under ~/.atomic_flow/tasks/<id>.json.
Writes go through tmp+replace so a crashed process never leaves a half-written task.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from atomic_flow.config import user_dir

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

    @classmethod
    def new(cls, query: str, project: Path, steps: list[Step], rationale: str = "") -> "Task":
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
    directory = tasks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{task.id}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def load(task_id: str) -> Task:
    path = tasks_dir() / f"{task_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such task: {task_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_steps = data.pop("steps", [])
    return Task(steps=[Step(**s) for s in raw_steps], **data)


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
