import json
import os
import pickle
import traceback

import pytest

from atomic_flow.agents.execution import run_python
from atomic_flow.config import ExecutionConfig
from atomic_flow.credentials import CredentialBroker, CredentialDenied, CredentialUseError


def grant(broker, project, **kwargs):
    return broker.grant(project=project, connector="remote-worker", operations=["submit"],
                        env_name="SCOPED_TEST_TOKEN", approved=True, **kwargs)


def use(broker, authority, project_root, consumer=lambda value: {"accepted": True}, **overrides):
    arguments = {"project": project_root, "connector": "remote-worker", "operation": "submit", "consumer": consumer}
    arguments.update(overrides)
    return broker.use(authority.id, **arguments)


def test_scope_gate_precedes_resolver_and_expiry_revocation(tmp_path):
    now = [1000]
    resolutions = []
    broker = CredentialBroker(clock=lambda: now[0], resolver=lambda name: resolutions.append(name) or "secret-value")
    authority = grant(broker, tmp_path, ttl_seconds=5)
    for overrides in ({"project": tmp_path / "other"}, {"connector": "different"}, {"operation": "delete"}):
        with pytest.raises(CredentialDenied):
            use(broker, authority, tmp_path, **overrides)
    assert resolutions == []
    assert use(broker, authority, tmp_path) == {"accepted": True}
    assert resolutions == ["SCOPED_TEST_TOKEN"]
    now[0] = 1005
    with pytest.raises(CredentialDenied):
        use(broker, authority, tmp_path)
    renewed = grant(broker, tmp_path)
    broker.revoke(renewed.id)
    with pytest.raises(CredentialDenied):
        use(broker, renewed, tmp_path)
    assert len(resolutions) == 1


def test_revocation_during_resolution_blocks_connector(tmp_path):
    authority = None
    called = []

    def resolve(name):
        broker.revoke(authority.id)
        return "secret-value"

    broker = CredentialBroker(resolver=resolve)
    authority = grant(broker, tmp_path)
    with pytest.raises(CredentialDenied):
        use(broker, authority, tmp_path, consumer=lambda value: called.append(True))
    assert not called


def test_approval_exact_scope_and_lifetime_bounds(tmp_path):
    broker = CredentialBroker()
    arguments = dict(project=tmp_path, connector="worker", operations=["submit"], env_name="TEST_TOKEN")
    with pytest.raises(CredentialDenied, match="approval"):
        broker.grant(**arguments)
    for ttl in (0, -1, 3601, True, float("nan"), float("inf")):
        with pytest.raises(CredentialDenied):
            broker.grant(**arguments, approved=True, ttl_seconds=ttl)
    for operations in (["*"], ["submit", "worker/*"], [], "submit"):
        with pytest.raises(CredentialDenied):
            broker.grant(**{**arguments, "operations": operations}, approved=True)
    with pytest.raises(CredentialDenied):
        broker.grant(**{**arguments, "env_name": "TEST=evil"}, approved=True)


def test_values_not_serialized_returned_or_retained(tmp_path, monkeypatch):
    secret = "opaque_test_credential_928"
    monkeypatch.setenv("SCOPED_TEST_TOKEN", secret)
    broker = CredentialBroker()
    authority = grant(broker, tmp_path)
    captured = []

    def connector(value):
        captured.append(value)
        assert value.reveal() == secret
        assert secret not in repr(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        return {"status": "accepted", "receipt": 42}

    result = use(broker, authority, tmp_path, connector)
    assert secret not in json.dumps([authority.metadata(), result])
    assert secret not in repr(broker) and secret not in repr(authority)
    with pytest.raises(CredentialDenied, match="no longer active"):
        captured[0].reveal()
    with pytest.raises(TypeError):
        pickle.dumps(broker)
    for callback in (lambda value: value.reveal(), lambda value: {"header": "Bearer " + value.reveal()},
                     lambda value: {value.reveal(): "key"}, lambda value: value, lambda value: b"opaque"):
        with pytest.raises(CredentialUseError):
            use(broker, authority, tmp_path, callback)


def test_exception_details_do_not_reach_error_or_traceback(tmp_path):
    secret = "exception_credential_9938"
    broker = CredentialBroker(resolver=lambda name: secret)
    authority = grant(broker, tmp_path)

    def broken(value):
        raise RuntimeError("header=" + value.reveal())

    with pytest.raises(CredentialUseError) as error:
        use(broker, authority, tmp_path, broken)
    formatted = "".join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))
    assert secret not in formatted
    assert "details suppressed" in formatted


def test_resolver_failure_and_missing_env_are_sanitized(tmp_path):
    def broken(name):
        raise ValueError("private-resolver-error")

    for resolver in (broken, lambda name: None, lambda name: "", lambda name: b"wrong-kind"):
        broker = CredentialBroker(resolver=resolver)
        authority = grant(broker, tmp_path)
        with pytest.raises(CredentialUseError) as error:
            use(broker, authority, tmp_path)
        assert "private-resolver-error" not in str(error.value)


def test_no_child_environment_injection_or_secret_artifact(tmp_path, monkeypatch):
    secret = "never_inherit_this_scoped_value"
    monkeypatch.setenv("SCOPED_TEST_TOKEN", secret)
    parent_before = dict(os.environ)
    broker = CredentialBroker()
    authority = grant(broker, tmp_path)

    def trusted_connector(value):
        assert value.reveal() == secret
        result = run_python(["-c", "import json,os; print(json.dumps(dict(os.environ)))"], tmp_path,
                            config=ExecutionConfig(mode="local"))
        environment = json.loads(result.stdout)
        assert "SCOPED_TEST_TOKEN" not in environment
        return {"returncode": result.returncode, "environment": environment}

    result = use(broker, authority, tmp_path, trusted_connector)
    assert dict(os.environ) == parent_before
    artifact = json.dumps({"grant": authority.metadata(), "result": result})
    assert secret not in artifact
    (tmp_path / "artifact.json").write_text(artifact)
    assert secret not in (tmp_path / "artifact.json").read_text()
    # New host process/broker does not recover authority from a metadata artifact.
    with pytest.raises(CredentialDenied):
        use(CredentialBroker(), authority, tmp_path)
