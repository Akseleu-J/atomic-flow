"""Donor qualification retains incompatibility and cleanup uncertainty."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p07_legacy_runtime_probe", ROOT / "tools/p07_legacy_runtime_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
IMAGE_ID = "sha256:" + "a" * 64


def observed():
    return {"version_info": [3, 6, 15], "version": "3.6.15", "probes": {
        **{name: {"status": "imported"} for name in ("ssl", "ctypes", "sqlite3", "bz2", "lzma")},
        "ast_name_false": {"status": "evaluated", "expected_value_matches": True, "value": {"evaluate": False}},
        "collections_mapping": {"status": "resolved", "warnings": []}}}


def container():
    return {"Image": IMAGE_ID, "Mounts": [], "Config": {"User": "65534:65534"}, "HostConfig": {
        "NetworkMode": "none", "Privileged": False, "Binds": None, "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"], "ReadonlyRootfs": True, "Memory": 1024 ** 3,
        "NanoCpus": 2_000_000_000, "PidsLimit": 128,
        "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m,mode=1777"}}}


def fake_docker(monkeypatch, *, result=None, process_exit=0, unknown_cleanup=False, wrong_digest=False):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[0] == "docker" and kwargs.get("capture_output") is True
        assert "shell" not in kwargs
        assert set(kwargs["env"]) <= {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
        code, out, err = 0, b"", b""
        if args[1:3] == ["image", "inspect"]:
            image = {"Id": IMAGE_ID, "RepoDigests": ["python@" + ("sha256:" + "0" * 64 if wrong_digest else probe.DIGEST)],
                     "Architecture": "amd64", "Os": "linux"}
            out = json.dumps([image]).encode()
        elif args[1] == "inspect":
            out = json.dumps([container()]).encode()
        elif args[1] == "create":
            assert args[-4:-1] == ["-I", "-B", "-c"]
            assert args[-1] == probe.PROBE
            assert "--mount" not in args and "--volume" not in args
        elif args[1] == "wait":
            out = (str(process_exit) + "\n").encode()
        elif args[1] == "logs":
            out = json.dumps(result if result is not None else observed()).encode()
            err = b"original probe stderr\n"
        elif args[1] == "rm" and unknown_cleanup:
            raise subprocess.TimeoutExpired(args, 30)
        elif args[1:3] == ["container", "ls"]:
            out = b"still-present\n" if unknown_cleanup else b""
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe.subprocess, "run", run)
    return calls


def test_compatible_donor_is_not_target_or_transplant_acceptance(tmp_path, monkeypatch):
    calls = fake_docker(monkeypatch)
    report = probe.qualify(tmp_path / "probe")
    assert report["status"] == "donor_probes_compatible" and report["probe_compatible"] is True
    assert report["calibrated"] is False and report["legacy_transplant"] == "not_attempted"
    assert report["target_setup_or_tests_executed"] is False and report["model_calls"] == 0
    assert report["cleanup_confirmed"] is True
    assert report["stdout_sha256"] == probe.sha((tmp_path / "probe/probe.stdout").read_bytes())
    assert (tmp_path / "probe/probe.stderr").read_bytes() == b"original probe stderr\n"
    assert all("exec" not in command and "run" not in command for command in calls)


@pytest.mark.parametrize("name", ["ssl", "ctypes", "sqlite3", "bz2", "lzma", "ast_name_false", "collections_mapping"])
def test_probe_exception_is_retained_and_never_compatible(tmp_path, monkeypatch, name):
    result = observed()
    result["probes"][name] = {"status": "exception", "exception_type": "ValueError"}
    fake_docker(monkeypatch, result=result)
    report = probe.qualify(tmp_path / "probe")
    assert report["status"] == "donor_probe_incompatibility" and report["probe_compatible"] is False
    assert report["observed"]["probes"][name]["exception_type"] == "ValueError"
    assert report["cleanup_confirmed"] is True


def test_warning_or_wrong_ast_value_is_not_runtime_compatibility(tmp_path, monkeypatch):
    result = observed()
    result["probes"]["collections_mapping"]["warnings"] = [{"category": "DeprecationWarning", "message": "old API"}]
    result["probes"]["ast_name_false"]["expected_value_matches"] = False
    fake_docker(monkeypatch, result=result)
    report = probe.qualify(tmp_path / "probe")
    assert report["status"] == "donor_probe_incompatibility"


def test_nonzero_process_retains_original_output_and_cleanup(tmp_path, monkeypatch):
    fake_docker(monkeypatch, process_exit=134)
    report = probe.qualify(tmp_path / "probe")
    assert report["status"] == "probe_unavailable" and report["process_exit"] == 134
    assert (tmp_path / "probe/probe.stdout").is_file() and report["cleanup_confirmed"] is True
    assert "observed" not in report


def test_cleanup_timeout_overrides_success_and_preserves_report(tmp_path, monkeypatch):
    fake_docker(monkeypatch, unknown_cleanup=True)
    output = tmp_path / "probe"
    report = probe.qualify(output)
    assert report["status"] == "cleanup_uncertain" and report["probe_compatible"] is False
    assert report["cleanup_confirmed"] is False
    assert json.loads((output / "legacy-runtime-probe.json").read_text())["status"] == "cleanup_uncertain"


def test_wrong_manifest_digest_never_starts_probe(tmp_path, monkeypatch):
    calls = fake_docker(monkeypatch, wrong_digest=True)
    report = probe.qualify(tmp_path / "probe")
    assert report["status"] == "probe_unavailable"
    assert not any(command[1] == "create" for command in calls)


@pytest.mark.parametrize("key,value", [("NetworkMode", "bridge"), ("ReadonlyRootfs", False), ("PidsLimit", 256),
                                     ("Memory", 6 * 1024 ** 3), ("CapDrop", []), ("NanoCpus", 0)])
def test_changed_execution_contract_is_refused(key, value):
    config = container()
    config["HostConfig"][key] = value
    with pytest.raises(ValueError, match="isolation"):
        probe.validate_container(config, IMAGE_ID)


def test_no_host_execution_on_unsupported_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not spawn"))
    with pytest.raises(ValueError, match="Linux"):
        probe.qualify(tmp_path / "probe")
