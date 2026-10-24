"""Measure one pinned, already checked-out source tree; no downloads or model calls.

Never imports or executes target project code. Hashes source on both passes;
reports one cold/warm sample, current RSS and Python allocation peaks, not SLOs.
Use a trusted checkout and an explicit output file outside that checkout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import tracemalloc
from pathlib import Path

from resource_scale_smoke import rss_bytes

from atomic_flow.project_resources import ProjectLimits
from atomic_flow.source_lock import source_mutation_lease
from atomic_flow.tree.builder import build_tree


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    with tempfile.TemporaryFile() as output:
        subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
                        "-c", "core.autocrlf=false", *args], cwd=root, env=env,
                       stdout=output, stderr=subprocess.DEVNULL, timeout=30, check=True)
        output.seek(0)
        data = output.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Git provenance output exceeds probe limit")
    return data.decode("utf-8").strip()


def graph(tree):
    return {n["node_id"]: (n["callees"], n["reference_ids"], n["fan_in"], n["fan_out"])
            for n in tree["nodes"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-wall-seconds", type=float, default=160)
    args = parser.parse_args()
    root, output = args.project.resolve(strict=True), args.output.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit) or not 1 <= args.max_wall_seconds <= 600:
        raise ValueError("full source SHA and bounded cooperative deadline required")
    if output.is_relative_to(root) or output.exists():
        raise ValueError("fresh output path outside measured source is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"source_commit": args.expected_commit, "model_calls": 0, "source_code_executed": False,
              "samples_per_phase": 1, "status": "not_completed", "phases": [],
              "deadline_kind": "cooperative_not_hard_process_kill", "max_wall_seconds": args.max_wall_seconds}
    started = time.perf_counter()

    def check():
        if time.perf_counter() - started >= args.max_wall_seconds:
            raise TimeoutError("probe cooperative deadline")

    def measured(previous):
        before = rss_bytes()
        tracemalloc.start()
        phase_started = time.perf_counter()
        try:
            tree = build_tree(root, previous=previous, limits=ProjectLimits(), cancel_check=check)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return tree, {"phase": "cold" if previous is None else "warm",
                      "wall_seconds": time.perf_counter() - phase_started, "stats": tree["stats"],
                      "files": tree["files"], "nodes": len(tree["nodes"]), "fingerprint": tree["fingerprint"],
                      "rss_before_bytes": before, "rss_after_bytes": rss_bytes(), "tracemalloc_peak_bytes": peak}

    try:
        if git(root, "rev-parse", "HEAD") != args.expected_commit:
            raise ValueError("source HEAD differs from pinned commit")
        if git(root, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"):
            raise ValueError("tracked source differs from pinned commit")
        untracked = git(root, "ls-files", "--others").splitlines()
        if any(not (p == ".atomic-flow-lock" or p.startswith(".atomic_flow/")) for p in untracked):
            raise ValueError("untracked files would contaminate pinned source measurement")
        with source_mutation_lease(root):
            cold, row = measured(None)
            report["phases"].append(row)
            warm, row = measured(cold)
            report["phases"].append(row)
        report["graph_parity"] = graph(cold) == graph(warm)
        report["source_fingerprint_parity"] = cold["fingerprint"] == warm["fingerprint"]
        if not report["graph_parity"] or not report["source_fingerprint_parity"]:
            raise ValueError("source or graph changed across measured passes")
        if warm["stats"]["parsed"] != 0 or warm["stats"]["reused"] != cold["files"]:
            raise ValueError("warm parse reuse incomplete")
        if (git(root, "rev-parse", "HEAD") != args.expected_commit
                or git(root, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--")):
            raise ValueError("pinned source changed during measurement")
        report["status"] = "passed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        report["total_wall_seconds"] = time.perf_counter() - started
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
