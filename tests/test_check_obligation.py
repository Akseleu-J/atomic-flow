"""Ordinary-test obligations stay immutable across admitted production edits."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from atomic_flow import check_obligation as obligations
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_spec import CheckResources, CheckSpec


def template(root, **changes):
    return CheckSpec(**({"task_id": "example-task", "task_commit": "a" * 40, "base_commit": "b" * 40,
        "source_revision": check_source_revision(root), "image": "example/checks@sha256:" + "c" * 64,
        "interpreter": "/usr/local/bin/python", "runner": "pytest", "test_paths": ("tests/test_example.py",)} | changes))


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "tests/test_example.py").write_text("def test_value():\n    assert True\n")
    (tmp_path / "tests/fixtures/input.json").write_text('{"value": 1}')
    (tmp_path / "example.py").write_text("value = 1\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\n')
    return tmp_path


def seal(root, **changes):
    return obligations.CheckObligation.seal(template(root), root, environment_digest="d" * 64,
        deadline_at=400, now=lambda: 100, **changes)


def test_production_edit_rebinds_same_complete_obligation_and_cannot_reuse_old_spec(project):
    original = template(project)
    bound = seal(project)
    (project / "example.py").write_text("value = 2\n")
    fresh = bound.derive(project, now=lambda: 110)
    assert fresh.source_revision == check_source_revision(project) != original.source_revision
    assert fresh.test_paths == original.test_paths
    assert fresh.task_commit == original.task_commit and fresh.base_commit == original.base_commit
    assert fresh.image == original.image and fresh.interpreter == original.interpreter
    assert bound.template == original


def test_same_bytes_copied_private_workspace_keep_source_identity(project, tmp_path_factory):
    import shutil

    bound = seal(project)
    other = tmp_path_factory.mktemp("another-private-snapshot")
    for item in project.iterdir():
        if item.name.startswith("."):
            continue
        shutil.copytree(item, other / item.name) if item.is_dir() else shutil.copyfile(item, other / item.name)
    assert bound.derive(other, now=lambda: 110).source_revision == bound.template.source_revision


@pytest.mark.parametrize("path", ["tests/test_example.py", "tests/fixtures/input.json", "pyproject.toml"])
def test_modified_protected_test_fixture_or_checker_config_refuses(project, path):
    bound = seal(project)
    (project / path).write_bytes(b"tampered")
    with pytest.raises(obligations.CheckObligationError, match="protected"):
        bound.derive(project, now=lambda: 110)


@pytest.mark.parametrize("path", ["tests/test_added.py", "tests/fixtures/new.json"])
def test_new_files_under_test_root_cannot_silently_expand_or_change_collection(project, path):
    bound = seal(project)
    (project / path).write_text("new")
    with pytest.raises(obligations.CheckObligationError, match="directory inventory"):
        bound.derive(project, now=lambda: 110)


def test_deleted_protected_input_refuses(project):
    bound = seal(project)
    (project / "tests/fixtures/input.json").unlink()
    with pytest.raises(obligations.CheckObligationError, match="inventory"):
        bound.derive(project, now=lambda: 110)


@pytest.mark.parametrize("path", ["conftest.py", "tests/conftest.py", "pytest.ini", "tests/setup.cfg"])
def test_absent_ancestor_checker_insertion_refuses(project, path):
    bound = seal(project)
    (project / path).write_text("host was not asked to admit this config")
    with pytest.raises(obligations.CheckObligationError, match="inserted|inventory"):
        bound.derive(project, now=lambda: 110)


def test_explicit_extra_runner_source_is_frozen(project):
    (project / "runner.py").write_text("runner_version = 1\n")
    bound = seal(project, protected_paths=("runner.py",))
    (project / "runner.py").write_text("runner_version = 2\n")
    with pytest.raises(obligations.CheckObligationError, match="protected"):
        bound.derive(project, now=lambda: 110)


def test_stale_initial_template_cannot_seal_unreviewed_test_bytes(project):
    initial = template(project)
    (project / "tests/test_example.py").write_text("def test_weakened():\n    pass\n")
    with pytest.raises(obligations.CheckObligationError, match="stale"):
        obligations.CheckObligation.seal(initial, project, environment_digest="d" * 64,
                                        deadline_at=400, now=lambda: 100)


def test_snapshot_consistency_rejects_concurrent_edit_at_sealing(project, monkeypatch):
    original = template(project)
    snapshot = obligations.check_source_snapshot
    calls = 0

    def changed(root, check):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "example.py").write_text("value = 2\n")
        return snapshot(root, check)

    monkeypatch.setattr(obligations, "check_source_snapshot", changed)
    with pytest.raises(obligations.CheckObligationError, match="while sealing"):
        obligations.CheckObligation.seal(original, project, environment_digest="d" * 64,
                                        deadline_at=400, now=lambda: 100)


def test_snapshot_consistency_rejects_concurrent_edit_at_derivation(project, monkeypatch):
    bound = seal(project)
    snapshot = obligations.check_source_snapshot
    calls = 0

    def changed(root, check):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / "example.py").write_text("value = 2\n")
        return snapshot(root, check)

    monkeypatch.setattr(obligations, "check_source_snapshot", changed)
    with pytest.raises(obligations.CheckObligationError, match="while deriving"):
        bound.derive(project, now=lambda: 110)


def test_nonrenewing_absolute_deadline_and_work_elapsed_during_admission(project):
    bound = seal(project)
    assert bound.derive(project, now=lambda: 300).timeout_seconds == 100
    assert bound.derive(project, now=lambda: 390).timeout_seconds == 10
    times = iter((390, 398.4))
    assert bound.derive(project, now=lambda: next(times)).timeout_seconds == 1
    times = iter((390, 400))
    with pytest.raises(obligations.CheckObligationError, match="expired during admission"):
        bound.derive(project, now=lambda: next(times))
    with pytest.raises(obligations.CheckObligationError, match="exhausted"):
        bound.derive(project, now=lambda: 400)


def test_fresh_spec_cannot_expand_resource_caps_and_can_use_smaller_limits(project):
    initial = template(project, resources=CheckResources(cpus=1, memory_bytes=512 * 1024**2))
    bound = obligations.CheckObligation.seal(initial, project, environment_digest="d" * 64,
                                          deadline_at=400, now=lambda: 100)
    with pytest.raises(obligations.CheckObligationError, match="ceiling"):
        bound.derive(project, resources=CheckResources(), now=lambda: 110)
    smaller = CheckResources(cpus=1, memory_bytes=256 * 1024**2, output_bytes_per_stream=4096)
    assert bound.derive(project, resources=smaller, now=lambda: 110).resources == smaller
    assert bound.template.resources.memory_bytes == 512 * 1024**2


@pytest.mark.parametrize("now", [True, 0, -1, float("nan"), float("inf"), "100"])
def test_untrusted_or_nonfinite_current_time_refuses(project, now):
    with pytest.raises(obligations.CheckObligationError, match="timestamp"):
        seal(project).derive(project, now=lambda: now)


@pytest.mark.parametrize("deadline", [True, 0, float("nan"), float("inf"), "400", 100])
def test_invalid_or_exhausted_original_deadline_refuses(project, deadline):
    with pytest.raises(obligations.CheckObligationError):
        obligations.CheckObligation.seal(template(project), project, environment_digest="d" * 64,
                                        deadline_at=deadline, now=lambda: 100)


def test_cancellation_propagates_without_returning_a_rebound_spec(project):
    bound = seal(project)

    def cancelled():
        raise RuntimeError("cancelled host task")

    with pytest.raises(RuntimeError, match="cancelled host task"):
        bound.derive(project, now=lambda: 110, cancel_check=cancelled)
    with pytest.raises(RuntimeError, match="cancelled host task"):
        seal(project, cancel_check=cancelled)


def test_root_or_test_link_refuses_without_symlink_privilege(project, monkeypatch):
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == project or real(path))
    with pytest.raises(obligations.CheckObligationError, match="link"):
        seal(project)
    monkeypatch.setattr(Path, "is_symlink", real)
    bound = seal(project)
    target = project / "tests/test_example.py"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or real(path))
    with pytest.raises(OSError, match="symlink"):
        bound.derive(project, now=lambda: 110)


def test_nested_package_test_scope_preserves_production_edit(project):
    (project / "package/tests").mkdir(parents=True)
    (project / "package/tests/test_nested.py").write_text("def test_value():\n    assert True\n")
    (project / "package/source.py").write_text("value = 1\n")
    initial = template(project, test_paths=("package/tests/test_nested.py",))
    bound = obligations.CheckObligation.seal(initial, project, environment_digest="d" * 64,
                                          deadline_at=400, now=lambda: 100)
    assert bound.test_directories == ("package/tests",)
    (project / "package/source.py").write_text("value = 2\n")
    assert bound.derive(project, now=lambda: 110).source_revision != initial.source_revision


def test_root_test_file_freezes_only_declared_file_and_ancestor_configs(project):
    (project / "test_example.py").write_text("def test_value():\n    assert True\n")
    initial = template(project, test_paths=("test_example.py",))
    bound = obligations.CheckObligation.seal(initial, project, environment_digest="d" * 64,
                                          deadline_at=400, now=lambda: 100)
    assert bound.test_directories == ()
    (project / "example.py").write_text("value = 3\n")
    assert bound.derive(project, now=lambda: 110).source_revision != initial.source_revision


def test_frozen_manifest_digest_and_environment_binding(project):
    bound = seal(project)
    assert dict(bound.protected_files)["tests/test_example.py"] == hashlib.sha256((project / "tests/test_example.py").read_bytes()).hexdigest()
    assert bound.digest == hashlib.sha256(json.dumps(bound.as_dict(), sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    assert bound.digest != replace(bound, environment_digest="e" * 64).digest
    assert bound.digest != replace(bound, deadline_at=401).digest
    with pytest.raises(FrozenInstanceError):
        bound.deadline_at = 1000
    exported = bound.as_dict()
    exported["template"]["test_paths"].append("tests/fake.py")
    assert bound.template.test_paths == ("tests/test_example.py",)


@pytest.mark.parametrize("changes", [
    {"environment_digest": "bad"}, {"protected_files": []}, {"absent_paths": []},
    {"protected_files": (("tests/test_example.py", "a" * 40),)},
    {"protected_files": ()}, {"directory_inventory": ("tests/absent.py",)},
    {"test_directories": ()}, {"absent_paths": ()},
])
def test_direct_construction_cannot_omit_or_weaken_inventory(project, changes):
    bound = seal(project)
    with pytest.raises(obligations.CheckObligationError):
        replace(bound, **changes)


@pytest.mark.parametrize("path", ["../runner.py", "/runner.py", "C:/runner.py", "tests//file.py", "tests/./file.py", "runner.py;bad"])
def test_extra_runner_path_cannot_escape(project, path):
    with pytest.raises(obligations.CheckObligationError):
        seal(project, protected_paths=(path,))


def test_missing_extra_runner_is_not_silently_ignored(project):
    with pytest.raises(obligations.CheckObligationError, match="absent from admitted"):
        seal(project, protected_paths=("missing_runner.py",))
