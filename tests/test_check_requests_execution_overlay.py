"""Host-only disposable C/E overlay proofs; never import or execute target code."""
import hashlib
from dataclasses import replace

import pytest

from atomic_flow.agents.source import SourceConflict
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_requests_execution_overlay import (
    CA_PATH,
    prepare_requests_execution_overlay,
    validate_requests_execution_overlay,
)
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec

PUBLIC_CA = b"-----BEGIN CERTIFICATE-----\npublic synthetic fixture\n-----END CERTIFICATE-----\n"
ORIGINAL_CA = b"upstream synthetic original CA bytes\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def bound(tmp_path):
    candidate = tmp_path / "candidate"
    (candidate / "requests").mkdir(parents=True)
    (candidate / "tests").mkdir()
    (candidate / CA_PATH).write_bytes(ORIGINAL_CA)
    (candidate / "requests/__init__.py").write_bytes(b"# fixture source is never imported\n")
    (candidate / "requests/api.py").write_bytes(b"value = 42\n")
    (candidate / "tests/test_requests.py").write_bytes(b"raise RuntimeError('must never execute on host')\n")
    # Core filtered inventory excludes this secret. E must never copy it.
    (candidate / ".env").write_bytes(b"SYNTHETIC_PRIVATE_VALUE=do-not-copy\n")
    (candidate / "requests/private-key.pem").write_bytes(b"SYNTHETIC PRIVATE KEY never copy\n")
    runtime = RuntimeIdentity(
        "example/requests@sha256:" + "a" * 64, "sha256:" + "b" * 64, "sha256:" + "c" * 64,
        "/opt/p07-python36/bin/python3.6", (3, 6, 15), "pytest", "d" * 64, "e" * 64, "f" * 64, "1" * 64,
        "/opt/p07-python36", "/opt/p07-runtime/python36", "2" * 64)
    helpers = reviewed_helper_inventory("function36-v3")
    installed = tuple(sorted((path, "3" * 64) for path in (
        "pytest.py", "_pytest/__init__.py", "pytest_httpbin/__init__.py", "pytest_httpbin/plugin.py", "pytest_mock.py")))
    identity = PrivatePytestIdentity("psf__requests-1963", "function36-v3", runtime.digest, (3, 6, 15), installed, helpers)
    fixture = FixtureIdentity(
        identity.task_id, identity.variant, runtime.digest, runtime.image_id,
        "2be37770e20e5379d6e1cbed7b67808308a7903fefb3ca70726c70b9280ecfde", helpers,
        (("/opt/p07-function-fixture/cacert.pem", sha(PUBLIC_CA)),
         ("/opt/p07-function-fixture/cert.pem", "4" * 64)), "5" * 64, "6" * 64)
    spec = CheckSpec(identity.task_id, "7" * 40, "8" * 40, check_source_revision(candidate), runtime.image_id,
                     runtime.interpreter, "pytest", ("tests/test_requests.py",), target_root="/testbed",
                     runtime_identity=runtime, private_pytest_identity=identity, fixture_identity=fixture)
    return candidate, tmp_path / "execution", spec


def prepare(bound, **kwargs):
    candidate, destination, spec = bound
    return prepare_requests_execution_overlay(spec, candidate, destination, PUBLIC_CA,
                                              original_ca_sha256=sha(ORIGINAL_CA), **kwargs)


def validate(bound, execution_spec, overlay, **kwargs):
    candidate, destination, spec = bound
    return validate_requests_execution_overlay(spec, execution_spec, overlay, candidate, destination,
                                               PUBLIC_CA, original_ca_sha256=sha(ORIGINAL_CA), **kwargs)


def test_fresh_execution_has_exact_sole_ca_append_and_preserves_candidate(bound):
    candidate, execution, spec = bound
    before = check_source_snapshot(candidate)
    execution_spec, overlay = prepare(bound)
    assert check_source_snapshot(candidate) == before
    assert (candidate / CA_PATH).read_bytes() == ORIGINAL_CA
    assert (candidate / ".env").exists() and not (execution / ".env").exists()
    assert not (execution / "requests/private-key.pem").exists()
    assert (execution / CA_PATH).read_bytes() == ORIGINAL_CA + b"\n" + PUBLIC_CA
    after = check_source_snapshot(execution)
    assert set(before) == set(after)
    assert CA_PATH not in before and CA_PATH not in after
    assert before == after  # The fixed CA resource never expands core admission.
    assert replace(spec, source_revision=execution_spec.source_revision) == execution_spec
    assert execution_spec.source_revision == spec.source_revision
    assert overlay.candidate_core_revision == spec.source_revision
    assert overlay.execution_core_revision == execution_spec.source_revision
    assert overlay.candidate_revision != overlay.execution_revision
    assert overlay.check_spec_digest == execution_spec.digest
    assert overlay.fixture_digest == spec.fixture_identity.digest
    assert overlay.runtime_digest == spec.runtime_identity.digest
    assert validate(bound, execution_spec, overlay) == overlay.digest


