import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import p07_python27_runtime_probe as probe  # noqa: E402


def valid():
    return {"version_info": [2, 7, 18], "executable": "/usr/local/bin/python2.7", "prefix": "/usr/local", "unicode_size": 4,
            "probes": {**{name: {"status": "imported"} for name in ("ssl", "ctypes", "sqlite3", "bz2", "zlib")},
                       "exception_message": {"status": "resolved", "matches": True, "value": "p07-message-contract"},
                       "collections_mapping": {"status": "resolved"}}}


def test_fixed_probe_has_no_target_source_and_checks_original_message_api():
    command = probe.runtime_command("p07-legacy-" + "a" * 24, "sha256:" + "b" * 64)
    assert command[-6:] == ["-E", "-s", "-S", "-B", "-c", probe.PROBE]
    assert "--read-only" in command and "--network" in command and "none" in command
    assert "--privileged" not in command and "--volume" not in command and "--mount" not in command
    assert "-I" not in command
    assert "exc.message" in probe.PROBE and "import requests" not in probe.PROBE and "/testbed" not in probe.PROBE
    ast.parse(probe.PROBE)


@pytest.mark.parametrize("field,value", [("version_info", [3, 6, 15]), ("executable", "/usr/bin/python"),
                                        ("prefix", "/testbed"), ("unicode_size", 2)])
def test_wrong_abi_or_runtime_never_qualifies(field, value):
    report = valid()
    assert probe.validate_observed(report)
    report[field] = value
    assert not probe.validate_observed(report)


@pytest.mark.parametrize("name", ["ssl", "ctypes", "sqlite3", "bz2", "zlib", "exception_message", "collections_mapping"])
def test_missing_or_failed_capability_never_qualifies(name):
    report = valid()
    del report["probes"][name]
    assert not probe.validate_observed(report)
    report = valid()
    report["probes"][name] = {"status": "exception"}
    assert not probe.validate_observed(report)


def test_mismatched_exception_message_is_not_compatible():
    report = valid()
    report["probes"]["exception_message"]["value"] = "different"
    assert not probe.validate_observed(report)


def test_runtime_command_rejects_other_owned_names_and_floating_images():
    with pytest.raises(ValueError):
        probe.runtime_command("other", "sha256:" + "b" * 64)
    with pytest.raises(ValueError):
        probe.runtime_command("p07-legacy-" + "a" * 24, "python:2.7")
