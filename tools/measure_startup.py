"""Fresh-process startup/peak RSS evidence; no servers, credentials or inference."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import json, sys, time
start = time.perf_counter()
from atomic_flow.sdk import components
catalog = components()
deferred = {name: name in sys.modules for name in (
    'fastapi', 'torch', 'transformers', 'kaggle', 'atomic_flow.integrations.mcp', 'atomic_flow.lsp')}
if sys.argv[1] != 'catalog':
    catalog.load(sys.argv[1])
elapsed = time.perf_counter() - start
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)] + [
            (name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
            'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not query(get_process(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    peak = counters.PeakWorkingSetSize
else:
    import resource
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = raw if sys.platform == 'darwin' else raw * 1024
print(json.dumps({'seconds':elapsed,'peak_rss_bytes':peak,'before_optional_load':deferred,
                  'loaded_components':[name for name,row in catalog.diagnostics().items() if row['state'] == 'loaded']}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.samples <= 5:
        parser.error("samples must be 1-5")
    report = {"schema_version": 1, "platform": platform.platform(), "python": sys.version,
              "scope": "Fresh interpreter SDK/catalog imports and explicit symbol loading; no service startup or inference",
              "model_calls": 0, "measurements": {}}
    for component in ("catalog", "lsp", "mcp"):
        samples = []
        for _ in range(args.samples):
            proc = subprocess.run([sys.executable, "-c", CHILD, component], capture_output=True, text=True, check=True, timeout=30)
            sample = json.loads(proc.stdout)
            if any(sample["before_optional_load"].values()):
                raise RuntimeError("optional component imported before explicit load")
            samples.append(sample)
        report["measurements"][component] = {"samples": samples,
            "median_seconds": statistics.median(row["seconds"] for row in samples),
            "median_peak_rss_bytes": statistics.median(row["peak_rss_bytes"] for row in samples)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: {name: value for name, value in row.items() if name != "samples"}
                      for key, row in report["measurements"].items()}))


if __name__ == "__main__":
    main()