@pytest.mark.parametrize("task", ["psf__requests-2148", "psf__requests-2317"])
def test_other_two_function36_tasks_use_their_exact_bound_identity(bound, task):
    candidate, execution, spec = bound
    identity = replace(spec.private_pytest_identity, task_id=task)
    fixture = replace(spec.fixture_identity, task_id=task)
    spec = replace(spec, task_id=task, private_pytest_identity=identity, fixture_identity=fixture)
    execution_spec, overlay = prepare((candidate, execution, spec))
    assert overlay.task_id == execution_spec.task_id == task
    assert validate((candidate, execution, spec), execution_spec, overlay) == overlay.digest


def test_stale_candidate_refuses_before_destination_creation(bound):
    candidate, execution, _ = bound
    (candidate / "requests/api.py").write_bytes(b"value = 0\n")
    with pytest.raises(SourceConflict, match="stale"):
        prepare(bound)
    assert not execution.exists()


def test_changed_candidate_during_copy_cannot_return_an_overlay(bound):
    candidate, execution, _ = bound
    changed = False

    def external_writer():
        nonlocal changed
        if not changed and (execution / "requests/api.py").exists():
            changed = True
            (candidate / "requests/api.py").write_bytes(b"value = -1\n")

    with pytest.raises(SourceConflict, match="stale|changed"):
        prepare(bound, cancel_check=external_writer)
    assert changed and execution.exists()  # Partial fresh attempt is retained.


@pytest.mark.parametrize("public", [b"", b"not PEM", PUBLIC_CA + b"wrong bytes", PUBLIC_CA + b"PRIVATE KEY"])
def test_wrong_public_ca_refuses_before_copy(bound, public):
    candidate, execution, spec = bound
    with pytest.raises(ValueError, match="fixture CA"):
        prepare_requests_execution_overlay(spec, candidate, execution, public, original_ca_sha256=sha(ORIGINAL_CA))
    assert not execution.exists()


@pytest.mark.parametrize("expected", [None, "", "A" * 64, "0" * 64])
def test_missing_or_wrong_admitted_upstream_ca_hash_refuses(bound, expected):
    candidate, execution, spec = bound
    with pytest.raises((ValueError, SourceConflict), match="original CA"):
        prepare_requests_execution_overlay(spec, candidate, execution, PUBLIC_CA, original_ca_sha256=expected)
    assert not execution.exists()


def test_missing_original_ca_refuses_even_with_fresh_candidate_revision(bound):
    candidate, execution, spec = bound
    (candidate / CA_PATH).unlink()
    spec = replace(spec, source_revision=check_source_revision(candidate))
    with pytest.raises(SourceConflict, match="CA bundle is missing"):
        prepare((candidate, execution, spec))
    assert not execution.exists()


def test_model_replaced_ca_is_not_implicitly_authorized_by_candidate_revision(bound):
    candidate, execution, spec = bound
    (candidate / CA_PATH).write_bytes(b"edited by candidate model\n")
    assert check_source_revision(candidate) == spec.source_revision
    spec = replace(spec, source_revision=check_source_revision(candidate))
    with pytest.raises(SourceConflict, match="upstream bundle"):
        prepare((candidate, execution, spec))
    assert not execution.exists()


def test_ca_change_after_preparation_refuses_even_when_core_revision_is_unchanged(bound):
    execution_spec, overlay = prepare(bound)
    candidate, _, spec = bound
    (candidate / CA_PATH).write_bytes(ORIGINAL_CA + b"changed after preparation\n")
    assert check_source_revision(candidate) == spec.source_revision
    with pytest.raises(SourceConflict, match="upstream bundle"):
        validate(bound, execution_spec, overlay)


def test_execution_ca_extra_append_refuses_even_when_core_revision_is_unchanged(bound):
    execution_spec, overlay = prepare(bound)
    (bound[1] / CA_PATH).write_bytes(ORIGINAL_CA + b"\n" + PUBLIC_CA + b"\n" + PUBLIC_CA)
    assert check_source_revision(bound[1]) == execution_spec.source_revision
    with pytest.raises(SourceConflict, match="exact CA append"):
        validate(bound, execution_spec, overlay)


