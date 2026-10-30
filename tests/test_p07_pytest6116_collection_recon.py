"""Incomplete collection is diagnostic evidence, never a passing check."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from p07_pytest6116_collection_recon import SOURCE, collection_source, parse_collection  # noqa: E402


def test_parse_actual_native_error_without_promoting_it():
    value = {"finished": True, "exit_code": 2, "items": [{"nodeid": "testing/x.py::test_x", "path": "testing/x.py"}],
             "collection_errors": ["testing/test_junitxml.py"], "collection_skips": []}
    raw = "some output\nP07_PUBLIC_COLLECTION=" + json.dumps(value) + "\n"
    assert parse_collection(raw, 2) == value
    for invalid, code in ((raw, 0), (raw + raw, 2), (raw.replace("COLLECTION=", "COLLECTION:"), 2)):
        with pytest.raises(ValueError):
            parse_collection(invalid, code)


def test_derived_collection_only_adds_fixed_dependency_path_and_single_plugin_block():
    assert collection_source(None) == SOURCE
    generated = collection_source("/opt/p07-pytest6116-deps")
    assert "['-p','no:typeguard','--collect-only','-q','testing']" in generated
    assert generated.count("no:typeguard") == 2
    assert "os.environ['PYTEST_ADDOPTS']='-p no:typeguard'" in generated
    assert "sys.path.insert(0,'/opt/p07-pytest6116-deps')" in generated
    assert "os.environ['HYPOTHESIS_STORAGE_DIRECTORY']='/tmp/p07-hypothesis'" in generated
    execute = collection_source("/opt/p07-pytest6116-deps", execute_public_tests=True)
    assert "['-p','no:typeguard','-q','testing']" in execute and "--collect-only" not in execute
    with pytest.raises(ValueError, match="requires the admitted"):
        collection_source(None, execute_public_tests=True)
    with pytest.raises(ValueError, match="fixed derived"):
        collection_source("/unreviewed")


def test_candidate_removes_env_override_and_only_shortens_traceback():
    source = collection_source("/opt/p07-pytest6116-deps", execute_public_tests=True,
                               neutralize_vendored_plugin=True)
    assert "PYTEST_ADDOPTS" not in source
    assert "['-p','no:typeguard','-q','--tb=line','testing']" in source
    assert "--collect-only" not in source
