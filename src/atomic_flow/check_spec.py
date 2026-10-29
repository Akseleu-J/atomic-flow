"""Immutable host-owned target-test contract used by the admitted check executor.

Construct this only after host admission of the image, source and ordinary test
inventory. Validation binds a contract; it does not attest its provenance, run
tests, prove sandbox availability or establish independent requirement success.
There is deliberately no model-facing loader, shell, environment or mount API.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal

from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity
from atomic_flow.check_pytest_self import PytestSelfIdentity
from atomic_flow.check_runtime import RuntimeIdentity

_IMAGE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-f]{64}")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,511}")
_INTERPRETERS = frozenset({"/opt/miniconda3/envs/testbed/bin/python", "/usr/local/bin/python", "/usr/bin/python3"})
SYMPY_RUNNER_PATHS = ("bin/get_sympy.py", "bin/test", "sympy/utilities/runtests.py")


def _integer(name: str, value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between1 and{maximum}")


def _digest(name: str, value: str, size: int) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is None:
        raise ValueError(f"{name} must be an exact lowercase SHA digest")


def _test_path(value: str) -> None:
    if (type(value) is not str or _PATH.fullmatch(value) is None or not value.endswith(".py")
            or any(part in {"", ".", ".."} or part.startswith((".", "-")) for part in value.split("/"))):
        raise ValueError("test paths must be confined ordinary relative Python files")


@dataclass(frozen=True, slots=True)
class CheckResources:
    """At most the existing checks profile; independent oracle bounds differ."""

    memory_bytes: int = 1024**3
    cpus: int = 2
    pids: int = 128
    tmpfs_bytes: int = 128 * 1024**2
    output_bytes_per_stream: int = 16 * 1024

    def __post_init__(self) -> None:
        for name, maximum in (("memory_bytes", 1024**3), ("cpus", 2), ("pids", 128),
                              ("tmpfs_bytes", 128 * 1024**2), ("output_bytes_per_stream", 1_000_000)):
            _integer(name, getattr(self, name), maximum)
        if self.output_bytes_per_stream < 1024:
            raise ValueError("output bound must be at least1024 bytes")

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class SymPyRunnerIdentity:
    """Host-admitted native runner bytes and exact runtime, not an attestation."""

    python_version: tuple[int, int, int]
    sympy_version: str
    source_sha256: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        version = self.python_version
        if (type(version) is not tuple or len(version) != 3 or any(type(part) is not int or part < 0 for part in version)
                or (version != (3, 6, 15) and (version[0] != 3 or version[1] < 8 or version[1] > 99 or version[2] > 999))):
            raise ValueError("native SymPy requires an exact Python3.8+ or privately bound Python3.6.15 version")
        if (type(self.sympy_version) is not str or not 1 <= len(self.sympy_version) <= 128
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", self.sympy_version) is None):
            raise ValueError("native SymPy requires an exact bounded package version")
        if (type(self.source_sha256) is not tuple or len(self.source_sha256) != len(SYMPY_RUNNER_PATHS)
                or any(type(item) is not tuple or len(item) != 2 for item in self.source_sha256)
                or tuple(path for path, _ in self.source_sha256) != SYMPY_RUNNER_PATHS):
            raise ValueError("native SymPy requires the complete sorted runner byte inventory")
        for _, digest in self.source_sha256:
            _digest("native runner source", digest, 64)

    def as_dict(self) -> dict:
        return {"python_version": list(self.python_version), "sympy_version": self.sympy_version,
                "source_sha256": dict(self.source_sha256)}


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """A host-admitted exact runner/image/source obligation, never a model command.

    task_commit identifies the frozen task definition; base_commit identifies
    target source ancestry. source_revision identifies the actual edited snapshot.
    test_paths are the complete host-declared ordinary-test obligation. The
    executor must enforce their inventory and source/image import identities.
    """

    task_id: str
    task_commit: str
    base_commit: str
    source_revision: str
    image: str
    interpreter: str
    runner: Literal["pytest", "sympy-native", "pytest-self"]
    test_paths: tuple[str, ...]
    target_root: Literal["/workspace", "/testbed"] = "/workspace"
    timeout_seconds: int = 180
    resources: CheckResources = field(default_factory=CheckResources)
    sympy_identity: SymPyRunnerIdentity | None = None
    pytest_self_identity: PytestSelfIdentity | None = None
    runtime_identity: RuntimeIdentity | None = None
    private_pytest_identity: PrivatePytestIdentity | None = None
    fixture_identity: FixtureIdentity | None = None

    def __post_init__(self) -> None:
        if type(self.task_id) is not str or _TASK.fullmatch(self.task_id) is None:
            raise ValueError("task identity must be a bounded plain identifier")
        _digest("task_commit", self.task_commit, 40)
        _digest("base_commit", self.base_commit, 40)
        _digest("source_revision", self.source_revision, 64)
        if self.runtime_identity is not None:
            if type(self.runtime_identity) is not RuntimeIdentity:
                raise ValueError("runtime identity must be the immutable host contract")
            self.runtime_identity.require_execution_eligible()
            if (type(self.image) is not str or type(self.interpreter) is not str
                    or self.image != self.runtime_identity.image_id or self.interpreter != self.runtime_identity.interpreter
                    or self.runner != self.runtime_identity.runner):
                raise ValueError("image, interpreter and runner must match the exact private runtime identity")
        else:
            if type(self.image) is not str or len(self.image) > 512 or _IMAGE.fullmatch(self.image) is None:
                raise ValueError("image must be an exact image reference at sha256 digest")
            if type(self.interpreter) is not str or self.interpreter not in _INTERPRETERS:
                raise ValueError("interpreter must be an admitted absolute image path")
        if type(self.runner) is not str or self.runner not in {"pytest", "sympy-native", "pytest-self"}:
            raise ValueError("unreviewed test runner")
        if type(self.target_root) is not str or self.target_root not in {"/workspace", "/testbed"}:
            raise ValueError("unreviewed target root")
        if type(self.test_paths) is not tuple or not 1 <= len(self.test_paths) <= 256:
            raise ValueError("host test obligation must be an immutable bounded tuple")
        for path in self.test_paths:
            _test_path(path)
        if len(set(self.test_paths)) != len(self.test_paths):
            raise ValueError("duplicate test obligations are not admitted")
        if self.runner == "sympy-native" and any(not path.startswith("sympy/") for path in self.test_paths):
            raise ValueError("native SymPy obligations must be under sympy/")
        if self.sympy_identity is not None and (self.runner != "sympy-native" or type(self.sympy_identity) is not SymPyRunnerIdentity):
            raise ValueError("native SymPy identity belongs only to a native runner specification")
        if self.pytest_self_identity is not None and (self.runner != "pytest-self" or type(self.pytest_self_identity) is not PytestSelfIdentity):
            raise ValueError("pytest self identity belongs only to a pytest self runner specification")
        if self.sympy_identity is not None:
            if self.sympy_identity.python_version == (3, 6, 15) and self.runtime_identity is None:
                raise ValueError("Python3.6.15 native SymPy requires an eligible private runtime identity")
            if self.runtime_identity is not None and self.sympy_identity.python_version != self.runtime_identity.python_version:
                raise ValueError("native SymPy Python version differs from the private runtime identity")
        if (self.pytest_self_identity is not None and self.runtime_identity is not None
                and self.pytest_self_identity.python_version != self.runtime_identity.python_version):
            raise ValueError("pytest self Python version differs from the private runtime identity")
        if self.private_pytest_identity is not None:
            identity = self.private_pytest_identity
            runtime = self.runtime_identity
            if (type(identity) is not PrivatePytestIdentity or self.runner != "pytest"
                    or runtime is None or identity.task_id != self.task_id
                    or identity.runtime_digest != runtime.digest or identity.python_version != runtime.python_version):
                raise ValueError("private pytest identity differs from its exact task/runtime/runner")
            if not {node.split("::", 1)[0] for node, _ in identity.optional_test_outcomes} <= set(self.test_paths):
                raise ValueError("declared optional outcomes require their complete ordinary test files")
        if self.fixture_identity is not None:
            fixture = self.fixture_identity
            identity = self.private_pytest_identity
            runtime = self.runtime_identity
            if (type(fixture) is not FixtureIdentity or identity is None or runtime is None
                    or fixture.task_id != self.task_id or fixture.variant != identity.variant
                    or fixture.runtime_digest != runtime.digest or fixture.image_id != runtime.image_id):
                raise ValueError("fixture identity differs from its exact private task/runtime")
        _integer("timeout_seconds", self.timeout_seconds, 180)
        if type(self.resources) is not CheckResources:
            raise ValueError("resource bounds must use the immutable host contract")

    @property
    def argv(self) -> tuple[str, ...]:
        """Exact non-shell argv; native execution additionally requires host runner identity."""
        if self.runner == "pytest":
            if self.private_pytest_identity is not None:
                return (self.interpreter, "-m", "pytest", *self.private_pytest_identity.plugin_args, *self.test_paths)
            return (self.interpreter, "-m", "pytest", "-q", "--no-header", "-x", *self.test_paths)
        if self.runner == "pytest-self":
            profile = ("--tb=short",) if self.pytest_self_identity is not None and self.pytest_self_identity.traceback == "short" else ()
            return (self.interpreter, "-m", "pytest", "-rA", *profile, *self.test_paths)
        return (self.interpreter, "bin/test", "-C", "--verbose", *self.test_paths)

    def as_dict(self) -> dict:
        value = {"schema_version": 1, "kind": "host_target_test_spec", "task_id": self.task_id,
                "task_commit": self.task_commit, "base_commit": self.base_commit,
                "source_revision": self.source_revision, "image": self.image,
                "interpreter": self.interpreter, "runner": self.runner, "test_paths": list(self.test_paths),
                "target_root": self.target_root, "timeout_seconds": self.timeout_seconds,
                "resources": self.resources.as_dict(), "argv": list(self.argv),
                "controls": {"network": "none", "root_read_only": True, "cap_drop": ["ALL"],
                             "no_new_privileges": True, "non_root": True, "automatic_pull": False,
                             "provider_environment": False}}
        if self.sympy_identity is not None:
            value["sympy_identity"] = self.sympy_identity.as_dict()
        if self.pytest_self_identity is not None:
            value["pytest_self_identity"] = self.pytest_self_identity.as_dict()
        if self.runtime_identity is not None:
            value["runtime_identity"] = self.runtime_identity.as_dict()
        if self.private_pytest_identity is not None:
            value["private_pytest_identity"] = self.private_pytest_identity.as_dict()
        if self.fixture_identity is not None:
            value["fixture_identity"] = self.fixture_identity.as_dict()
        return value

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
