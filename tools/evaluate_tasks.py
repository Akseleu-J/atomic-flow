"""Calibrate the historical task graders; never calls a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from atomic_flow.config import ExecutionConfig
from atomic_flow.evaluation import calibrate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/pilot/tasks.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execution", choices=("docker", "local"), default="docker",
                        help="local is only for these trusted historical revisions, without sandbox isolation")
    args = parser.parse_args()
    report = calibrate(Path.cwd(), args.manifest, execution=ExecutionConfig(mode=args.execution))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("kind", "calibrated", "independent_changes", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
