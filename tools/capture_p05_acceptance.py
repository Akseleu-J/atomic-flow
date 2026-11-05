"""Capture exact-source CI provenance and JUnit outcome; never invokes models."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("python-results"))
    parser.add_argument("--additional-junit", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "junit.xml"
    by_id = {}
    reports = [report, *args.additional_junit]
    for item in reports:
        if item.exists():
            for case in ET.parse(item).getroot().iter("testcase"):
                by_id[(case.get("classname"), case.get("name"))] = case
    cases = list(by_id.values())
    failures = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    source = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for directory in ("src", "tests") for path in sorted((root / directory).rglob("*.py"))}
    normalized = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                  for directory in ("src", "tests") for path in sorted((root / directory).rglob("*.py"))}
    payload = {"schema_version": 1, "tested_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "runtime_worktree_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--", "src", "tests", "tools", ".github"], cwd=root, text=True).strip()),
        "python": platform.python_version(), "platform": platform.system(), "provider_calls": 0,
        "status": "passed" if cases and not failures else "failed_or_unavailable",
        "testcases": len(cases), "failures": failures, "skipped": skipped,
        "source_sha256": source,
        "source_normalized_sha256": normalized,
        "junit_sha256": hashlib.sha256(report.read_bytes()).hexdigest() if report.exists() else None,
        "additional_junit_sha256": {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                                    for item in args.additional_junit if item.exists()},
        "limits": "Host fixture acceptance, not live model utility, isolation attestation or semantic requirement proof"}
    (args.output / "source-evidence.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("tested_commit", "status", "testcases", "failures", "skipped")}))


if __name__ == "__main__":
    main()
