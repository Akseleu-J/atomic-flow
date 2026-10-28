"""Pure Requests fixture/overlay byte proofs, with no targets or key loading."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from atomic_flow.agents.step_evidence import manifest_digest
from atomic_flow.check_fixture import ExecutionOverlay, FixtureIdentity, PrivatePytestIdentity, reviewed_helper_inventory

ROOT = Path(__file__).resolve().parents[1]
CA = b"-----BEGIN CERTIFICATE-----\npublic-benchmark-fixture\n-----END CERTIFICATE-----\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def helpers(variant):
    return {path: (ROOT / path).read_bytes() for path, _ in reviewed_helper_inventory(variant)}


def pytest_identity(variant="function36-v3", task_id="psf__requests-1963", version=(3, 6, 15)):
    files = {"pytest.py": b"entrypoint", "_pytest/__init__.py": b"private package"}
    identity = PrivatePytestIdentity(task_id, variant, "a" * 64, version,
                                     tuple(sorted((path, sha(raw)) for path, raw in files.items())), reviewed_helper_inventory(variant))
    return identity, files


def fixture(variant="function36-v3", task_id="psf__requests-1963"):
    profile_paths = {"3362-cn": "p07-requests-legacy-profile-v2.json", "function36-v3": "p07-requests-function-service-profile-v3.json",
                     "2674-v2": "p07-requests2674-python27-service-profile-v2.json", "863-v7": "p07-requests863-python27-service-profile-v7.json"}
    raw_profile = (ROOT / "benchmarks/stage4" / profile_paths[variant]).read_bytes().replace(b"\r\n", b"\n")
    cert_root = "/opt/p07-python36/lib/python3.6/site-packages/pytest_httpbin/certs/" if variant == "3362-cn" else "/opt/p07-function-fixture/"
    certs = {cert_root + name: CA for name in ("cacert.pem", "cert.pem")}
    metadata = {"common_name": "127.0.0.1" if variant == "3362-cn" else "httpbin.org",
                "dns_sans": [] if variant == "3362-cn" else sorted(["httpbin.org", "github.com"] if variant == "863-v7" else ["httpbin.org"]), "ip_sans": []}
    raw_metadata = json.dumps(metadata).encode()
    health = b"fixed reviewed health policy bytes"
    identity = FixtureIdentity(task_id, variant, "a" * 64, "sha256:" + "b" * 64,
                               sha(raw_profile), reviewed_helper_inventory(variant), tuple(sorted((path, sha(raw)) for path, raw in certs.items())),
                               sha(raw_metadata), sha(health))
    supplied = dict(profile_bytes=raw_profile, helper_bytes=helpers(variant), public_certificate_bytes=certs,
                    certificate_metadata_bytes=raw_metadata, health_policy_bytes=health)
    return identity, supplied


def overlay():
    original = b"original upstream CA bundle\n"
    result = original + b"\n" + CA
    before = {"requests/cacert.pem": hashlib.sha1(original).hexdigest(), "requests/api.py": "a" * 40}
    after = before | {"requests/cacert.pem": hashlib.sha1(result).hexdigest()}
    candidate, execution = manifest_digest(before), manifest_digest(after)
    identity = ExecutionOverlay("psf__requests-1963", candidate, execution, "b" * 64, "c" * 64, "d" * 64,
                                sha(original), sha(CA), sha(result), candidate, execution)
    return identity, dict(original=original, fixture_ca=CA, result=result, before_manifest=before, after_manifest=after)


def test_pytest_dependency_bytes_and_helper_bytes_are_both_complete_and_immutable():
    identity, files = pytest_identity()
    assert identity.validate_bytes(installed_files_bytes=files, helper_bytes=helpers(identity.variant)) == identity.digest
    with pytest.raises(FrozenInstanceError):
        identity.pytest_version = "modern"
    with pytest.raises(ValueError):
        identity.validate_bytes(installed_files_bytes=files | {"foreign.py": b"foreign"}, helper_bytes=helpers(identity.variant))
    changed = files | {"pytest.py": b"substituted"}
    with pytest.raises(ValueError):
        identity.validate_bytes(installed_files_bytes=changed, helper_bytes=helpers(identity.variant))
    changed_helpers = helpers(identity.variant)
    changed_helpers[next(iter(changed_helpers))] += b"changed"
    with pytest.raises(ValueError):
        identity.validate_bytes(installed_files_bytes=files, helper_bytes=changed_helpers)


def test_empty_installed_package_initializer_preserves_exact_inventory_hash():
    identity, files = pytest_identity()
    files["_pytest/empty_package/__init__.py"] = b""
    identity = replace(identity, installed_files=tuple(sorted((path, sha(raw)) for path, raw in files.items())))
    assert identity.validate_bytes(installed_files_bytes=files, helper_bytes=helpers(identity.variant)) == identity.digest
    with pytest.raises(ValueError):
        identity.validate_bytes(installed_files_bytes=files | {"_pytest/empty_package/__init__.py": b"changed"},
                                helper_bytes=helpers(identity.variant))


@pytest.mark.parametrize("variant,task_id,version", [
    ("3362-cn", "psf__requests-3362", (3, 6, 15)), ("function36-v3", "psf__requests-1963", (3, 6, 15)),
    ("function36-v3", "psf__requests-2148", (3, 6, 15)), ("function36-v3", "psf__requests-2317", (3, 6, 15)),
    ("2674-v2", "psf__requests-2674", (2, 7, 18)), ("863-v7", "psf__requests-863", (2, 7, 13)),
])
def test_six_exact_task_variants_do_not_collapse_runtime_or_certificate_semantics(variant, task_id, version):
    runner, _ = pytest_identity(variant, task_id, version)
    identity, supplied = fixture(variant, task_id)
    assert identity.validate_bytes(**supplied) == identity.digest
    assert runner.as_dict()["pytest_version"] == "2.8.7"
    assert identity.as_dict()["certificate_assurance"] == "metadata_and_bytes_integrity_only"
    assert len(identity.host_mappings) == (0 if variant == "3362-cn" else 10 if variant == "863-v7" else 1 if task_id.endswith("1963") else 2)
    assert runner.site_packages.startswith("/opt/p07-python36/" if version[0] == 3 else "/opt/p07-python27/")


@pytest.mark.parametrize("changes", [
    {"variant": "unknown"}, {"task_id": "psf__requests-863"}, {"python_version": [3, 6, 15]},
    {"python_version": (2, 7, 18)}, {"pytest_version": "8.0"}, {"plugin_args": ("--no-header", "-x")},
    {"entrypoints": (("arbitrary", "foreign_plugin"),)}, {"runtime_digest": "A" * 64},
    {"helper_sha256": (("tools/arbitrary.py", "a" * 64),)},
    {"installed_files": (("../pytest.py", "a" * 64),)},
    {"installed_files": (("_pytest/__init__.py", "a" * 64), ("fixture/key.pem", "b" * 64), ("pytest.py", "c" * 64))},
])
def test_private_pytest_rejects_runtime_plugin_or_helper_widening(changes):
    with pytest.raises(ValueError):
        replace(pytest_identity()[0], **changes)


def test_fixture_refuses_foreign_profiles_wrong_sans_unbound_bytes_and_private_key_material():
    identity, supplied = fixture()
    with pytest.raises(ValueError):
        replace(identity, profile_sha256="a" * 64)
    for key in ("profile_bytes", "certificate_metadata_bytes", "health_policy_bytes"):
        with pytest.raises(ValueError):
            identity.validate_bytes(**(supplied | {key: b"changed"}))
    metadata = json.dumps({"common_name": "httpbin.org", "dns_sans": ["foreign.example"], "ip_sans": []}).encode()
    changed = replace(identity, certificate_metadata_sha256=sha(metadata))
    with pytest.raises(ValueError, match="metadata differs"):
        changed.validate_bytes(**(supplied | {"certificate_metadata_bytes": metadata}))
    certs = {path: b"-----BEGIN PRIVATE KEY-----\nnot-public" for path in supplied["public_certificate_bytes"]}
    changed = replace(identity, certificate_files=tuple(sorted((path, sha(raw)) for path, raw in certs.items())))
    with pytest.raises(ValueError, match="public certificate"):
        changed.validate_bytes(**(supplied | {"public_certificate_bytes": certs}))


def test_overlay_reuses_core_sha1_file_manifests_and_sha256_byte_bindings():
    identity, supplied = overlay()
    assert len(supplied["before_manifest"][identity.path]) == 40
    assert len(identity.original_ca_sha256) == 64
    assert identity.validate_bytes(**supplied) == identity.digest
    assert identity.candidate_revision == manifest_digest(supplied["before_manifest"])
    assert identity.execution_revision == manifest_digest(supplied["after_manifest"])
    assert identity.as_dict()["hidden_mount"] is False


@pytest.mark.parametrize("change", ["other_file", "new_file", "missing_file", "sha256_file_value", "different_original", "different_ca", "different_result"])
def test_overlay_refuses_all_source_or_byte_substitution(change):
    identity, supplied = overlay()
    if change == "other_file":
        supplied["after_manifest"]["requests/api.py"] = "b" * 40
    elif change == "new_file":
        supplied["after_manifest"]["extra.py"] = "b" * 40
    elif change == "missing_file":
        supplied["after_manifest"].pop("requests/api.py")
    elif change == "sha256_file_value":
        supplied["before_manifest"][identity.path] = sha(supplied["original"])
    else:
        key = {"different_original": "original", "different_ca": "fixture_ca", "different_result": "result"}[change]
        supplied[key] += b"changed"
    with pytest.raises(ValueError):
        identity.validate_bytes(**supplied)


def test_overlay_rejects_arbitrary_operations_identical_source_and_3362():
    identity, _ = overlay()
    for changes in ({"path": "requests/api.py"}, {"operation": "replace_file"}, {"task_id": "psf__requests-3362"},
                    {"execution_revision": identity.candidate_revision, "after_manifest_sha256": identity.candidate_revision}):
        with pytest.raises(ValueError):
            replace(identity, **changes)
