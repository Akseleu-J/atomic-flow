"""Explicit root-worker function36 resource/fixture boundary qualification.

Only fixed synthetic ordinary tests run against full admitted public source.
No hidden patch, gold, provider call or generic dispatch is used.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import p07_legacy_transplant as transplant
import p07_requests_function_image as provision
from p07_requests3362_check_smoke import encoded, save, sha, source_inventory

from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.verify import check_source_revision, check_source_snapshot
from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_requests_function import health_bytes
from atomic_flow.check_requests_function_execution import run_function36_check
from atomic_flow.check_requests_resources import FUNCTION36_TASKS, RequestsCAIdentity
from atomic_flow.check_runtime import RuntimeIdentity, validate_runtime_admission
from atomic_flow.check_spec import CheckResources, CheckSpec

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "/usr/bin/docker"
MODULE = "requests/p07_function_probe.py"
TEST = "test_p07_function_fixture.py"
ORDINARY = '''import requests

def test_actual_target_http_and_execution_ca_https():
    for scheme in ('http','https'):
        response=requests.get(scheme+'://httpbin.org/get?ordinary=p07',timeout=3,verify='/testbed/requests/cacert.pem')
        assert response.status_code==200
        assert response.json()['args']=={'ordinary':'p07'}

def test_required_production_value():
    from requests.p07_function_probe import value
    assert value==42
'''
SKIP = ORDINARY.replace("    from requests.p07_function_probe import value\n    assert value==42",
                        "    import pytest\n    pytest.skip('synthetic required skip must refuse')")


def smoke(runtime_receipt, output, *, host_callback=None):
    if (platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0
            or output.exists()):
        raise ValueError("explicit root Linux worker and fresh output required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "synthetic full-source function36 ordinary resource boundary",
              "provider_calls": 0, "model_calls": 0, "host_target_imports": False, "target_setup_executed": False,
              "historical_target_tests": False, "hidden_oracle": "not_run", "verified_success": False,
              "generic_dispatch_enabled": False, "taskrunner_wired": False, "cases": []}
    source_id = None
    cidfile = None
    try:
        with tempfile.TemporaryDirectory(prefix="p07-function-smoke-") as directory:
            private = Path(directory)
            private.chmod(0o755)
            home = private / "operator"
            home.mkdir(mode=0o700)
            environment = clean_environment(home)
            counter = 0

            def checked(args, timeout=60):
                nonlocal counter
                counter += 1
                tag = "command-" + str(counter).zfill(3)
                try:
                    result = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True, timeout=timeout, check=False)
                except subprocess.TimeoutExpired as error:
                    (output / (tag + ".stdout")).write_bytes(error.stdout or b"")
                    (output / (tag + ".stderr")).write_bytes(error.stderr or b"")
                    raise
                (output / (tag + ".stdout")).write_bytes(result.stdout)
                (output / (tag + ".stderr")).write_bytes(result.stderr)
                save(output / (tag + ".json"), {"command_prefix": args[:3], "exit_code": result.returncode})
                if result.returncode or len(result.stdout) + len(result.stderr) > 4 * 1024**2:
                    raise ValueError("fixed provenance command failed or exceeded byte bound")
                return result.stdout

            report["tested_commit"] = checked(["git", "-c", "safe.directory=" + str(ROOT), "rev-parse", "HEAD"]).decode().strip()
            if re.fullmatch(r"[a-f0-9]{40}", report["tested_commit"]) is None:
                raise ValueError("full tested commit required")
            raw_receipt, admitted = provision.admit_receipt(runtime_receipt)
            task = admitted["task"]
            if task not in FUNCTION36_TASKS:
                raise ValueError("only three fixed Python3.6 task cohorts allowed")
            report["task_id"] = task
            profile_raw, target, _, _ = provision.frozen_task(task)
            image = json.loads(checked([ENGINE, "image", "inspect", admitted["image_id"]]))[0]
            base = json.loads(checked([ENGINE, "image", "inspect", target["official_base_image"]]))[0]
            if image["Id"] != admitted["image_id"] or base["Id"] != admitted["base_image_inspect"]["Id"]:
                raise ValueError("actual immutable image/base differ from provisioning")
            save(output / "image-inspect.json", image)
            save(output / "base-inspect.json", base)
            (output / "provisioning-receipt.json").write_bytes(raw_receipt)
            (output / "runtime-profile.json").write_bytes(profile_raw)
            cidfile = private / "source.cid"
            try:
                checked([ENGINE, "create", "--cidfile", str(cidfile), "--pull=never", "--network=none", "--read-only",
                         "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=1g",
                         "--cpus=2", "--pids-limit=128", "--entrypoint=/bin/sleep", "--", image["Id"], "infinity"])
            finally:
                if cidfile.is_file() and not cidfile.is_symlink():
                    source_id = cidfile.read_text(encoding="ascii").strip()
            if not source_id or re.fullmatch(r"[a-f0-9]{64}", source_id) is None:
                raise ValueError("immutable source extraction container ID required")
            source_config = json.loads(checked([ENGINE, "inspect", source_id]))[0]
            h = source_config["HostConfig"]
            if (source_config["Id"] != source_id or source_config["Image"] != image["Id"] or source_config.get("Mounts")
                    or h.get("Binds") or h.get("Privileged") or not h.get("ReadonlyRootfs") or h.get("NetworkMode") != "none"
                    or h.get("CapDrop") != ["ALL"] or h.get("CapAdd") or "no-new-privileges" not in h.get("SecurityOpt", [])
                    or h.get("Memory") != 1024**3 or h.get("NanoCpus") != 2_000_000_000 or h.get("PidsLimit") != 128
                    or source_config["State"].get("Running") or source_config["Config"].get("User") != "65534:65534"):
                raise ValueError("stopped source container isolation differs")
            save(output / "source-inspect.json", source_config)
            candidate = private / "candidate"
            candidate.mkdir(mode=0o755)
            checked([ENGINE, "cp", source_id + ":/testbed/.", str(candidate)], 180)
            launcher = private / "launcher"
            checked([ENGINE, "cp", source_id + ":" + transplant.LAUNCHER, str(launcher)])
            if launcher.is_symlink() or launcher.read_bytes() != transplant.LAUNCHER_SOURCE.encode():
                raise ValueError("actual launcher differs from reviewed bytes")
            inventory_before = source_inventory(candidate)
            for relative in inventory_before:
                if relative.endswith((".pyc", ".pyo")):
                    (candidate / relative).unlink()
            for entry in candidate.rglob("*"):
                entry.chmod(0o755 if entry.is_dir() else 0o644)
            inventory_after = source_inventory(candidate)
            if inventory_after != {p: v for p, v in inventory_before.items() if not p.endswith((".pyc", ".pyo"))}:
                raise ValueError("bytecode removal changed admitted source")
            save(output / "copied-source-before.json", inventory_before)
            save(output / "copied-source-after-bytecode-removal.json", inventory_after)
            original_path = candidate / "requests/cacert.pem"
            original_ca = original_path.read_bytes()
            original_git = checked(["git", "-c", "safe.directory=" + str(candidate), "-c", "core.fsmonitor=false",
                                    "-C", str(candidate), "cat-file", "blob", target["base_commit"] + ":requests/cacert.pem"])
            if original_ca != original_git:
                raise ValueError("image original CA differs from exact public base Git object")
            original_path.unlink()  # Only the disposable copy; image and publication candidate remain separate.
            save(output / "candidate-resource-detachment.json", {"path": "requests/cacert.pem", "sha256": sha(original_ca),
                "base_commit": target["base_commit"], "public_git_bytes_equal": True, "image_unchanged": True})
            helper_pairs = reviewed_helper_inventory("function36-v3")
            helper_data = {name: (ROOT / name).read_bytes() for name, _ in helper_pairs}
            if any(sha(helper_data[name]) != digest for name, digest in helper_pairs):
                raise ValueError("reviewed helper bytes differ")
            helper_raw, inventory_raw = encoded(dict(helper_pairs)), encoded(admitted["installed_files"])
            runtime = RuntimeIdentity(target["official_base_image"], base["Id"], image["Id"],
                transplant.PREFIX + "/bin/python3.6", (3, 6, 15), "pytest", sha(profile_raw), sha(helper_raw),
                sha(inventory_raw), sha(raw_receipt), transplant.PREFIX, transplant.LAUNCHER, sha(launcher.read_bytes()))
            validate_runtime_admission(runtime, base_inspect=base, image_inspect=image, profile_bytes=profile_raw,
                helper_bytes=helper_raw, inventory_bytes=inventory_raw, provisioning_receipt_bytes=raw_receipt,
                launcher_bytes=launcher.read_bytes())
            installed = {name: digest for name, digest in admitted["installed_files"].items()
                         if not name.endswith(("/key.pem", ".key")) and name != "key.pem"}
            actual_bytes = {}
            dep_dir = private / "dependencies"
            dep_dir.mkdir()
            for index, relative in enumerate(sorted(installed)):
                destination = dep_dir / str(index)
                checked([ENGINE, "cp", source_id + ":" + transplant.PREFIX + "/lib/python3.6/site-packages/" + relative,
                         str(destination)])
                if destination.is_symlink() or not destination.is_file():
                    raise ValueError("actual dependency is not a regular file")
                actual_bytes[relative] = destination.read_bytes()
            identity = PrivatePytestIdentity(task, "function36-v3", runtime.digest, (3, 6, 15),
                                             tuple(sorted(installed.items())), helper_pairs)
            identity.validate_bytes(installed_files_bytes=actual_bytes, helper_bytes=helper_data)
            fixture_ca = (runtime_receipt / "certificates/cacert.pem").read_bytes()
            wrong_ca = (runtime_receipt / "wrongca.pem").read_text(encoding="ascii")
            ca = RequestsCAIdentity(task, sha(original_ca), sha(fixture_ca), sha(original_ca + b"\n" + fixture_ca), wrong_ca)
            ca.validate_bytes(original_ca, fixture_ca)
            certs = tuple(sorted((provision.CERT_ROOT + "/" + name, sha((runtime_receipt / "certificates" / name).read_bytes()))
                                 for name in ("cacert.pem", "cert.pem")))
            metadata = encoded({"common_name": "httpbin.org", "dns_sans": ["httpbin.org"], "ip_sans": []})
            fixture = FixtureIdentity(task, "function36-v3", runtime.digest, runtime.image_id, sha(profile_raw), helper_pairs,
                                     certs, sha(metadata), "0" * 64)
            policy = health_bytes(fixture, ca)
            fixture = replace(fixture, health_policy_sha256=sha(policy))
            fixture.validate_bytes(profile_bytes=profile_raw, helper_bytes=helper_data,
                public_certificate_bytes={name: (runtime_receipt / "certificates" / name.rsplit("/", 1)[1]).read_bytes()
                                          for name, _ in certs}, certificate_metadata_bytes=metadata, health_policy_bytes=policy)
            for name, data in (("helper-inventory.json", helper_raw), ("installed-inventory.json", inventory_raw),
                               ("certificate-policy.json", metadata), ("health-policy.json", policy)):
                (output / name).write_bytes(data)
            save(output / "runtime-identity.json", runtime.as_dict())
            save(output / "private-pytest-identity.json", identity.as_dict())
            save(output / "fixture-identity.json", fixture.as_dict())
            report.update(runtime_digest=runtime.digest, fixture_digest=fixture.digest, private_pytest_digest=identity.digest,
                          ca_identity_digest=ca.digest, admitted_core_files=len(check_source_snapshot(candidate)))
            if (candidate / MODULE).exists() or (candidate / TEST).exists():
                raise ValueError("fixed probe and ordinary paths must be absent")
            for label, value, test in (("base_fail", 1, ORDINARY), ("edited_pass", 42, ORDINARY), ("required_skip", 42, SKIP)):
                report["current_case"] = label
                case = output / label
                case.mkdir()
                (candidate / MODULE).write_bytes(("value = " + str(value) + "\n").encode())
                (candidate / TEST).write_bytes(test.encode())
                before = check_source_snapshot(candidate)
                spec = CheckSpec(task, report["tested_commit"], target["base_commit"], check_source_revision(candidate),
                    runtime.image_id, runtime.interpreter, "pytest", (TEST,), target_root="/testbed", runtime_identity=runtime,
                    private_pytest_identity=identity, fixture_identity=fixture, requests_ca_identity=ca,
                    resources=CheckResources(output_bytes_per_stream=1_000_000))
                save(case / "spec.json", spec.as_dict())
                save(case / "candidate-before.json", before)
                receipt, topology, resources = run_function36_check(spec, candidate)
                save(case / "receipt.json", receipt.as_dict())
                save(case / "topology.json", topology.as_dict())
                save(case / "resources.json", resources)
                (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                after = check_source_snapshot(candidate)
                save(case / "candidate-after.json", after)
                lines = [line[len("ATOMIC_CHECK_SPEC_RECEIPT:"):] for line in receipt.result.stdout.splitlines()
                         if line.startswith("ATOMIC_CHECK_SPEC_RECEIPT:")]
                observed = json.loads(lines[0]) if len(lines) == 1 else {}
                expected = {"base_fail": (1, 1, 0), "edited_pass": (0, 0, 0), "required_skip": (5, 0, 1)}[label]
                actual = (receipt.result.returncode, observed.get("call_failed"), observed.get("skipped"))
                if (actual != expected or observed.get("collected") != 2 or observed.get("completed") != 2
                        or before != after or receipt.source_before != spec.source_revision or receipt.source_after != spec.source_revision
                        or (candidate / "requests/cacert.pem").exists() or not resources["resource_container"]["cleanup_confirmed"]
                        or not resources["candidate_and_execution_specs_equal"] or topology.nonce != observed.get("nonce")
                        or observed.get("requests_function_fixture", {}).get("servers_stopped") is not True
                        or receipt.check_passed != (label == "edited_pass")
                        or (receipt.runner_receipt is None) != (label == "required_skip")):
                    raise ValueError("complete resource/fixture/required-skip behavior did not qualify")
                report["cases"].append({"case": label, "process_exit": receipt.result.returncode,
                    "check_passed": receipt.check_passed, "source_revision": spec.source_revision,
                    "candidate_ca_absent": True, "candidate_source_unchanged": True, "container_id": topology.container_id,
                    "fixture_servers_stopped": True, "controller_returned_after_cleanup": True})
            report.pop("current_case", None)
            report["status"] = "function36_private_resource_boundary_qualified"
            if host_callback is not None:
                if not callable(host_callback):
                    raise ValueError("trusted host callback required")
                host_callback(candidate, runtime, identity, fixture, ca, target, report, output)
    except Exception as error:
        report.update(status="not_qualified", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        cleanup = {"container_id": source_id, "confirmed": False}
        try:
            if source_id is None and cidfile is not None and cidfile.is_file():
                source_id = cidfile.read_text(encoding="ascii").strip()
            if source_id is not None and re.fullmatch(r"[a-f0-9]{64}", source_id):
                env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
                removed = subprocess.run([ENGINE, "rm", "-f", source_id], env=env, capture_output=True, timeout=30, check=False)
                absent = subprocess.run([ENGINE, "container", "ls", "-a", "--no-trunc", "--filter", "id=" + source_id,
                                         "--format", "{{.ID}}"], env=env, capture_output=True, timeout=15, check=False)
                (output / "source-cleanup.stdout").write_bytes(removed.stdout + absent.stdout)
                (output / "source-cleanup.stderr").write_bytes(removed.stderr + absent.stderr)
                cleanup.update(container_id=source_id, remove_exit=removed.returncode,
                               confirmed=absent.returncode == 0 and not absent.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            cleanup["error_type"] = type(error).__name__
        report["source_container_cleanup"] = cleanup
        report["cleanup_confirmed"] = cleanup["confirmed"] and len(report["cases"]) == 3 and not report.get("task_controller_errors")
        if not cleanup["confirmed"]:
            report["status"] = "cleanup_uncertain"
        save(output / "summary.json", report)
        save(output / "artifact-sha256.json", {p.relative_to(output).as_posix(): sha(p.read_bytes())
             for p in output.rglob("*") if p.is_file() and p.name != "artifact-sha256.json"})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.runtime_receipt, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "function36_private_resource_boundary_qualified" else 1)
