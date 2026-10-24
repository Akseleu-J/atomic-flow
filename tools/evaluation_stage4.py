"""Offline Stage 4 patch grading and paired result admission.

Never calls a model, installs dependencies or downloads source. Each evaluator
is a trusted host command against a private temporary clone. External tests are
applied after a source-only candidate and protected from candidate modification.
Use on trusted evaluator workers; subprocess timeouts are not an OS sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_PATCH = 2_000_000
MAX_LOG = 1_000_000


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def patch_paths(patch):
    if len(patch.encode()) > MAX_PATCH:
        raise ValueError("patch exceeds byte limit")
    paths = set()
    for line in patch.splitlines():
        if line.endswith((" 120000", " 160000")) or line == "GIT binary patch":
            raise ValueError("symlink/submodule/binary changes are unsupported")
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t")[0]
            if value == "/dev/null":
                continue
            if not value.startswith(("a/", "b/")) or '"' in value:
                raise ValueError("only unquoted repository-relative patch paths are supported")
            path = PurePosixPath(value[2:])
            if path.is_absolute() or ".." in path.parts or any(part.startswith(".git") for part in path.parts):
                raise ValueError("unsafe patch path")
            paths.add(str(path))
    if not paths:
        raise ValueError("patch has no file changes")
    return paths


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = data["tasks"]
    if data.get("version") != 1 or len(tasks) != 30 or len({t["repo"] for t in tasks}) != 5:
        raise ValueError("expected thirty tasks from five repositories")
    if len({t["instance_id"] for t in tasks}) != 30:
        raise ValueError("duplicate task")
    for task in tasks:
        if not SHA.fullmatch(task["base_commit"]) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", task["repo"]):
            raise ValueError("invalid pinned source identity")
        for key in ("patch", "test_patch"):
            if digest(task[key].encode()) != task[key + "_sha256"]:
                raise ValueError("task patch digest mismatch")
            patch_paths(task[key])
        if set(task["FAIL_TO_PASS"]) & set(task["PASS_TO_PASS"]):
            raise ValueError("overlapping oracle tests")
        if not task["FAIL_TO_PASS"]:
            raise ValueError("task lacks fail-to-pass oracle")
    return data


def run(command, cwd, timeout, log):
    """File-backed output avoids unbounded capture in low-memory workers."""
    started = time.monotonic()
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    try:
        with Path(log).open("wb") as stream:
            completed = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=timeout, check=False)
        code, status = completed.returncode, "finished"
    except subprocess.TimeoutExpired:
        code, status = None, "timeout"
    except OSError:
        code, status = None, "infrastructure_error"
    raw = Path(log).read_bytes() if Path(log).exists() else b""
    return {"status": status, "exit_code": code, "wall_seconds": time.monotonic() - started,
            "log_sha256": digest(raw), "log_bytes": len(raw), "log_prefix": raw[:MAX_LOG].decode("utf-8", errors="replace")}


def checked_git(args, cwd=None):
    # core.autocrlf=false keeps checkouts byte-identical to committed blobs;
    # Git for Windows defaults to autocrlf=true, which rewrites every checked
    # out file to CRLF and breaks patch-context matching against LF sources.
    result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd,
                            capture_output=True, timeout=120, check=False)
    if result.returncode:
        raise ValueError("git source/patch operation failed: " + result.stderr.decode(errors="replace")[:500])
    return result.stdout.decode().strip()


def grade(task, mirror, patch, evaluator, output, *, label="candidate", timeout=180):
    """Evaluator emits oracle.json outside project: exact test id->pass/fail/error.

    The grader executable/arguments come from a reviewed host config, not dataset
    eval_script or model text. Full regressions and required tests must be listed
    in that report; process exit zero alone never establishes a successful fix.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    row = {"instance_id": task["instance_id"], "label": label, "base_commit": task["base_commit"],
           "patch_sha256": digest(patch.encode()), "status": "infrastructure_error", "verified_success": False}
    try:
        protected = patch_paths(task["test_patch"])
        if patch:
            changed = patch_paths(patch)
            if changed & protected or any("test" in p.lower() or p.endswith(("pytest.ini", "conftest.py")) for p in changed):
                raise ValueError("candidate modifies protected test/grader paths")
        with tempfile.TemporaryDirectory(prefix="stage4-") as temp:
            work = Path(temp) / "repo"
            checked_git(["clone", "--no-hardlinks", "--no-checkout", str(Path(mirror).resolve()), str(work)])
            checked_git(["checkout", "--detach", task["base_commit"]], work)
            if checked_git(["rev-parse", "HEAD"], work) != task["base_commit"]:
                raise ValueError("source identity mismatch")
            for name, text in (("candidate", patch), ("oracle", task["test_patch"])):
                if text:
                    target = Path(temp) / (name + ".patch")
                    target.write_bytes(text.encode())
                    checked_git(["apply", "--check", str(target)], work)
                    checked_git(["apply", str(target)], work)
            if patch and any((work / p).is_symlink() or not (work / p).resolve().is_relative_to(work.resolve()) for p in patch_paths(patch)):
                raise ValueError("candidate path resolves outside workspace")
            protected_hashes = {p: digest((work / p).read_bytes()) for p in protected if (work / p).exists()}
            oracle = output / (task["instance_id"] + "-" + label + "-oracle.json")
            oracle.unlink(missing_ok=True)
            command = [str(arg).replace("{workspace}", str(work)).replace("{report}", str(oracle)) for arg in evaluator]
            row["execution"] = run(command, work, timeout, output / (task["instance_id"] + "-" + label + ".log"))
            if any(not (work / p).exists() or digest((work / p).read_bytes()) != value for p, value in protected_hashes.items()):
                raise ValueError("oracle files changed during evaluation")
            if row["execution"]["status"] != "finished":
                row["status"] = row["execution"]["status"]
                return row
            if not oracle.exists() or oracle.stat().st_size > 1_000_000:
                raise ValueError("missing/big independent oracle report")
            observation = json.loads(oracle.read_text(encoding="utf-8"))
            if observation.get("infrastructure_error") is True:
                raise ValueError("evaluator infrastructure/collection failure")
            tests = observation["tests"]
            required = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
            if not isinstance(tests, dict) or any(t not in tests or tests[t] not in {"pass", "fail", "error"} for t in required):
                raise ValueError("oracle missing required outcomes")
            row["oracle_sha256"] = digest(oracle.read_bytes())
            row["tests"] = tests
            row["verified_success"] = (row["execution"]["exit_code"] == 0 and observation.get("regressions_passed") is True
                                        and all(tests[t] == "pass" for t in required))
            row["status"] = "verified_success" if row["verified_success"] else "requirement_failed"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        row["error_type"] = type(exc).__name__
    return row


