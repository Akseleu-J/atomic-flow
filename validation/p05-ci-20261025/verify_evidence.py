"""Verify downloaded CI bytes against accepted runtime source; no model calls."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HEAD = "91d22e2d1ed3e4d415bbb72076503cc4c6bf2fe3"
TESTED = "be9fb63a075274bdbf7dd8e40671053f01daf34a"
TREE = "c95e2dbdd4dfb90098a6e7f1601e8b64c1d1978a"
ARCHIVES = {
    "host-py311.zip": "ef5ee8892b849d5d454ad060a32b61e42ed82127910a654864bbd54a07113a50",
    "host-py312.zip": "60a2ab6725d5ccba5cd556a211d745ca820b8e0acbd11983927a6b6385141928",
    "external-composition.zip": "53c3e190caff26e6084f9a6a4b2f38121930d067514c57635044fdfc920e869e",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    local = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes().replace(b"\r\n", b"\n"))
             for folder in ("src", "tests") for path in sorted((ROOT / folder).rglob("*.py"))}
    reports = []
    for name, expected in ARCHIVES.items():
        archive = HERE / name
        assert sha(archive.read_bytes()) == expected, f"archive checksum mismatch: {name}"
        with ZipFile(archive) as files:
            if name.startswith("host"):
                evidence_bytes = files.read("source-evidence.json")
                evidence = json.loads(evidence_bytes)
                junit = files.read("junit.xml")
                assert evidence["tested_commit"] == TESTED
                assert not evidence["runtime_worktree_dirty"]
                assert evidence["status"] == "passed" and evidence["failures"] == 0
                assert evidence["source_normalized_sha256"] == local, "tested source differs from local runtime"
                assert sha(junit) == evidence["junit_sha256"]
                cases = list(ET.fromstring(junit).iter("testcase"))
                assert cases and all(c.find("failure") is None and c.find("error") is None for c in cases)
                skipped = sum(c.find("skipped") is not None for c in cases)
                assert len(cases) == evidence["testcases"] and skipped == evidence["skipped"]
                (HERE / (name[:-4] + "-source.json")).write_bytes(evidence_bytes)
                reports.append({"archive": name, "passed": len(cases) - skipped, "skipped": skipped,
                                "source_files_matched": len(local), "junit_sha256": sha(junit)})
            else:
                assert files.read("tested-commit.txt").decode().strip() == TESTED
                result_bytes = files.read("result.json")
                result = json.loads(result_bytes)
                assert result["status"] == "passed" and result["paid_calls"] == 0
                assert all(s["status"] == "passed" for s in result["scenarios"])
                expected_script = sha((ROOT / "tools/integration_acceptance.py").read_bytes().replace(b"\r\n", b"\n"))
                assert result["script_sha256"] == expected_script
                (HERE / "external-composition.json").write_bytes(result_bytes)
                reports.append({"archive": name, "scenarios": [s["name"] for s in result["scenarios"]],
                                "status": "passed", "script_sha256": expected_script})
    result = {"status": "verified", "published_head": HEAD, "tested_merge": TESTED, "same_tree": TREE,
              "tree_verified_by": "git fetch refs/pull/2/merge and rev-parse of published head/merge tree",
              "runs": [40175334384, 40175334261], "archive_sha256": ARCHIVES, "reports": reports,
              "installed_matrix": "12 jobs passed; individual installed wheel archives not downloaded in this package",
              "scope": "P05 host core acceptance, not production readiness or model utility"}
    (HERE / "verified.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
