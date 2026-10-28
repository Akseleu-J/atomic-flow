"""Prospective full-source Requests3362 private CheckSpec/Docker qualification.

Synthetic fixture health plus FAIL/PASS/required-SKIP behavior is the entire
scope. This never grades the historical issue, calls a model or enables generic
execution dispatch. Run only as the explicitly selected root Linux CI operator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

import p07_legacy_transplant as transplant
import p07_requests3362_cert_image as certificate
import p07_requests3362_health as health
import p07_requests3362_legacy_image as provision

from atomic_flow.agents.execution import clean_environment
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity, reviewed_helper_inventory
from atomic_flow.check_private_pytest import MARKER
from atomic_flow.check_requests_execution import run_requests3362_check
from atomic_flow.check_runtime import RuntimeIdentity, validate_runtime_admission
from atomic_flow.check_spec import CheckResources, CheckSpec

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "/usr/bin/docker"
MODULE_PATH = "requests/p07_private_probe.py"
TEST_PATH = "test_p07_private_fixture.py"
WRONG_CA = "p07_private_wrongca.pem"
SITE = transplant.PREFIX + "/lib/python3.6/site-packages/"
HEALTH_SOURCE = health.TEST_SOURCE.replace("'/tmp/p07-health/wrongca.pem'", repr("/testbed/" + WRONG_CA))
ORDINARY_SOURCE = HEALTH_SOURCE + "\n\ndef test_required_production_value():\n    from requests.p07_private_probe import value\n    assert value == 42\n"
SKIP_SOURCE = HEALTH_SOURCE + "\n\n@pytest.mark.skip(reason='synthetic required-test skip must refuse')\ndef test_required_production_value():\n    assert False\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def save(path, value):
    path.write_bytes(encoded(value))


def source_inventory(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Copied source contains links or nonregular files")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return result


def _case_observation(label, spec, receipt, topology):
    result = receipt.result
    observations = [json.loads(line[len(MARKER):]) for line in result.stdout.splitlines() if line.startswith(MARKER)]
    if len(observations) != 1:
        raise ValueError("Exactly one complete private pytest observation required")
    observed = observations[0]
    expected_exit = {"base_fail": 1, "edited_pass": 0, "required_skip": 5}[label]
    fixture = observed.get("requests3362_fixture", {})
    if (result.returncode != expected_exit or result.timed_out or result.stdout_truncated or result.stderr_truncated
            or receipt.source_before != spec.source_revision or receipt.source_after != spec.source_revision
            or observed.get("collected") != 4 or observed.get("completed") != 4
            or observed.get("files") != [TEST_PATH] or observed.get("finished") is not True
            or observed.get("collection_complete") is not True or observed.get("collection_errors") != 0
            or observed.get("setup_failed") != 0 or observed.get("teardown_failed") != 0
            or observed.get("xpassed") != 0 or observed.get("source_revision") != spec.source_revision
            or observed.get("check_spec_digest") != spec.digest
            or observed.get("runtime_digest") != spec.runtime_identity.digest
            or observed.get("private_pytest_identity_digest") != spec.private_pytest_identity.digest
            or fixture.get("servers_stopped") is not True or fixture.get("httpbin_ca_bundle_observed") is not True
            or fixture.get("ca_overlay") is not False or fixture.get("fixture_digest") != spec.fixture_identity.digest
            or topology.nonce != observed.get("nonce") or topology.image_id != spec.image
            or topology.fixture_digest != spec.fixture_identity.digest
            or topology.host_netns_inode == topology.netns_inode or topology.status != "topology_applied_only"):
        raise ValueError("Private fixture completion, source or owned namespace binding differs")
    expected = {"base_fail": (3, 1, 0), "edited_pass": (4, 0, 0), "required_skip": (3, 0, 1)}[label]
    if (observed.get("passed"), observed.get("call_failed"), observed.get("skipped")) != expected:
        raise ValueError("Synthetic ordinary health/production counts differ")
    if label == "required_skip":
        if receipt.check_passed or receipt.runner_receipt is not None:
            raise ValueError("Required skip must not become an accepted completed check")
    elif receipt.check_passed != (label == "edited_pass") or receipt.runner_receipt != observed:
        raise ValueError("Completed negative/positive receipt acceptance differs")
    return observed


def smoke(certificate_receipt: Path, health_receipt: Path, output: Path):
    if platform.system() != "Linux" or not hasattr(os, "geteuid") or os.geteuid() != 0 or output.exists():
        raise ValueError("Fresh output and explicitly selected root Linux Docker worker required")
    output.mkdir(parents=True)
    report = {"status": "not_qualified", "scope": "synthetic full-source Requests3362 private CheckSpec fixture boundary",
              "provider_calls": 0, "model_calls": 0, "host_target_imports": False, "target_setup_executed": False,
              "historical_target_tests": False, "taskrunner_wired": False, "generic_dispatch_enabled": False,
              "verified_success": False, "independent_requirement_success": False, "cases": []}
    source_id = None
    source_cidfile = None
    try:
        with tempfile.TemporaryDirectory(prefix="p07-requests-check-") as directory:
            private = Path(directory)
            private.chmod(0o755)
            home = private / "operator-home"
            home.mkdir(mode=0o700)
            environment = clean_environment(home)
            number = 0

            def checked(args, timeout=60):
                nonlocal number
                number += 1
                tag = "command-" + str(number).zfill(3)
                try:
                    process = subprocess.run(args, cwd=ROOT, env=environment, capture_output=True, timeout=timeout, check=False)
                except subprocess.TimeoutExpired as error:
                    (output / (tag + ".stdout")).write_bytes(error.stdout or b"")
                    (output / (tag + ".stderr")).write_bytes(error.stderr or b"")
                    raise
                (output / (tag + ".stdout")).write_bytes(process.stdout)
                (output / (tag + ".stderr")).write_bytes(process.stderr)
                save(output / (tag + ".json"), {"command_prefix": args[:3], "exit_code": process.returncode})
                if process.returncode or len(process.stdout) + len(process.stderr) > 4 * 1024**2:
                    raise RuntimeError("Fixed Docker/provenance command failed or exceeded audit bound")
                return process.stdout

            report["tested_commit"] = checked(["git", "-c", "safe.directory=" + str(ROOT), "rev-parse", "HEAD"]).decode().strip()
            if re.fullmatch(r"[0-9a-f]{40}", report["tested_commit"]) is None:
                raise ValueError("Exact host source commit required")
            raw_receipt, admitted = certificate.admit_certificate_receipt(certificate_receipt)
            profile_raw, profile = provision.receipt_profile(admitted["parent_runtime_report"])
            if admitted["parent_runtime_report"].get("profile_path") != provision.PROFILE_V2_NAME:
                raise ValueError("Only reviewed Requests3362 v2 HTTPBin0.5.0 profile admitted")
            health_raw = (health_receipt / "health.json").read_bytes()
            qualified_health = json.loads(health_raw)
            if (qualified_health.get("status") != "synthetic_legacy_health_qualified"
                    or qualified_health.get("cleanup_confirmed") is not True
                    or qualified_health.get("image_id") != admitted["image_id"]
                    or qualified_health.get("certificate_receipt_sha256") != sha(raw_receipt)
                    or qualified_health.get("before") != admitted["after"]
                    or qualified_health.get("after") != admitted["after"]
                    or qualified_health.get("model_calls") != 0 or qualified_health.get("httpbin_version") != "0.5.0"):
                raise ValueError("Fresh same-image admitted TLS/plugin health prerequisite missing")
            health_stdout = (health_receipt / "health.stdout").read_bytes()
            health_stderr = (health_receipt / "health.stderr").read_bytes()
            if sha(health_stdout) != qualified_health.get("stdout_sha256") or sha(health_stderr) != qualified_health.get("stderr_sha256"):
                raise ValueError("Fresh health raw stream hashes differ")
            observed_health = health.validate_health(health_stdout.decode("utf-8"), httpbin_version="0.5.0")
            if observed_health != qualified_health.get("observed"):
                raise ValueError("Fresh health raw observation differs from report")
            wrong_ca = (certificate_receipt / "wrongca.pem").read_bytes()
            expected_health_source = health.bootstrap_source(admitted["installed_files"], wrong_ca.decode(), httpbin_version="0.5.0").encode()
            if ((health_receipt / "health.py").read_bytes() != expected_health_source
                    or qualified_health.get("bootstrap_sha256") != sha(expected_health_source)):
                raise ValueError("Fresh health did not run the exact reviewed bootstrap")
            actual_image = json.loads(checked([ENGINE, "image", "inspect", admitted["image_id"]]))[0]
            actual_parent = json.loads(checked([ENGINE, "image", "inspect", admitted["parent_image_id"]]))[0]
            actual_base = json.loads(checked([ENGINE, "image", "inspect", provision.BASE]))[0]
            health.admit_image(actual_image, actual_parent, admitted)
            for label, value in (("image", actual_image), ("parent", actual_parent), ("base", actual_base)):
                save(output / (label + "-inspect.json"), value)
            (output / "provisioning-receipt.json").write_bytes(raw_receipt)
            (output / "health-admission.json").write_bytes(health_raw)
            (output / "runtime-profile.json").write_bytes(profile_raw)

            source_cidfile = private / "source.cid"
            try:
                checked([ENGINE, "create", "--cidfile", str(source_cidfile), "--pull=never", "--network=none", "--read-only",
                         "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=1g",
                         "--cpus=2", "--pids-limit=128", "--entrypoint=/bin/sleep", actual_image["Id"], "infinity"])
            finally:
                # Recover a written immutable ID before TemporaryDirectory can
                # disappear during exception unwinding after uncertain create.
                if source_cidfile.is_file() and not source_cidfile.is_symlink() and source_cidfile.stat().st_size <= 65:
                    candidate = source_cidfile.read_text(encoding="ascii").strip()
                    if re.fullmatch(r"[a-f0-9]{64}", candidate):
                        source_id = candidate
            if source_id is None:
                raise ValueError("Immutable source extraction container ID required")
            source_config = json.loads(checked([ENGINE, "inspect", source_id]))[0]
            host = source_config["HostConfig"]
            if (source_config.get("Id") != source_id or source_config.get("Image") != actual_image["Id"]
                    or source_config.get("Mounts") or host.get("Binds") or host.get("Privileged")
                    or not host.get("ReadonlyRootfs") or host.get("NetworkMode") != "none"
                    or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
                    or "no-new-privileges" not in host.get("SecurityOpt", [])
                    or host.get("Memory") != 1024**3 or host.get("NanoCpus") != 2_000_000_000
                    or host.get("PidsLimit") != 128 or source_config["Config"].get("User") != "65534:65534"
                    or source_config["State"].get("Running")):
                raise ValueError("Stopped source extraction container policy differs")
            save(output / "source-container-inspect.json", source_config)
            workspace = private / "workspace"
            workspace.mkdir(mode=0o755)
            checked([ENGINE, "cp", source_id + ":/testbed/.", str(workspace)], 180)
            launcher = private / "launcher"
            checked([ENGINE, "cp", source_id + ":" + transplant.LAUNCHER, str(launcher)])
            if launcher.is_symlink() or launcher.read_bytes() != transplant.LAUNCHER_SOURCE.encode():
                raise ValueError("Actual private launcher bytes differ")
            before_strip = source_inventory(workspace)
            for relative in before_strip:
                if relative.endswith((".pyc", ".pyo")):
                    (workspace / relative).unlink()
            after_strip = source_inventory(workspace)
            if after_strip != {name: digest for name, digest in before_strip.items() if not name.endswith((".pyc", ".pyo"))}:
                raise ValueError("Disposable bytecode cleanup changed retained bytes")
            save(output / "copied-source-before-bytecode-removal.json", before_strip)
            save(output / "copied-source-after-bytecode-removal.json", after_strip)
            save(output / "disposable-bytecode-removal.json", {"scope": "copied disposable source only",
                 "removed": {name: digest for name, digest in before_strip.items() if name not in after_strip},
                 "retained_bytes_unchanged": True})
            for path in workspace.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            helper_pairs = reviewed_helper_inventory("3362-cn")
            helper_bytes = {name: (ROOT / name).read_bytes() for name, _ in helper_pairs}
            if any(sha(helper_bytes[name]) != digest for name, digest in helper_pairs):
                raise ValueError("Actual reviewed fixture helper bytes differ")
            helper_raw = encoded({name: sha(raw) for name, raw in helper_bytes.items()})
            inventory_raw = encoded(admitted["installed_files"])
            runtime = RuntimeIdentity(base_image=provision.BASE, base_image_id=actual_base["Id"], image_id=actual_image["Id"],
                interpreter=transplant.PREFIX + "/bin/python3.6", python_version=(3, 6, 15), runner="pytest",
                profile_sha256=sha(profile_raw), helper_sha256=sha(helper_raw), inventory_sha256=sha(inventory_raw),
                provisioning_receipt_sha256=sha(raw_receipt), private_prefix=transplant.PREFIX,
                launcher=transplant.LAUNCHER, launcher_sha256=sha(launcher.read_bytes()))
            validate_runtime_admission(runtime, base_inspect=actual_base, image_inspect=actual_image, profile_bytes=profile_raw,
                helper_bytes=helper_raw, inventory_bytes=inventory_raw, provisioning_receipt_bytes=raw_receipt,
                launcher_bytes=launcher.read_bytes())
            installed = {name: digest for name, digest in admitted["installed_files"].items()
                         if not name.endswith(("/key.pem", ".key")) and name != "key.pem"}
            actual_bytes = {}
            dependency_dir = private / "dependencies"
            dependency_dir.mkdir()
            for index, relative in enumerate(sorted(installed)):
                path = dependency_dir / str(index)
                checked([ENGINE, "cp", source_id + ":" + SITE + relative, str(path)])
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Actual installed dependency is not a regular file")
                actual_bytes[relative] = path.read_bytes()
            pytest_identity = PrivatePytestIdentity(provision.TASK, "3362-cn", runtime.digest, (3, 6, 15),
                tuple(sorted(installed.items())), helper_pairs)
            pytest_identity.validate_bytes(installed_files_bytes=actual_bytes, helper_bytes=helper_bytes)
            metadata_raw = encoded({"common_name": "127.0.0.1", "dns_sans": [], "ip_sans": []})
            health_policy_raw = encoded({"scope": "same invocation ordinary synthetic tests",
                "tests_sha256": sha(HEALTH_SOURCE.encode()), "prerequisite_receipt_sha256": sha(health_raw),
                "wrong_ca_sha256": sha(wrong_ca), "ca_verified_https": True, "hostname_validation": True,
                "unrelated_ca_rejected": True, "actual_httpbin": "0.5.0"})
            public = {certificate.CERT_ROOT + "/" + name: (certificate_receipt / "certificates" / name).read_bytes()
                      for name in ("cacert.pem", "cert.pem")}
            if any(actual_bytes["pytest_httpbin/certs/" + name] != public[certificate.CERT_ROOT + "/" + name]
                   for name in ("cacert.pem", "cert.pem")):
                raise ValueError("Actual image public certificates differ from provisioning evidence")
            fixture = FixtureIdentity(provision.TASK, "3362-cn", runtime.digest, runtime.image_id, sha(profile_raw),
                helper_pairs, tuple(sorted((name, sha(raw)) for name, raw in public.items())), sha(metadata_raw), sha(health_policy_raw))
            fixture.validate_bytes(profile_bytes=profile_raw, helper_bytes=helper_bytes, public_certificate_bytes=public,
                certificate_metadata_bytes=metadata_raw, health_policy_bytes=health_policy_raw)
            for name, raw in (("installed-inventory.json", inventory_raw), ("helper-inventory.json", helper_raw),
                              ("certificate-policy.json", metadata_raw), ("health-policy.json", health_policy_raw)):
                (output / name).write_bytes(raw)
            save(output / "runtime-identity.json", runtime.as_dict())
            save(output / "private-pytest-identity.json", pytest_identity.as_dict())
            save(output / "fixture-identity.json", fixture.as_dict())
            if any((workspace / name).exists() for name in (MODULE_PATH, TEST_PATH, WRONG_CA)):
                raise ValueError("Synthetic source/test/public CA paths must be absent")
            (workspace / WRONG_CA).write_bytes(wrong_ca)
            report.update(full_source_file_count=len(after_strip), full_source_revision=check_source_revision(workspace),
                          runtime_digest=runtime.digest, private_pytest_digest=pytest_identity.digest, fixture_digest=fixture.digest,
                          httpbin_version=next(item["version"] for item in profile["wheels"] if item["name"].lower() == "httpbin"))
            for label, value, test in (("base_fail", 1, ORDINARY_SOURCE), ("edited_pass", 42, ORDINARY_SOURCE),
                                       ("required_skip", 42, SKIP_SOURCE)):
                report["current_case"] = label
                case = output / label
                case.mkdir()
                (workspace / MODULE_PATH).write_bytes(("value = " + str(value) + "\n").encode())
                (workspace / TEST_PATH).write_bytes(test.encode())
                spec = CheckSpec(task_id=provision.TASK, task_commit=report["tested_commit"], base_commit=provision.COMMIT,
                    source_revision=check_source_revision(workspace), image=runtime.image_id, interpreter=runtime.interpreter,
                    runner="pytest", test_paths=(TEST_PATH,), target_root="/testbed", runtime_identity=runtime,
                    private_pytest_identity=pytest_identity, fixture_identity=fixture,
                    resources=CheckResources(output_bytes_per_stream=1_000_000))
                save(case / "spec.json", spec.as_dict())
                (case / "synthetic-production.py").write_bytes((workspace / MODULE_PATH).read_bytes())
                (case / "synthetic-ordinary-test.py").write_bytes((workspace / TEST_PATH).read_bytes())
                receipt, topology = run_requests3362_check(spec, workspace)
                save(case / "receipt.json", receipt.as_dict())
                save(case / "topology.json", topology.as_dict())
                (case / "stdout.txt").write_bytes(receipt.result.stdout.encode())
                (case / "stderr.txt").write_bytes(receipt.result.stderr.encode())
                observed = _case_observation(label, spec, receipt, topology)
                if check_source_revision(workspace) != spec.source_revision:
                    raise ValueError("Controller returned after source changed")
                row = {"case": label, "status": "expected_private_boundary_behavior", "process_exit": receipt.result.returncode,
                       "check_passed": receipt.check_passed, "source_before": receipt.source_before, "source_after": receipt.source_after,
                       "fixture_servers_stopped": observed["requests3362_fixture"]["servers_stopped"],
                       "controller_returned_after_immutable_cleanup": True, "container_id": topology.container_id,
                       "stdout_sha256": sha(receipt.result.stdout.encode()), "stderr_sha256": sha(receipt.result.stderr.encode())}
                report["cases"].append(row)
                save(case / "result.json", row)
            report.pop("current_case", None)
            if len(report["cases"]) == 3:
                report["status"] = "requests3362_private_check_boundary_qualified"
    except Exception as error:
        report.update(status="not_qualified", error_type=type(error).__name__, error=str(error)[:500])
    finally:
        # The source-only container is stopped throughout. Reconcile immutable
        # ID even if create returned an uncertain outcome after writing cidfile.
        cleanup = {"container_id": source_id, "confirmed": False}
        try:
            if source_id is None and source_cidfile is not None and source_cidfile.is_file():
                candidate = source_cidfile.read_text(encoding="ascii").strip()
                if re.fullmatch(r"[a-f0-9]{64}", candidate):
                    source_id = candidate
            if source_id is not None:
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
        report["cleanup_confirmed"] = cleanup["confirmed"] and len(report["cases"]) == 3
        if source_cidfile is not None and not cleanup["confirmed"]:
            report["status"] = "cleanup_uncertain"
        save(output / "summary.json", report)
        save(output / "artifact-sha256.json", {path.relative_to(output).as_posix(): sha(path.read_bytes())
            for path in output.rglob("*") if path.is_file() and path.name != "artifact-sha256.json"})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-receipt", type=Path, required=True)
    parser.add_argument("--health-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.certificate_receipt, args.health_receipt, args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "requests3362_private_check_boundary_qualified" else 1)
