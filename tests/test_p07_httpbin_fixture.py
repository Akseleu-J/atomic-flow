"""No fixture package availability or TLS/network semantics are fabricated."""
import ast
import hashlib
import importlib.util
import json
import sysconfig
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p07_httpbin_fixture", ROOT / "tools/p07_httpbin_fixture.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
builder_spec = importlib.util.spec_from_file_location("prepare_p07_httpbin_image", ROOT / "tools/prepare_p07_httpbin_image.py")
builder = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(builder)


def test_real_round9_requests_image_lacks_httpbin_fixture_packages():
    freeze = "pytest @ file:///croot/pytest_1717793244625/work\nrequests @ file:///testbed\ncertifi==2024.8.30\n"
    report = fixture.readiness(fixture.freeze_versions(freeze), require_plugin=True)
    assert report["status"] == "unavailable" and not report["ready_to_start_real_fixture"]
    assert set(report["packages"]) == {"httpbin", "flask", "werkzeug", "pytest-httpbin"}
    assert all(row["status"] == "missing_distribution" for row in report["packages"].values())
    assert report["model_calls"] == 0 and not report["fixture_started"]


def test_presence_without_reviewed_exact_pins_does_not_admit_fixture():
    installed = {"HTTPBin": "reviewed-a", "Flask": "reviewed-b", "Werkzeug": "reviewed-c"}
    report = fixture.readiness(installed)
    assert all(row["status"] == "reviewed_pin_missing" for row in report["packages"].values())
    assert not report["ready_to_start_real_fixture"]


def test_exact_fixture_dependency_lock_is_not_a_claim_of_running_server():
    expected = {"httpbin": "1", "Flask": "2", "Werkzeug": "3", "pytest_httpbin": "4"}
    report = fixture.readiness(expected, expected, require_plugin=True)
    assert report["ready_to_start_real_fixture"] and not report["fixture_started"]
    assert report["status"] == "packages_ready_runtime_fixture_not_started"
    assert "never verify=False" in report["tls_contract"]


def test_mismatched_or_unversioned_fixture_distribution_fails_closed():
    installed = fixture.freeze_versions("httpbin @ file:///unversioned\nFlask==2\nWerkzeug==3\n")
    report = fixture.readiness(installed, {"httpbin": "1", "flask": "99", "werkzeug": "3"})
    assert report["packages"]["httpbin"]["status"] == "installed_version_unverified"
    assert report["packages"]["flask"]["status"] == "version_mismatch"
    assert not report["ready_to_start_real_fixture"]


def test_plugin_is_explicit_requirement_only_for_plugin_based_requests_suite():
    versions = {"httpbin": "1", "flask": "2", "werkzeug": "3"}
    assert fixture.readiness(versions, versions)["ready_to_start_real_fixture"]
    assert not fixture.readiness(versions, versions, require_plugin=True)["ready_to_start_real_fixture"]


def test_serving_requires_pinned_fixture_dependencies_and_test_certificate_hashes(tmp_path, monkeypatch):
    certificate, key = tmp_path / "certificate.pem", tmp_path / "test-key.pem"
    certificate.write_bytes(b"test certificate bytes")
    key.write_bytes(b"public benchmark fixture key bytes")
    packages = {"httpbin": "1", "Flask": "2", "Werkzeug": "3", "dependency": "4"}
    lock = {"schema_version": 1, "packages": packages, "wheel_hashes": {"fixture.whl": "a" * 64},
            "certificate_sha256": hashlib.sha256(certificate.read_bytes()).hexdigest(),
            "test_key_sha256": hashlib.sha256(key.read_bytes()).hexdigest()}
    monkeypatch.setattr(fixture.importlib.metadata, "version", lambda name: packages[name])
    assert fixture.verify_serve_lock(lock, certificate, key) == packages
    certificate.write_bytes(b"changed certificate")
    with pytest.raises(ValueError, match="TLS fixture hash mismatch"):
        fixture.verify_serve_lock(lock, certificate, key)


def test_serving_does_not_accept_merely_installed_unlocked_dependencies(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="versioned fixture package lock"):
        fixture.verify_serve_lock({}, tmp_path / "cert", tmp_path / "key")
    monkeypatch.setattr(fixture.importlib.metadata, "version", lambda name: "999")
    with pytest.raises(ValueError, match="version mismatch"):
        fixture.verify_serve_lock({"schema_version": 1, "packages": {"httpbin": "1", "Flask": "2", "Werkzeug": "3"}},
                                  tmp_path / "cert", tmp_path / "key")


