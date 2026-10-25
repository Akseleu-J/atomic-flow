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
HEAD = "395b8accfb8b5aa61a4270543c42185dde596db1"
MERGE = "a8a76f86f2a6a60f9e02bee0db0294270970608a"


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
local = {}
for filename in ("structural-edit-reviewed.xml", "semantic-autonomy-final.xml", "accounting-fail-closed.xml"):
    selection = cases((HERE / filename).read_bytes())
    local.update(selection)
    assert stats(selection)["failed"] == 0
verified = {"status": "verified", "published_head": HEAD, "tested_merge": MERGE,
            "tree": git("rev-parse", HEAD + "^{tree}").decode().strip(),
            "runs": [40212848955, 40212848950], "reports": reports,
            "local_unique_union": stats(local), "new_provider_calls": 0,
            "limits": "Wheel matrix job status recorded separately; its 12 archives were not downloaded. No live utility, independent review, worker deployment or production acceptance claim."}
(HERE / "ci-verified.json").write_text(json.dumps(verified, indent=2) + "\n", encoding="utf-8")
print(json.dumps(verified))
