from __future__ import annotations

import ast
import copy
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_requests_function_image as image  # noqa: E402


@pytest.mark.parametrize("task", image.TASK_IDS)
def test_own_base_task_profile_and_offline_build_are_exact(task):
    raw, target, dep_raw, config = image.frozen_task(task)
    docker = image.dockerfile(target)
    assert image.sha(raw) == image.PROFILE_SHA
    assert image.sha(dep_raw) == json.loads(raw)["source_bindings"]["legacy_wheel_profile"]["git_lf_sha256"]
    assert len(config["wheels"]) == 14 and config["pins"]["httpbin"] == "0.5.0"
    assert "FROM " + target["official_base_image"] in docker
    assert image.transplant.DONOR in docker
    assert "--network=none" in docker and "--no-index --no-deps --no-compile" in docker
    assert "--ignore-installed --require-hashes" in docker
    assert "COPY certificates/ /opt/p07-function-fixture/" in docker
    assert "wrongca" not in docker and "setup.py" not in docker


@pytest.mark.parametrize("task", ["psf__requests-2674", "psf__requests-3362", "863", "../863"])
def test_other_source_images_are_not_admitted(task):
    with pytest.raises(ValueError, match="four reviewed"):
        image.frozen_task(task)


def test_fresh_context_is_required_before_download(tmp_path):
    with pytest.raises(ValueError, match="fresh"):
        image.prepare(image.TASK_IDS[0], tmp_path, downloader=lambda _: pytest.fail("must not download"))


def test_changed_primary_publication_refused_before_context_write(tmp_path):
    with pytest.raises(ValueError, match="publisher wheel"):
        image.prepare(image.TASK_IDS[0], tmp_path / "fresh", downloader=lambda _: b'{"urls":[]}')
    assert not (tmp_path / "fresh").exists()


def test_certificate_profile_is_dns_only_chain_verified_and_bounded():
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    certs = image.create_certificates()
    metadata = image.validate_certificates(certs)
    leaf = x509.load_pem_x509_certificate(certs["cert.pem"])
    assert leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "httpbin.org"
    assert list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) == [x509.DNSName("httpbin.org")]
    assert set(certs) == {*image.CERT_NAMES, "wrongca.pem"}
    assert metadata["cert.pem"]["issuer"] == metadata["cacert.pem"]["subject"]
    assert metadata["key.pem"]["public_key_sha256"] == metadata["cert.pem"]["public_key_sha256"]


@pytest.mark.parametrize("change", ["key", "ca", "expired", "extra"])
def test_certificate_substitution_or_expiry_is_refused(change):
    import datetime as dt

    certs = image.create_certificates()
    if change == "key":
        certs["key.pem"] = image.create_certificates()["key.pem"]
    elif change == "ca":
        certs["cacert.pem"] = certs["wrongca.pem"]
    elif change == "extra":
        certs["ca-private-key.pem"] = b"unreviewed"
    with pytest.raises(ValueError):
        image.validate_certificates(certs, now=image.utcnow() + dt.timedelta(days=20) if change == "expired" else None)


def test_certificate_probe_is_fixed_and_has_no_target_imports():
    files = {image.CERT_ROOT + "/" + name: "a" * 64 for name in image.CERT_NAMES}
    code = image.certificate_source(files)
    ast.parse(code)
    assert "/testbed" not in code and "import requests" not in code
    assert "path.is_symlink()" in code and "path.resolve()" in code
    with pytest.raises(ValueError, match="three certificate paths"):
        image.certificate_source({"/tmp/key.pem": "a" * 64})


