"""Selected binding drift detection never implies runtime or effect admission."""
from dataclasses import FrozenInstanceError, replace

import pytest
from test_p07_matched_manifest import IDS
from test_p07_matched_runner_probe import NeverSend, fixture

from atomic_flow.project_resources import ProjectLimits
from tools.p07_matched_object_watch import ObjectWatch, watch_runner


@pytest.mark.parametrize("arm", ["atomic", "flat"])
def test_unchanged_watched_objects_are_observed_without_calls(tmp_path, monkeypatch, arm):
    manifest, runner = fixture(tmp_path, monkeypatch, arm)
    watch = watch_runner(manifest, IDS[0], arm, runner)
    result = watch.check()
    assert result["scope"] == "selected_in_process_observation_only_no_authority"
    assert result["arm"] == arm
    assert list(runner.backend.root.iterdir()) == []
    with pytest.raises(FrozenInstanceError):
        watch._arm = "other"
    with pytest.raises(ValueError, match="use watch_runner"):
        ObjectWatch(token=None, manifest=manifest, task_id=IDS[0], arm=arm, runner=runner, baseline={})


@pytest.mark.parametrize("arm", ["atomic", "flat"])
@pytest.mark.parametrize("field", ["resources", "approval", "question", "transport", "root", "poll", "wait", "cancel",
                                    "config", "runner_config", "identity", "transport_identity"])
def test_selected_runtime_changes_are_detected_without_queue_calls(tmp_path, monkeypatch, arm, field):
    manifest, runner = fixture(tmp_path, monkeypatch, arm)
    watch = watch_runner(manifest, IDS[0], arm, runner)
    if field == "resources":
        runner.resource_limits = ProjectLimits(max_files=5)
    elif field == "approval":
        runner.approval_callback = lambda *_: False
    elif field == "question":
        runner.question_callback = lambda *_: None
    elif field == "transport":
        replacement = NeverSend()
        replacement.binding = dict(runner.backend.transport.binding)
        runner.backend.transport = replacement
    elif field == "root":
        runner.backend.root = tmp_path / "different-journal"
    elif field == "poll":
        runner.backend.poll_seconds = 3.0
    elif field == "wait":
        runner.backend.wait_seconds = 59.0
    elif field == "cancel":
        runner.backend.cancel_check = lambda: None
    elif field == "config":
        runner.backend.config = runner.backend.config.model_copy(deep=True)
    elif field == "runner_config":
        runner.config = runner.config.model_copy(deep=True)
    elif field == "identity":
        runner.backend.identity = dict(runner.backend.identity)
    else:
        runner.backend.transport_identity = dict(runner.backend.transport_identity)
    with pytest.raises(ValueError, match="watched"):
        watch.check()


def test_in_place_resource_mutation_is_detected(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.resource_limits = ProjectLimits()
    watch = watch_runner(manifest, IDS[0], "atomic", runner)
    object.__setattr__(runner.resource_limits, "max_files", 7)
    with pytest.raises(ValueError, match="watched host value"):
        watch.check()


def test_existing_snapshot_rechecks_config_and_obligation(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    watch = watch_runner(manifest, IDS[0], "atomic", runner)
    runner.check_obligation = replace(runner.check_obligation, deadline_at=1_799_999_999)
    with pytest.raises(ValueError, match="watched object reference changed"):
        watch.check()


def test_drift_during_snapshot_is_caught(tmp_path, monkeypatch):
    import tools.p07_matched_object_watch as module

    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    watch = watch_runner(manifest, IDS[0], "atomic", runner)
    original = module.inspect_runner_snapshot

    def mutate_after_observation(*args):
        observation = original(*args)
        runner.backend.root = tmp_path / "other"
        return observation

    monkeypatch.setattr(module, "inspect_runner_snapshot", mutate_after_observation)
    with pytest.raises(ValueError, match="during observation"):
        watch.check()
