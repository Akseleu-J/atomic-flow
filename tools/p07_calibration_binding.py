"""Redacted host calibration and initial source bindings for the development pilot.

Digests are integrity bindings, not signatures, remote attestation or authority.
Only trusted host preparation against the full private corpus can create an
admission. The launcher must separately pin the prepared bundle/source bytes;
candidate tools must never write these records. This stays source-only P07 work.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCOPE = "source_scoped_development_not_full_p07"
PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement", "source_path")
BINDING_FIELDS = frozenset({"schema_version", "kind", "scope", "instance_id", "repo", "base_commit",
    "public_task_sha256", "full_task_sha256", "corpus_sha256", "calibration_report_sha256",
    "controls_sha256", "runtime_profile_sha256", "oracle_image_id", "required_count", "regression_count",
    "result", "validator_version", "host_validator_sha256", "config_sha256", "runtime_sha256", "binding_helper_sha256"})


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode("ascii")).hexdigest()


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("exact SHA256 binding required")
    return value


def public_task(task):
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
    if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
        raise ValueError("exact redacted host calibration binding schema required")
    if canonical_sha(binding) != _sha(expected_digest):
        raise ValueError("host calibration binding digest differs from prepared index")
    if (binding["schema_version"] != 1 or binding["kind"] != "host_calibration_binding" or binding["scope"] != SCOPE
            or binding["validator_version"] != 1 or binding["result"] != "calibrated"
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


def source_manifest(workspace):
    """Inventory an initial production-only snapshot, before model execution."""
    root = Path(workspace)
    if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("ordinary source snapshot directory required")
    result, total = {}, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("source snapshot links are not admitted")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if (not path.is_file() or not relative.endswith((".py", ".pyi")) or "\\" in relative
                or ":" in relative or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("source snapshot contains undeclared inputs")
        if path.stat().st_size > 1_000_000:
            raise ValueError("source snapshot exceeds per-file bounds")
        raw = path.read_bytes()
        total += len(raw)
        if len(raw) > 1_000_000 or total > 25_000_000 or len(result) >= 3000:
            raise ValueError("source snapshot exceeds declared bounds")
        result[relative] = hashlib.sha256(raw).hexdigest()
    if not result:
        raise ValueError("nonempty source snapshot required")
    return result


def create_source_binding(task, host_binding, files, *, config, runtime_sha256, binding_helper_sha256):
    """Bind the separately constructed source manifest to the prepared host row."""
    if task["source_path"] not in files or not files:
        raise ValueError("declared source is absent from source manifest")
    for digest in files.values():
        _sha(digest)
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
    if binding != expected or canonical_sha(binding) != _sha(expected_digest) or source_manifest(workspace) != files:
        raise ValueError("initial source snapshot/binding differs before model admission")
    return canonical_sha(binding)