def test_forged_core_binding_cannot_validate_current_execution(bound):
    execution_spec, overlay = prepare(bound)
    forged = replace(overlay, candidate_core_revision="e" * 64, execution_core_revision="e" * 64)
    with pytest.raises(ValueError, match="binding"):
        validate(bound, execution_spec, forged)


def test_destination_inside_candidate_or_existing_destination_is_never_reused(bound):
    candidate, execution, spec = bound
    with pytest.raises(SourceConflict, match="outside candidate"):
        prepare((candidate, candidate / "execution", spec))
    execution.mkdir()
    sentinel = execution / "preserve.txt"
    sentinel.write_bytes(b"existing data")
    with pytest.raises(SourceConflict, match="fresh"):
        prepare(bound)
    assert sentinel.read_bytes() == b"existing data"


def test_missing_destination_parent_is_not_implicitly_created(bound):
    candidate, execution, spec = bound
    with pytest.raises(SourceConflict, match="existing owned"):
        prepare((candidate, execution / "missing-parent" / "snapshot", spec))
    assert not execution.exists()


@pytest.mark.parametrize("change", [
    {"fixture_identity": None},
    {"fixture_identity": None, "private_pytest_identity": None},
])
def test_missing_qualification_identity_metadata_refuses(bound, change):
    candidate, execution, spec = bound
    with pytest.raises(ValueError, match="metadata"):
        prepare((candidate, execution, replace(spec, **change)))
    assert not execution.exists()


@pytest.mark.parametrize("path", ["requests/api.py", CA_PATH, "extra.py", ".env", ".tox/hidden.txt"])
def test_execution_mutation_including_ignored_extra_files_invalidates_overlay(bound, path):
    execution_spec, overlay = prepare(bound)
    _, execution, _ = bound
    target = execution / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"unadmitted execution bytes\n")
    with pytest.raises(SourceConflict):
        validate(bound, execution_spec, overlay)


@pytest.mark.parametrize("directory", ["empty-extra", ".tox", ".hidden"])
def test_extra_execution_directories_are_not_silently_filtered(bound, directory):
    execution_spec, overlay = prepare(bound)
    (bound[1] / directory).mkdir()
    with pytest.raises(SourceConflict, match="unadmitted directory"):
        validate(bound, execution_spec, overlay)


def test_changed_candidate_invalidates_previously_prepared_overlay(bound):
    execution_spec, overlay = prepare(bound)
    (bound[0] / "requests/api.py").write_bytes(b"changed candidate\n")
    with pytest.raises(SourceConflict, match="stale"):
        validate(bound, execution_spec, overlay)


def test_overlay_cannot_change_spec_deadline_or_binding_hash(bound):
    execution_spec, overlay = prepare(bound)
    with pytest.raises(ValueError, match="only the source revision"):
        validate(bound, replace(execution_spec, timeout_seconds=execution_spec.timeout_seconds - 1), overlay)
    with pytest.raises(ValueError, match="binding"):
        validate(bound, execution_spec, replace(overlay, check_spec_digest="f" * 64))


def _symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit symlink creation")


def test_source_symlink_is_refused_without_copying_target(bound):
    candidate, execution, _ = bound
    outside = candidate.parent / "outside.py"
    outside.write_bytes(b"must not copy outside source")
    _symlink_or_skip(candidate / "requests/linked.py", outside)
    with pytest.raises(SourceConflict, match="symlink|escapes"):
        prepare(bound)
    assert not execution.exists()


def test_symlink_destination_parent_is_refused(bound):
    candidate, execution, spec = bound
    actual = candidate.parent / "actual"
    actual.mkdir()
    alias = candidate.parent / "alias"
    _symlink_or_skip(alias, actual, directory=True)
    with pytest.raises(SourceConflict, match="link"):
        prepare((candidate, alias / "execution", spec))
    assert not execution.exists() and list(actual.iterdir()) == []


def test_execution_symlink_is_refused_by_complete_inventory(bound):
    execution_spec, overlay = prepare(bound)
    _symlink_or_skip(bound[1] / ".hidden-link", bound[0] / "requests/api.py")
    with pytest.raises(SourceConflict):
        validate(bound, execution_spec, overlay)


def test_preexisting_cancellation_refuses_before_any_destination_mutation(bound):
    def cancelled():
        raise RuntimeError("synthetic caller cancellation")

    with pytest.raises(RuntimeError, match="caller cancellation"):
        prepare(bound, cancel_check=cancelled)
    assert not bound[1].exists()
