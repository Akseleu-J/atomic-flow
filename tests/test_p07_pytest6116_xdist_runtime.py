"""The runtime candidate must bind a complete, matching double-build receipt."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import p07_pytest6116_xdist_runtime as runtime  # noqa: E402


def artifact(*, second=b"wheel", clean=True, commit=None):
    wheel = b"wheel"
    digest = hashlib.sha256(wheel).hexdigest()
    summary = {
        "status": "offline_double_build_byte_identical",
        "runtime_acceptance": False,
        "attempts": [{"status": "built", "cleanup_confirmed": clean, "wheel_sha256": digest}, {"status": "built", "cleanup_confirmed": True, "wheel_sha256": digest}],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tested-commit.txt", commit or runtime.BUILD_COMMIT)
        z.writestr("diagnostic/summary.json", json.dumps(summary))
        z.writestr("diagnostic/preparation.json", json.dumps({"profile_sha256": runtime.source.PROFILE_SHA256}))
        z.writestr("diagnostic/build-1.whl", wheel)
        z.writestr("diagnostic/build-2.whl", second)
    return buf.getvalue(), digest


def test_artifact_binds_archive_commit_two_wheels_and_cleanup(monkeypatch):
    raw, digest = artifact()
    monkeypatch.setattr(runtime, "ARCHIVE_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(runtime, "WHEEL_SHA", digest)
    monkeypatch.setattr(runtime.built, "check_wheel", lambda raw: {"sha256": hashlib.sha256(raw).hexdigest()})
    assert runtime.verify_build_artifact(raw)[0] == b"wheel"
    with pytest.raises(ValueError, match="archive differs"):
        runtime.verify_build_artifact(raw + b"changed")
    for values in ({"second": b"other"}, {"clean": False}, {"commit": "0" * 40}):
        raw, _ = artifact(**values)
        monkeypatch.setattr(runtime, "ARCHIVE_SHA", hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError, match="receipt"):
            runtime.verify_build_artifact(raw)


def test_smoke_source_is_compileable_and_keeps_focused_scope():
    compile(runtime.SMOKE, "<xdist-smoke>", "exec")
    assert runtime.SMOKE.count("testing/test_runner.py::TestExecutionForked::") == 2
    assert all(name in runtime.SMOKE for name in ("xdist.boxed", "xdist.looponfail", "execnet"))
