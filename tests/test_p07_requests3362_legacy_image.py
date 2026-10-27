"""Requests legacy dependencies are verified as bytes without importing them."""
import ast
import copy
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("p07_requests3362_legacy_image", ROOT / "tools/p07_requests3362_legacy_image.py")
image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image)


def wheel(record, *, traversal=False, duplicate=False):
    metadata = "Name: " + record["name"] + "\nVersion: " + record["version"] + "\n"
    if record["requires_python"]:
        metadata += "Requires-Python: " + record["requires_python"] + "\n"
    metadata += "".join("Requires-Dist: " + requirement + "\n" for requirement in record["requires_dist"])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        package = image.canonicalize_name(record["name"]).replace("-", "_")
        archive.writestr(package + "-" + record["version"] + ".dist-info/METADATA", metadata)
        archive.writestr(package + "/__init__.py", "raise AssertionError('must never import')\n")
        if traversal:
            archive.writestr("../escape", "x")
        if duplicate:
            archive.writestr(package + "/__init__.py", "shadow")
    raw = stream.getvalue()
    return {**record, "bytes": len(raw), "sha256": image.sha(raw)}, raw


def record():
    return {"name": "example", "version": "1.0", "filename": "example-1.0-py2.py3-none-any.whl",
            "requires_python": None, "requires_dist": []}


def test_wheel_inventory_reads_metadata_and_modules_without_importing():
    selected, raw = wheel(record())
    proof = image.verified_wheel(selected, raw)
    assert proof["metadata_sha256"] and proof["installed_files"]["example/__init__.py"]
    assert len(proof["installed_files"]) == 2


@pytest.mark.parametrize("mutation", ["hash", "size", "metadata", "python", "platform", "traversal", "duplicate"])
def test_changed_unsafe_or_incompatible_wheel_is_refused(mutation):
    selected, raw = wheel(record(), traversal=mutation == "traversal", duplicate=mutation == "duplicate")
    if mutation == "hash":
        selected["sha256"] = "0" * 64
    elif mutation == "size":
        selected["bytes"] += 1
    elif mutation == "metadata":
        selected["requires_dist"] = ["surprise"]
    elif mutation == "python":
        selected, raw = wheel({**record(), "requires_python": ">=3.9"})
    elif mutation == "platform":
        selected["filename"] = "example-1.0-cp39-cp39-win_amd64.whl"
    with pytest.raises(ValueError):
        image.verified_wheel(selected, raw)


def test_cp36_manylinux_binary_wheel_tag_is_admitted():
    selected, raw = wheel({**record(), "filename": "example-1.0-cp36-cp36m-manylinux1_x86_64.whl"})
    assert image.verified_wheel(selected, raw)["version"] == "1.0"


def test_reviewed_active_closure_respects_linux_python36_and_unselected_extras():
    profile = json.loads(image.PROFILE.read_bytes())
    verified = [{"name": item["name"], "version": item["version"], "requires_dist": item["requires_dist"]}
                for item in profile["wheels"]]
    assert len(image.closure(profile, verified)) == 16
    altered = copy.deepcopy(verified)
    altered[0]["requires_dist"].append("surprise>=1")
    with pytest.raises(ValueError, match="outside"):
        image.closure(profile, altered)
    altered = copy.deepcopy(verified)
    altered[0]["requires_dist"].append("six>=99")
    with pytest.raises(ValueError, match="outside"):
        image.closure(profile, altered)


def test_dockerfile_keeps_target_base_and_offline_hashed_private_install():
    source = image.dockerfile()
    assert "FROM " + image.BASE + "\n" in source and image.transplant.BASE not in source
    assert "FROM " + image.DONOR + " AS donor" in source
    assert "--network=none /opt/p07-runtime/python36 -m pip install" in source
    assert "--no-index --no-deps --no-compile --ignore-installed --require-hashes" in source
    assert "setup.py" not in source and "pytest " not in source and "verify=False" not in source


def test_inventory_program_uses_only_stdlib_and_preserves_byte_obligations():
    source = image.inventory_source({"example/__init__.py": "a" * 64})
    ast.parse(source)
    assert "import hashlib,json,pathlib,sys" in source
    assert "import pytest" not in source and "import requests" not in source and "import httpbin" not in source
    assert "is_symlink" in source and "installed dependency bytes mismatch" in source


@pytest.fixture
def context(tmp_path, monkeypatch):
    profile = json.loads(image.PROFILE.read_bytes())
    payloads = {}
    for index, selected in enumerate(profile["wheels"]):
        changed, raw = wheel(selected)
        profile["wheels"][index] = changed
        payloads[changed["url"]] = raw
        payloads[changed["primary_metadata_url"]] = json.dumps({"urls": [{"filename": changed["filename"],
            "url": changed["url"], "digests": {"sha256": changed["sha256"]}}]}).encode()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(image, "PROFILE", path)
    output = tmp_path / "context"
    report = image.prepare(path, output, downloader=payloads.__getitem__)
    return path, output, report


def test_complete_preparation_reverifies_all13_and_never_claims_calibration(context):
    _, output, report = context
    assert len(report["wheels"]) == 13 and report["status"] == "prepared_not_built"
    assert report["calibrated"] is False and report["target_or_plugins_imported"] is False
    assert image.validate_context(output)["installed_files"] == report["installed_files"]


@pytest.mark.parametrize("target", ["Dockerfile", "requirements.lock", "python36", "wheel"])
def test_context_tampering_is_refused_before_worker_execution(context, target):
    _, output, report = context
    path = output / target if target != "wheel" else output / "wheels" / report["wheels"][0]["filename"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        image.validate_context(output)


def test_windows_build_refused_before_reading_context(tmp_path, monkeypatch):
    monkeypatch.setattr(image.platform, "system", lambda: "Windows")
    with pytest.raises(ValueError, match="Linux"):
        image.build(tmp_path / "absent", tmp_path / "output")


def test_large_inventory_is_streamed_to_existing_readonly_container_without_copy():
    payload = image.inventory_source({"package/file" + str(index) + ".py": "a" * 64 for index in range(3000)})
    assert len(payload.encode()) > 128 * 1024
    calls = []
    sentinel = object()

    def runner(argv, timeout, *, input_bytes):
        calls.append((argv, timeout, input_bytes))
        return sentinel

    assert image.run_inventory("p07-transplant-fixed", payload, runner) is sentinel
    argv, timeout, data = calls[0]
    assert data == payload.encode() and timeout == 60
    assert argv[:4] == ["docker", "exec", "-i", "p07-transplant-fixed"]
    assert argv[4] == image.transplant.LAUNCHER and argv[5] == "-c"
    assert len(argv[-1]) < 200 and payload not in argv
    assert "cp" not in argv and "/tmp/p07-inventory.py" not in argv
    ast.parse(argv[-1])


def test_fixed_worker_command_failure_retains_bounded_daemon_reason():
    from types import SimpleNamespace

    stderr = b"read-only filesystem: cannot write destination\n" + b"x" * 4000
    process = SimpleNamespace(returncode=1, stdout=b"", stderr=stderr)
    receipt = image.command_failure(["docker", "cp", "host-owned-code", "container:/tmp/file"], process)
    assert receipt["exit_code"] == 1 and "read-only filesystem" in receipt["stderr"]
    assert receipt["stderr_truncated"] and len(receipt["stderr"]) == 2000
    assert receipt["stderr_sha256"] == image.sha(stderr)
    assert receipt["command"] == ["docker", "cp"]
