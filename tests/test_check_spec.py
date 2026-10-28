"""Host check contracts bind exact capabilities without admitting commands."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckResources, CheckSpec, SymPyRunnerIdentity


def spec(**changes):
    values = {"task_id": "pytest-dev__pytest-6116", "task_commit": "a" * 40, "base_commit": "b" * 40,
              "source_revision": "c" * 64, "image": "swebench/task@sha256:" + "d" * 64,
              "interpreter": "/opt/miniconda3/envs/testbed/bin/python", "runner": "pytest",
              "test_paths": ("testing/test_example.py",)}
    return CheckSpec(**(values | changes))


def test_exact_argv_canonical_hash_and_fixed_security_controls():
    check = spec()
    assert check.argv == (check.interpreter, "-m", "pytest", "-q", "--no-header", "-x", "testing/test_example.py")
    assert check.digest == hashlib.sha256(check.canonical_bytes()).hexdigest()
    assert json.loads(check.canonical_bytes()) == check.as_dict()
    assert check.digest == spec().digest
    assert check.as_dict()["controls"] == {"network": "none", "root_read_only": True, "cap_drop": ["ALL"],
        "no_new_privileges": True, "non_root": True, "automatic_pull": False, "provider_environment": False}
    exported = check.as_dict()
    exported["test_paths"].append("other.py")
    exported["resources"]["cpus"] = 999
    assert check.test_paths == ("testing/test_example.py",) and check.resources.cpus == 2


def test_native_sympy_is_explicit_and_never_disguised_as_pytest():
    check = spec(runner="sympy-native", test_paths=("sympy/core/tests/test_kind.py",), target_root="/testbed")
    assert check.argv == (check.interpreter, "bin/test", "-C", "--verbose", "sympy/core/tests/test_kind.py")
    assert check.as_dict()["runner"] == "sympy-native"
    with pytest.raises(ValueError, match="under sympy"):
        spec(runner="sympy-native")


def test_spec_and_nested_resources_are_immutable():
    check = spec()
    with pytest.raises(FrozenInstanceError):
        check.image = "mutable:latest"
    with pytest.raises(FrozenInstanceError):
        check.resources.memory_bytes = 6 * 1024**3
    with pytest.raises((TypeError, AttributeError)):
        check.extra_mount = "/private"


@pytest.mark.parametrize("changes", [
    {"task_commit": "a" * 39}, {"base_commit": "B" * 40}, {"source_revision": "c" * 63},
    {"source_revision": "../" + "c" * 64}, {"task_id": "task; curl remote"},
    {"image": "swebench/task:latest"}, {"image": "sha256:" + "d" * 64},
    {"image": "swebench/task@sha256:" + "D" * 64}, {"image": "user:password@registry/task@sha256:" + "d" * 64},
    {"image": "swebench/task@sha256:" + "d" * 64 + "\n--privileged"},
    {"interpreter": "python"}, {"interpreter": "/bin/sh"}, {"interpreter": "/opt/../bin/python"},
    {"runner": "shell"}, {"target_root": "/"}, {"target_root": "/testbed/../private"},
    {"test_paths": []}, {"test_paths": ["tests/test.py"]}, {"test_paths": ()},
    {"test_paths": ("tests/test.py",) * 2}, {"test_paths": tuple(f"tests/test_{i}.py" for i in range(257))},
    {"timeout_seconds": 181}, {"timeout_seconds": 0}, {"timeout_seconds": True},
    {"resources": {"memory_bytes": 1024**3}},
])
def test_untrusted_identity_command_and_capability_changes_are_refused(changes):
    with pytest.raises(ValueError):
        spec(**changes)


@pytest.mark.parametrize("path", ["../test.py", "/etc/test.py", "tests/../test.py", "tests//test.py",
    "tests/./test.py", "tests\\test.py", "C:/test.py", "tests/test.py::case", "-c.py", "tests/-option.py",
    "tests/test.py;touch owned", "tests/$(id).py", "tests/test.py\n--rootdir=/", ".private/test.py",
    "tests/.private/test.py", "tests/test.txt", "tests/*test.py"])
def test_test_targets_cannot_escape_become_flags_or_shell_fragments(path):
    with pytest.raises(ValueError):
        spec(test_paths=(path,))


@pytest.mark.parametrize("changes", [{"memory_bytes": 1024**3 + 1}, {"cpus": 3}, {"pids": 129},
    {"tmpfs_bytes": 128 * 1024**2 + 1}, {"output_bytes_per_stream": 1_000_001},
    {"output_bytes_per_stream": 1023}, {"cpus": True}, {"memory_bytes": 1.0}, {"pids": 0}])
def test_resources_cannot_exceed_live_checks_profile(changes):
    with pytest.raises(ValueError):
        CheckResources(**changes)


def test_no_environment_network_mount_or_arbitrary_argument_extension_exists():
    for extra in ({"env": {"KEY": "secret"}}, {"network": "host"}, {"mounts": ["/"]},
                  {"argv": ("sh", "-c", "true")}, {"shell": True}):
        with pytest.raises(TypeError):
            spec(**extra)


def test_every_source_runtime_and_obligation_change_changes_contract_digest():
    original = spec()
    for changed in (replace(original, source_revision="e" * 64), replace(original, base_commit="e" * 40),
                    replace(original, task_commit="e" * 40), replace(original, image="registry:5000/task@sha256:" + "e" * 64),
                    replace(original, timeout_seconds=120), replace(original, resources=CheckResources(cpus=1)),
                    replace(original, test_paths=("testing/test_other.py",)), replace(original, target_root="/testbed")):
        assert changed.digest != original.digest


def private_identity(**changes):
    values = dict(base_image="swebench/task@sha256:" + "d" * 64,
                  base_image_id="sha256:" + "e" * 64, image_id="sha256:" + "f" * 64,
                  interpreter="/opt/p07-python36/bin/python3.6", python_version=(3, 6, 15),
                  runner="pytest", profile_sha256="1" * 64, helper_sha256="2" * 64,
                  inventory_sha256="3" * 64, provisioning_receipt_sha256="4" * 64,
                  private_prefix="/opt/p07-python36", launcher="/opt/p07-runtime/python36",
                  launcher_sha256="5" * 64)
    return RuntimeIdentity(**(values | changes))


def private_spec(identity=None, **changes):
    identity = private_identity() if identity is None else identity
    return spec(**({"runtime_identity": identity, "image": identity.image_id,
                   "interpreter": identity.interpreter, "runner": identity.runner} | changes))


def native_identity(version=(3, 6, 15)):
    return SymPyRunnerIdentity(version, "1.4", tuple((path, "a" * 64) for path in SYMPY_RUNNER_PATHS))


def test_optional_runtime_identity_preserves_exact_legacy_serialization_and_digest():
    expected = {"schema_version": 1, "kind": "host_target_test_spec", "task_id": "pytest-dev__pytest-6116",
                "task_commit": "a" * 40, "base_commit": "b" * 40, "source_revision": "c" * 64,
                "image": "swebench/task@sha256:" + "d" * 64,
                "interpreter": "/opt/miniconda3/envs/testbed/bin/python", "runner": "pytest",
                "test_paths": ["testing/test_example.py"], "target_root": "/workspace", "timeout_seconds": 180,
                "resources": {"memory_bytes": 1024**3, "cpus": 2, "pids": 128,
                              "tmpfs_bytes": 128 * 1024**2, "output_bytes_per_stream": 16 * 1024},
                "argv": ["/opt/miniconda3/envs/testbed/bin/python", "-m", "pytest", "-q", "--no-header", "-x", "testing/test_example.py"],
                "controls": {"network": "none", "root_read_only": True, "cap_drop": ["ALL"],
                             "no_new_privileges": True, "non_root": True, "automatic_pull": False, "provider_environment": False}}
    raw = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    assert spec().as_dict() == expected
    assert spec(runtime_identity=None).canonical_bytes() == raw
    assert spec().digest == hashlib.sha256(raw).hexdigest()


def test_private_runtime_binds_local_image_interpreter_runner_and_evidence_bytes():
    identity = private_identity()
    check = private_spec(identity)
    assert check.as_dict()["runtime_identity"] == identity.as_dict()
    assert check.image == identity.image_id
    assert check.argv == (identity.interpreter, "-m", "pytest", "-q", "--no-header", "-x", "testing/test_example.py")
    assert "launcher" not in check.argv
    for field in ("profile_sha256", "helper_sha256", "inventory_sha256", "provisioning_receipt_sha256", "launcher_sha256"):
        changed = private_identity(**{field: "9" * 64})
        assert private_spec(changed).digest != check.digest
    with pytest.raises(FrozenInstanceError):
        check.runtime_identity.image_id = "sha256:" + "0" * 64


@pytest.mark.parametrize("changes", [
    {"image": "swebench/task@sha256:" + "d" * 64}, {"image": "sha256:" + "e" * 64},
    {"image": "sha256:" + "0" * 64}, {"interpreter": "/usr/local/bin/python"},
    {"interpreter": "/opt/p07-runtime/python36"}, {"runner": "pytest-self"},
    {"runtime_identity": {}}, {"runtime_identity": "f" * 64},
])
def test_private_identity_cannot_be_forged_or_substituted(changes):
    with pytest.raises(ValueError):
        private_spec(**changes)


@pytest.mark.parametrize("changes", [{"launcher_sha256": None}, {"image_id": "sha256:" + "e" * 64}])
def test_metadata_only_identity_cannot_authorize_private_checkspec(changes):
    identity = private_identity(**changes)
    assert identity.execution_eligible is False
    with pytest.raises(ValueError, match="private execution"):
        private_spec(identity)


def test_private_identity_requires_exact_type_not_subclass_override():
    class ForgedRuntime(RuntimeIdentity):
        def require_execution_eligible(self):
            return None
    original = private_identity(launcher_sha256=None)
    forged = ForgedRuntime(**{name: getattr(original, name) for name in original.__dataclass_fields__})
    with pytest.raises(ValueError, match="immutable host contract"):
        private_spec(forged)


def test_sympy_python36_identity_is_data_until_matching_private_runtime_is_bound():
    native = native_identity()
    with pytest.raises(ValueError, match="eligible private runtime"):
        spec(runner="sympy-native", test_paths=("sympy/core/tests/test_value.py",), sympy_identity=native)
    runtime = private_identity(runner="sympy-native")
    check = private_spec(runtime, test_paths=("sympy/core/tests/test_value.py",), sympy_identity=native)
    assert check.argv == (runtime.interpreter, "bin/test", "-C", "--verbose", "sympy/core/tests/test_value.py")
    assert check.sympy_identity.python_version == check.runtime_identity.python_version


def test_private_native_runner_cannot_claim_another_python_version():
    runtime = private_identity(runner="sympy-native")
    with pytest.raises(ValueError, match="Python version differs"):
        private_spec(runtime, test_paths=("sympy/core/tests/test_value.py",), sympy_identity=native_identity((3, 9, 20)))


@pytest.mark.parametrize("version", [(3, 6, 14), (3, 7, 17), (2, 7, 18)])
def test_no_other_legacy_native_python_version_is_enabled(version):
    with pytest.raises(ValueError, match="exact Python"):
        native_identity(version)


@pytest.mark.parametrize("changes", [{"target_root": "/private"}, {"test_paths": ("../test.py",)},
                                    {"resources": {"cpus": 4}}, {"timeout_seconds": 181}])
def test_private_runtime_does_not_expand_check_capabilities(changes):
    with pytest.raises(ValueError):
        private_spec(**changes)
