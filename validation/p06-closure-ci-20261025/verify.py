"""Read-only offline evidence verifier; no extraction/import/execution of ZIP members."""
import hashlib
import io
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HEAD = "995e3bb5cdc07bec4b56005c937187a1408645e6"
MERGE = "c134cb411944b5b6373f511c4983af81a8d206a1"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def cases(raw):
    unique = {}
    for c in ET.fromstring(raw).iter("testcase"):
        unique[(c.get("classname"), c.get("name"))] = c
    return unique


def stats(items):
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in items.values())
    skipped = sum(c.find("skipped") is not None for c in items.values())
    return {"cases": len(items), "passed": len(items) - failed - skipped, "failed": failed, "skipped": skipped}


assert git("rev-parse", HEAD + "^{tree}") == git("rev-parse", MERGE + "^{tree}")
paths = [p for p in git("ls-tree", "-r", "--name-only", HEAD).decode().splitlines()
         if p.endswith(".py") and p.startswith(("src/", "tests/"))]
process = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT,
                         input="".join(HEAD + ":" + p + "\n" for p in paths).encode(), capture_output=True, check=True)
stream = io.BytesIO(process.stdout)
source = {}
for p in paths:
    header = stream.readline().split()
    assert header[1] == b"blob", header
    raw = stream.read(int(header[2]))
    assert stream.read(1) == b"\n"
    source[p] = sha(raw.replace(b"\r\n", b"\n"))
runs = json.loads((HERE / "ci-runs.json").read_text())["workflow_runs"]
assert {r["id"] for r in runs} == {40224092775, 40224092891}
assert all(r["status"] == "completed" and r["conclusion"] == "success" for r in runs)
for filename in ("ci-check-jobs.json", "ci-interop-jobs.json"):
    jobs = json.loads((HERE / filename).read_text())["jobs"]
    assert jobs and all(j["conclusion"] == "success" for j in jobs)
artifacts = json.loads((HERE / "ci-artifacts.json").read_text())
assert len(artifacts) == 4
assert sum(a["name"].startswith("host-tests-") for a in artifacts) == 2
assert sum(a["name"].startswith("validation-") for a in artifacts) == 1
assert sum(a["name"].startswith("external-interoperability-") for a in artifacts) == 1
matrix = json.loads((HERE / "acceptance-matrix.json").read_text())["rows"]
assert len(matrix) == 9
reports = []
for artifact in json.loads((HERE / "ci-artifacts.json").read_text()):
    archive = HERE / (str(artifact["id"]) + ".zip")
    assert "sha256:" + sha(archive.read_bytes()) == artifact["digest"]
    item = {"artifact_id": artifact["id"], "archive_sha256": sha(archive.read_bytes()), "name": artifact["name"]}
    with zipfile.ZipFile(archive) as z:
        if "source-evidence.json" in z.namelist():
            evidence = json.loads(z.read("source-evidence.json"))
            assert evidence["tested_commit"] == MERGE and evidence["status"] == "passed"
            assert not evidence["runtime_worktree_dirty"] and evidence["provider_calls"] == 0
            assert evidence["source_normalized_sha256"] == source
            junit = z.read("junit.xml")
            assert sha(junit) == evidence["junit_sha256"]
            item.update(stats(cases(junit)), source_files_matched=len(source), python=evidence["python"])
            passed_matrix = {}
            for row in matrix:
                covered = {}
                for required in row["cases"]:
                    matched = {key: c for key, c in cases(junit).items()
                               if key[0] == required["classname"] and key[1].split("[", 1)[0] == required["test"]}
                    assert matched and stats(matched)["failed"] == stats(matched)["skipped"] == 0, required
                    covered.update(matched)
                passed_matrix[row["id"]] = len(covered)
            item["matrix_passed_cases"] = passed_matrix
            assert item["cases"] > 0 and item["failed"] == 0
        elif "results.json" in z.namelist():
            assert z.read("tested-commit.txt").decode().strip() == MERGE
            result = json.loads(z.read("results.json"))
            assert result["container"] == "PASS" and result["tests_exit"] == result["extra_tests_exit"] == result["compile_exit"] == 0
            item["docker"] = result
        else:
            assert z.read("tested-commit.txt").decode().strip() == MERGE
            result = json.loads(z.read("result.json"))
            assert result["status"] == "passed" and result["paid_calls"] == 0
            assert all(s["status"] == "passed" for s in result["scenarios"])
            item["composition_scenarios"] = [s["name"] for s in result["scenarios"]]
    reports.append(item)
local = cases((HERE.parent / "p06-independent-review-20261025" / "deadline-final.xml").read_bytes())
assert stats(local) == {"cases": 8, "passed": 8, "failed": 0, "skipped": 0}
verified = {"status": "verified", "published_head": HEAD, "tested_merge": MERGE,
            "tree": git("rev-parse", HEAD + "^{tree}").decode().strip(),
            "runs": [40224092775, 40224092891], "reports": reports,
            "local_final_new_cases": stats(local),
            "local_history": "Historical failed fixtures and expected-red unbound-lineage repro retained; counts from different selections are not summed.", "new_provider_calls": 0,
            "limits": "Wheel matrix job status recorded separately; its 12 archives were not downloaded. No live utility, independent review, worker deployment or production acceptance claim."}
(HERE / "ci-verified.json").write_text(json.dumps(verified, indent=2) + "\n", encoding="utf-8")
print(json.dumps(verified))