def calibrate(task, mirror, evaluator, output, wrong_patch, timeout=180):
    """No task may enter measured pairs unless base FAIL/gold PASS/wrong FAIL."""
    if not wrong_patch or wrong_patch == task["patch"]:
        raise ValueError("calibration needs a distinct nonempty plausible wrong patch")
    rows = [grade(task, mirror, patch, evaluator, output, label=label, timeout=timeout)
            for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong_patch))]
    base, gold, wrong = rows
    valid = (base["status"] == "requirement_failed" and gold["verified_success"]
             and wrong["status"] == "requirement_failed"
             and any(base.get("tests", {}).get(test) == "fail" for test in task["FAIL_TO_PASS"]))
    return {"instance_id": task["instance_id"], "calibrated": valid, "rows": rows,
            "wrong_patch_sha256": digest(wrong_patch.encode()), "model_quality_measured": False}


def validate_attempt(row, limits):
    """Strict result admission retains incomplete/unknown usage, never imputes zero."""
    if row["arm"] not in {"baseline", "atomic"} or row["limits"] != limits:
        raise ValueError("arm or paired limits mismatch")
    if not row["model"] or not row["provider"] or not SHA.fullmatch(row["base_commit"]):
        raise ValueError("missing source/model identity")
    for key in ("calls", "tokens_in", "tokens_out", "human_interventions"):
        if key in {"tokens_in", "tokens_out"} and row[key] is None:
            continue
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError("invalid usage counter")
    for key in ("wall_seconds", "cost_usd"):
        value = row[key]
        if key == "cost_usd" and value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid usage amount")
    if row["cost_complete"] is not (row["cost_usd"] is not None):
        raise ValueError("unknown usage must remain incomplete")
    if row["status"] not in {"finished", "timeout", "quota_unavailable", "unknown_provider_outcome", "failed", "not_run"}:
        raise ValueError("unrecognized attempt status")
    return row


