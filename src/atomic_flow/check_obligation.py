"""Seal host ordinary-test obligations and derive fresh, bounded CheckSpecs.

This factory neither approves edits nor proves image/source ancestry. Callers must
supply an admitted private snapshot and environment evidence. The source lease
coordinates atomic-flow writers; an unrelated external writer remains detectable
only through the before/after snapshot checks, not an OS snapshot guarantee.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from atomic_flow.agents.source import confined_path
from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.agents.verify import check_source_snapshot
from atomic_flow.check_spec import CheckResources, CheckSpec
from atomic_flow.source_lock import source_mutation_lease
from atomic_flow.tree.defaults import MAX_FILE_BYTES

_CONFIG_NAMES = ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "setup.py")
_PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,511}")


class CheckObligationError(ValueError):
    """A host obligation cannot be sealed or safely rebound to this snapshot."""


def _relative(value: str) -> str:
    if (type(value) is not str or _PATH.fullmatch(value) is None
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise CheckObligationError("protected paths must be confined plain relative files")
    return value


def _root(workspace: Path) -> Path:
    root = Path(workspace).absolute()
    for candidate in (root, *root.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise CheckObligationError("obligation workspace contains a link")
    if not root.is_dir():
        raise CheckObligationError("obligation requires an admitted workspace directory")
    return root


def _finite(name: str, value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise CheckObligationError(name + " must be a finite positive host timestamp")


def _sha256(root: Path, relative: str, check: Callable[[], None]) -> str:
    path = confined_path(root, relative)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise CheckObligationError("protected file is missing or oversized: " + relative)
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while True:
            check()
            block = stream.read(64 * 1024)
            if not block:
                break
            count += len(block)
            if count > MAX_FILE_BYTES:
                raise CheckObligationError("protected file exceeds byte bound: " + relative)
            digest.update(block)
    return digest.hexdigest()


def _test_directories(paths: tuple[str, ...]) -> tuple[str, ...]:
    directories = set()
    for relative in paths:
        parts = PurePosixPath(relative).parts
        indexes = [index for index, part in enumerate(parts[:-1]) if part in {"tests", "testing", "test"}]
        if indexes:
            directories.add("/".join(parts[:indexes[0] + 1]))
        elif len(parts) > 1:
            directories.add("/".join(parts[:-1]))
    return tuple(sorted(directories))


def _config_paths(paths: tuple[str, ...]) -> set[str]:
    output = set(_CONFIG_NAMES)
    for relative in paths:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            output.update((parent / name).as_posix() for name in _CONFIG_NAMES)
            parent = parent.parent
    return output


def _directory_inventory(snapshot: dict[str, str], directories: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(path for path in snapshot if any(path.startswith(directory + "/") for directory in directories)))


@dataclass(frozen=True, slots=True)
class CheckObligation:
    """Immutable obligation; constructing one directly is not host admission.

    template preserves the complete original test inventory, environment and
    ceilings. protected_files are SHA256 byte identities; absent_paths prevent
    later insertion of checker/configuration files. Directory inventory follows
    the same admitted source filter as CheckSpec revisions. Ignored/generated
    files are not attested by this object. Environment evidence is a host binding,
    never an image attestation. Repair allowances stay owned by the task scheduler.
    """

    template: CheckSpec
    environment_digest: str
    deadline_at: float
    protected_files: tuple[tuple[str, str], ...]
    absent_paths: tuple[str, ...]
    test_directories: tuple[str, ...]
    directory_inventory: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.template) is not CheckSpec:
            raise CheckObligationError("immutable CheckSpec template required")
        if type(self.environment_digest) is not str or re.fullmatch(r"[0-9a-f]{64}", self.environment_digest) is None:
            raise CheckObligationError("environment admission digest must be SHA256")
        _finite("deadline", self.deadline_at)
        for value in (self.protected_files, self.absent_paths, self.test_directories, self.directory_inventory):
            if type(value) is not tuple or len(value) > 100_000:
                raise CheckObligationError("immutable bounded obligation inventory required")
        paths = []
        for item in self.protected_files:
            if type(item) is not tuple or len(item) != 2:
                raise CheckObligationError("immutable protected path/digest pairs required")
            path, digest = item
            paths.append(_relative(path))
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise CheckObligationError("protected byte identity must be SHA256")
        for group in (self.absent_paths, self.test_directories, self.directory_inventory):
            if list(group) != sorted(set(group)):
                raise CheckObligationError("obligation inventory must be sorted and unique")
            for path in group:
                _relative(path)
        if paths != sorted(set(paths)) or set(paths) & set(self.absent_paths):
            raise CheckObligationError("protected inventory must be unique and disjoint")
        if not set(self.template.test_paths) <= set(paths) or not set(self.directory_inventory) <= set(paths):
            raise CheckObligationError("all test and test-directory files must be protected")
        if self.test_directories != _test_directories(self.template.test_paths):
            raise CheckObligationError("test-directory scope differs from complete test obligation")
        if not _config_paths(self.template.test_paths) <= set(paths) | set(self.absent_paths):
            raise CheckObligationError("ancestor checker configuration inventory is incomplete")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "kind": "host_ordinary_test_obligation", "template": self.template.as_dict(),
                "environment_digest": self.environment_digest, "deadline_at": self.deadline_at,
                "protected_files": [list(item) for item in self.protected_files], "absent_paths": list(self.absent_paths),
                "test_directories": list(self.test_directories), "directory_inventory": list(self.directory_inventory)}

    @property
    def digest(self) -> str:
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def seal(cls, spec: CheckSpec, workspace: Path, *, environment_digest: str, deadline_at: float,
             protected_paths: tuple[str, ...] = (), now: Callable[[], float] = time.time,
             cancel_check: Callable[[], None] | None = None) -> CheckObligation:
        """Seal only a fresh host-admitted spec before model execution begins."""
        if type(spec) is not CheckSpec or type(protected_paths) is not tuple:
            raise CheckObligationError("immutable spec and protected paths required")
        _finite("deadline", deadline_at)
        current = now()
        _finite("current time", current)
        if deadline_at - current < 1:
            raise CheckObligationError("task verification deadline is exhausted")
        check = cancel_check or (lambda: None)
        check()
        root = _root(workspace)
        for path in protected_paths:
            _relative(path)
        with source_mutation_lease(root):
            before = check_source_snapshot(root, check)
            if manifest_digest(before) != spec.source_revision:
                raise CheckObligationError("cannot seal a stale source specification")
            directories = _test_directories(spec.test_paths)
            inventory = _directory_inventory(before, directories)
            configs = _config_paths(spec.test_paths)
            requested = set(spec.test_paths) | set(protected_paths) | set(inventory)
            missing = requested - before.keys()
            if missing:
                raise CheckObligationError("protected file is absent from admitted source inventory: " + sorted(missing)[0])
            present = requested | (configs & before.keys())
            absent = configs - before.keys()
            # A hidden or ignored path at a guarded configuration name must not
            # become an apparent absent-file obligation.
            for relative in absent:
                check()
                if confined_path(root, relative).exists():
                    raise CheckObligationError("checker configuration was not admitted in source inventory")
            protected = tuple((path, _sha256(root, path, check)) for path in sorted(present))
            if check_source_snapshot(root, check) != before:
                raise CheckObligationError("source changed while sealing ordinary tests")
            check()
            return cls(spec, environment_digest, deadline_at, protected, tuple(sorted(absent)), directories, inventory)

    def derive(self, workspace: Path, *, now: Callable[[], float] = time.time,
               resources: CheckResources | None = None,
               cancel_check: Callable[[], None] | None = None) -> CheckSpec:
        """Rebind source only; test inventory, environment and global deadline stay fixed.

        This grants no edit/scope authority. The caller must first admit the
        snapshot via its existing changeset and task envelope machinery. Final
        verification always retains every declared test path; there is no subset
        or deadline reset argument. A narrower resource profile cannot grow back
        beyond the original immutable ceiling.
        """
        check = cancel_check or (lambda: None)
        check()
        current = now()
        _finite("current time", current)
        remaining = math.floor(self.deadline_at - current)
        if remaining < 1:
            raise CheckObligationError("task verification deadline is exhausted")
        selected = self.template.resources if resources is None else resources
        if type(selected) is not CheckResources or any(
                getattr(selected, name) > getattr(self.template.resources, name) for name in selected.__dataclass_fields__):
            raise CheckObligationError("check resource ceiling cannot be expanded")
        root = _root(workspace)
        with source_mutation_lease(root):
            before = check_source_snapshot(root, check)
            if _directory_inventory(before, self.test_directories) != self.directory_inventory:
                raise CheckObligationError("ordinary-test directory inventory changed")
            for relative in self.absent_paths:
                check()
                if confined_path(root, relative).exists():
                    raise CheckObligationError("checker configuration was inserted after admission")
            for relative, expected in self.protected_files:
                check()
                if relative not in before or _sha256(root, relative, check) != expected:
                    raise CheckObligationError("protected ordinary-test or checker bytes changed: " + relative)
            if check_source_snapshot(root, check) != before:
                raise CheckObligationError("source changed while deriving a fresh check specification")
            check()
            finished = now()
            _finite("current time", finished)
            remaining = min(remaining, math.floor(self.deadline_at - finished))
            if remaining < 1:
                raise CheckObligationError("task verification deadline expired during admission")
            return replace(self.template, source_revision=manifest_digest(before),
                           timeout_seconds=min(self.template.timeout_seconds, remaining), resources=selected)


def obligation_binding(obligation: CheckObligation | None) -> dict | None:
    """Persist only the host contract identity, never a deserializable authority."""
    if obligation is None:
        return None
    if type(obligation) is not CheckObligation:
        raise CheckObligationError("host-owned CheckObligation required")
    return {"digest": obligation.digest, "environment_digest": obligation.environment_digest,
            "deadline_at": obligation.deadline_at}


def validate_obligation_binding(value: dict) -> None:
    if (type(value) is not dict or set(value) != {"digest", "environment_digest", "deadline_at"}
            or any(type(value[name]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
                   for name in ("digest", "environment_digest"))):
        raise CheckObligationError("invalid persisted ordinary-test obligation binding")
    _finite("deadline", value["deadline_at"])


def require_obligation_binding(approval: dict, obligation: CheckObligation | None) -> None:
    expected = approval.get("check_obligation")
    if expected is not None:
        validate_obligation_binding(expected)
    if expected != obligation_binding(obligation):
        raise CheckObligationError("ordinary-test obligation is missing or changed; fresh review required")


def run_obligation_check(obligation: CheckObligation, workspace: Path, *, cancel_check=None):
    """Derive and execute one complete host check using the admitted Docker path."""
    from atomic_flow.check_execution import run_check_spec

    if type(obligation) is not CheckObligation:
        raise CheckObligationError("host-owned CheckObligation required")
    spec = obligation.derive(workspace, cancel_check=cancel_check)
    return spec, run_check_spec(spec, workspace, cancel_check=cancel_check)


def check_observation(obligation: CheckObligation, spec: CheckSpec, receipt) -> dict:
    """Host result fields whose source and immutable contract can be checked later."""
    return {"check_obligation_digest": obligation.digest, "check_spec_digest": spec.digest,
            "check_spec": spec.as_dict(), "check_passed": receipt.check_passed,
            "checked_source_revision": spec.source_revision}


def observation_matches(obligation: CheckObligation, observation: dict, revision: str) -> bool:
    """Validate a trusted tool receipt's complete contract, not model prose.

    This is an integrity check over host tool observations. It is not a signature
    or attestation against someone who can rewrite host task/journal state.
    """
    if (type(obligation) is not CheckObligation or not isinstance(observation, dict)
            or observation.get("check_obligation_digest") != obligation.digest
            or observation.get("checked_source_revision") != revision
            or observation.get("check_passed") is not True):
        return False
    value = observation.get("check_spec")
    if not isinstance(value, dict):
        return False
    try:
        selected = CheckResources(**value["resources"])
        if any(getattr(selected, name) > getattr(obligation.template.resources, name)
               for name in selected.__dataclass_fields__):
            return False
        candidate = replace(obligation.template, source_revision=revision,
                            timeout_seconds=value["timeout_seconds"], resources=selected)
        return (candidate.timeout_seconds <= obligation.template.timeout_seconds
                and candidate.as_dict() == value and candidate.digest == observation.get("check_spec_digest"))
    except (KeyError, TypeError, ValueError):
        return False
