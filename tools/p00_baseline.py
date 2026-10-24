"""P00 baseline card: reproducible offline latency/RSS/disk indicators.

Captures source/config/environment identity and measures cold import, tree
build, save/load and a retrieval sample on a reviewed checkout. Model calls=0;
this is an infrastructure baseline, not an SLO or a quality claim. Repeat runs
differ by host load; the card records N samples and dispersion, never a single
point. Stdlib only.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPEATS = 5


def rss_mib() -> float:
    if platform.system() == "Windows":
        class Counter(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                        ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                        ("quota_peak_paged", ctypes.c_size_t), ("quota_paged", ctypes.c_size_t),
                        ("quota_peak_non_paged", ctypes.c_size_t), ("quota_non_paged", ctypes.c_size_t),
                        ("page_file_usage", ctypes.c_size_t), ("peak_page_file", ctypes.c_size_t)]
        counter = Counter()
        counter.cb = ctypes.sizeof(Counter)
        kernel32 = ctypes.windll.kernel32
        info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if info is None:
            info = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        info.restype = ctypes.c_ulong
        if info(kernel32.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
            return counter.working_set / (1 << 20)
        return -1.0
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:
        return -1.0


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def samples(function, *, repeats: int):
    rows = []
    for _ in range(repeats):
        started = time.perf_counter()
        value = function()
        rows.append({"seconds": round(time.perf_counter() - started, 4), "value": value})
    seconds = sorted(row["seconds"] for row in rows)
    return {"n": repeats, "min_s": seconds[0], "median_s": seconds[len(seconds) // 2],
            "max_s": seconds[-1], "rows": rows}


def git(*args: str) -> str:
    engine = os.environ.get("ATOMIC_GIT", "git")
    result = subprocess.run([engine, *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[:200])
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 50:
        raise ValueError("repeats must stay within 1..50")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("fresh output file required; never overwrite a baseline card")

    head = git("rev-parse", "HEAD")
    dirty = git("status", "--short")
    config_digests = {name: digest_file(ROOT / name) for name in ("pyproject.toml",)
                      if (ROOT / name).exists()}
    card = {"schema": "p00-baseline/1", "model_calls": 0, "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": {"head": head, "dirty_files": dirty.splitlines() if dirty else [], "branch": git("branch", "--show-current")},
            "config_digests": config_digests,
            "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                            "machine": platform.machine(), "cpu_count": os.cpu_count()},
            "indicators": {}, "notes": ["Host-load dependent indicators; not an SLO.",
                                        "Cold import runs in a fresh subprocess each repeat."]}

    # Cold import of the package in a fresh interpreter (import cost only).
    code = "import time;started=time.perf_counter();import atomic_flow;print(round(time.perf_counter()-started,4))"
    def cold_import():
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=120)
        return float(result.stdout.strip() or -1)
    card["indicators"]["cold_import"] = samples(cold_import, repeats=args.repeats)

    # Tree build/save/load and a retrieval sample in-process.
    sys.path.insert(0, str(ROOT / "src"))
    from atomic_flow.tree.builder import build_tree, load_tree, save_tree  # noqa: E402
    card["indicators"]["tree_build_self"] = samples(lambda: len(build_tree(ROOT, None)["nodes"]),
                                                    repeats=max(1, args.repeats // 5))
    import tempfile

    from atomic_flow.tree.builder import _tree_path
    with tempfile.TemporaryDirectory(prefix="p00-tree-"):
        marker = _tree_path(ROOT)
        def save_measure():
            tree = build_tree(ROOT, None)
            path = save_tree(ROOT, tree)
            return path.stat().st_size
        card["indicators"]["tree_save_bytes"] = samples(save_measure, repeats=1)
        card["indicators"]["tree_load"] = samples(lambda: len(load_tree(ROOT)["nodes"]), repeats=max(1, args.repeats // 5))
        if marker.exists():
            card["indicators"]["tree_artifact_bytes"] = marker.stat().st_size
            card["indicators"]["tree_artifact_sha256"] = digest_file(marker)
            marker.unlink(missing_ok=True)

    card["indicators"]["process_rss_mib_final"] = round(rss_mib(), 2)
    output.write_text(json.dumps(card, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"head": head, "output": str(output), "rss_mib": card["indicators"]["process_rss_mib_final"],
                      "cold_import_median_s": card["indicators"]["cold_import"]["median_s"],
                      "tree_build_median_s": card["indicators"]["tree_build_self"]["median_s"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
