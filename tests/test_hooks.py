from dataclasses import asdict

import pytest

from atomic_flow.agents.base import ToolContract
from atomic_flow.hooks import HookFailed, HookManager, HookRejected


def test_explicit_registration_immutable_nested_and_veto():
    manager = HookManager()
    with pytest.raises(PermissionError):
        manager.register("before_tool", lambda _: None)
    observed = []

    def hook(data):
        observed.append(data)
        with pytest.raises(TypeError):
            data["args"]["path"] = "other.py"
        assert data["args"]["paths"] == ("a.py",)
        return False

    manager.register("before_tool", hook, approved=True)
    args = {"path": "a.py", "paths": ["a.py"]}
    with pytest.raises(HookRejected):
        manager.before_tool("read_file", args, ToolContract(), "reader")
    assert args == {"path": "a.py", "paths": ["a.py"]}
    assert observed[0]["contract"] == asdict(ToolContract())


def test_hook_failure_deadline_and_no_argument_replacement(monkeypatch):
    manager = HookManager()
    manager.register("before_tool", lambda _: {"path": "changed"}, approved=True)
    with pytest.raises(HookFailed, match="only None"):
        manager.before_tool("read_file", {}, ToolContract())
    manager = HookManager()
    manager.register("run_start", lambda _: None, approved=True, timeout_seconds=0.1)
    clock = iter([1.0, 1.2])
    monkeypatch.setattr("atomic_flow.hooks.time.monotonic", lambda: next(clock))
    with pytest.raises(HookFailed, match="cooperative"):
        manager.lifecycle("run_start", {})


def test_after_callback_receives_proof_not_body_and_failure_safe():
    manager = HookManager()
    seen = []
    manager.register("after_tool", lambda value: seen.append(value), approved=True)
    manager.after_tool("patch_file", {}, ToolContract(effects=("write",)),
                       {"status": "applied", "path": "x.py", "source": "private", "content_hash": "a"})
    assert "source" not in seen[0]["result"]
    assert seen[0]["result"]["status"] == "applied"
    manager = HookManager()

    def bad(_):
        raise RuntimeError("SECRET")

    manager.register("run_error", bad, approved=True)
    with pytest.raises(HookFailed) as exc:
        manager.lifecycle("run_error", {})
    assert "SECRET" not in str(exc.value)


def test_hook_limits_and_cancellation():
    manager = HookManager()
    manager.lifecycle("run_start", {"value": "x" * 33000})
    manager.register("run_start", lambda _: None, approved=True)
    with pytest.raises(HookFailed):
        manager.lifecycle("run_start", {"value": "x" * 33000})
    with pytest.raises(ValueError):
        manager.register("run_start", lambda _: None, approved=True, timeout_seconds=float("nan"))
    called = []
    manager.register("before_tool", lambda _: called.append(True), approved=True)

    def cancel():
        raise TimeoutError("cancel")

    with pytest.raises(TimeoutError):
        manager.before_tool("x", {}, ToolContract(), cancel_check=cancel)
    assert not called
