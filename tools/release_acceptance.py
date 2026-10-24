"""Build/install wheel in an isolated external venv and retain exact environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", choices=("core", "api"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(output)], cwd=repo, env=env, check=True)
    wheel, = output.glob("*.whl")
    with tempfile.TemporaryDirectory(prefix="atomic-wheel-venv-") as tmp:
        subprocess.run([sys.executable, "-m", "venv", tmp], check=True)
        python = Path(tmp) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        target = str(wheel) + ("[api]" if args.extra == "api" else "")
        command = [str(python), "-m", "pip", "install", "--report", str(output / "install-report.json"), target]
        if args.extra == "api":
            command += ["httpx==0.28.1"]
        subprocess.run(command, cwd=tmp, env=env, check=True)
        frozen = subprocess.check_output([str(python), "-m", "pip", "freeze", "--all"], env=env, text=True)
        (output / "installed-freeze.txt").write_text(frozen, encoding="utf-8")
        smoke = [str(python), str(repo / "tools/wheel_smoke.py"), "--project-root", str(repo)]
        if args.extra == "api":
            smoke += ["--require-api"]
        result = subprocess.run(smoke, cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (output / "smoke.txt").write_text(result.stdout, encoding="utf-8")
        print(result.stdout)
        result.check_returncode()
    manifest = {"wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "python": sys.version, "platform": sys.platform, "extra": args.extra,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
