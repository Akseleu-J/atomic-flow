"""Capture tracked runtime/contract bytes for exact post-download Git-object audit."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
paths = subprocess.check_output(["git", "ls-files", "-z", "--", "rust", "ts/af-client", 
    "docs/PRODUCTION_STATUS_2026-11-04.json", "validation/production-20261104/check_registry.py",
    "validation/production-20261104/capture_source.py", "validation/production-ci-20261105", "validation/production-20261104/sdk-native-consumer.mjs", ".github/workflows/studio.yml", ".github/workflows/rust.yml", ".github/workflows/surfaces.yml",
    "validation/zcode-studio-20261104/http_smoke.py", "validation/zcode-studio-20261104/ui-tests.mjs"], cwd=root).decode("utf-8").split("\0")
source = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in paths if name}
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "validation/production-20261104/local-source.json"
output.write_text(json.dumps(source, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"Captured {len(source)} tracked source files")
