"""Contract tests for the disclosed certificate-only Requests runtime layer."""
from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_requests3362_cert_image as cert  # noqa: E402


@pytest.fixture(scope="module")
def public_files():
    return cert.create_certificates()


def original_parent():
    return json.loads((ROOT / "validation/p07-budget20/requests3362-runtime-v2/requests-runtime.json").read_bytes())


def write_parent(directory, parent=None):
    directory.mkdir()
    cert.legacy.save(directory / "requests-runtime.json", parent or original_parent())


def test_fresh_ca_server_key_and_unrelated_ca_are_disclosed_only(public_files):
    metadata = cert.validate_certificates(public_files)
    assert set(public_files) == {"cacert.pem", "cert.pem", "key.pem", "wrongca.pem"}
    assert metadata["cert.pem"]["subject"] == "CN=127.0.0.1"
    assert "2.5.29.17" not in metadata["cert.pem"]["extensions"]
    assert metadata["key.pem"]["public_key_sha256"] == metadata["cert.pem"]["public_key_sha256"]
    assert metadata["cacert.pem"]["public_key_sha256"] != metadata["wrongca.pem"]["public_key_sha256"]
    assert metadata["key.pem"]["purpose"] == "public benchmark fixture only"


def test_current_bounded_validity_cannot_be_reused_after_expiry(public_files):
    with pytest.raises(ValueError, match="validity"):
        cert.validate_certificates(public_files, now=cert.utcnow() + dt.timedelta(days=40))


def test_key_from_another_fixture_cannot_be_mixed(public_files):
    files = dict(public_files)
    files["key.pem"] = cert.create_certificates()["key.pem"]
    with pytest.raises(ValueError, match="key differ"):
        cert.validate_certificates(files)


def test_ca_cannot_be_substituted_by_unrelated_ca(public_files):
    files = dict(public_files)
    files["cacert.pem"] = files["wrongca.pem"]
    with pytest.raises(ValueError, match="issuer mismatch"):
        cert.validate_certificates(files)


@pytest.mark.parametrize("name", ["cacert.pem", "cert.pem", "key.pem", "wrongca.pem"])
def test_missing_certificate_material_is_refused(public_files, name):
    files = dict(public_files)
    files.pop(name)
    with pytest.raises(ValueError, match="exact disclosed"):
        cert.validate_certificates(files)


def test_committed_parent_has_complete_inventory(tmp_path):
    directory = tmp_path / "parent"
    write_parent(directory)
    raw, parent = cert.admit_parent(directory)
    assert cert.sha(raw) == cert.sha((directory / "requests-runtime.json").read_bytes())
    assert len(parent["installed_files"]) == 235
    assert len(parent["wheels"]) == 13


def test_v2_parent_profile_is_bound_before_any_qualification(tmp_path):
    parent = original_parent()
    parent["profile_path"] = cert.legacy.PROFILE_V2_NAME
    raw, _ = cert.legacy.frozen_profile(cert.legacy.PROFILE_V2)
    parent["profile_sha256"] = cert.sha(raw)
    directory = tmp_path / "parent"
    write_parent(directory, parent)
    with pytest.raises(ValueError):
        cert.admit_parent(directory)


@pytest.mark.parametrize("field,value", [
    ("status", "prepared_not_built"), ("cleanup_confirmed", False), ("cleanup", []),
    ("probe_compatible", False), ("base_commit", "0" * 40), ("profile_sha256", "0" * 64),
    ("target_or_plugins_imported", True), ("inventory_exit", 1), ("model_calls", 1),
    ("installed_files", {}), ("dockerfile_sha256", "0" * 64),
])
def test_unqualified_or_tampered_parent_is_refused(tmp_path, field, value):
    parent = original_parent()
    parent[field] = value
    directory = tmp_path / "parent"
    write_parent(directory, parent)
    with pytest.raises(ValueError, match="fully qualified"):
        cert.admit_parent(directory)


