"""Private legacy donor startup is separate from target calibration."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p07_legacy_transplant", ROOT / "tools/p07_legacy_transplant.py")
transplant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transplant)


def runtime_observation():
    return {"version_info": [3, 6, 15], "prefix": transplant.PREFIX, "base_prefix": transplant.PREFIX,
            "executable": transplant.PREFIX + "/bin/python3.6", "stdlib": transplant.PREFIX + "/lib/python3.6",
            "sys_path": [transplant.PREFIX + "/lib/python3.6", transplant.PREFIX + "/lib/python3.6/lib-dynload"],
            "imports": {name: {"status": "imported", "path": transplant.PREFIX + "/lib/python3.6/" + name + ".py"}
                        for name in ("ssl", "ctypes", "sqlite3", "bz2", "lzma", "encodings")},
            "mapped_libraries": [transplant.PREFIX + "/lib/libpython3.6m.so.1.0",
                                 "/opt/p07-donor/lib/x86_64-linux-gnu/libc-2.28.so",
                                 "/opt/p07-donor/lib/x86_64-linux-gnu/ld-2.28.so"]}


def test_prepared_context_copies_only_private_runtime_destinations(tmp_path):
    context = tmp_path / "context"
    receipt = transplant.prepare(context)
    text = (context / "Dockerfile").read_text()
    assert text.startswith("FROM " + transplant.DONOR + " AS donor\nFROM " + transplant.BASE + "\n")
    destinations = [line.split()[-1] for line in text.splitlines() if line.startswith("COPY ")]
    assert all(path.startswith("/opt/p07-") for path in destinations)
    assert "/testbed" not in text and "apt-get" not in text and "pip install" not in text
    assert "RUN --network=none chmod" in text
    assert receipt["calibrated"] is False and receipt["target_setup_or_tests_executed"] is False
    assert receipt["launcher_sha256"] == transplant.sha((context / "python36").read_bytes())
    with pytest.raises(ValueError, match="fresh"):
        transplant.prepare(context)


def test_full_private_runtime_observation_is_required():
    assert transplant.validate_probe(runtime_observation())


@pytest.mark.parametrize("change", ["version", "prefix", "executable", "stdlib", "module", "hostlib", "libpython", "path", "exception"])
def test_mixed_host_conda_or_incomplete_runtime_never_qualifies(change):
    observed = runtime_observation()
    if change == "version":
        observed["version_info"] = [3, 9, 20]
    elif change in {"prefix", "executable", "stdlib"}:
        observed[change] = "/usr/local"
    elif change == "module":
        observed["imports"]["ssl"]["path"] = "/opt/miniconda3/lib/python3.9/ssl.py"
    elif change == "hostlib":
        observed["mapped_libraries"].append("/lib/x86_64-linux-gnu/libssl.so.3")
    elif change == "libpython":
        observed["mapped_libraries"] = [path for path in observed["mapped_libraries"] if "libpython" not in path]
    elif change == "path":
        observed["sys_path"].append("/testbed")
    else:
        observed["imports"]["ctypes"] = {"status": "exception", "exception_type": "ImportError"}
    assert transplant.validate_probe(observed) is False


def test_changed_build_context_is_refused_before_docker(tmp_path, monkeypatch):
    context = tmp_path / "context"
    transplant.prepare(context)
    with (context / "Dockerfile").open("a") as stream:
        stream.write("COPY donor-root /\n")
    monkeypatch.setattr(transplant.platform, "system", lambda: "Linux")
    monkeypatch.setattr(transplant.subprocess, "run", lambda *args, **kwargs: pytest.fail("must refuse before Docker"))
    with pytest.raises(ValueError, match="fixed reviewed bytes"):
        transplant.build(context, tmp_path / "build")


def test_pull_failure_retains_original_diagnostics_without_target_start(tmp_path, monkeypatch):
    context = tmp_path / "context"
    transplant.prepare(context)
    monkeypatch.setattr(transplant.platform, "system", lambda: "Linux")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args == ["docker", "pull", transplant.BASE]
        assert not any("TOKEN" in key or "KEY" in key for key in kwargs["env"])
        return subprocess.CompletedProcess(args, 1, b"original pull stdout", b"original pull stderr")

    monkeypatch.setattr(transplant.subprocess, "run", run)
    report = transplant.build(context, tmp_path / "build")
    assert report["status"] == "transplant_unavailable"
    assert report["target_setup_or_tests_executed"] is False and report["calibrated"] is False
    assert (tmp_path / "build/base-pull.stderr").read_bytes() == b"original pull stderr"
    assert json.loads((tmp_path / "build/legacy-transplant.json").read_text())["error_type"] == "RuntimeError"
    assert len(calls) == 1


def test_unsupported_host_never_spawns(tmp_path, monkeypatch):
    monkeypatch.setattr(transplant.platform, "system", lambda: "Windows")
    monkeypatch.setattr(transplant.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not spawn"))
    with pytest.raises(ValueError, match="Linux"):
        transplant.build(tmp_path / "absent", tmp_path / "build")
