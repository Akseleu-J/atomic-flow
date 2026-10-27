"""A private mpmath overlay is byte-bound and never admits target execution."""
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("p07_sympy13971_image", ROOT / "tools/p07_sympy13971_image.py")
image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image)


def wheel(*, active=False, unsafe=False):
    metadata = ("Name: mpmath\nVersion: 1.2.1\nRequires-Dist: pytest ; extra == 'tests'\n"
                + ("Requires-Dist: surprise>=1\n" if active else "")).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("mpmath-1.2.1.dist-info/METADATA", metadata)
        archive.writestr("mpmath/__init__.py", "__version__ = '1.2.1'\n")
        if unsafe:
            archive.writestr("../escape.py", "raise AssertionError('never execute')")
    raw = stream.getvalue()
    return {"name": "mpmath", "version": "1.2.1", "filename": "mpmath-1.2.1-py3-none-any.whl",
            "sha256": image.transplant.sha(raw), "wheel_metadata_sha256": image.transplant.sha(metadata)}, raw


def test_unselected_test_extra_does_not_expand_runtime_dependencies():
    record, raw = wheel()
    assert image.verified_wheel(record, raw) == image.transplant.sha(b"__version__ = '1.2.1'\n")


@pytest.mark.parametrize("kind", ["hash", "metadata", "version", "active_dependency", "traversal"])
def test_active_dependencies_or_changed_wheel_bytes_are_refused(kind):
    record, raw = wheel(active=kind == "active_dependency", unsafe=kind == "traversal")
    if kind == "hash":
        record["sha256"] = "0" * 64
    elif kind == "metadata":
        record["wheel_metadata_sha256"] = "0" * 64
    elif kind == "version":
        record["version"] = "1.3.0"
    with pytest.raises(ValueError):
        image.verified_wheel(record, raw)


def test_dockerfile_installs_only_locked_wheel_using_qualified_private_launcher():
    parent = "sha256:" + "a" * 64
    text = image.dockerfile(parent)
    assert text.startswith("FROM " + image.parent_tag(parent) + "\n")
    assert "RUN --network=none /opt/p07-runtime/python36 -m pip install" in text
    assert "--no-index --no-deps --no-compile --require-hashes" in text
    assert "sympy" not in text.lower() and "/testbed" not in text
    assert "apt-get" not in text and "curl" not in text
    with pytest.raises(ValueError):
        image.dockerfile("mutable:tag")


def test_parent_qualification_cannot_be_inferred_from_image_id_alone(tmp_path):
    (tmp_path / "legacy-transplant.json").write_text(json.dumps({"image_id": "sha256:" + "a" * 64}))
    with pytest.raises(ValueError, match="independently qualified"):
        image.parent_report(tmp_path)


def test_profile_or_existing_output_is_refused_without_download(tmp_path):
    changed = tmp_path / "profile.json"
    changed.write_bytes(image.PROFILE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="reviewed checkout"):
        image.prepare(changed, tmp_path, tmp_path / "context", downloader=lambda url: pytest.fail("no download"))
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        image.prepare(image.PROFILE, tmp_path, output, downloader=lambda url: pytest.fail("no download"))


def test_non_linux_build_never_reads_or_executes_context(tmp_path, monkeypatch):
    monkeypatch.setattr(image.platform, "system", lambda: "Windows")
    monkeypatch.setattr(image.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not spawn"))
    with pytest.raises(ValueError, match="Linux"):
        image.build(tmp_path / "absent", tmp_path / "output")
