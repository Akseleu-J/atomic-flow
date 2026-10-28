"""Pure candidate Requests launcher/receipt contracts; no target execution."""
import ast
import json
from dataclasses import replace

import pytest

from atomic_flow.agents.execution import CommandResult
from atomic_flow.check_fixture import PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_private_pytest import MARKER, private_pytest_args, private_pytest_receipt
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckSpec

NONCE = "7" * 32


def bound(version=(3, 6, 15)):
    major = "36" if version[0] == 3 else "27"
    executable = "python3.6" if version[0] == 3 else "python2.7"
    task, variant = { (3, 6, 15): ("psf__requests-1963", "function36-v3"),
                      (2, 7, 18): ("psf__requests-2674", "2674-v2"),
                      (2, 7, 13): ("psf__requests-863", "863-v7") }[version]
    runtime = RuntimeIdentity(base_image="example/requests@sha256:" + "a" * 64,
        base_image_id="sha256:" + "b" * 64, image_id="sha256:" + "c" * 64,
        interpreter="/opt/p07-python" + major + "/bin/" + executable, python_version=version, runner="pytest",
        profile_sha256="d" * 64, helper_sha256="e" * 64, inventory_sha256="f" * 64,
        provisioning_receipt_sha256="1" * 64, private_prefix="/opt/p07-python" + major,
        launcher="/opt/p07-runtime/python" + major, launcher_sha256="2" * 64)
    identity = PrivatePytestIdentity(task, variant, runtime.digest, version,
        tuple((path, "3" * 64) for path in ("_pytest/__init__.py", "pytest.py", "pytest_httpbin/__init__.py",
                                           "pytest_httpbin/plugin.py", "pytest_mock.py")), reviewed_helper_inventory(variant))
    spec = CheckSpec(task, "4" * 40, "5" * 40, "6" * 64, runtime.image_id, runtime.interpreter, "pytest",
                     ("tests/test_requests.py", "tests/test_utils.py"), runtime_identity=runtime)
    return spec, identity


def payload(spec, identity):
    source = private_pytest_args(spec, identity, NONCE)[1]
    tree = ast.parse(source, feature_version=(3, 6))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "payload" for target in node.targets))
    return json.loads(ast.literal_eval(assignment.value.args[0])), tree, source


def observation(spec, identity, *, failed=False, **changes):
    value, _, _ = payload(spec, identity)
    return {"nonce": NONCE, "root": spec.target_root, "runner": "pytest-private", "interpreter": spec.interpreter,
            "interpreter_resolved": spec.interpreter, "python_version": list(identity.python_version),
            "pytest_version": "2.8.7", "pytest_module": identity.site_packages + "pytest.py",
            "_pytest_module": identity.site_packages + "_pytest/__init__.py",
            "requests_module": spec.target_root + "/requests/__init__.py", "runtime_digest": spec.runtime_identity.digest,
            "check_spec_digest": spec.digest, "source_revision": spec.source_revision,
            "private_pytest_identity_digest": identity.digest, "launcher_sha256": spec.runtime_identity.launcher_sha256,
            "installed_inventory_sha256": value["installed_inventory_sha256"],
            "argv": [*identity.plugin_args, *spec.test_paths], "files": sorted(spec.test_paths),
            "collection_complete": True, "collection_errors": 0, "finished": True, "collected": 2, "completed": 2,
            "passed": 1 if failed else 2, "failed": int(failed), "skipped": 0, "call_failed": int(failed),
            "setup_failed": 0, "teardown_failed": 0, "xpassed": 0, "exit_code": int(failed),
            "fixture_lifecycle": "requires_separate_host_admission", **changes}


def result(value, **changes):
    return CommandResult(**({"returncode": value["exit_code"], "stdout": MARKER + json.dumps(value), "stderr": ""} | changes))