def qualified_fixture(directory):
    # Immutable retained publisher inventory avoids inventing the expected238 hashes.
    archive = ROOT / "validation/p07-budget20/requests3362-calibration-v4/12725386677.zip"
    with zipfile.ZipFile(archive) as entries:
        parent = json.loads(entries.read("runtime/requests-runtime.json"))
        for name in ("probe.stdout", "probe.stderr", "inventory.stdout", "inventory.stderr"):
            (directory / name).write_bytes(entries.read("runtime/" + name))
    raw, task, dep_raw, config = image.frozen_task(image.TASK_IDS[0])
    certs = image.create_certificates()
    (directory / "certificates").mkdir()
    for name in image.CERT_NAMES:
        (directory / "certificates" / name).write_bytes(certs[name])
    (directory / "wrongca.pem").write_bytes(certs["wrongca.pem"])
    cert_files = {image.CERT_ROOT + "/" + name: image.sha(certs[name]) for name in image.CERT_NAMES}
    certificate_output = {"certificate_files": cert_files, "target_imported": False}
    stdout = json.dumps(certificate_output).encode()
    (directory / "certificates.stdout").write_bytes(stdout)
    (directory / "certificates.stderr").write_bytes(b"")
    report = copy.deepcopy(parent)
    report.update(status="function_runtime_qualified", task=image.TASK_IDS[0], task_record=task,
        base_image=task["official_base_image"], base_commit=task["base_commit"],
        profile_sha256=image.sha(raw), service_profile_sha256=image.sha(raw), dependency_profile_sha256=image.sha(dep_raw),
        dependency_profile_path=image.legacy.PROFILE_V2_NAME, certificate_files=cert_files,
        certificates_metadata=image.validate_certificates(certs), wrong_ca_sha256=image.sha(certs["wrongca.pem"]),
        dockerfile_sha256=image.sha(image.dockerfile(task).encode()), certificate_exit=0,
        certificate_probe_sha256=image.sha(image.certificate_source(cert_files).encode()),
        certificate_stdout_sha256=image.sha(stdout), certificate_stderr_sha256=image.sha(b""),
        certificate_inventory=certificate_output)
    report["base_image_inspect"]["RepoDigests"] = [task["official_base_image"]]
    labels = report["image_inspect"]["Config"]["Labels"]
    labels.update({"atomic.p07.legacy.base": task["official_base_image"],
        "atomic.p07.requests.function.profile": image.sha(raw), "atomic.p07.requests.function.task": image.TASK_IDS[0],
        "atomic.p07.requests.function.dependencies": image.sha(dep_raw)})
    image.save(directory / image.RECEIPT, report)
    return report


def test_complete_qualified_runtime_receipt_remains_uncalibrated(tmp_path):
    report = qualified_fixture(tmp_path)
    raw, admitted = image.admit_receipt(tmp_path)
    assert json.loads(raw) == report == admitted
    assert admitted["calibrated"] is False and admitted["model_calls"] == 0
    assert admitted["target_or_plugins_imported"] is False
    assert len(admitted["installed_files"]) == 238 and len(admitted["certificate_files"]) == 3


@pytest.mark.parametrize("field,value", [("status", "prepared_not_built"), ("cleanup_confirmed", False),
    ("probe_compatible", False), ("inventory_exit", 1), ("certificate_exit", 1),
    ("profile_sha256", "0" * 64), ("dependency_profile_sha256", "0" * 64),
    ("base_commit", "0" * 40), ("base_image", image.legacy.BASE), ("model_calls", 1),
    ("target_setup_or_tests_executed", True), ("calibrated", True)])
def test_receipt_tampering_fails_closed(tmp_path, field, value):
    report = qualified_fixture(tmp_path)
    report[field] = value
    image.save(tmp_path / image.RECEIPT, report)
    with pytest.raises(ValueError):
        image.admit_receipt(tmp_path)


def test_raw_output_substitution_is_refused(tmp_path):
    qualified_fixture(tmp_path)
    (tmp_path / "inventory.stdout").write_bytes(b"different")
    with pytest.raises(ValueError, match="raw probe output"):
        image.admit_receipt(tmp_path)


def test_certificate_overlay_or_source_change_is_refused(tmp_path):
    report = qualified_fixture(tmp_path)
    report["after"] = {**report["after"], "diff": "changed tracked source"}
    image.save(tmp_path / image.RECEIPT, report)
    with pytest.raises(ValueError):
        image.admit_receipt(tmp_path)
