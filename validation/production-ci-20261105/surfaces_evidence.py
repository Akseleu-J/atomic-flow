"""Retain host image provenance and check the real af-exec CI receipt.

Docker runs only on the CI host. These observations are not remote attestation
or semantic verification; the native receipt must retain not_assessed.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def docker(*args):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=30
    ).stdout


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def immutable(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value)


def provision(destination, image):
    destination.mkdir(parents=True, exist_ok=True)
    # Record actual availability without promoting it to Firecracker acceptance.
    write(destination / "firecracker-status.json", {
        "kvm_device_present": Path("/dev/kvm").exists(),
        "real_acceptance": "not_run",
        "gate": "requires a suitable self-hosted Linux/KVM runner and separate acceptance",
    })
    inspection = json.loads(docker("image", "inspect", image))
    require(len(inspection) == 1, "expected one provisioned image")
    item = inspection[0]
    image_id = item.get("Id")
    require(immutable(image_id), "provisioned image has no immutable sha256 ID")
    require(item.get("RepoDigests"), "pulled image has no recorded repository digests")
    write(destination / "image-inspect.json", inspection)
    write(destination / "image-identity.json", {
        "requested_tag": image, "image_id": image_id,
        "repo_digests": item["RepoDigests"],
    })
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env_file:
        env_file.write(f"AF_EXEC_TEST_IMAGE={image_id}\n")


def verify(destination):
    log = (destination / "acceptance.log").read_text(encoding="utf-8")
    # Libtest writes the test name before stdout on the same line.
    marker = "AF_DOCKER_RECEIPT="
    require(log.count(marker) == 1, "expected exactly one actual native Docker receipt")
    status = json.loads(log.split(marker, 1)[1].splitlines()[0])
    require(re.search(
        r"test result: ok\. 1 passed; 0 failed; 0 ignored; 0 measured; \d+ filtered out;",
        log,
    ), "expected exactly one passing, non-ignored acceptance test")
    require(status.get("status") == "succeeded", "Docker job did not succeed")
    require(status.get("exit_code") == 0, "Docker target exit was not zero")
    require(status.get("cleanup_confirmed") is True, "Docker cleanup was not witnessed")
    require(status.get("verification") == "not_assessed", "unexpected semantic verification claim")
    require(status.get("reconcile_required") is False, "Docker job still needs reconciliation")
    for field in ("timed_out", "stdout_truncated", "stderr_truncated"):
        require(status.get(field) is False, f"incomplete Docker receipt: {field}")
    sandbox = status.get("sandbox", {})
    require(sandbox.get("driver") == "docker", "wrong execution boundary")
    for field in ("network_none", "root_read_only", "cleanup_confirmed"):
        require(sandbox.get(field) is True, f"missing Docker posture: {field}")
    require("shared kernel" in sandbox.get("isolation", ""), "missing Docker isolation limit")
    require(sandbox.get("verification") == "not_assessed", "unexpected sandbox verification")
    provisioned = json.loads((destination / "image-identity.json").read_text(encoding="utf-8"))
    require(sandbox.get("image_id") == provisioned["image_id"], "execution image differs from provisioned image")
    require(immutable(sandbox.get("image_id")), "receipt lacks immutable image")
    container_id = sandbox.get("container_id")
    require(isinstance(container_id, str) and re.fullmatch(r"[0-9a-f]{64}", container_id), "missing immutable container ID")
    observations = sandbox.get("observations", {})
    for label in ("image", "create", "before", "target", "after", "remove", "absence"):
        observed = observations.get(label, {})
        require(observed.get("exit_code") == 0, f"failed/missing Docker observation: {label}")
        require(observed.get("client_cleanup_confirmed") is True, f"unsettled Docker client: {label}")
        for field in ("timed_out", "stdout_truncated", "stderr_truncated"):
            require(observed.get(field) is False, f"incomplete observation: {label}/{field}")
    require(observations["absence"].get("stdout") == "", "native receipt does not witness absence")
    require("sandbox-live" in observations["target"].get("stdout", ""), "target witness missing")
    remaining = docker("container", "ls", "-a", "--no-trunc", "--format", "{{.ID}}")
    require(container_id not in remaining.splitlines(), "test container remains on host")
    write(destination / "receipt.json", status)
    write(destination / "receipt-validation.json", {
        "receipt_valid": True, "container_id": container_id,
        "image_id": sandbox["image_id"], "host_container_absence_confirmed": True,
        "verification": "not_assessed",
    })


if __name__ == "__main__":
    require(len(sys.argv) >= 3, "usage: surfaces_evidence.py provision|verify DEST [IMAGE]")
    mode, destination = sys.argv[1], Path(sys.argv[2])
    if mode == "provision":
        require(len(sys.argv) == 4, "provision requires the host image tag")
        provision(destination, sys.argv[3])
    elif mode == "verify":
        require(len(sys.argv) == 3, "verify takes only the evidence directory")
        verify(destination)
    else:
        raise SystemExit("unknown mode")
