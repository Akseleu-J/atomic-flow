"""A dependency overlay never admits source archives or mutable image inputs."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p07_dependency_image", ROOT / "tools/p07_dependency_image.py")
dependency = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dependency)
TASK = "sphinx-doc__sphinx-8721"


def wheel(name, version, *, member=None, requires=None, metadata_name=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        text = f"Name: {metadata_name or name}\nVersion: {version}\n"
        if requires:
            text += "Requires-Dist: " + requires + "\n"
        archive.writestr(name + ".dist-info/METADATA", text)
        archive.writestr(member or name + "/__init__.py", "raise AssertionError('must never import on host')\n")
    return stream.getvalue()


def prepared_profile(tmp_path):
    config = json.loads((ROOT / "benchmarks/stage4/p07-sphinx-environment-profile.json").read_text())
    payloads = {}
    for record in config["wheels"]:
        raw = wheel(record["name"], record["version"])
        record["sha256"] = hashlib.sha256(raw).hexdigest()
        payloads[record["url"]] = raw
        payloads[record["primary_metadata_url"]] = json.dumps({"urls": [{
            "filename": record["filename"], "url": record["url"],
            "digests": {"sha256": record["sha256"]}}]}).encode()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(config))
    return profile, payloads


def test_preparation_only_records_hashed_wheels_and_offline_install(tmp_path):
    profile, payloads = prepared_profile(tmp_path)
    output = tmp_path / "context"
    receipt = dependency.prepare(profile, TASK, output, fetch=payloads.__getitem__)
    assert receipt["status"] == "prepared_not_built" and receipt["model_calls"] == 0
    assert receipt["calibrated"] is False and receipt["target_setup_or_tests_executed"] is False
    docker = (output / "Dockerfile").read_text()
    assert "RUN --network=none" in docker
    assert "--no-index --no-deps --no-compile --require-hashes" in docker
    assert "pytest" not in docker and "setup.py" not in docker
    assert docker.startswith("FROM " + receipt["base_image"] + "\n")
    assert set(path.name for path in (output / "wheels").iterdir()) == {item["filename"] for item in receipt["wheels"]}
    with pytest.raises(ValueError, match="fresh"):
        dependency.prepare(profile, TASK, output, fetch=payloads.__getitem__)


@pytest.mark.parametrize("change", ["hash", "metadata", "dependency", "traversal", "absolute", "backslash"])
def test_untrusted_wheel_changes_are_refused_before_import(change):
    raw = wheel("docutils", "0.16", metadata_name="unrelated" if change == "metadata" else None,
                requires="unreviewed>=1" if change == "dependency" else None,
                member={"traversal": "../outside.py", "absolute": "/absolute.py", "backslash": "..\\outside.py"}.get(change))
    record = {"name": "docutils", "version": "0.16", "filename": "docutils-0.16-py2.py3-none-any.whl",
              "sha256": "a" * 64 if change == "hash" else dependency.sha(raw)}
    with pytest.raises(ValueError):
        dependency.validate_wheel(record, raw)


@pytest.mark.parametrize("url", ["http://files.pythonhosted.org/a", "https://evil.example/a",
                                "https://user@files.pythonhosted.org/a", "https://files.pythonhosted.org/a?q=secret"])
def test_dependency_source_urls_cannot_change_transport_or_authority(url):
    with pytest.raises(ValueError):
        dependency.checked_url(url, "files.pythonhosted.org")


def test_primary_metadata_mismatch_stops_before_context_creation(tmp_path):
    profile, payloads = prepared_profile(tmp_path)
    config = json.loads(profile.read_text())
    payloads[config["wheels"][0]["primary_metadata_url"]] = b'{"urls": []}'
    output = tmp_path / "context"
    with pytest.raises(ValueError, match="primary PyPI"):
        dependency.prepare(profile, TASK, output, fetch=payloads.__getitem__)
    assert not output.exists()


def test_mutable_or_wrong_task_base_is_not_prepared(tmp_path):
    profile, payloads = prepared_profile(tmp_path)
    config = json.loads(profile.read_text())
    target = next(item for item in config["tasks"] if item["instance_id"] == TASK)
    target["base_image_digest"] = "swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8721:latest"
    profile.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="profile base"):
        dependency.prepare(profile, TASK, tmp_path / "context", fetch=payloads.__getitem__)


def test_modified_dockerfile_cannot_be_authorized_by_rehashing_receipt(tmp_path, monkeypatch):
    profile, payloads = prepared_profile(tmp_path)
    context = tmp_path / "context"
    receipt = dependency.prepare(profile, TASK, context, fetch=payloads.__getitem__)
    mutated = (context / "Dockerfile").read_bytes() + b"RUN curl https://unreviewed.example/\n"
    (context / "Dockerfile").write_bytes(mutated)
    receipt["dockerfile_sha256"] = dependency.sha(mutated)
    dependency.save(context / "preparation.json", receipt)
    monkeypatch.setattr(dependency.platform, "system", lambda: "Linux")
    monkeypatch.setattr(dependency.subprocess, "run", lambda *args, **kwargs: pytest.fail("Docker must not be called"))
    with pytest.raises(ValueError, match="context differs"):
        dependency.build(context, tmp_path / "build")


def test_build_is_refused_on_non_linux_before_reading_context(tmp_path, monkeypatch):
    monkeypatch.setattr(dependency.platform, "system", lambda: "Windows")
    with pytest.raises(ValueError, match="Linux"):
        dependency.build(tmp_path / "absent", tmp_path / "build")
