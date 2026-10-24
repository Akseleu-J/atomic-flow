"""Bounded offline synthetic snapshot/journal measurements; never calls models.

Run from an installed package or with PYTHONPATH=src. Output/work roots are explicit;
workspaces are retained for inspection. This is a smoke experiment, not acceptance
of large production repositories. Journal read-byte counts reveal current work.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

from atomic_flow.agents.changeset import ChangeSet
from atomic_flow.project_resources import ProjectLimits
from atomic_flow.sessions import SessionJournal


def rss_bytes():
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        values = Counters()
        values.cb = ctypes.sizeof(values)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(values), values.cb):
            return values.WorkingSetSize
        return None
    try:
        # Linux current RSS; deliberately do not mislabel cumulative ru_maxrss.
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        return None


@contextmanager
def count_reads(target):
    original = Path.open
    counts = {"read_calls": 0, "read_bytes": 0}

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def _read(self, method, *args):
            data = getattr(self.handle, method)(*args)
            counts["read_calls"] += 1
            counts["read_bytes"] += len(data)
            return data

        def read(self, *args):
            return self._read("read", *args)

        def readline(self, *args):
            return self._read("readline", *args)

    def opened(path, mode="r", *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        return Reader(handle) if path == target and mode == "rb" else handle

    Path.open = opened
    try:
        yield counts
    finally:
        Path.open = original


def measure(root, label, count):
    directory = root / label
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "source"
    source.mkdir()
    os.environ["ATOMIC_FLOW_HOME"] = str(directory / "state")
    content = ("value = 1\n" + "# synthetic\n" * 341).encode()
    for index in range(count):
        folder = source / f"group{index // 32:03}"
        folder.mkdir(exist_ok=True)
        (folder / f"item{index:05}.py").write_bytes(content)
    tracemalloc.start()
    started = time.perf_counter()
    checks = [0]

    def checkpoint():
        checks[0] += 1

    change = ChangeSet.create(source, limits=ProjectLimits(), cancel_check=checkpoint)
    snapshot_seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    journal = SessionJournal.create(source, session_id="scale_" + label)
    journal.read()  # Establish validated prefix before measured appends.
    timings = []
    with count_reads(journal.path) as counters:
        for index in range(count):
            started = time.perf_counter()
            journal.append("question", {"text": "synthetic " + "x" * 64, "index": index})
            timings.append(time.perf_counter() - started)
    assert len(change.state["baseline"]) == count
    assert len(journal.read().events) == count + 1
    return {"label": label, "files": count, "source_bytes": count * len(content),
            "snapshot_seconds": snapshot_seconds, "snapshot_tracemalloc_peak_bytes": peak,
            "cancel_check_calls": checks[0], "rss_after_bytes": rss_bytes(),
            "journal_appends": count, "journal_bytes": journal.path.stat().st_size,
            "journal_append_median_seconds": statistics.median(timings),
            "journal_append_max_seconds": max(timings), **counters}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": 1, "model_calls": 0, "synthetic": True,
              "journal_quadratic_work_fixed": False, "cases": []}
    for label, count in (("small", 16), ("medium", 64), ("large", 256)):
        result["cases"].append(measure(args.work_root.resolve(), label, count))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
