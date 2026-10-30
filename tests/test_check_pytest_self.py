"""Pure host-contract tests: no target imports, subprocess or Docker execution."""
import json
from dataclasses import FrozenInstanceError

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_pytest_self import (
    MARKER,
    PytestSelfIdentity,
    pytest_self_args,
    pytest_self_receipt,
)

NONCE = "a" * 32
ROOT = "/workspace"
PYTHON = "/usr/local/bin/python"
PATHS = ("testing/test_example.py",)


def identity(**changes):
    values = dict(python_version=(3, 11, 9), pytest_version="5.4.0",
                  source_sha256=(("src/_pytest/__init__.py", "a" * 64), ("src/pytest.py", "b" * 64)))
    return PytestSelfIdentity(**(values | changes))


def receipt(runner=None, **changes):
    runner = runner or identity()
    prefix = ROOT + ("/" + runner.source_root if runner.source_root else "")
    value = {"nonce": NONCE, "root": ROOT, "runner": "pytest-self", "interpreter": PYTHON,
             "interpreter_resolved": "/usr/local/bin/python3.11", "python_version": list(runner.python_version),
             "pytest_version": runner.pytest_version, "pytest_module": ROOT + "/" + runner.pytest_entrypoint,
             "_pytest_module": prefix + "/_pytest/__init__.py", "pytest_self_identity": runner.as_dict(),
             "argv": ["-rA", *(["--tb=short"] if runner.traceback == "short" else []), *PATHS],
             "files": list(PATHS), "finished": True, "collection_complete": True,
             "collected": 2, "completed": 2, "collection_errors": 0, "skipped": 0, "failed": 0,
             "call_failed": 0, "setup_failed": 0, "teardown_failed": 0, "exit_code": 0}
    return value | changes


def result(value, **changes):
    return CommandResult(**({"returncode": value["exit_code"], "stdout": MARKER + json.dumps(value), "stderr": ""} | changes))


def validate(value, runner=None, **changes):
    return pytest_self_receipt(result(value, **changes), runner or identity(), NONCE, ROOT, PYTHON, PATHS)


def test_identity_is_immutable_and_does_not_freeze_editable_implementation():
    runner = identity()
    with pytest.raises(FrozenInstanceError):
        runner.pytest_version = "other"
    assert "src/_pytest/python.py" not in dict(runner.source_sha256)
    assert "src/_pytest/main.py" not in dict(runner.source_sha256)


@pytest.mark.parametrize("changes", [
    {"python_version": (3, 7, 9)}, {"python_version": [3, 11, 9]},
    {"source_sha256": (("src/pytest.py", "b" * 64),)},
    {"source_sha256": (("src/_pytest/__init__.py", "a" * 64), ("src/pytest.py", "B" * 64))},
    {"source_sha256": (("../pytest.py", "a" * 64), ("src/pytest.py", "b" * 64))},
    {"source_root": "installed"}, {"pytest_entrypoint": "pytest.py"},
    {"traceback": "none"}, {"pytest_version": "version with spaces"},
])
def test_reject_unadmitted_identity(changes):
    with pytest.raises(ValueError):
        identity(**changes)


@pytest.mark.parametrize("source_root,entry", [("", "pytest.py"), ("src", "src/pytest/__init__.py")])
def test_supported_source_layouts(source_root, entry):
    internal = (source_root + "/" if source_root else "") + "_pytest/__init__.py"
    runner = identity(source_root=source_root, pytest_entrypoint=entry,
                      source_sha256=tuple(sorted(((internal, "a" * 64), (entry, "b" * 64)))))
    value = receipt(runner)
    assert validate(value, runner) == value


def test_builder_is_isolated_and_uses_actual_target_imports():
    args = pytest_self_args(identity(traceback="short"), NONCE, ROOT, PYTHON, PATHS)
    assert args[:2] == ["-I", "-c"]
    assert args[3:6] == [NONCE, ROOT, PYTHON]
    assert args[-1] == PATHS[0]
    source = args[2]
    compile(source, "<trusted-pytest-self-bootstrap>", "exec")
    assert "import pytest,_pytest" in source
    assert "sys.path.insert(0,os.path.join(root,identity['source_root']))" in source
    assert "sys.pycache_prefix=tempfile.mkdtemp" in source
    assert "--tb=short" in source
    assert "filterwarnings" not in source
    assert "-x" not in source


def test_short_traceback_profile_is_receipt_bound():
    runner = identity(traceback="short")
    assert validate(receipt(runner), runner) is not None
    assert validate(receipt(runner), identity()) is None


def test_complete_pass_and_call_failure_are_distinct_completed_receipts():
    assert validate(receipt()) is not None
    assert validate(receipt(exit_code=1, failed=1, call_failed=1)) is not None


@pytest.mark.parametrize("changes", [
    {"collected": 0, "completed": 0}, {"completed": 1}, {"finished": False},
    {"collection_complete": False}, {"collection_errors": 1}, {"skipped": 1},
    {"setup_failed": 1, "failed": 1, "exit_code": 1},
    {"teardown_failed": 1, "failed": 1, "exit_code": 1},
    {"exit_code": 2}, {"exit_code": 5}, {"failed": 1, "call_failed": 1},
    {"exit_code": 1}, {"exit_code": 1, "failed": 3, "call_failed": 3},
    {"collected": True}, {"completed": -1}, {"finished": 1},
    {"pytest_module": "/usr/lib/python/site-packages/pytest.py"},
    {"_pytest_module": "/usr/lib/python/site-packages/_pytest/__init__.py"},
    {"files": []}, {"argv": ["-rA", "-x", *PATHS]}, {"nonce": "b" * 32},
    {"python_version": [3, 12, 0]}, {"pytest_self_identity": {}},
    {"interpreter_resolved": None}, {"interpreter_resolved": "/usr/../bin/python"},
])
def test_partial_infrastructure_or_wrong_identity_cannot_pass_or_repair(changes):
    assert validate(receipt(**changes)) is None


@pytest.mark.parametrize("changes", [
    {"timed_out": True}, {"stdout_truncated": True}, {"stderr_truncated": True},
    {"stdout": "no receipt"}, {"stdout": MARKER + "{"},
    {"stdout": (MARKER + json.dumps(receipt()) + "\n") * 2},
])
def test_uncertain_or_duplicate_output_refuses(changes):
    assert validate(receipt(), **changes) is None


@pytest.mark.parametrize("paths", [("../testing/test.py",), ("testing/test.py::test_one",), (), ("-test.py",)])
def test_builder_rejects_test_selection_or_escape(paths):
    with pytest.raises(ValueError):
        pytest_self_args(identity(), NONCE, ROOT, PYTHON, paths)
