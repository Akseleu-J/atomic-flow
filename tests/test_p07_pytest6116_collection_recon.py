"""Incomplete collection is diagnostic evidence, never a passing check."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from p07_pytest6116_collection_recon import parse_collection  # noqa: E402


def test_parse_actual_native_error_without_promoting_it():
    value = {"finished": True, "exit_code": 2, "items": [{"nodeid": "testing/x.py::test_x", "path": "testing/x.py"}],
             "collection_errors": ["testing/test_junitxml.py"], "collection_skips": []}
    raw = "some output\nP07_PUBLIC_COLLECTION=" + json.dumps(value) + "\n"
    assert parse_collection(raw, 2) == value
    for invalid, code in ((raw, 0), (raw + raw, 2), (raw.replace("COLLECTION=", "COLLECTION:"), 2)):
        with pytest.raises(ValueError):
            parse_collection(invalid, code)
