"""Offline exact-source CI audit with cross-platform skipped-case coverage.

Reads archives as data; never extracts or executes their contents. Every skipped
host testcase must have an actual passing outcome on an applicable CI executor.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

WORKFLOWS = {
    "checks": "Project and container checks",
    "interop": "External interoperability and installed release",
}
REQUIRED_JOBS = {
    "checks": {"Installed wheel acceptance (3.11)", "Installed wheel acceptance (3.12)",
               "Python 3.11", "Python 3.12", "Windows-specific runtime acceptance", "Real Docker acceptance"},
    "interop": {"Real reviewed task, notebook and recovery composition"} | {
        f"Wheel {system} Python {python} {extra}" for system in ("ubuntu-24.04", "windows-2022", "macos-14")
        for python in ("3.11", "3.12") for extra in ("core", "api")},
}
REQUIRED_CRITERIA = {f"P{chapter:02}.{item}" for chapter, count in
                     ((0, 5), (1, 5), (2, 9), (3, 7), (4, 5), (5, 6), (6, 7))
                     for item in range(1, count + 1)}


def _commit(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("source identity requires a full immutable Git SHA")
    return value


def validate_ci_metadata(identity, runs, job_sets, artifacts):
    """Bind the API's run/job/artifact projections without inventing missing fields.

    The run-list connector omits head_sha; workflow_run inside each downloaded
    artifact is the required source binding. When a fuller run includes head_sha,
    that field must agree as well. PR artifacts name the tested merge while their
    workflow_run.head_sha names the reviewed branch head.
    """
    head, merge = _commit(identity["head"]), _commit(identity["merge"])
    _commit(identity["tree"])
    if len(runs) != 2 or {r.get("name") for r in runs} != set(WORKFLOWS.values()):
        raise ValueError("both distinct mandatory workflows are required")
    by_kind = {kind: next(r for r in runs if r["name"] == name) for kind, name in WORKFLOWS.items()}
    if (len({r.get("id") for r in runs}) != 2
            or len({r.get("workflow_id") for r in runs}) != 2
            or any(type(r.get("id")) is not int or type(r.get("workflow_id")) is not int
                   or r["status"] != "completed" or r["conclusion"] != "success"
                   or ("head_sha" in r and r["head_sha"] != head) for r in runs)):
        raise ValueError("both source-bound mandatory workflows must succeed")
    seen_jobs = set()
    for kind, run in by_kind.items():
        jobs = job_sets[kind]
        names = [job.get("name") for job in jobs]
        if len(names) != len(set(names)) or not REQUIRED_JOBS[kind] <= set(names):
            raise ValueError("mandatory CI job list is missing or duplicates required jobs")
        for job in jobs:
            if (type(job.get("id")) is not int or job["id"] in seen_jobs
                    or job.get("run_id") != run["id"] or job.get("status") != "completed"
                    or job.get("conclusion") != "success"):
                raise ValueError("mandatory CI job identity/run/status mismatch")
            seen_jobs.add(job["id"])
    expected = {
        f"host-tests-3.11-{merge}": "checks",
        f"host-tests-3.12-{merge}": "checks",
        f"windows-runtime-{merge}": "checks",
        f"validation-{merge}": "checks",
        f"external-interoperability-{merge}": "interop",
    }
    if (len(artifacts) != len(expected) or {a.get("name") for a in artifacts} != set(expected)
            or len({a.get("id") for a in artifacts}) != len(expected)):
        raise ValueError("distinct Python 3.11/3.12, Windows, Docker and composition artifacts required")
    for artifact in artifacts:
        run = artifact.get("workflow_run", {})
        if (type(artifact.get("id")) is not int or artifact["id"] < 1
                or run.get("id") != by_kind[expected[artifact["name"]]]["id"]
                or run.get("head_sha") != head
                or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")) is None):
            raise ValueError("artifact workflow/source/digest binding mismatch")


def validate_matrix(matrix):
    if (not isinstance(matrix, list) or len(matrix) != len(REQUIRED_CRITERIA)
            or {r.get("id") for r in matrix} != REQUIRED_CRITERIA):
        raise ValueError("matrix must cover each original P00-P06 numbered criterion exactly once")
    for row in matrix:
        if not row.get("criterion") or not isinstance(row.get("cases"), list) or not row["cases"]:
            raise ValueError("every criterion requires named acceptance cases")
        seen = set()
        for case in row["cases"]:
            if (not isinstance(case, dict) or set(case) != {"classname", "test"}
                    or not isinstance(case["classname"], str) or not case["classname"].startswith("tests.")
                    or not isinstance(case["test"], str) or not case["test"].startswith("test_")
                    or "[" in case["test"]):
                raise ValueError("acceptance case requires exact module and unparametrized test name")
            key = case["classname"], case["test"]
            if key in seen:
                raise ValueError("duplicate acceptance case in criterion")
            seen.add(key)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def case_map(raw):
    result = {}
    for case in ET.fromstring(raw).iter("testcase"):
        key = (case.get("classname"), case.get("name"))
        if not all(isinstance(part, str) and part.strip() for part in key):
            raise ValueError("testcase identity missing")
        if key in result:
            raise ValueError(f"duplicate testcase identity: {key}")
        status = ("failed" if case.find("failure") is not None or case.find("error") is not None else
                  "skipped" if case.find("skipped") is not None else "passed")
        result[key] = status
    if not result:
        raise ValueError("empty JUnit is not acceptance")
    return result


def source_hashes(root, head):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    paths = [p for p in git("ls-tree", "-r", "--name-only", head).decode().splitlines()
             if p.startswith(("src/", "tests/")) and p.endswith(".py")]
    if not paths:
        raise ValueError("source identity contains no runtime or tests")
    output = subprocess.check_output(["git", "cat-file", "--batch"], cwd=root,
                                     input="".join(head + ":" + p + "\n" for p in paths).encode())
    stream, result = io.BytesIO(output), {}
    for path in paths:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError("source Git object missing")
        raw = stream.read(int(header[2]))
        if stream.read(1) != b"\n":
            raise ValueError("invalid Git object framing")
        result[path] = sha(raw.replace(b"\r\n", b"\n"))
    return result


def verify(root, directory):
    identity = json.loads((directory / "source-identity.json").read_text())
    head, merge = _commit(identity["head"]), _commit(identity["merge"])
    trees = [subprocess.check_output(["git", "rev-parse", ref + "^{tree}"], cwd=root).decode().strip()
             for ref in (head, merge)]
    if trees != [identity["tree"], identity["tree"]]:
        raise ValueError("tested merge differs from published source")
    runs = json.loads((directory / "ci-runs.json").read_text())["workflow_runs"]
    job_sets = {kind: json.loads((directory / filename).read_text())["jobs"] for kind, filename in
                (("checks", "ci-check-jobs.json"), ("interop", "ci-interop-jobs.json"))}
    artifacts = json.loads((directory / "ci-artifacts.json").read_text())
    validate_ci_metadata(identity, runs, job_sets, artifacts)
    matrix = json.loads((directory / "acceptance-matrix.json").read_text())["rows"]
    validate_matrix(matrix)
    expected_source = source_hashes(root, head)
    passed, skipped, reports, kinds = set(), set(), [], []
    host_cases = []
    for artifact in artifacts:
        raw = (directory / (str(artifact["id"]) + ".zip")).read_bytes()
        if "sha256:" + sha(raw) != artifact["digest"]:
            raise ValueError("artifact checksum mismatch")
        report = {"id": artifact["id"], "name": artifact["name"], "sha256": sha(raw)}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or sum(item.file_size for item in archive.infolist()) > 64_000_000:
                raise ValueError("duplicate or oversized archive members")
            if "source-evidence.json" in names:
                evidence = json.loads(archive.read("source-evidence.json"))
                if (evidence["tested_commit"] != merge or evidence["status"] != "passed"
                        or evidence["runtime_worktree_dirty"] or evidence["provider_calls"] != 0
                        or evidence["source_normalized_sha256"] != expected_source):
                    raise ValueError("host acceptance source identity/status mismatch")
                junit = archive.read("junit.xml")
                if sha(junit) != evidence["junit_sha256"]:
                    raise ValueError("host JUnit checksum mismatch")
                cases = case_map(junit)
                kind = "windows" if artifact["name"].startswith("windows-runtime-") else "host"
                version = "3.11." if artifact["name"].startswith("host-tests-3.11-") else "3.12."
                if (not isinstance(evidence.get("python"), str) or not evidence["python"].startswith(version)
                        or evidence.get("platform") != ("Windows" if kind == "windows" else "Linux")
                        or evidence.get("testcases") != len(cases) or evidence.get("failures") != 0
                        or evidence.get("skipped") != sum(status == "skipped" for status in cases.values())):
                    raise ValueError("host environment/counts differ from required runtime and JUnit")
                if kind == "host":
                    host_cases.append(cases)
            elif "results.json" in names:
                if archive.read("tested-commit.txt").decode().strip() != merge:
                    raise ValueError("Docker tested source mismatch")
                result = json.loads(archive.read("results.json"))
                if (result["container"] != "PASS" or result["tests_exit"] != 0
                        or result["extra_tests_exit"] != 0 or result["compile_exit"] != 0):
                    raise ValueError("Docker acceptance not successful")
                cases = case_map(archive.read("container-junit.xml"))
                kind = "docker"
            elif "result.json" in names:
                if archive.read("tested-commit.txt").decode().strip() != merge:
                    raise ValueError("composition tested source mismatch")
                result = json.loads(archive.read("result.json"))
                if result["status"] != "passed" or result["paid_calls"] != 0 or not result["scenarios"] or not all(
                        s["status"] == "passed" for s in result["scenarios"]):
                    raise ValueError("composition scenarios not successful")
                cases, kind = {}, "composition"
            else:
                raise ValueError("unexpected artifact type")
        if "failed" in cases.values():
            raise ValueError("failed testcase in acceptance archive")
        passed.update(key for key, status in cases.items() if status == "passed")
        skipped.update(key for key, status in cases.items() if status == "skipped")
        report.update(kind=kind, passed=sum(s == "passed" for s in cases.values()),
                      skipped=sum(s == "skipped" for s in cases.values()))
        reports.append(report)
        kinds.append(kind)
    if sorted(kinds) != ["composition", "docker", "host", "host", "windows"]:
        raise ValueError("two host, Docker, Windows and composition evidence archives required")
    if skipped - passed:
        raise ValueError(f"skipped cases have no actual PASS on another executor: {sorted(skipped - passed)}")
    for row in matrix:
        for required in row["cases"]:
            for cases in host_cases:
                matched = {key: status for key, status in cases.items()
                           if key[0] == required["classname"] and key[1].split("[", 1)[0] == required["test"]}
                if not matched or any(status != "passed" for status in matched.values()):
                    raise ValueError(f"chapter criterion lacks passing host cases: {row['id']}: {required}")
    return {"status": "verified", "identity": identity, "source_files_matched": len(expected_source),
            "reports": reports, "criterion_rows": len(matrix), "unique_executed_pass_cases": len(passed),
            "skipped_cases_covered_by_actual_pass": len(skipped), "uncovered_skips": [],
            "new_provider_calls": 0, "limits": "Host contracts only; no live model quality or production acceptance claim."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = verify(root, args.directory)
    (args.directory / "verified.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
