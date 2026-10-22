"""Historical task calibration. No model calls; Docker execution by default."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from atomic_flow.agents.execution import ExecutionUnavailable, run_python
from atomic_flow.config import ExecutionConfig


def load_suite(path: Path) -> dict:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != 1 or not suite.get("cases"):
        raise ValueError("Expected a nonempty schema_version=1 suite")
    for key in ("base_commit", "gold_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", suite.get(key, "")):
            raise ValueError(f"{key} must be a pinned full Git SHA")
    ids = set()
    for case in suite["cases"]:
        if not re.fullmatch(r"[a-z0-9-]+", case.get("id", "")) or case["id"] in ids:
            raise ValueError("Case IDs must be unique safe names")
        ids.add(case["id"])
        if not re.fullmatch(r"[a-z_]+", case.get("check", "")) or not case.get("request"):
            raise ValueError("Each case requires a check and request")
        for name in case.get("paths", []):
            _relative_path(name)
    return suite


def _relative_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not path.parts or "\\" in name or ":" in name or path.is_absolute() or any(p in {"..", ".git"} for p in path.parts):
        raise ValueError(f"Unsafe snapshot path: {name!r}")
    return path


def export_snapshot(repository: Path, commit: str, destination: Path) -> None:
    """Export tracked regular files only, without Git history or worktree changes."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected full Git SHA")
    archive = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repository,
                             check=True, capture_output=True, timeout=60).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 128 * 1024 * 1024:
            raise ValueError("Snapshot exceeds 128 MiB")
        for member in members:
            _relative_path(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError("Snapshots must not contain links or special files")
        # Validate every member before writing anything. Never use tar.extractall.
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source:
                    target.write_bytes(source.read())


def check_snapshot(workspace: Path, checker_source: str, check_id: str, execution: ExecutionConfig) -> dict:
    # Fresh interpreter avoids module-cache contamination between revisions.
    program = (
        "import sys,json, pathlib\n"
        "sys.path.insert(0,str(pathlib.Path.cwd()/'src'))\n"
        "import atomic_flow\n"
        "assert pathlib.Path(atomic_flow.__file__).resolve().is_relative_to(pathlib.Path.cwd()), 'wrong package'\n"
        f"exec(compile({checker_source!r},'<trusted-grader>','exec'))\n"
        "try:\n"
        f"    verify({check_id!r})\n"
        "except (AssertionError, ModuleNotFoundError) as exc:\n"
        "    print(json.dumps({'grader_status':'fail','error_type':type(exc).__name__, 'detail':str(exc)}))\n"
        "    sys.exit(1)\n"
        "print(json.dumps({'grader_status':'pass'}))\n"
    )
    start = time.monotonic()
    try:
        result = run_python(["-c", program], workspace, config=execution, timeout=60)
    except (ExecutionUnavailable, OSError) as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__, "seconds": time.monotonic() - start}
    status = "error"
    detail = {}
    try:
        detail = json.loads(result.stdout.strip().splitlines()[-1])
        if not result.timed_out and result.returncode == 0 and detail.get("grader_status") == "pass":
            status = "pass"
        elif not result.timed_out and result.returncode == 1 and detail.get("grader_status") == "fail":
            # Missing third-party dependencies are infrastructure errors, never a baseline failure.
            if detail.get("error_type") != "ModuleNotFoundError" or "atomic_flow." in detail.get("detail", ""):
                status = "fail"
    except (ValueError, IndexError, AttributeError):
        pass
    return {"status": status, "returncode": result.returncode, "timed_out": result.timed_out,
            "seconds": round(time.monotonic() - start, 3), "grader": detail,
            "stdout_tail": result.stdout[-4096:], "stderr_tail": result.stderr[-4096:]}


def calibrate(repository: Path, manifest: Path, *, execution: ExecutionConfig | None = None) -> dict:
    execution = execution or ExecutionConfig(mode="docker")
    suite = load_suite(manifest)
    checker_path = manifest.with_name("checks.py")
    checker_bytes = checker_path.read_bytes()
    checker_source = checker_bytes.decode("utf-8")
    checks = list(dict.fromkeys(case["check"] for case in suite["cases"]))
    rows = []
    with tempfile.TemporaryDirectory(prefix="atomic-calibration-") as temp:
        for label in ("base", "gold"):
            workspace = Path(temp) / label
            export_snapshot(repository, suite[f"{label}_commit"], workspace)
            for check_id in checks:
                rows.append({"revision": label, "check": check_id,
                             **check_snapshot(workspace, checker_source, check_id, execution)})
    return {"schema_version": 1, "suite": suite["suite"], "kind": "grader_calibration",
            "created_at": datetime.now(UTC).isoformat(), "execution": execution.model_dump(),
            "base_commit": suite["base_commit"], "gold_commit": suite["gold_commit"],
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "checker_sha256": hashlib.sha256(checker_bytes).hexdigest(),
            "wordings": len(suite["cases"]), "independent_changes": len(checks),
            "calibrated": all(row["status"] == ("fail" if row["revision"] == "base" else "pass") for row in rows),
            "model_calls": 0, "disclosure": suite["disclosure"], "results": rows}
