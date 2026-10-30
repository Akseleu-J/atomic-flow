"""All-30 base/gold/wrong calibrations or candidate grades on a Docker Linux host."""
import argparse
import concurrent.futures
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

from evaluation_stage4 import load_manifest
from p07_campaign import save, select, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, choices=(3, 30), default=30)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--campaign", type=Path, help="Already downloaded/extracted campaign directory, to grade candidates too")
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("Use a Docker-capable Linux worker, not this Windows host or an unverified Kaggle runtime")
    if args.output.exists():
        raise SystemExit("Fresh output required")
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    tasks = select(load_manifest("benchmarks/stage4/corpus.json")["tasks"], args.count)
    args.output.mkdir(parents=True)
    clean_env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"}}

    def run(task):
        task_id = task["instance_id"]
        task_root = args.output / task_id
        task_root.mkdir()
        command = [sys.executable, "tools/evaluation_p07_isolated.py", "--task", task_id]
        with (task_root / "driver.txt").open("wb") as log:
            result = subprocess.run(command + ["--output", str(task_root / "calibration")], env=clean_env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=2400)
        path = task_root / "calibration/calibration.json"
        report = json.loads(path.read_text()) if path.exists() else {"instance_id": task_id, "calibrated": False, "status": "worker_failed"}
        save(args.output / (task_id + ".json"), report)
        row = {"instance_id": task_id, "calibrated": report.get("calibrated", False), "exit": result.returncode, "candidates": {}}
        if args.campaign:
            for arm in ("baseline", "atomic"):
                patch = args.campaign / "tasks" / task_id / arm / "candidate.patch"
                if not patch.exists():
                    row["candidates"][arm] = {"status": "no_candidate", "verified_success": False}
                    continue
                if not patch.resolve().is_relative_to(args.campaign.resolve()) or patch.is_symlink():
                    raise ValueError("unsafe candidate location")
                destination = task_root / arm
                with (task_root / (arm + "-driver.txt")).open("wb") as log:
                    subprocess.run(command + ["--candidate", str(patch), "--output", str(destination)], env=clean_env,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=2400)
                candidate_report = json.loads((destination / "calibration.json").read_text())
                row["candidates"][arm] = candidate_report.get("candidate", {"status": "not_admitted", "verified_success": False})
        return row

    rows = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run, task): task["instance_id"] for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                try:
                    rows.append(future.result())
                except Exception as exc:
                    rows.append({"instance_id": futures[future], "calibrated": False, "status": "worker_error", "error_type": type(exc).__name__})
                save(args.output / "summary.json", {"p07": "OPEN", "model_calls": 0, "rows": rows,
                    "expected_tasks": len(tasks), "complete": len(rows) == len(tasks)})
    finally:
        files = {p.relative_to(args.output).as_posix(): sha(p.read_bytes()) for p in args.output.rglob("*") if p.is_file()}
        save(args.output / "checksums.json", files)
        with zipfile.ZipFile(str(args.output) + ".zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in args.output.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(args.output))


if __name__ == "__main__":
    main()
