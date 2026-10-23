"""CPU-only Colab/Linux validation. Never mounts Drive or accesses model credentials."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-docker", action="store_true")
    parser.add_argument("--require-docker", action="store_true", help="Fail if real container checks cannot run (CI).")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "colab-results"
    output.mkdir(exist_ok=True)
    results = {"platform": platform.platform(), "python": sys.version, "gpu_requested": False,
               "model_calls": 0, "container": "UNAVAILABLE"}
    if (root / "bundle-manifest.json").exists():
        results["source"] = json.loads((root / "bundle-manifest.json").read_text())

    def run(label, command, timeout=180, env=None):
        with (output / f"{label}.txt").open("w") as log:
            try:
                proc = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                      timeout=timeout, env=env)
                code = proc.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.write(str(exc))
                code = -1
        print(f"{label}: exit={code}", flush=True)
        return code

    env = dict(os.environ, ATOMIC_FLOW_EXECUTION_MODE="local", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("ATOMIC_FLOW_TEST_DOCKER", None)
    results["tests_exit"] = run("pytest", [sys.executable, "-m", "pytest", "-q", "tests"], env=env)
    extras = sorted(str(p.relative_to(root)) for p in root.glob("test*v3*.py"))
    if extras:
        results["extra_tests_exit"] = run("pytest-extra", [sys.executable, "-m", "pytest", "-q", *extras], env=env)
    results["compile_exit"] = run("compile", [sys.executable, "-m", "compileall", "-q", "src"])
    if args.install_docker and not shutil.which("docker"):
        if sys.platform == "linux" and os.geteuid() == 0:
            if run("apt-update", ["apt-get", "update"], timeout=240) == 0:
                run("apt-docker", ["apt-get", "install", "-y", "docker.io"], timeout=300)
    if shutil.which("docker"):
        available = run("docker-info", ["docker", "info"], timeout=20) == 0
        if not available and args.install_docker and sys.platform == "linux" and os.geteuid() == 0:
            # Standard daemon only; no privileged nesting or security bypasses.
            with (output / "dockerd.txt").open("w") as log:
                daemon = subprocess.Popen(["dockerd", "--host=unix:///var/run/docker.sock"],
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            for _ in range(15):
                if daemon.poll() is not None:
                    break
                if run("docker-info", ["docker", "info"], timeout=10) == 0:
                    available = True
                    break
                time.sleep(1)
        if available:
            built = run("docker-build", ["docker", "build", "-t", "atomic-flow-checks:local",
                                         "-f", "tools/Dockerfile.checks", "."], timeout=600)
            if built == 0:
                image = subprocess.run(["docker", "image", "inspect", "atomic-flow-checks:local", "--format", "{{.Id}}"],
                                       capture_output=True, text=True, timeout=15)
                results["image_id"] = image.stdout.strip() if image.returncode == 0 else "unavailable"
                env["ATOMIC_FLOW_TEST_DOCKER"] = "1"
                code = run("container-boundaries", [sys.executable, "-m", "pytest", "-q",
                           "tests/test_execution.py::test_real_container_boundaries",
                           "tests/test_container_integration.py",
                           "tests/test_execution_profiles.py::test_real_read_only_workspace_profile",
                           "tests/test_remote_execution.py::test_real_remote_protocol_docker_job_and_cancel",
                           "tests/test_execution_confidentiality.py::test_real_docker_checker_rejects_shadow_module_and_protects_output",
                           "tests/test_jobs.py"], env=env)
                results["container"] = "PASS" if code == 0 else "FAIL"
            else:
                results["container"] = "IMAGE_BUILD_FAILED"
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    shutil.make_archive(str(root / "colab-results"), "zip", output)
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    if results["container"] == "UNAVAILABLE":
        print("Container isolation NOT VERIFIED in this runtime. See Docker logs; use a Docker-capable Linux VM.")
    return int((args.require_docker and results["container"] != "PASS")
               or results["tests_exit"] != 0 or results["compile_exit"] != 0
               or results.get("extra_tests_exit", 0) != 0 or results["container"] in {"FAIL", "IMAGE_BUILD_FAILED"})


if __name__ == "__main__":
    raise SystemExit(main())
