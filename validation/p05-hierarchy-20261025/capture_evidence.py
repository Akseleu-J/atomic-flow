"""Record final local selection and retain diagnostic failures separately."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    suites = ET.parse(OUT / "junit.xml").getroot()
    cases = list(suites.iter("testcase"))
    assert cases
    assert len({(case.get("classname"), case.get("name")) for case in cases}) == len(cases)
    skipped = len(list(suites.iter("skipped")))
    failed = len(list(suites.iter("failure")))
    errors = len(list(suites.iter("error")))
    passed = len(cases) - skipped - failed - errors
    source = {path.relative_to(ROOT).as_posix(): sha(path)
              for folder in ("src", "tests") for path in sorted((ROOT / folder).rglob("*.py"))}
    (OUT / "tracked-runtime.diff").write_bytes(subprocess.check_output(
        ["git", "diff", "--binary", "--", "src", "tests"], cwd=ROOT))
    artifact_paths = [path for path in OUT.iterdir() if path.suffix in {".xml", ".txt", ".py", ".diff"}]
    data = {"package": 38, "date": "2026-10-25", "local_only": True, "provider_calls": 0,
            "base_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "final_selection": {"passed": passed, "failed": failed, "errors": errors, "skipped": skipped,
                                "status": "failed" if failed or errors else "passed"},
            "hierarchy_cases": sum(case.get("classname", "").endswith("test_goal_hierarchy") for case in cases),
            "source_sha256": source,
            "artifacts_sha256": {path.name: sha(path) for path in sorted(artifact_paths)},
            "limits": ["focused offline selection, not full suite/CI",
                       "initial intermittent rollback failures retained; root cause not established",
                       "P05 revision evidence and rolling expansion remain open"]}
    (OUT / "manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**data["final_selection"], "hierarchy_cases": data["hierarchy_cases"], "source_files": len(source)}))


if __name__ == "__main__":
    main()
