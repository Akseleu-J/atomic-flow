"""W5 installed-wheel smoke: prove the package works from site-packages, not the checkout.

Run with the CLEAN venv's interpreter (the one that installed the wheel), optionally
from inside the checkout: ``<clean-venv-python> tools/wheel_smoke.py --project-root <repo>``.
Exit code 0 means every check passed. No model calls, no network.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sysconfig
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=None,
                        help="checkout root used only to verify it does NOT shadow the installed package")
    parser.add_argument("--require-api", action="store_true", help="fail if the API extra is unavailable")
    args = parser.parse_args()

    import atomic_flow

    location = Path(atomic_flow.__file__).resolve()
    site_packages = any(part.name == "site-packages" for part in location.parents)
    assert site_packages, f"atomic_flow must resolve inside site-packages, got {location}"
    if args.project_root is not None:
        checkout = args.project_root.resolve()
        assert not location.is_relative_to(checkout), \
            f"checkout source is shadowing the installed package: {location}"
    print(f"import location: {location}")

    # Packaged fixtures ship as package data and must be readable from the wheel.
    from importlib.resources import files

    cases = files("atomic_flow.benchmark_data") / "cases.json"
    assert cases.is_file(), f"packaged fixtures missing: {cases}"
    print(f"packaged fixtures: cases.json ({cases.stat().st_size} bytes)")

    # Core journey without a model backend: index a synthetic project, save and reload.
    from atomic_flow.tree.builder import build_tree, load_tree, save_tree
    with tempfile.TemporaryDirectory(prefix="atomic-wheel-") as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        (project / "sample.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        tree = build_tree(project)
        assert tree["files"] == 1, tree["stats"]
        save_tree(project, tree)
        reloaded = load_tree(project)
        assert reloaded["fingerprint"] == tree["fingerprint"]
        print("tree build/save/load: ok")

    # SDK surface imports from the installed package.
    from atomic_flow.sdk import start_answer  # noqa: F401
    print("sdk import: ok")

    # API surface when the extra is installed.
    if importlib.util.find_spec("fastapi") is None:
        assert not args.require_api, "API extra is required for this acceptance run"
        print("api extra: not installed (skipped)")
    else:
        # Import errors inside the installed package must fail acceptance rather
        # than being misreported as an optional extra that was not installed.
        from fastapi.testclient import TestClient

        from atomic_flow.api.server import create_app
        with TestClient(create_app()) as client:
            assert client.get("/health").status_code == 200
            ready = client.get("/ready")
            assert ready.status_code == 200 and ready.json()["ready"] is True
        print("api health/ready: ok")

    # Console entry point of the installed distribution.
    console = Path(sysconfig.get_path("scripts")) / ("atomic.exe" if os.name == "nt" else "atomic")
    assert console.is_file(), f"installed console entry point missing: {console}"
    cli = [str(console), "--help"]
    result = subprocess.run(cli, capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
    assert result.returncode == 0, result.stderr[:500]
    print("cli --help: ok")

    print("wheel smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