def test_dockerfile_changes_only_three_certificate_paths():
    image_id = "sha256:" + "a" * 64
    source = cert.dockerfile(image_id)
    assert source.splitlines()[0] == "FROM " + cert.parent_tag(image_id)
    copies = [line for line in source.splitlines() if line.startswith("COPY ")]
    assert len(copies) == 3
    assert all(line.endswith(cert.CERT_ROOT + "/" + name) for line, name in zip(copies, cert.CERT_NAMES, strict=True))
    assert "wrongca" not in source and "RUN " not in source and "CA_signing" not in source


@pytest.mark.parametrize("image_id", ["latest", "sha256:" + "a" * 63, "sha256:" + "A" * 64, "x;touch file"])
def test_parent_tag_cannot_be_a_mutable_or_shell_reference(image_id):
    with pytest.raises(ValueError, match="immutable"):
        cert.parent_tag(image_id)


def test_preparation_binds_original_and_new_bytes_separately(tmp_path, public_files, monkeypatch):
    parent_dir, context = tmp_path / "parent", tmp_path / "context"
    write_parent(parent_dir)
    monkeypatch.setattr(cert, "create_certificates", lambda: public_files)
    prepared = cert.prepare(parent_dir, context)
    checked, _, parent = cert.validate_context(context)
    assert checked == prepared
    assert set(prepared["overlay_files"]) == set(cert.CERT_PATHS)
    assert prepared["original_certificate_files"] == {path: parent["installed_files"][path] for path in cert.CERT_PATHS}
    assert all(prepared["overlay_files"][path] != parent["installed_files"][path] for path in cert.CERT_PATHS)
    assert prepared["target_or_plugins_imported"] is False
    assert prepared["CA_signing_key_retained"] is False
    assert set(path.name for path in context.iterdir()) == {"certificates", "wrongca.pem", "parent-runtime.json", "Dockerfile", "preparation.json"}
    assert set(path.name for path in (context / "certificates").iterdir()) == set(cert.CERT_NAMES)
    with pytest.raises(ValueError, match="fresh"):
        cert.prepare(parent_dir, context)


def test_changed_context_dockerfile_is_refused(tmp_path, public_files, monkeypatch):
    parent_dir, context = tmp_path / "parent", tmp_path / "context"
    write_parent(parent_dir)
    monkeypatch.setattr(cert, "create_certificates", lambda: public_files)
    cert.prepare(parent_dir, context)
    (context / "Dockerfile").write_text("FROM malicious:latest\n")
    with pytest.raises(ValueError, match="changed"):
        cert.validate_context(context)


def qualified_receipt(context, prepared, parent):
    return {**copy.deepcopy(prepared), "status": "certificate_overlay_qualified", "parent_runtime_report": parent,
        "image_id": "sha256:" + "b" * 64, "image_inspect": {"Id": "sha256:" + "b" * 64},
        "installed_files": {**parent["installed_files"], **prepared["overlay_files"]},
        "before": parent["after"], "after": parent["after"], "observed": parent["observed"],
        "probe_compatible": True, "cleanup_confirmed": True,
        "cleanup": [{"confirmed": True}, {"confirmed": True}]}


def test_receipt_cannot_qualify_an_unfinished_or_source_changed_layer(tmp_path, public_files, monkeypatch):
    parent_dir, context = tmp_path / "parent", tmp_path / "context"
    write_parent(parent_dir)
    monkeypatch.setattr(cert, "create_certificates", lambda: public_files)
    prepared = cert.prepare(parent_dir, context)
    _, _, parent = cert.validate_context(context)
    report = qualified_receipt(context, prepared, parent)
    cert.legacy.save(context / cert.RECEIPT, report)
    cert.admit_certificate_receipt(context)
    for field, value in (("status", "certificate_overlay_unavailable"), ("cleanup_confirmed", False),
                         ("overlay_files", {}), ("installed_files", parent["installed_files"]),
                         ("after", {}), ("parent_runtime_report_sha256", "0" * 64),
                         ("CA_signing_key_retained", True)):
        cert.legacy.save(context / cert.RECEIPT, {**report, field: value})
        with pytest.raises(ValueError, match="qualification mismatch"):
            cert.admit_certificate_receipt(context)
