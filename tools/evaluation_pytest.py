"""Trusted pytest oracle adapter: run via pinned interpreter -I outside checkout.

Host config supplies immutable required test IDs and regression paths. This is
an offline evaluator command, never a model tool or provider credential holder.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pytest


class Results:
    def __init__(self):
        self.tests = {}

    def pytest_runtest_logreport(self, report):
        previous = self.tests.get(report.nodeid)
        if report.failed:
            self.tests[report.nodeid] = "fail" if report.when == "call" else "error"
        elif report.skipped:
            self.tests[report.nodeid] = "error"
        elif report.when == "call" and previous not in {"fail", "error"}:
            self.tests[report.nodeid] = "pass"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--tests", required=True, help="trusted external JSON file containing pytest targets")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    targets = json.loads(Path(args.tests).read_text())
    if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or t.startswith("-") for t in targets):
        raise ValueError("expected explicit test target list")
    # Import pytest from installed host environment before project import paths.
    os.chdir(root)
    sys.path[:0] = [str(root / "src"), str(root)]
    results = Results()
    code = pytest.main(["--override-ini", "addopts=", "-p", "no:cacheprovider", "-q", *targets], plugins=[results])
    report = {"tests": results.tests, "regressions_passed": code == 0 and bool(results.tests), "exit_code": int(code)}
    Path(args.report).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
