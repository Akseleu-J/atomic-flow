"""Seal local test reports against current source; never invokes providers."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    cases = {}
    for name in ("junit.xml", "junit-frontier.xml"):
        suites = ET.parse(OUT / name).getroot()
        assert not list(suites.iter("failure")) and not list(suites.iter("error"))
        for case in suites.iter("testcase"):
            cases[(case.get("classname"), case.get("name"))] = case
    assert cases
    skipped = sum(case.find("skipped") is not None for case in cases.values())
    source = {path.relative_to(ROOT).as_posix(): digest(path)
              for directory in ("src", "tests") for path in sorted((ROOT / directory).rglob("*.py"))}
    diff = subprocess.check_output(["git", "diff", "--binary", "--", "src", "tests"], cwd=ROOT)
    (OUT / "tracked-runtime.diff").write_bytes(diff)
    data = {"date": "2026-10-25", "package": "P05 frontier / 37", "local_only": True,
            "base_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "provider_calls": 0, "passed": len(cases) - skipped, "skipped": skipped,
            "source_sha256": source,
            "artifacts_sha256": {name: digest(OUT / name) for name in
                                 ("junit.xml", "pytest.txt", "junit-frontier.xml", "pytest-frontier.txt",
                                  "tracked-runtime.diff", "capture_evidence.py")},
            "limits": ["focused offline selection, not full suite or CI",
                       "P05 hierarchy and semantic acceptance remain open"]}
    (OUT / "manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": data["passed"], "skipped": skipped, "source_files": len(source)}))


if __name__ == "__main__":
    main()