def wheel(tmp_path, name, requires=()):
    path = tmp_path / (name + "-1.0-py3-none-any.whl")
    metadata = "Metadata-Version: 2.1\nName: " + name + "\nVersion: 1.0\n"
    metadata += "".join("Requires-Dist: " + requirement + "\n" for requirement in requires)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(name + "-1.0.dist-info/METADATA", metadata)
    return path


def test_wheel_closure_is_verified_from_bytes_and_primary_metadata(tmp_path):
    first = wheel(tmp_path, "fixture", ["dependency>=1.0"])

    def metadata(url):
        name = url.split("/")[-3]
        path = tmp_path / (name + "-1.0-py3-none-any.whl")
        return {"urls": [{"filename": path.name, "packagetype": "bdist_wheel",
            "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, "url": "https://files.pythonhosted.org/fixture.whl"}]}

    with pytest.raises(ValueError, match="dependency closure"):
        builder.validate_wheels(tmp_path, {"fixture": "1.0"}, metadata)
    wheel(tmp_path, "dependency")
    locked, requirements = builder.validate_wheels(tmp_path, {"fixture": "1.0", "dependency": "1.0"}, metadata)
    assert set(locked) == {"fixture", "dependency"}
    assert len(requirements) == 2 and all("--hash=sha256:" in line for line in requirements)
    assert locked["fixture"]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()


def test_wheel_digest_mismatch_cannot_be_accepted(tmp_path):
    path = wheel(tmp_path, "fixture")
    with pytest.raises(ValueError, match="primary PyPI digest"):
        builder.validate_wheels(tmp_path, {"fixture": "1.0"}, lambda url: {"urls": [{
            "filename": path.name, "packagetype": "bdist_wheel", "digests": {"sha256": "a" * 64}}]})


def test_lock_order_uses_canonical_package_names_not_platform_path_order(tmp_path):
    paths = {name.lower().replace("_", "-"): wheel(tmp_path, name) for name in ("Zulu", "alpha", "alpha_extra")}

    def metadata(url):
        path = paths[url.split("/")[-3]]
        return {"urls": [{"filename": path.name, "packagetype": "bdist_wheel",
            "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            "url": "https://files.pythonhosted.org/fixture.whl"}]}

    _, requirements = builder.validate_wheels(tmp_path, {"Zulu": "1.0", "alpha": "1.0", "alpha-extra": "1.0"}, metadata)
    assert [line.split("==", 1)[0] for line in requirements] == ["alpha", "alpha-extra", "zulu"]


def test_preparation_json_hashes_exact_bytes_on_windows(tmp_path):
    path = tmp_path / "lock.json"
    builder.json_write(path, {"fixture": "value"})
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    assert json.loads(raw) == {"fixture": "value"}


def test_generated_finalizer_writes_parseable_runtime_lock_and_identical_adapter(tmp_path, monkeypatch):
    # Execute the actual generated image-build program, rather than checking
    # that it merely compiles: escaping mistakes can produce invalid JSON.
    tree = ast.parse(Path(builder.__file__).read_text())
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "finalize" for target in node.targets))
    program = ast.literal_eval(assignment.value)
    marker = "pathlib.Path('/opt/p07-fixture')"
    assert program.count(marker) == 1
    program = program.replace(marker, "pathlib.Path(" + repr(str(tmp_path)) + ")")
    plugin = b"# reviewed host-owned pytest fixture adapter\n"
    (tmp_path / "p07_httpbin_pytest.py").write_bytes(plugin)
    (tmp_path / "httpbin-cert.pem").write_bytes(b"benchmark test certificate")
    (tmp_path / "httpbin-key.pem").write_bytes(b"benchmark test key")
    lock = {"fixture_sources_sha256": {"p07_httpbin_pytest.py": hashlib.sha256(plugin).hexdigest()}}
    builder.json_write(tmp_path / "fixture-lock.build.json", lock)
    site_packages = tmp_path / "target-site-packages"
    site_packages.mkdir()
    monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(site_packages)})
    exec(compile(program, "finalize_fixture.py", "exec"), {})
    raw = (tmp_path / "fixture-lock.json").read_bytes()
    actual = json.loads(raw)
    assert raw.endswith(b"\n") and not raw.endswith(b"\\n")
    assert actual["fixture_sources_sha256"] == lock["fixture_sources_sha256"]
    for key, name in (("certificate_sha256", "httpbin-cert.pem"), ("test_key_sha256", "httpbin-key.pem")):
        assert actual[key] == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    assert (site_packages / "p07_httpbin_pytest.py").read_bytes() == plugin
