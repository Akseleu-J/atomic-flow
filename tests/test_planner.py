"""Planner: parses valid plans, retries on malformed output."""
from __future__ import annotations

from pathlib import Path

import pytest

from atomic_flow.agents.planner import PlanStep, plan_task
from atomic_flow.backends.base import ChatResult


class FakeBackend:
    name = "fake"

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._i = 0

    def chat(self, messages, *, json_mode=False, model=None):
        text = self._responses[self._i]
        self._i += 1
        return ChatResult(text)


def test_plan_parses_valid():
    backend = FakeBackend([
        '{"steps": [{"description": "Rename refresh_tree parameter", "tools_needed": ["edit_node"], "expected_outcome": "root renamed", "verify_with": "py_compile"}], "rationale": "one edit"}'
    ])
    plan = plan_task("rename root", project_root=Path("."), backend=backend,
                     candidates=["builder.py::refresh_tree"], n_files=10)
    assert len(plan.steps) == 1
    assert plan.steps[0].verify_with == "py_compile"


def test_plan_retries_on_malformed():
    backend = FakeBackend([
        "not json at all",
        '{"steps": [{"description": "fix bug in X", "verify_with": "none"}], "rationale": "single step"}',
    ])
    plan = plan_task("fix bug", project_root=Path("."), backend=backend,
                     candidates=[], n_files=1)
    assert len(plan.steps) == 1


def test_plan_rejects_empty_steps():
    from atomic_flow.errors import ResponseFormatError

    # Two identical answers: plan_task retries once before surfacing the parse error.
    backend = FakeBackend(['{"steps": [], "rationale": ""}', '{"steps": [], "rationale": ""}'])
    with pytest.raises(ResponseFormatError, match="Plan validation failed"):
        plan_task("x", project_root=Path("."), backend=backend, candidates=[], n_files=1)


def test_plan_step_defaults():
    step = PlanStep(description="Add docstring")
    assert step.verify_with == "py_compile"
    assert step.tools_needed == []
