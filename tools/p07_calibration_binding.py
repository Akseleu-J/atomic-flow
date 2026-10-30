"""Redacted host calibration and initial source bindings for the development pilot.

Digests are integrity bindings, not signatures, remote attestation or authority.
Only trusted host preparation against the full private corpus can create an
admission. The launcher must separately pin the prepared bundle/source bytes;
candidate tools must never write these records. This stays source-only P07 work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

SCOPE = "source_scoped_development_not_full_p07"
MAX_ENTRIES = 100_000
MAX_DEPTH = 64
MAX_FILES = 3000
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 25_000_000
PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement", "source_path")
BINDING_FIELDS = frozenset({"schema_version", "kind", "scope", "instance_id", "repo", "base_commit",
    "public_task_sha256", "full_task_sha256", "corpus_sha256", "calibration_report_sha256",
    "controls_sha256", "runtime_profile_sha256", "oracle_image_id", "required_count", "regression_count",
    "result", "validator_version", "host_validator_sha256", "config_sha256", "runtime_sha256", "binding_helper_sha256"})


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode("ascii")).hexdigest()


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("exact SHA256 binding required")
    return value


def public_task(task):
    if type(task) is not dict:
        raise ValueError("plain public task object required")
    result = {name: task[name] for name in PUBLIC_FIELDS}
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise ValueError("complete plain public task fields required")
    return result


def create_host_binding(full_task, calibration, *, source_path, corpus_sha256, config, runtime_sha256,
                        binding_helper_sha256):
    """Validate private evidence first, then return only a redacted host binding."""
    from evaluation_p07_source_pilot import admit_calibration

    admit_calibration(calibration, full_task)
    task = public_task({**full_task, "source_path": source_path})
    binding = {"schema_version": 1, "kind": "host_calibration_binding", "scope": SCOPE,
        "instance_id": task["instance_id"], "repo": task["repo"], "base_commit": task["base_commit"],
        "public_task_sha256": canonical_sha(task), "full_task_sha256": canonical_sha(full_task),
        "corpus_sha256": _sha(corpus_sha256), "calibration_report_sha256": canonical_sha(calibration),
        "controls_sha256": _sha(calibration["controls_sha256"]),
        "runtime_profile_sha256": calibration.get("runtime_profile_sha256", calibration.get("service_profile_sha256")),
        "oracle_image_id": calibration.get("image_id"), "required_count": len(full_task["FAIL_TO_PASS"]) + len(full_task["PASS_TO_PASS"]),
        "regression_count": len(full_task["PASS_TO_PASS"]), "result": "calibrated", "validator_version": 1,
        "host_validator_sha256": hashlib.sha256((Path(__file__).parent / "evaluation_p07_source_pilot.py").read_bytes()).hexdigest(),
        "config_sha256": canonical_sha(config), "runtime_sha256": _sha(runtime_sha256),
        "binding_helper_sha256": _sha(binding_helper_sha256)}
    validate_host_binding(task, binding, expected_digest=canonical_sha(binding), corpus_sha256=corpus_sha256,
                          config=config, runtime_sha256=runtime_sha256, binding_helper_sha256=binding_helper_sha256)
    return binding


def validate_host_binding(task, binding, *, expected_digest, corpus_sha256, config, runtime_sha256,
                          binding_helper_sha256):
    """Check a trusted prepared binding; this does not re-admit hidden evidence."""
    if set(task) - set(PUBLIC_FIELDS) - {"calibration_binding"}:
        raise ValueError("protected or undeclared task data must not enter the generator packet")
    public = public_task(task)
    if type(binding) is not dict or set(binding) != BINDING_FIELDS:
        raise ValueError("exact redacted host calibration binding schema required")
    if canonical_sha(binding) != _sha(expected_digest):
        raise ValueError("host calibration binding digest differs from prepared index")
    if (type(binding["schema_version"]) is not int or binding["schema_version"] != 1
            or binding["kind"] != "host_calibration_binding" or binding["scope"] != SCOPE
            or type(binding["validator_version"]) is not int or binding["validator_version"] != 1
            or binding["result"] != "calibrated"
            or any(binding[key] != public[key] for key in ("instance_id", "repo", "base_commit"))
            or binding["public_task_sha256"] != canonical_sha(public)
            or binding["corpus_sha256"] != _sha(corpus_sha256) or binding["config_sha256"] != canonical_sha(config)
            or binding["runtime_sha256"] != _sha(runtime_sha256)
            or binding["binding_helper_sha256"] != _sha(binding_helper_sha256)):
        raise ValueError("host calibration binding task/corpus/config/runtime identity differs")
    for key in ("full_task_sha256", "calibration_report_sha256", "controls_sha256", "host_validator_sha256"):
        _sha(binding[key])
    if binding["runtime_profile_sha256"] is not None:
        _sha(binding["runtime_profile_sha256"])
    if (not isinstance(binding["oracle_image_id"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", binding["oracle_image_id"]) is None
            or type(binding["required_count"]) is not int or binding["required_count"] < 1
            or type(binding["regression_count"]) is not int or not 0 <= binding["regression_count"] < binding["required_count"]):
        raise ValueError("host calibration binding counts/image are invalid")
    return canonical_sha(binding)


def _source_path(value):
    if (type(value) is not str or not 1 <= len(value) <= 512 or value.startswith("/")
            or not value.endswith((".py", ".pyi")) or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("confined plain POSIX Python source path required")
    return value


def _manifest(files):
    if type(files) is not dict or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("bounded plain source manifest mapping required")
    for relative, digest in files.items():
        _source_path(relative)
        _sha(digest)


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _file_digest(path, before, remaining):
    """A raced growth can read at most one byte beyond its admitted byte limit."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or _linked(path)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("source snapshot file changed during admission")
        digest, count = hashlib.sha256(), 0
        limit = min(MAX_FILE_BYTES, remaining)
        while True:
            block = os.read(fd, min(64 * 1024, limit - count + 1))
            if not block:
                break
            count += len(block)
            if count > limit:
                raise ValueError("source snapshot exceeds declared byte bounds")
            digest.update(block)
        after = os.fstat(fd)
        if ((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                or count != after.st_size):
            raise ValueError("source snapshot file changed while reading")
        return digest.hexdigest(), count
    finally:
        os.close(fd)


def source_manifest(workspace):
    """Bound traversal and streaming bytes before any model execution.

    This detects ordinary filesystem races, not an OS snapshot or an attestation
    against an adversarial host. The caller owns the initial private workspace.
    """
    root = Path(workspace).absolute()
    if not root.is_dir() or any(_linked(path) for path in (root, *root.parents)):
        raise ValueError("ordinary source snapshot directory without linked ancestors required")
    result, total, entries = {}, 0, 0
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if _linked(directory):
            raise ValueError("source snapshot links are not admitted")
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ValueError("source snapshot traversal entry bound exceeded")
                path = Path(child.path)
                if child.is_symlink() or _linked(path):
                    raise ValueError("source snapshot links are not admitted")
                # Windows DirEntry.stat can return st_ino/st_dev=0 from its
                # directory cache. A fresh path stat provides the file identity
                # that must match the subsequently opened descriptor.
                observed = os.stat(path, follow_symlinks=False)
                if stat.S_ISDIR(observed.st_mode):
                    if depth >= MAX_DEPTH:
                        raise ValueError("source snapshot directory depth bound exceeded")
                    pending.append((path, depth + 1))
                    continue
                relative = _source_path(path.relative_to(root).as_posix())
                if not stat.S_ISREG(observed.st_mode):
                    raise ValueError("source snapshot contains undeclared inputs")
                if len(result) >= MAX_FILES or observed.st_size > MAX_FILE_BYTES or total + observed.st_size > MAX_TOTAL_BYTES:
                    raise ValueError("source snapshot exceeds declared bounds")
                digest, size = _file_digest(path, observed, MAX_TOTAL_BYTES - total)
                total += size
                result[relative] = digest
    _manifest(result)
    return dict(sorted(result.items()))


def create_source_binding(task, host_binding, files, *, config, runtime_sha256, binding_helper_sha256):
    """Bind the separately constructed source manifest to the prepared host row."""
    _manifest(files)
    _source_path(task["source_path"])
    if task["source_path"] not in files:
        raise ValueError("declared source is absent from source manifest")
    return {"schema_version": 1, "kind": "initial_source_binding", "scope": SCOPE,
        "instance_id": task["instance_id"], "base_commit": task["base_commit"],
        "public_task_sha256": canonical_sha(public_task(task)), "host_binding_sha256": canonical_sha(host_binding),
        "source_manifest_sha256": canonical_sha(files), "initial_source_revision": canonical_sha(files),
        "config_sha256": canonical_sha(config), "runtime_sha256": _sha(runtime_sha256),
        "binding_helper_sha256": _sha(binding_helper_sha256)}


def validate_source_binding(task, host_binding, binding, *, expected_digest, files, workspace, config,
                            runtime_sha256, binding_helper_sha256):
    expected = create_source_binding(task, host_binding, files, config=config, runtime_sha256=runtime_sha256,
                                     binding_helper_sha256=binding_helper_sha256)
    if (type(binding) is not dict or canonical_sha(binding) != canonical_sha(expected)
            or canonical_sha(binding) != _sha(expected_digest) or source_manifest(workspace) != files):
        raise ValueError("initial source snapshot/binding differs before model admission")
    return canonical_sha(binding)