def pair_report(tasks, attempts, limits):
    indexed = {}
    known_ids = {t["instance_id"] for t in tasks}
    for attempt in attempts:
        validate_attempt(attempt, limits)
        if attempt["instance_id"] not in known_ids:
            raise ValueError("unknown task attempt")
        key = (attempt["instance_id"], attempt["arm"])
        if key in indexed:
            raise ValueError("duplicate attempt")
        indexed[key] = attempt
    rows = []
    for task in tasks:
        pair = {arm: indexed.get((task["instance_id"], arm)) for arm in ("baseline", "atomic")}
        present = [r for r in pair.values() if r]
        if any(r["base_commit"] != task["base_commit"] for r in present):
            raise ValueError("wrong attempt source")
        if len(present) == 2 and any(present[0][k] != present[1][k] for k in ("model", "provider")):
            raise ValueError("paired model/provider differs")
        rows.append({"instance_id": task["instance_id"], "split": task["split"], "attempts": pair,
                     "status": "paired" if len(present) == 2 else "incomplete"})
    return {"version": 1, "task_count": len(tasks), "pairs": rows,
            "missing_attempts": sum(sum(value is None for value in row["attempts"].values()) for row in rows),
            "unknown_cost_attempts": sum(r["cost_usd"] is None for r in attempts),
            "model_quality_measured": False,
            "note": "Attempt admission is not independent patch grading; join calibrated grader evidence before scoring."}


def score_pairs(tasks, attempts, limits, grades, calibrations):
    """Join host oracle evidence; include every missing/failed task in denominator."""
    report = pair_report(tasks, attempts, limits)
    indexed = {(g["instance_id"], g["label"]): g for g in grades}
    if len(indexed) != len(grades):
        raise ValueError("duplicate independent grade")
    calibrated = {row["instance_id"]: row for row in calibrations}
    if len(calibrated) != len(calibrations):
        raise ValueError("duplicate calibration")
    task_map = {t["instance_id"]: t for t in tasks}
    totals = {}
    for arm in ("baseline", "atomic"):
        successes, known_cost, unknown, measured, token_unknown = 0, 0.0, 0, 0, 0
        for pair in report["pairs"]:
            attempt = pair["attempts"][arm]
            grade_result = indexed.get((pair["instance_id"], arm))
            calibration = calibrated.get(pair["instance_id"], {})
            task = task_map[pair["instance_id"]]
            observations = {r["label"]: r for r in calibration.get("rows", [])}
            calibration_ok = (calibration.get("calibrated") is True and set(observations) == {"base", "gold", "wrong"}
                              and observations["base"].get("status") == "requirement_failed"
                              and observations["gold"].get("verified_success") is True
                              and observations["wrong"].get("status") == "requirement_failed"
                              and observations["gold"].get("patch_sha256") == task.get("patch_sha256")
                              and all(r.get("base_commit") == task["base_commit"] for r in observations.values()))
            accepted = bool(attempt and calibration_ok and grade_result
                            and grade_result.get("base_commit") == attempt["base_commit"]
                            and grade_result.get("patch_sha256") == attempt.get("patch_sha256")
                            and grade_result.get("oracle_sha256"))
            if attempt:
                measured += attempt["status"] != "not_run"
                unknown += attempt["cost_usd"] is None
                token_unknown += attempt["tokens_in"] is None or attempt["tokens_out"] is None
                known_cost += attempt["cost_usd"] or 0
            else:
                unknown += 1
            success = accepted and attempt["status"] == "finished" and grade_result.get("verified_success") is True
            successes += success
            pair.setdefault("verified", {})[arm] = bool(success)
        totals[arm] = {"verified_successes": successes, "all_task_denominator": len(tasks), "attempted": measured,
                       "known_cost_lower_bound_usd": known_cost, "cost_complete": unknown == 0,
                       "unknown_or_missing_cost_attempts": unknown,
                       "unknown_token_attempts": token_unknown,
                       "cost_per_success_usd": known_cost / successes if successes and unknown == 0 else None}
    report.update(totals=totals, model_quality_measured=all(t["attempted"] == len(tasks) for t in totals.values())
                  and all(indexed.get((t["instance_id"], arm), {}).get("oracle_sha256")
                          for t in tasks for arm in ("baseline", "atomic")),
                  note="Measured flag denotes attempts, not a superiority claim. Missing calibration never earns success.")
    return report


