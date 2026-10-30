"""Pure request/receipt tests; no target code, Docker or bootstrap execution."""
import ast
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_private_runtime import (
    SYNTAX_MARKER,
    SYNTAX_POLICY,
    PrivateSyntaxRequest,
    private_syntax_args,
    private_syntax_receipt,
)
from atomic_flow.check_runtime import RuntimeIdentity


def runtime():
    return RuntimeIdentity(base_image="example/target@sha256:" + "a" * 64,
                           base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
                           interpreter="/opt/p07-python36/bin/python3.6", python_version=(3, 6, 15),
                           runner="sympy-native", profile_sha256="d" * 64, helper_sha256="e" * 64,
                           inventory_sha256="f" * 64, provisioning_receipt_sha256="1" * 64,
                           private_prefix="/opt/p07-python36", launcher="/opt/p07-runtime/python36",
                           launcher_sha256="2" * 64)


def request():
    return PrivateSyntaxRequest(runtime(), "3" * 64, "4" * 64, "module/source.py", "5" * 64, "6" * 32)


def observation(bound=None, *, failed=False):
    bound = bound or request()
    return {"schema_version": 1, "kind": "private_syntax_observation", "policy": SYNTAX_POLICY,
            "nonce": bound.nonce, "request_digest": bound.digest, "runtime_digest": bound.runtime.digest,
            "check_spec_digest": bound.check_spec_digest, "source_revision": bound.source_revision,
            "path": bound.path, "file_sha256": bound.file_sha256, "root": bound.target_root,
            "interpreter": bound.runtime.interpreter, "python_version": list(bound.runtime.python_version),
            "prefix": bound.runtime.private_prefix, "outcome": "failed" if failed else "passed",
            "finished": True, "diagnostic": {"kind": "SyntaxError", "line": 2, "offset": 1} if failed else None}


def result(value, **changes):
    return CommandResult(**({"returncode": int(value["outcome"] == "failed"),
                             "stdout": SYNTAX_MARKER + json.dumps(value) + "\n", "stderr": ""} | changes))


def test_request_frozen_canonical_and_bound_to_all_source_contract_fields():
    bound = request()
    assert json.loads(bound.canonical_bytes()) == bound.as_dict()
    with pytest.raises(FrozenInstanceError):
        bound.path = "other.py"
    for key in ("check_spec_digest", "source_revision", "file_sha256"):
        changed = replace(bound, **{key: "7" * 64})
        assert changed.digest != bound.digest
        assert private_syntax_receipt(result(observation()), changed) is None


@pytest.mark.parametrize("changes", [
    {"path": "../source.py"}, {"path": "a//source.py"}, {"path": "source.txt"},
    {"path": "/source.py"}, {"path": ".hidden/source.py"}, {"path": "source.py;exec"},
    {"nonce": "a" * 31}, {"nonce": "A" * 32}, {"source_revision": "a" * 40},
    {"file_sha256": None}, {"check_spec_digest": "A" * 64}, {"target_root": "/tmp"},
    {"runtime": {}},
])
def test_reject_invalid_request(changes):
    with pytest.raises(ValueError):
        replace(request(), **changes)


def test_legacy_identity_without_launcher_hash_is_not_execution_eligible():
    with pytest.raises(ValueError, match="private execution"):
        replace(request(), runtime=replace(runtime(), launcher_sha256=None))


@pytest.mark.parametrize("version", [(2, 7, 13), (2, 7, 18), (3, 6, 15)])
def test_fixed_launcher_arguments_embed_payload_without_positional_argv(version):
    bound = request()
    if version[0] == 2:
        bound = replace(bound, runtime=replace(runtime(), python_version=version, runner="pytest",
                                               interpreter="/opt/p07-python27/bin/python2.7",
                                               private_prefix="/opt/p07-python27", launcher="/opt/p07-runtime/python27"))
    args = private_syntax_args(bound)
    assert len(args) == 2 and args[0] == "-c"
    tree = ast.parse(args[1])  # Host syntax parsing only; never exec the bootstrap.
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports == {"hashlib", "json", "os", "sys"}
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.JoinedStr)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"exec", "eval", "__import__"} for node in ast.walk(tree))
    assert "compile(raw,request['path'],'exec',0,True)" in args[1]
    assert bound.digest in args[1] and bound.file_sha256 in args[1]
    assert "REQUEST_JSON" not in args[1] and "REQUEST_DIGEST" not in args[1]
    assert "sys.argv" not in args[1]


@pytest.mark.parametrize("failed", [False, True])
def test_complete_distinct_syntax_observation_accepted(failed):
    value = observation(failed=failed)
    assert private_syntax_receipt(result(value), request()) == value


@pytest.mark.parametrize("changes", [
    {"nonce": "7" * 32}, {"runtime_digest": "7" * 64}, {"check_spec_digest": "7" * 64},
    {"source_revision": "7" * 64}, {"file_sha256": "7" * 64}, {"path": "different.py"},
    {"root": "/testbed"}, {"interpreter": "/usr/bin/python3"}, {"prefix": "/usr"},
    {"python_version": [3, 6, True]}, {"finished": 1}, {"schema_version": True},
    {"policy": "unknown"}, {"kind": "ordinary_test"}, {"unknown": "extra"},
    {"diagnostic": {"kind": "SyntaxError", "line": 1, "offset": 1}},
])
def test_mismatched_or_malformed_observations_refused(changes):
    assert private_syntax_receipt(result(observation() | changes), request()) is None


@pytest.mark.parametrize("diagnostic", [
    None, {}, {"kind": "RuntimeError", "line": 1, "offset": 1},
    {"kind": "SyntaxError", "line": True, "offset": 1},
    {"kind": "SyntaxError", "line": 1, "offset": -1},
    {"kind": "SyntaxError", "line": 1, "offset": 1, "source": "unbounded source"},
])
def test_failure_diagnostic_is_bounded_structured_syntax_only(diagnostic):
    value = observation(failed=True) | {"diagnostic": diagnostic}
    assert private_syntax_receipt(result(value), request()) is None


@pytest.mark.parametrize("changes", [
    {"returncode": 125}, {"returncode": True}, {"timed_out": True}, {"stdout_truncated": True},
    {"stderr_truncated": True}, {"stderr": "infrastructure error"}, {"stdout": "{}"},
])
def test_infrastructure_failure_and_incomplete_streams_are_not_syntax_evidence(changes):
    assert private_syntax_receipt(result(observation(), **changes), request()) is None


def test_wrong_marker_duplicate_marker_and_noise_refused():
    value = observation()
    good = result(value)
    for text in (good.stdout * 2, "noise\n" + good.stdout,
                 good.stdout.replace(SYNTAX_MARKER, "ATOMIC_CHECK_SPEC_RECEIPT:")):
        assert private_syntax_receipt(replace(good, stdout=text), request()) is None
