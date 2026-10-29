"""Snapshot real runner objects without running a task or sending a model call."""
from dataclasses import replace

import pytest
from test_p07_matched_arm_contract import declared
from test_p07_matched_manifest import IDS

from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.backends.github_queue import GitHubQueueBackend, QueueAdmissionRefused
from atomic_flow.check_obligation import CheckObligation, _config_paths
from tools.p07_flat_task_runner import FlatTaskRunner
from tools.p07_matched_runner_probe import inspect_runner_snapshot


class NeverSend:
    binding = {"repo": "example/scratch", "branch": "codex/p07-model-queue", "prefix": "queue"}

    def put_request(self, *_):
        raise AssertionError("snapshot must never send")

    def get_response(self, *_):
        raise AssertionError("snapshot must never poll")


def fixture(tmp_path, monkeypatch, arm):
    monkeypatch.setenv("ATOMIC_FLOW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("tools.p07_matched_runner_probe.time.time", lambda: 1_700_000_000)
    manifest, config, backend_config, identity, spec = declared(arm)
    queue = NeverSend()
    queue.binding = dict(queue.binding)
    backend = GitHubQueueBackend(backend_config, queue, identity, tmp_path / "journal")
    test_path = spec.test_paths[0]
    obligation = CheckObligation(template=spec, environment_digest="7" * 64, deadline_at=1_800_000_000,
                                 protected_files=((test_path, "3" * 64),),
                                 absent_paths=tuple(sorted(_config_paths(spec.test_paths))),
                                 test_directories=("sympy/tests",), directory_inventory=(test_path,))
    runner_class = TaskRunner if arm == "atomic" else FlatTaskRunner
    runner = runner_class(config, tmp_path, backend, check_obligation=obligation)
    return manifest, runner


@pytest.mark.parametrize("arm", ["atomic", "flat"])
def test_actual_runner_and_queue_can_be_snapshotted_without_calls(tmp_path, monkeypatch, arm):
    manifest, runner = fixture(tmp_path, monkeypatch, arm)
    result = inspect_runner_snapshot(manifest, IDS[0], arm, runner)
    assert result["scope"] == "live_objects_snapshot_only_not_execution_authority"
    assert result["selected_fields"]["arm"] == arm
    assert result["obligation_digest"] == runner.check_obligation.digest
    assert len(result["runtime_digest"]) == 64
    assert list((tmp_path / "journal").iterdir()) == []


def test_actual_queue_mutation_is_refused_without_sending(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.backend.transport.binding["branch"] = "other"
    with pytest.raises(QueueAdmissionRefused):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


@pytest.mark.parametrize("mutation", ["identity", "wire"])
def test_actual_queue_identity_or_wire_mutation_is_refused(tmp_path, monkeypatch, mutation):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    if mutation == "identity":
        runner.backend.identity["owner"] = "umirbayulgaisha"
    else:
        runner.backend.config.max_output_tokens = 512
    with pytest.raises(QueueAdmissionRefused):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


def test_actual_config_or_protected_obligation_drift_is_refused(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.config.task.max_model_calls += 1
    with pytest.raises(ValueError, match="runtime limits"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)
    manifest, runner = fixture(tmp_path / "other", monkeypatch, "atomic")
    runner.check_obligation = replace(runner.check_obligation,
                                      protected_files=((runner.check_obligation.template.test_paths[0], "4" * 64),))
    with pytest.raises(ValueError, match="protected file bytes"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


def test_obligation_cannot_exceed_campaign_deadline(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.check_obligation = replace(runner.check_obligation, deadline_at=1_900_000_001)
    with pytest.raises(ValueError, match="deadline"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


def test_expired_obligation_is_refused(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.check_obligation = replace(runner.check_obligation, deadline_at=1_700_000_000)
    with pytest.raises(ValueError, match="expired"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


def test_config_drift_during_runtime_digest_is_refused(tmp_path, monkeypatch):
    import tools.p07_matched_runner_probe as probe

    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    original = probe.runtime_digest

    def mutate_during_digest(*args, **kwargs):
        digest = original(*args, **kwargs)
        runner.config.task.max_model_calls += 1
        return digest

    monkeypatch.setattr(probe, "runtime_digest", mutate_during_digest)
    with pytest.raises(ValueError, match="runtime limits"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)


def test_injected_capability_or_wrong_runner_class_is_refused(tmp_path, monkeypatch):
    manifest, runner = fixture(tmp_path, monkeypatch, "atomic")
    runner.capability_factory = lambda *_: None
    with pytest.raises(ValueError, match="built-in capabilities"):
        inspect_runner_snapshot(manifest, IDS[0], "atomic", runner)
    with pytest.raises(ValueError, match="runner class"):
        inspect_runner_snapshot(manifest, IDS[0], "flat", runner)
