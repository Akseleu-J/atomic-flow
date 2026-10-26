"""Build and health-check a locked HTTPBin image on Linux; no target tests/models.

The final printed evaluator command is intentionally not executed here. The
evaluator must independently admit the immutable image and lifecycle receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import time
import uuid
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def checked_context(context, task):
    context = context.resolve()
    if not re.fullmatch(r"psf__requests-[0-9]+", task):
        raise ValueError("one exact Requests task required")
    receipt = json.loads((context / "preparation.json").read_text())
    lock = json.loads((context / "fixture-lock.build.json").read_text())
    base = receipt["base_image"]
    expected = "swebench/sweb.eval.x86_64.psf_1776_requests-" + task.rsplit("-", 1)[1] + "@sha256:"
    if (not base.startswith(expected) or not re.fullmatch(r"swebench/[A-Za-z0-9_.-]+@sha256:[0-9a-f]{64}", base)
            or base != lock.get("base_image")):
        raise ValueError("fixture base image belongs to a different task or is mutable")
    if sha((context / "Dockerfile").read_bytes()) != receipt["dockerfile_sha256"]:
        raise ValueError("Dockerfile differs from prepared bytes")
    if sha((context / "requirements.lock").read_bytes()) != receipt["dependency_lock_sha256"]:
        raise ValueError("fixture dependency lock differs from prepared bytes")
    for name, digest in lock["fixture_sources_sha256"].items():
        if Path(name).name != name or sha((context / name).read_bytes()) != digest:
            raise ValueError("fixture source differs from reviewed bytes")
    for name, digest in lock["wheel_hashes"].items():
        if Path(name).name != name or sha((context / "wheels" / name).read_bytes()) != digest:
            raise ValueError("fixture wheel differs from reviewed bytes")
    return context, receipt, lock


def build_and_probe(context, output, task):
    if platform.system() != "Linux":
        raise ValueError("build/probe requires an approved Docker-capable Linux worker")
    if output.exists():
        raise ValueError("fresh build/probe output required")
    context, prepared, build_lock = checked_context(context, task)
    output.mkdir(parents=True)

    def command(args, *, timeout=60, log=None):
        if log:
            with (output / log).open("wb") as stream:
                process = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            if process.returncode:
                raise RuntimeError("trusted fixture command failed; see " + log)
            return ""
        process = subprocess.run(args, capture_output=True, timeout=timeout)
        if process.returncode:
            raise RuntimeError("fixture command failed: " + args[0] + ": " + process.stderr.decode(errors="replace")[:500])
        return process.stdout.decode().strip()

    report = {"status": "build_or_probe_failed", "task": task, "base_image": prepared["base_image"],
              "model_calls": 0, "target_tests_executed": False, "provider_credentials": "none"}
    name = "p07-httpbin-probe-" + uuid.uuid4().hex[:12]
    created = False
    try:
        # Only trusted image/wheel provisioning may contact registries. Every
        # Dockerfile RUN and the resulting test container retain network=none.
        command(["docker", "pull", prepared["base_image"]], timeout=600, log="base-pull.log")
        tag = "p07-httpbin-fixture:" + uuid.uuid4().hex
        command(["docker", "build", "--network", "none", "--tag", tag,
                 "--label", "atomic.p07.fixture.base=" + prepared["base_image"], str(context)], timeout=1200, log="build.log")
        image = json.loads(command(["docker", "image", "inspect", tag]))[0]
        image_id = image["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("immutable built image identity unavailable")
        report.update(image_id=image_id, image_inspect=image, build_context_sha256=sha((context / "fixture-lock.build.json").read_bytes()))
        arguments = ["docker", "create", "--name", name, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--memory", "6g", "--cpus", "2", "--pids-limit", "256", *prepared["docker_runtime_additions"], image_id,
                     "/opt/p07-httpbin/bin/python", "/opt/p07-fixture/p07_httpbin_fixture.py", "--serve",
                     "--expected-lock", "/opt/p07-fixture/fixture-lock.json", "--certificate", "/opt/p07-fixture/httpbin-cert.pem",
                     "--test-key", "/opt/p07-fixture/httpbin-key.pem", "--output", "/tmp/p07-httpbin-ready.json"]
        command(arguments)
        created = True
        inspect = json.loads(command(["docker", "inspect", name]))[0]
        host = inspect["HostConfig"]
        if (host["NetworkMode"] != "none" or inspect["Mounts"] or host["CapDrop"] != ["ALL"]
                or "no-new-privileges" not in host["SecurityOpt"] or host["PidsLimit"] != 256):
            raise ValueError("probe container isolation contract mismatch")
        report["container_isolation"] = {"network": "none", "host_mounts": [], "caps": "ALL dropped",
            "no_new_privileges": True, "memory_bytes": host["Memory"], "pids_limit": host["PidsLimit"],
            "extra_hosts": host["ExtraHosts"], "sysctls": host["Sysctls"]}
        command(["docker", "start", name])
        deadline = time.monotonic() + 40
        ready = None
        while time.monotonic() < deadline:
            result = subprocess.run(["docker", "exec", name, "cat", "/tmp/p07-httpbin-ready.json"], capture_output=True, timeout=5)
            if result.returncode == 0:
                ready = json.loads(result.stdout)
                break
            current = json.loads(command(["docker", "inspect", name]))[0]
            if not current["State"]["Running"]:
                raise RuntimeError("real fixture process terminated before health receipt")
            time.sleep(.25)
        if ready is None or ready.get("status") != "real_httpbin_started" or ready.get("probes") != {"http": 204, "https": 204}:
            raise ValueError("real HTTPBin health did not become ready")
        runtime_lock = json.loads(command(["docker", "exec", name, "cat", "/opt/p07-fixture/fixture-lock.json"]))
        if (runtime_lock["packages"] != build_lock["packages"]
                or runtime_lock["fixture_sources_sha256"] != build_lock["fixture_sources_sha256"]
                or ready.get("fixture_sources_sha256") != build_lock["fixture_sources_sha256"]
                or ready.get("certificate_sha256") != runtime_lock.get("certificate_sha256")):
            raise ValueError("built runtime identity differs from reviewed fixture lock")
        save(output / "runtime-lock.json", runtime_lock)
        save(output / "health.json", ready)
        report.update(status="image_built_and_network_none_fixture_health_verified", health=ready,
            evaluator_command=["python", "tools/evaluation_p07_isolated.py", "--task", task,
                               "--httpbin-image", image_id, "--output", str(output.parent / (output.name + "-calibration"))],
            evaluator_status="not_run_requires_evaluator_adapter")
    finally:
        if created:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, timeout=15)
            (output / "fixture-service.log").write_bytes(logs.stdout + logs.stderr)
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            report["cleanup_exit"] = cleanup.returncode
            if cleanup.returncode:
                report.update(status="cleanup_uncertain", cleanup_error=cleanup.stderr.decode(errors="replace")[:500])
        save(output / "build-probe.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", default="psf__requests-3362")
    print(json.dumps(build_and_probe(**vars(parser.parse_args())), indent=2))
