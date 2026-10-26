"""Offline evidence auditing refuses unrelated green runs and vacuous coverage."""
import copy
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "chapter_acceptance_verifier", Path(__file__).resolve().parents[1] / "tools/verify_chapter_acceptance.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def metadata():
    identity = {"head": "a" * 40, "merge": "b" * 40, "tree": "c" * 40}
    runs = [{"id": index, "workflow_id": index + 100, "name": name,
             "status": "completed", "conclusion": "success"}
            for index, name in enumerate(verifier.WORKFLOWS.values(), 1)]
    jobs = {kind: [{"id": index * 100 + number, "name": name, "run_id": index,
                   "status": "completed", "conclusion": "success"}
                  for number, name in enumerate(sorted(verifier.REQUIRED_JOBS[kind]), 1)]
            for index, kind in enumerate(verifier.WORKFLOWS, 1)}
    names = ["host-tests-3.11-", "host-tests-3.12-", "windows-runtime-", "validation-",
             "external-interoperability-"]
    artifacts = [{"id": index, "name": name + identity["merge"], "digest": "sha256:" + "d" * 64,
                  "workflow_run": {"id": 2 if index == 5 else 1, "head_sha": identity["head"]}}
                 for index, name in enumerate(names, 1)]
    return identity, runs, jobs, artifacts


def test_ci_metadata_binds_pr_head_and_tested_merge_without_inventing_run_fields():
    verifier.validate_ci_metadata(*metadata())
    data = metadata()
    for run in data[1]:
        run["head_sha"] = data[0]["head"]
    verifier.validate_ci_metadata(*data)


@pytest.mark.parametrize("tamper", [
    "run_source", "workflow_name", "duplicate_run", "job_run", "job_incomplete", "job_duplicate",
    "artifact_source", "artifact_run", "artifact_merge", "duplicate_artifact_id", "duplicate_host",
    "missing_artifact", "bad_digest", "short_source", "missing_job",
])
def test_ci_metadata_rejects_green_but_unbound_or_duplicate_evidence(tamper):
    identity, runs, jobs, artifacts = metadata()
    if tamper == "run_source":
        runs[0]["head_sha"] = "e" * 40
    elif tamper == "workflow_name":
        runs[0]["name"] = runs[1]["name"]
    elif tamper == "duplicate_run":
        runs[0]["id"] = runs[1]["id"]
    elif tamper == "job_run":
        jobs["checks"][0]["run_id"] = 2
    elif tamper == "job_incomplete":
        jobs["checks"][0]["status"] = "in_progress"
    elif tamper == "job_duplicate":
        jobs["checks"].append(copy.deepcopy(jobs["checks"][0]))
    elif tamper == "artifact_source":
        artifacts[0]["workflow_run"]["head_sha"] = "e" * 40
    elif tamper == "artifact_run":
        artifacts[0]["workflow_run"]["id"] = 2
    elif tamper == "artifact_merge":
        artifacts[0]["name"] = "host-tests-3.11-" + identity["head"]
    elif tamper == "duplicate_artifact_id":
        artifacts[0]["id"] = artifacts[1]["id"]
    elif tamper == "duplicate_host":
        artifacts[0]["name"] = artifacts[1]["name"]
    elif tamper == "missing_artifact":
        artifacts.pop()
    elif tamper == "bad_digest":
        artifacts[0]["digest"] = "sha256:unknown"
    elif tamper == "short_source":
        identity["head"] = "aaaaaaa"
    elif tamper == "missing_job":
        jobs["checks"].pop()
    with pytest.raises(ValueError):
        verifier.validate_ci_metadata(identity, runs, jobs, artifacts)


def matrix():
    return [{"id": criterion, "criterion": "host contract",
             "cases": [{"classname": "tests.test_contract", "test": "test_contract"}]}
            for criterion in sorted(verifier.REQUIRED_CRITERIA)]


def test_complete_original_criterion_matrix_is_accepted():
    verifier.validate_matrix(matrix())


@pytest.mark.parametrize("tamper", ["empty", "missing", "duplicate", "empty_cases", "bad_case", "duplicate_case"])
def test_matrix_cannot_hide_missing_original_criteria_or_unmeasured_rows(tamper):
    rows = matrix()
    if tamper == "empty":
        rows.clear()
    elif tamper == "missing":
        rows.pop()
    elif tamper == "duplicate":
        rows[0] = copy.deepcopy(rows[1])
    elif tamper == "empty_cases":
        rows[0]["cases"] = []
    elif tamper == "bad_case":
        rows[0]["cases"][0]["classname"] = ""
    elif tamper == "duplicate_case":
        rows[0]["cases"].append(copy.deepcopy(rows[0]["cases"][0]))
    with pytest.raises(ValueError):
        verifier.validate_matrix(rows)


@pytest.mark.parametrize("raw", [
    b"<testsuite/>", b'<testsuite><testcase name="test_x"/></testsuite>',
    b'<testsuite><testcase classname="tests.a" name="test_x"/><testcase classname="tests.a" name="test_x"/></testsuite>',
])
def test_junit_missing_empty_or_duplicate_case_identity_is_not_acceptance(raw):
    with pytest.raises(ValueError):
        verifier.case_map(raw)


def test_junit_failure_skip_and_actual_pass_stay_distinct():
    cases = verifier.case_map(b'<testsuite><testcase classname="tests.a" name="test_pass"/>'
                              b'<testcase classname="tests.a" name="test_skip"><skipped/></testcase>'
                              b'<testcase classname="tests.a" name="test_fail"><failure/></testcase></testsuite>')
    assert cases == {("tests.a", "test_pass"): "passed", ("tests.a", "test_skip"): "skipped",
                     ("tests.a", "test_fail"): "failed"}