def prepare(manifest, output, limits):
    """Separate model-visible prompts from host oracle/gold artifacts."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    schedule = []
    for task in manifest["tasks"]:
        task_id = task["instance_id"]
        public = {k: task[k] for k in ("instance_id", "repo", "base_commit", "problem_statement")}
        public["limits"] = limits
        public["instructions"] = "Change source to satisfy the issue. Do not change tests or test configuration. Return source patch plus complete usage evidence."
        prompt = output / (task_id + "-prompt.json")
        prompt.write_text(json.dumps(public, indent=2), encoding="utf-8")
        targets = output / (task_id + "-oracle-targets.json")
        targets.write_text(json.dumps(task["FAIL_TO_PASS"] + task["PASS_TO_PASS"], indent=2), encoding="utf-8")
        order = ["atomic", "baseline"] if int(digest(task_id.encode())[:2], 16) % 2 else ["baseline", "atomic"]
        schedule.append({"instance_id": task_id, "repo": task["repo"], "base_commit": task["base_commit"],
                         "split": task["split"], "arm_order": order, "prompt_sha256": digest(prompt.read_bytes()),
                         "oracle_targets_sha256": digest(targets.read_bytes()), "environment_status": "not_prepared",
                         "calibration_status": "not_run", "paired_status": "not_run"})
    plan = {"version": 1, "limits": limits, "schedule": schedule, "model_quality_measured": False,
            "required_host_gates": ["review isolated environment/interpreter/dependency identities per task",
                                    "independent base FAIL/gold PASS/plausible wrong FAIL",
                                    "pin same model/provider for both arms; preserve permissions/runtime differences",
                                    "save all physical-attempt usage including refusals/timeouts/unknown costs",
                                    "grade candidate patches independently; never expose gold/oracles to generation"]}
    (output / "schedule.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--mirror")
    parser.add_argument("--host-config", help="trusted JSON containing evaluator argv, wrong_patch path, timeout")
    parser.add_argument("--output")
    parser.add_argument("--paired-report", help="JSON containing attempts, limits, grades, calibrations; never executes agents")
    parser.add_argument("--prepare", action="store_true", help="export prompts/host oracle targets and deterministic paired schedule")
    parser.add_argument("--candidate-patch", help="independently grade an already generated source diff; does not call a model")
    parser.add_argument("--label", choices=("baseline", "atomic"), default="atomic")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.prepare:
        prepare(manifest, args.output, {"max_calls": 20, "max_input_tokens": 60_000, "max_output_tokens": 20_000,
                                       "max_wall_seconds": 600, "max_cost_usd": 0.05, "retry": "confirmed_refusal_only"})
        return
    if args.validate_only:
        print(json.dumps({"tasks": len(manifest["tasks"]), "manifest_sha256": digest(Path(args.manifest).read_bytes())}))
        return
    if args.paired_report:
        source = json.loads(Path(args.paired_report).read_text())
        result = score_pairs(manifest["tasks"], source["attempts"], source["limits"], source["grades"], source["calibrations"])
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        return
    task = next(t for t in manifest["tasks"] if t["instance_id"] == args.task)
    config = json.loads(Path(args.host_config).read_text())
    if args.candidate_patch:
        result = grade(task, args.mirror, Path(args.candidate_patch).read_text(encoding="utf-8"),
                       config["evaluator"], args.output, label=args.label, timeout=config.get("timeout", 180))
        Path(args.output, "grade.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise SystemExit(0 if result["verified_success"] else 1)
    report = calibrate(task, args.mirror, config["evaluator"], args.output,
                       Path(config["wrong_patch"]).read_text(), config.get("timeout", 180))
    Path(args.output, "calibration.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["calibrated"] else 1)


if __name__ == "__main__":
    main()
