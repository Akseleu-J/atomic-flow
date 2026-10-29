"""Demonstrate omissions in a snapshot, without making or authorizing a call."""
from pathlib import Path

import pytest
from test_p07_matched_manifest import IDS
from test_p07_matched_runner_probe import NeverSend, fixture

from atomic_flow.project_resources import ProjectLimits
from tools.p07_matched_runner_probe import inspect_runner_snapshot


@pytest.mark.parametrize("field", ["resource_limits", "approval", "question", "transport", "journal_root"])
@pytest.mark.parametrize("arm", ["atomic", "flat"])
def test_snapshot_does_not_bind_selected_host_fields(tmp_path: Path, monkeypatch, field, arm):
    manifest, runner = fixture(tmp_path, monkeypatch, arm)
    before = inspect_runner_snapshot(manifest, IDS[0], arm, runner)
    if field == "resource_limits":
        runner.resource_limits = ProjectLimits(max_files=5)
    elif field == "approval":
        runner.approval_callback = lambda *_: False
    elif field == "question":
        runner.question_callback = lambda *_: None
    elif field == "transport":
        replacement = NeverSend()
        replacement.binding = dict(runner.backend.transport.binding)
        assert replacement is not runner.backend.transport
        runner.backend.transport = replacement
    else:
        runner.backend.root = tmp_path / "other-empty-journal"
        runner.backend.root.mkdir()
    after = inspect_runner_snapshot(manifest, IDS[0], arm, runner)
    assert before["runtime_digest"] == after["runtime_digest"]
    assert after["scope"] == "live_objects_snapshot_only_not_execution_authority"