def sealed_directory(tmp_path, monkeypatch, *, windows_pass=True, scenarios=True):
    identity, runs, jobs, artifacts = metadata()
    source = {"src/atomic_flow/example.py": "e" * 64}
    monkeypatch.setattr(verifier, "source_hashes", lambda *args: source)
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda *args, **kwargs: (identity["tree"] + "\n").encode())
    files = {"source-identity.json": identity, "ci-runs.json": {"workflow_runs": runs},
             "ci-check-jobs.json": {"jobs": jobs["checks"]}, "ci-interop-jobs.json": {"jobs": jobs["interop"]},
             "acceptance-matrix.json": {"rows": matrix()}}
    host_junit = (b'<testsuite><testcase classname="tests.test_contract" name="test_contract"/>'
                  b'<testcase classname="tests.platform" name="test_windows"><skipped/></testcase></testsuite>')
    windows_junit = (b'<testsuite><testcase classname="tests.platform" name="test_windows">'
                     + (b"" if windows_pass else b"<skipped/>") + b'</testcase></testsuite>')
    for artifact in artifacts:
        name = artifact["name"]
        if name.startswith(("host-tests-", "windows-runtime-")):
            junit = windows_junit if name.startswith("windows-runtime-") else host_junit
            members = {"source-evidence.json": json.dumps({
                "tested_commit": identity["merge"], "status": "passed", "runtime_worktree_dirty": False,
                "provider_calls": 0, "source_normalized_sha256": source, "junit_sha256": verifier.sha(junit),
                "python": "3.11.9" if name.startswith("host-tests-3.11-") else "3.12.9",
                "platform": "Windows" if name.startswith("windows-runtime-") else "Linux",
                "testcases": len(verifier.case_map(junit)), "failures": 0,
                "skipped": sum(s == "skipped" for s in verifier.case_map(junit).values())}).encode(),
                "junit.xml": junit}
        elif name.startswith("validation-"):
            members = {"tested-commit.txt": identity["merge"].encode(),
                       "results.json": json.dumps({"container": "PASS", "tests_exit": 0,
                                                   "extra_tests_exit": 0, "compile_exit": 0}).encode(),
                       "container-junit.xml": b'<testsuite><testcase classname="tests.docker" name="test_container"/></testsuite>'}
        else:
            members = {"tested-commit.txt": identity["merge"].encode(),
                       "result.json": json.dumps({"status": "passed", "paid_calls": 0,
                                                   "scenarios": [{"status": "passed"}] if scenarios else []}).encode()}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for member, raw in members.items():
                archive.writestr(member, raw)
        raw = buffer.getvalue()
        artifact["digest"] = "sha256:" + verifier.sha(raw)
        (tmp_path / (str(artifact["id"]) + ".zip")).write_bytes(raw)
    files["ci-artifacts.json"] = artifacts
    for filename, content in files.items():
        (tmp_path / filename).write_text(json.dumps(content), encoding="utf-8")
    return tmp_path


def test_complete_seal_reconciles_skips_only_to_an_actual_source_bound_pass(tmp_path, monkeypatch):
    result = verifier.verify(tmp_path, sealed_directory(tmp_path, monkeypatch))
    assert result["status"] == "verified" and result["criterion_rows"] == 44
    assert result["skipped_cases_covered_by_actual_pass"] == 1 and result["uncovered_skips"] == []


def test_platform_skip_without_actual_runtime_pass_cannot_close_chapters(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="no actual PASS"):
        verifier.verify(tmp_path, sealed_directory(tmp_path, monkeypatch, windows_pass=False))


def test_empty_composition_is_not_scenario_acceptance(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="composition scenarios"):
        verifier.verify(tmp_path, sealed_directory(tmp_path, monkeypatch, scenarios=False))


def test_downloaded_archive_tamper_fails_before_reading_junit(tmp_path, monkeypatch):
    directory = sealed_directory(tmp_path, monkeypatch)
    path = directory / "1.zip"
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verifier.verify(tmp_path, directory)


@pytest.mark.parametrize("tamper", ["python", "platform", "counts", "source", "junit_digest"])
def test_resealed_archive_cannot_relabel_runtime_source_or_junit(tmp_path, monkeypatch, tamper):
    directory = sealed_directory(tmp_path, monkeypatch)
    path = directory / "1.zip"
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    report = json.loads(members["source-evidence.json"])
    if tamper == "python":
        report["python"] = "3.12.9"  # Archive 1 is the required Python 3.11 run.
    elif tamper == "platform":
        report["platform"] = "Windows"
    elif tamper == "counts":
        report["testcases"] += 1
    elif tamper == "source":
        report["source_normalized_sha256"]["src/atomic_flow/example.py"] = "f" * 64
    elif tamper == "junit_digest":
        report["junit_sha256"] = "f" * 64
    members["source-evidence.json"] = json.dumps(report).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    artifacts_path = directory / "ci-artifacts.json"
    artifacts = json.loads(artifacts_path.read_text())
    artifacts[0]["digest"] = "sha256:" + verifier.sha(path.read_bytes())
    artifacts_path.write_text(json.dumps(artifacts), encoding="utf-8")
    with pytest.raises(ValueError):
        verifier.verify(tmp_path, directory)
