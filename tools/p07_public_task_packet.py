"""Pure public-base task packet projection; no corpus gold, filesystem or execution access.

The host supplies the complete admitted base-file SHA256 inventory, after checking
regular-file/link identities against immutable Git objects. This pure helper
cannot attest the filesystem or infer whether an inventory was gold-selected.
Never derive the inventory or protected_paths from hidden patches or solutions.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from types import MappingProxyType

PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")
SELECTION_POLICY_VERSION = "p07-public-python-production-v1"
MAX_FILES = 10000
MAX_PROBLEM_BYTES = 131072
MAX_PACKET_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"[a-f0-9]{64}")
_SHA40 = re.compile(r"[a-f0-9]{40}")
_PATH = re.compile(r"[A-Za-z0-9_.-][A-Za-z0-9_./-]{0,511}")
REPOSITORY_FAMILIES = MappingProxyType({
    "psf/requests": ("psf__requests-", ("requests/",), ()),
    "pytest-dev/pytest": ("pytest-dev__pytest-", ("_pytest/", "src/_pytest/", "src/pytest/"),
                          ("pytest.py", "src/pytest.py")),
    "pylint-dev/pylint": ("pylint-dev__pylint-", ("pylint/",), ()),
    "sphinx-doc/sphinx": ("sphinx-doc__sphinx-", ("sphinx/",), ()),
    "sympy/sympy": ("sympy__sympy-", ("sympy/",), ()),
})
_EXCLUDED_PARTS = frozenset({
    "test", "tests", "testing", "testdata", "test_data", "fixtures", "conftest",
    "benchmarks", "benchmark", "examples", "docs", "doc", "scripts", "bin",
    "build", "dist", "vendor", "vendored", "__pycache__", "site-packages",
})
_CHECKER_NAMES = frozenset({
    "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
    "setup.py", "noxfile.py", "tasks.py", "manage.py", "conf.py",
})


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def packet_sha256(packet: dict) -> str:
    """Hash exact canonical packet content; no self-referential digest field."""
    return hashlib.sha256(canonical_bytes(packet)).hexdigest()


def _path(value: str) -> str:
    if (type(value) is not str or _PATH.fullmatch(value) is None
            or any(part in {"", ".", ".."} or part.startswith("-") for part in value.split("/"))):
        raise ValueError("admitted inventory requires confined plain POSIX relative file paths")
    return value


def _sha(value, pattern, name):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(name + " requires an exact lowercase digest")
    return value


def _production_path(path: str, roots: tuple[str, ...], entrypoints: tuple[str, ...]) -> bool:
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    if (not path.endswith((".py", ".pyi")) or any(part.startswith(".") for part in parts)
            or any(part in _EXCLUDED_PARTS for part in parts)
            or name in _CHECKER_NAMES or name.startswith(("test_", "conftest"))
            or name.endswith(("_test.py", "_test.pyi", "_tests.py", "_tests.pyi"))):
        return False
    return path in entrypoints or any(path.startswith(root) for root in roots)


def prepare_public_task_packet(raw_task: Mapping, source_manifest: Mapping[str, str], *,
                               framework_sha: str, workflow_sha: str,
                               source_profile_sha256: str,
                               protected_paths: tuple[str, ...] = ()) -> dict:
    """Project only four public task fields; forbidden values are never inspected.

    Input can be a full private corpus row: patch/test_patch/requirements/hints and
    extra fields are intentionally not read or copied. Host admission must ensure
    the four public fields came from the public dataset, and the manifest is the
    complete public base inventory rather than gold-selected paths. A fresh packet
    carries production scope only; it grants no approval, runner or model authority.
    """
    if not isinstance(raw_task, Mapping):
        raise ValueError("public task mapping required")
    try:
        public = {name: raw_task[name] for name in PUBLIC_FIELDS}
    except KeyError:
        raise ValueError("all four public task fields are required") from None
    if any(type(value) is not str for value in public.values()):
        raise ValueError("public task fields must be plain strings")
    repo = public["repo"]
    if repo not in REPOSITORY_FAMILIES:
        raise ValueError("repository family is not registered")
    prefix, roots, entrypoints = REPOSITORY_FAMILIES[repo]
    if (not public["instance_id"].startswith(prefix)
            or re.fullmatch(re.escape(prefix) + r"[1-9][0-9]{0,11}", public["instance_id"]) is None):
        raise ValueError("instance identity differs from registered repository family")
    _sha(public["base_commit"], _SHA40, "base_commit")
    problem = public["problem_statement"]
    try:
        problem_size = len(problem.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise ValueError("problem statement must contain valid UTF-8 text") from None
    if (not problem.strip() or problem_size > MAX_PROBLEM_BYTES or "\x00" in problem):
        raise ValueError("bounded nonempty public problem statement required")
    _sha(framework_sha, _SHA40, "framework_sha")
    _sha(workflow_sha, _SHA40, "workflow_sha")
    _sha(source_profile_sha256, _SHA256, "source_profile_sha256")
    if not isinstance(source_manifest, Mapping) or not 1 <= len(source_manifest) <= MAX_FILES:
        raise ValueError("complete bounded public base-file manifest required")
    manifest = {}
    for path, digest in source_manifest.items():
        relative = _path(path)
        manifest[relative] = _sha(digest, _SHA256, "base-file SHA256")
    if type(protected_paths) is not tuple or len(protected_paths) > MAX_FILES:
        raise ValueError("immutable bounded explicit host protected paths required")
    protected = tuple(sorted({_path(value) for value in protected_paths}))
    protected_set = set(protected)
    writable = sorted(path for path in manifest
                      if path not in protected_set and _production_path(path, roots, entrypoints))
    if not writable:
        raise ValueError("no production Python source admitted by the fixed public selection policy")
    policy = {
        "version": SELECTION_POLICY_VERSION,
        "source": "complete_host_admitted_public_base_inventory",
        "production_roots": list(roots), "production_entrypoints": list(entrypoints),
        "extensions": [".py", ".pyi"], "excluded_parts": sorted(_EXCLUDED_PARTS),
        "excluded_names": sorted(_CHECKER_NAMES),
        "excluded_patterns": ["hidden path components", "test_*", "conftest*", "*_test.py",
                              "*_test.pyi", "*_tests.py", "*_tests.pyi"],
        "existing_files_only": True,
    }
    packet = {
        "schema_version": 1, "kind": "public_task_source_packet",
        "scope": "public_base_production_python_existing_files",
        "task": public,
        "runtime": {"framework_sha": framework_sha, "workflow_sha": workflow_sha,
                    "source_profile_sha256": source_profile_sha256},
        "selection_policy": policy,
        "source_manifest": dict(sorted(manifest.items())),
        "source_manifest_sha256": packet_sha256(manifest),
        "protected_paths": list(protected), "writable_paths": writable,
        "writable_paths_sha256": hashlib.sha256(canonical_bytes(writable)).hexdigest(),
        "authority": "data_only_requires_separate_host_review",
    }
    if len(canonical_bytes(packet)) > MAX_PACKET_BYTES:
        raise ValueError("public task packet exceeds declared wire size")
    return packet


def prepare_with_receipt(*args, **kwargs) -> dict:
    """Return packet plus stable byte-level receipt; neither field grants authority."""
    packet = prepare_public_task_packet(*args, **kwargs)
    raw = canonical_bytes(packet)
    return {"packet": packet, "packet_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_bytes": len(raw)}
