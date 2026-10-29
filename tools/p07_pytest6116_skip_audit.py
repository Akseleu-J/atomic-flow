"""Summarize a retained pytest6116 stdout receipt without admitting any skips."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

EXPECTED_SHA256 = "7f2878d554df55e7b34280c8fb8d7c5a75ddc99ed1640f4cd26992e20d56d2af"
EXPECTED_COMMIT = "78fbf649fd58bbdc7b51230412c0ebe2ec5df7d8"
SKIP = re.compile(r"^SKIPPED \[(\d+)\] (.+?): (.+)$")
SUMMARY = re.compile(r"^(\d+) failed, (\d+) passed, (\d+) skipped, (\d+) xfailed, .* in .+$")


def summarize(raw: bytes, *, expected_sha256: str = EXPECTED_SHA256):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("retained stdout digest differs")
    lines = raw.decode("utf-8").splitlines()
    summaries = [match for line in lines if (match := SUMMARY.fullmatch(line))]
    markers = [line.removeprefix("P07_PUBLIC_COLLECTION=") for line in lines
               if line.startswith("P07_PUBLIC_COLLECTION=")]
    if len(summaries) != 1 or len(markers) != 1:
        raise ValueError("one native summary and structured marker required")
    failed, passed, skipped, xfailed = map(int, summaries[0].groups())
    marker = json.loads(markers[0])
    items = marker.get("items", [])
    if (not marker.get("finished") or marker.get("exit_code") != 1
            or len(items) != 2617 or marker.get("completed") != len(items)
            or len({item["nodeid"] for item in items}) != len(items)
            or (failed, passed, skipped, xfailed) != (3, 2489, 115, 10)):
        raise ValueError("native result or execution inventory differs")
    rows = []
    categories = Counter()
    dependencies = Counter()
    for line in lines:
        match = SKIP.fullmatch(line)
        if not match:
            continue
        count, location, reason = match.groups()
        count = int(count)
        dep = re.match(r"could not import '([^']+)'", reason)
        if dep:
            category = "missing_python_dependency"
            dependencies[dep.group(1)] += count
        elif reason == "xdist plugin not installed":
            category = "missing_python_dependency"
            dependencies["xdist"] += count
        elif reason == "argcomplete not available":
            category = "missing_python_dependency"
            dependencies["argcomplete"] += count
        elif reason.startswith("could not run 'lsof'"):
            category = "missing_system_tool"
        elif reason in ("only py3.6+ on windows", "win only", "only relevant for case insensitive file systems"):
            category = "platform_condition"
        elif reason == "This test should be enabled again before pytest 6.0 is released":
            category = "source_disabled"
        else:
            category = "unclassified"
        categories[category] += count
        rows.append({"count": count, "location": location, "reason": reason, "category": category})
    if sum(row["count"] for row in rows) != skipped:
        raise ValueError("native skip lines do not account for all skips")
    return {"scope": "diagnostic grouping only; no skip admitted", "tested_commit": EXPECTED_COMMIT,
            "stdout_sha256": digest, "native_counts": {"failed": failed, "passed": passed,
            "skipped": skipped, "xfailed": xfailed}, "completed": marker["completed"],
            "categories": dict(sorted(categories.items())), "missing_python_dependencies": dict(sorted(dependencies.items())),
            "skip_groups": rows, "ordinary_acceptance": False, "provider_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stdout", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = summarize(args.stdout.read_bytes())
    with args.output.open("x", encoding="utf8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
