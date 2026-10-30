"""Synthetic private grammar integration; no target/bootstrap execution."""
import hashlib
import json
import time

import pytest

from atomic_flow import check_execution
from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.agents.completion import assess_completion
from atomic_flow.agents.execution import CommandResult, ExecutionUnavailable
from atomic_flow.agents.task import Step, Task
from atomic_flow.agents.verify import check_source_revision, verify_edit
from atomic_flow.check_obligation import CheckObligation, obligation_binding
from atomic_flow.check_private_runtime import SYNTAX_MARKER, SYNTAX_POLICY
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec
from atomic_flow.config import AtomicConfig


@pytest.fixture
def bound(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_value.py").write_bytes(b"def test_value():\n    assert True\n")
    (tmp_path / "value.py").write_bytes(b"value = 1\n")
    runtime = RuntimeIdentity(base_image="example/target@sha256:" + "a" * 64,
        base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
        interpreter="/opt/p07-python27/bin/python2.7", python_version=(2, 7, 18), runner="pytest",
        profile_sha256="d" * 64, helper_sha256="e" * 64, inventory_sha256="f" * 64,
        provisioning_receipt_sha256="1" * 64, private_prefix="/opt/p07-python27",
        launcher="/opt/p07-runtime/python27", launcher_sha256="2" * 64)
    spec = CheckSpec("requests_private_synthetic", "3" * 40, "4" * 40, check_source_revision(tmp_path),
                     runtime.image_id, runtime.interpreter, "pytest", ("tests/test_value.py",), runtime_identity=runtime)
    obligation = CheckObligation.seal(spec, tmp_path, environment_digest="5" * 64, deadline_at=time.time() + 600)
    return tmp_path, obligation


def observation(request, *, failed=False):
    return {"schema_version": 1, "kind": "private_syntax_observation", "policy": SYNTAX_POLICY,
            "nonce": request.nonce, "request_digest": request.digest, "runtime_digest": request.runtime.digest,
            "check_spec_digest": request.check_spec_digest, "source_revision": request.source_revision,
            "path": request.path, "file_sha256": request.file_sha256, "root": request.target_root,
            "interpreter": request.runtime.interpreter, "python_version": list(request.runtime.python_version),
            "prefix": request.runtime.private_prefix, "outcome": "failed" if failed else "passed", "finished": True,
            "diagnostic": {"kind": "SyntaxError", "line": 1, "offset": 1} if failed else None}


def syntax_response(request, *, failed=False, **changes):
    value = observation(request, failed=failed) | changes
    return CommandResult(int(failed), SYNTAX_MARKER + json.dumps(value) + "\n", "")


def fake_ordinary(monkeypatch, calls):
    def ordinary(obligation, root, *, cancel_check=None):
        spec = obligation.derive(root)
        calls.append(("ordinary", spec))
        # Deliberately synthetic; this test does not qualify private pytest execution.
        return spec, check_execution.CheckReceipt(spec.digest, spec.source_revision, spec.source_revision,
                                                  True, None, CommandResult(0, "synthetic ordinary result", ""))
    monkeypatch.setattr("atomic_flow.check_obligation.run_obligation_check", ordinary)


def test_python2_edit_uses_target_grammar_before_ordinary_check(bound, monkeypatch):
    root, obligation = bound
    (root / "value.py").write_bytes(b"print 'valid Python2 grammar'\n")
    calls = []
    def syntax(spec, workspace, path, **kwargs):
        assert spec.runtime_identity.python_version == (2, 7, 18)
        assert spec.source_revision == check_source_revision(workspace)
        assert (workspace / path).read_bytes().startswith(b"print '")
        calls.append(("syntax", spec))
        return {"outcome": "passed", "diagnostic": None}
    monkeypatch.setattr(check_execution, "run_private_syntax", syntax)
    fake_ordinary(monkeypatch, calls)
    verdict = verify_edit(root, "value.py", private_workspace=True, check_obligation=obligation)
    assert verdict.ok and verdict.checks == ["syntax", "ordinary_test"]
    assert [kind for kind, _ in calls] == ["syntax", "ordinary"]
    assert calls[0][1].source_revision == calls[1][1].source_revision


def test_target_syntax_failure_stops_post_edit_ordinary_check(bound, monkeypatch):
    root, obligation = bound
    calls = []
    monkeypatch.setattr(check_execution, "run_private_syntax", lambda *args, **kwargs:
                        {"outcome": "failed", "diagnostic": {"kind": "SyntaxError", "line": 1, "offset": 1}})
    fake_ordinary(monkeypatch, calls)
    verdict = verify_edit(root, "value.py", private_workspace=True, check_obligation=obligation)
    assert not verdict.ok and verdict.checks == ["syntax"] and verdict.exit_code == 1
    assert verdict.diagnostics[0]["kind"] == "SyntaxError" and calls == []


def test_target_syntax_infrastructure_is_not_reported_as_source_failure(bound, monkeypatch):
    root, obligation = bound
    calls = []
    def unavailable(*args, **kwargs):
        raise ExecutionUnavailable("private container unavailable")
    monkeypatch.setattr(check_execution, "run_private_syntax", unavailable)
    fake_ordinary(monkeypatch, calls)
    with pytest.raises(ExecutionUnavailable, match="container unavailable"):
        verify_edit(root, "value.py", private_workspace=True, check_obligation=obligation)
    assert calls == []


def test_run_private_syntax_binds_current_source_file_nonce_and_runtime(bound, monkeypatch):
    root, obligation = bound
    (root / "value.py").write_bytes(b"print 'target only'\n")
    spec = obligation.derive(root)
    requests = []
    def execute(args, workspace, **kwargs):
        request = kwargs["host_syntax_request"]
        assert args == list(spec.argv[1:]) and workspace == root
        assert kwargs["host_spec"] == spec and kwargs["host_spec_nonce"] == request.nonce
        assert request.check_spec_digest == spec.digest and request.source_revision == spec.source_revision
        assert request.runtime == spec.runtime_identity and request.target_root == spec.target_root
        assert request.file_sha256 == hashlib.sha256((root / "value.py").read_bytes()).hexdigest()
        assert len(request.nonce) == 32
        requests.append(request)
        return syntax_response(request)
    monkeypatch.setattr("atomic_flow.agents.execution.run_python", execute)
    first = check_execution.run_private_syntax(spec, root, "value.py")
    second = check_execution.run_private_syntax(spec, root, "value.py")
    assert first["outcome"] == second["outcome"] == "passed"
    assert requests[0].nonce != requests[1].nonce


@pytest.mark.parametrize("field", ["nonce", "request_digest", "runtime_digest", "source_revision", "file_sha256", "check_spec_digest"])
def test_private_syntax_rejects_receipt_binding_substitution(bound, monkeypatch, field):
    root, obligation = bound
    spec = obligation.derive(root)
    def execute(*args, **kwargs):
        return syntax_response(kwargs["host_syntax_request"], **{field: "9" * (32 if field == "nonce" else 64)})
    monkeypatch.setattr("atomic_flow.agents.execution.run_python", execute)
    with pytest.raises(ExecutionUnavailable, match="incomplete or source identity changed"):
        check_execution.run_private_syntax(spec, root, "value.py")


def test_private_syntax_detects_source_mutation_after_received_pass(bound, monkeypatch):
    root, obligation = bound
    def execute(*args, **kwargs):
        response = syntax_response(kwargs["host_syntax_request"])
        (root / "value.py").write_bytes(b"value = 2\n")
        return response
    monkeypatch.setattr("atomic_flow.agents.execution.run_python", execute)
    with pytest.raises(ExecutionUnavailable, match="source identity changed"):
        check_execution.run_private_syntax(obligation.derive(root), root, "value.py")


def test_private_syntax_failure_remains_distinct_completed_observation(bound, monkeypatch):
    root, obligation = bound
    monkeypatch.setattr("atomic_flow.agents.execution.run_python", lambda *args, **kwargs:
                        syntax_response(kwargs["host_syntax_request"], failed=True))
    value = check_execution.run_private_syntax(obligation.derive(root), root, "value.py")
    assert value["outcome"] == "failed" and value["finished"] is True


@pytest.mark.parametrize("outcome", ["passed", "failed", "unavailable"])
def test_final_gate_uses_target_grammar_and_stops_before_ordinary_on_failure(bound, monkeypatch, outcome):
    root, obligation = bound
    changeset = ChangeSet.create(root)
    edited = changeset.workspace / "value.py"
    edited.write_bytes(b"print 'valid Python2 grammar'\n")
    from atomic_flow.agents.source import digest
    changeset.track("value.py", digest(edited.read_bytes()))
    task = Task.new("Check target grammar", root, [Step(0, "Check", verify_with="none", status="done")])
    task.current_index = 1
    task.plan_approval["check_obligation"] = obligation_binding(obligation)
    calls = []
    def syntax(spec, workspace, path, **kwargs):
        calls.append(("syntax", spec))
        if outcome == "unavailable":
            raise ExecutionUnavailable("synthetic container failure")
        return {"outcome": outcome, "diagnostic": None if outcome == "passed" else {"kind": "SyntaxError", "line": 1, "offset": 1}}
    monkeypatch.setattr(check_execution, "run_private_syntax", syntax)
    fake_ordinary(monkeypatch, calls)
    config = AtomicConfig()
    config.execution.mode = "docker"
    config.execution.image = obligation.template.image
    report = assess_completion(task, changeset, config, check_obligation=obligation)
    assert report["ready_to_publish"] is (outcome == "passed")
    assert [kind for kind, _ in calls] == (["syntax", "ordinary"] if outcome == "passed" else ["syntax"])
    if outcome == "unavailable":
        assert report["tests"] == "unavailable"
    else:
        assert report["target_syntax_observations"][0]["outcome"] == outcome


@pytest.mark.parametrize("binding", [None, {"digest": "9" * 64, "environment_digest": "5" * 64, "deadline_at": 400.0}])
def test_final_gate_rejects_unapproved_obligation_before_target_syntax_execution(bound, monkeypatch, binding):
    root, obligation = bound
    changeset = ChangeSet.create(root)
    edited = changeset.workspace / "value.py"
    edited.write_bytes(b"print 'target only'\n")
    from atomic_flow.agents.source import digest
    changeset.track("value.py", digest(edited.read_bytes()))
    task = Task.new("Check", root, [Step(0, "Check", verify_with="none", status="done")])
    task.current_index = 1
    if binding is not None:
        task.plan_approval["check_obligation"] = binding
    monkeypatch.setattr(check_execution, "run_private_syntax", lambda *args, **kwargs:
                        pytest.fail("unapproved obligation must refuse before target Docker syntax execution"))
    calls = []
    fake_ordinary(monkeypatch, calls)
    report = assess_completion(task, changeset, AtomicConfig(), check_obligation=obligation)
    assert report["ready_to_publish"] is False and calls == []
