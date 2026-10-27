"""Host check contracts bind exact capabilities without admitting commands."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow.check_spec import CheckResources, CheckSpec


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