@pytest.mark.parametrize("version", [(3, 6, 15), (2, 7, 18), (2, 7, 13)])
def test_fixed_python27_compatible_payload_and_exact_explicit_plugins(version):
    spec, identity = bound(version)
    args = private_pytest_args(spec, identity, NONCE)
    assert args[0] == "-c" and len(args) == 2
    value, tree, source = payload(spec, identity)
    assert value["paths"] == list(spec.test_paths)
    assert value["identity"] == identity.as_dict()
    assert value["runtime"] == spec.runtime_identity.as_dict()
    assert value["check_spec_digest"] == spec.digest and value["source_revision"] == spec.source_revision
    assert value["identity"]["plugin_args"] == ["-p", "no:httpbin", "-p", "pytest_httpbin.plugin", "-p", "pytest_mock", "-rA", "-v"]
    assert not any(isinstance(node, (ast.AnnAssign, ast.JoinedStr, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    assert "PAYLOAD_JSON" not in source and "sys.argv" not in source
    assert "pytest.main(arguments,plugins=[evidence])" in source
    assert "entrypoints!=sorted(identity['entrypoints'].items())" in source
    assert source.index("protected()\nentries=0") < source.index("import pytest,_pytest,pkg_resources")
    assert source.index("import pytest,_pytest,pkg_resources") < source.index("sys.path.insert(0,root)")
    assert source.index("name.endswith(('.pyc','.pyo'))") < source.index("import requests")
    assert source.count("\nprotected()\n") == 2
    assert "warnings.filterwarnings" not in source and "subprocess" not in source
    assert "requests_module!=os.path.join(root,'requests','__init__.py')" in source


@pytest.mark.parametrize("failed", [False, True])
def test_complete_pass_and_complete_call_failure_are_observations_only(failed):
    spec, identity = bound()
    value = observation(spec, identity, failed=failed)
    assert private_pytest_receipt(result(value), spec, identity, NONCE) == value
    assert value["fixture_lifecycle"] == "requires_separate_host_admission"


@pytest.mark.parametrize("changes", [
    {"completed": 1}, {"collected": 0}, {"finished": False}, {"collection_complete": False},
    {"collection_errors": 1}, {"skipped": 1}, {"setup_failed": 1}, {"teardown_failed": 1}, {"xpassed": 1},
    {"passed": 0}, {"passed": True}, {"call_failed": 3, "failed": 3}, {"exit_code": 0}, {"exit_code": 2},
    {"files": ["tests/test_requests.py"]}, {"argv": ["-q", "-x", "tests/test_requests.py"]},
    {"pytest_module": "/testbed/pytest.py"}, {"requests_module": "/usr/lib/requests/__init__.py"},
    {"pytest_version": "8.0"}, {"nonce": "8" * 32}, {"runtime_digest": "8" * 64},
    {"source_revision": "8" * 64}, {"private_pytest_identity_digest": "8" * 64},
    {"installed_inventory_sha256": "8" * 64}, {"check_spec_digest": "8" * 64},
])
def test_partial_skipped_infrastructure_and_unbound_observations_never_accept(changes):
    spec, identity = bound()
    value = observation(spec, identity, failed=True)
    value.update(changes)
    assert private_pytest_receipt(result(value), spec, identity, NONCE) is None


@pytest.mark.parametrize("changes", [{"timed_out": True}, {"stdout_truncated": True}, {"stderr_truncated": True},
                                    {"stdout": MARKER + "{"}, {"stdout": "no receipt"}])
def test_incomplete_transport_has_no_completed_receipt(changes):
    spec, identity = bound()
    value = observation(spec, identity)
    assert private_pytest_receipt(result(value, **changes), spec, identity, NONCE) is None


def test_duplicate_receipts_cannot_accept():
    spec, identity = bound()
    value = observation(spec, identity)
    response = result(value)
    response.stdout += "\n" + response.stdout
    assert private_pytest_receipt(response, spec, identity, NONCE) is None


def test_runtime_task_digest_and_complete_plugin_inventory_are_required():
    spec, identity = bound()
    for changed_spec, changed_identity in (
        (replace(spec, task_id="psf__requests-2148"), identity),
        (spec, replace(identity, runtime_digest="8" * 64)),
        (spec, replace(identity, installed_files=tuple(pair for pair in identity.installed_files if pair[0] == "pytest.py" or pair[0] == "_pytest/__init__.py"))),
    ):
        with pytest.raises(ValueError):
            private_pytest_args(changed_spec, changed_identity, NONCE)
    for nonce in (None, "8" * 31, "G" * 32):
        with pytest.raises(ValueError):
            private_pytest_args(spec, identity, nonce)
    with pytest.raises(ValueError):
        private_pytest_args(spec, identity.as_dict(), NONCE)


def test_builder_does_not_enable_existing_execution_dispatch():
    from atomic_flow.agents.execution import ExecutionUnavailable
    from atomic_flow.check_execution import checker_args
    spec, identity = bound()
    assert private_pytest_args(spec, identity, NONCE)
    with pytest.raises(ExecutionUnavailable, match="not implemented"):
        checker_args(spec, NONCE)
