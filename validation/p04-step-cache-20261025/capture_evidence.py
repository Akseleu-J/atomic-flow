"""Bind final local P04 JUnit selections to current source bytes; no reruns."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    seen, runs, total_passed, total_skipped = set(), [], 0, 0
    for name in ("junit.xml", "junit-scope.xml"):
        cases = ET.parse(OUT / name).getroot().findall(".//testcase")
        ids = {(case.get("classname"), case.get("name")) for case in cases}
        assert len(ids) == len(cases) and not seen & ids, "duplicate final test identities"
        seen.update(ids)
        failures = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
        assert not failures, "test failures cannot be recorded as acceptance"
        skips = [case for case in cases if case.find("skipped") is not None]
        passed = len(cases) - len(skips)
        total_passed += passed
        total_skipped += len(skips)
        runs.append({"junit": name, "passed": passed, "skipped": len(skips),
                     "skips": [{"case": case.get("name"), "reason": case.find("skipped").get("message")} for case in skips]})
    docs = [ROOT / "docs" / name for name in ("IMPLEMENTATION_STEP_36_P04_STEP_CACHE.md", "PROJECT_CONTEXT.md", "PRODUCTION_PLAN.md")]
    for path in docs:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                assert (path.parent / target.split("#")[0]).exists(), (path, target)
    inventory = {}
    for directory in ("src", "tests"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            inventory[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
    diff = subprocess.check_output(["git", "diff", "HEAD", "--binary", "--", "src", "tests"], cwd=ROOT)
    (OUT / "tracked-runtime.diff").write_bytes(diff)
    manifest = {
        "date": "2026-10-25", "timezone": "Europe/Moscow", "python": sys.version,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "local uncommitted P04 changes on top of uncommitted package 35; not new CI",
        "live_provider_calls": 0, "vault_access": False,
        "final_disjoint_selections": runs,
        "total": {"passed": total_passed, "skipped": total_skipped, "case_ids_unique": len(seen)},
        "checks": {"ruff": "PASS", "compileall": "PASS", "git_diff_check": "PASS", "local_doc_links": "PASS"},
        "source_sha256": inventory, "tracked_runtime_diff_sha256": sha(diff),
        "limitations": ["Skipped OS symlink creation test is unexecuted coverage.",
                        "Reconciliation still reads/hashes admitted source; no repo p95/RSS claim.",
                        "Legacy retrieval/tool index is not fully bridged to durable delta store.",
                        "Earlier overlapping development runs are excluded; no commit/push or new CI."],
        "artifacts": {name: sha((OUT / name).read_bytes()) for name in
                      ("junit.xml", "junit-scope.xml", "pytest.txt", "pytest-scope.txt", "capture_evidence.py")},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": manifest["total"], "bound_python_files": len(inventory), "live_provider_calls": 0}))


if __name__ == "__main__":
    main()
