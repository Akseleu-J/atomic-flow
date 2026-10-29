"""Exercise the generated private pytest bootstrap on the real Python 2.7 image.

Only synthetic source is mounted. This does not qualify a Requests fixture,
historical task, TaskRunner, provider, or independent oracle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import p07_python27_support as legacy
import p07_requests2674_python27_image as provision

from atomic_flow.agents.execution import CommandResult
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_fixture import REQUESTS2674_OPTIONAL_OUTCOMES, PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_private_pytest import private_pytest_args, private_pytest_receipt
from atomic_flow.check_runtime import RuntimeIdentity
from atomic_flow.check_spec import CheckResources, CheckSpec

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "/usr/bin/docker"
NONCE = "6" * 32


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(receipt_dir: Path, output: Path):
    if os.geteuid() != 0 or output.exists():
        raise ValueError("root Linux and a fresh output directory are required")
    output.mkdir(parents=True)
    raw, admitted = provision.admit_receipt(receipt_dir)
    if admitted["task"] != "psf__requests-2674":
        raise ValueError("exact 2674 runtime required")
    profile, target, _, _ = provision.frozen_task(admitted["task"])
    helpers = reviewed_helper_inventory("2674-v2")
    if any(sha((ROOT / name).read_bytes()) != digest for name, digest in helpers):
        raise ValueError("reviewed helper bytes changed")
    installed = tuple(sorted((name, digest) for name, digest in admitted["installed_files"].items()
                             if not name.endswith(("/key.pem", ".key")) and name != "key.pem"))
    def encoded(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    image_id = admitted["image_id"]
    runtime = RuntimeIdentity(target["official_base_image"], admitted["base_image_inspect"]["Id"], image_id,
        legacy.PREFIX + "/bin/python2.7", (2, 7, 18), "pytest", sha(profile), sha(encoded(dict(helpers))),
        sha(encoded(dict(installed))), sha(raw), legacy.PREFIX, legacy.LAUNCHER, sha(legacy.LAUNCHER_SOURCE.encode()))
    identity = PrivatePytestIdentity(admitted["task"], "2674-v2", runtime.digest, (2, 7, 18), installed, helpers,
                                     optional_test_outcomes=REQUESTS2674_OPTIONAL_OUTCOMES)
    commit = subprocess.check_output(["git", "-c", "safe.directory=" + str(ROOT), "rev-parse", "HEAD"],
                                     cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("exact tested commit required")
    results = []
    with tempfile.TemporaryDirectory(prefix="p07-python27-bootstrap-") as temporary:
        parent = Path(temporary)
        parent.chmod(0o755)
        root = parent / "candidate"
        root.mkdir(mode=0o755)
        (root / "requests").mkdir(mode=0o755)
        (root / "requests/__init__.py").write_text("", encoding="ascii")
        test = root / "test_requests.py"
        for case, body, expected in (
            ("pass", "assert True", True),
            ("fail", "assert False", False),
            ("skip", "pytest.skip('required skip')", False),
        ):
            test.write_text("import pytest\nimport unittest\nclass RequestsTestCase(unittest.TestCase):\n"
                            "    @pytest.mark.xfail\n    def test_response_iter_lines_reentrant(self):\n        assert True\n"
                            "    def test_required(self):\n        " + body + "\n", encoding="ascii")
            spec = CheckSpec(admitted["task"], commit, target["base_commit"], check_source_revision(root), image_id,
                             runtime.interpreter, "pytest", ("test_requests.py",), runtime_identity=runtime,
                             private_pytest_identity=identity, resources=CheckResources(output_bytes_per_stream=1000000))
            cidfile = parent / (case + ".cid")
            args = [ENGINE, "run", "--cidfile", str(cidfile), "--pull=never", "--network=none", "--read-only",
                    "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=1g",
                    "--cpus=2", "--pids-limit=128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
                    "--mount", "type=bind,src=" + str(root) + ",dst=/testbed,readonly", "--workdir=/testbed",
                    "--entrypoint", legacy.LAUNCHER, image_id, *private_pytest_args(spec, identity, NONCE)]
            try:
                process = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=120, check=False)
                stdout, stderr = process.stdout.decode("utf-8"), process.stderr.decode("utf-8")
                (output / (case + ".stdout")).write_bytes(process.stdout)
                (output / (case + ".stderr")).write_bytes(process.stderr)
                result = CommandResult(returncode=process.returncode, stdout=stdout, stderr=stderr)
                observed = private_pytest_receipt(result, spec, identity, NONCE)
                if (observed is not None) != (case != "skip") or bool(observed and not observed["call_failed"]) != expected:
                    raise ValueError("native bootstrap case did not meet expected result: " + case)
                if check_source_revision(root) != spec.source_revision:
                    raise ValueError("synthetic candidate changed during execution")
                results.append({"case": case, "exit": process.returncode, "admitted_receipt": observed,
                                "stdout_sha256": sha(process.stdout), "stderr_sha256": sha(process.stderr)})
            finally:
                if cidfile.is_file():
                    cid = cidfile.read_text(encoding="ascii").strip()
                    if not re.fullmatch(r"[0-9a-f]{64}", cid):
                        raise ValueError("invalid container ID")
                    removed = subprocess.run([ENGINE, "rm", "-f", cid], capture_output=True, check=False)
                    if removed.returncode:
                        raise ValueError("container cleanup failed: " + cid)
                    absent = subprocess.run([ENGINE, "inspect", cid], capture_output=True, check=False)
                    if absent.returncode == 0:
                        raise ValueError("container remains after cleanup: " + cid)
    (output / "summary.json").write_text(json.dumps({"tested_commit": commit, "image_id": image_id,
        "scope": "synthetic private pytest bootstrap only", "provider_calls": 0, "cases": results}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    run(options.runtime_receipt, options.output)
