"""The diagnostic skip audit must bind and reconcile the complete receipt."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from p07_pytest6116_skip_audit import summarize  # noqa: E402


def receipt(skip_count=115):
    marker = {"finished": True, "exit_code": 1, "completed": 2617,
              "items": [{"nodeid": f"test_{i}"} for i in range(2617)]}
    data = (f"SKIPPED [{skip_count}] test.py:3: could not import 'nose': absent\n"
            "3 failed, 2489 passed, 115 skipped, 10 xfailed, 3 warnings in 1.0s\n"
            "P07_PUBLIC_COLLECTION=" + json.dumps(marker) + "\n").encode()
    return data, hashlib.sha256(data).hexdigest()


def test_skip_audit_reconciles_counts_and_rejects_unbound_input():
    raw, digest = receipt()
    result = summarize(raw, expected_sha256=digest)
    assert result["categories"] == {"missing_python_dependency": 115}
    assert result["ordinary_acceptance"] is False
    with pytest.raises(ValueError, match="digest"):
        summarize(raw + b"changed", expected_sha256=digest)
    raw, digest = receipt(114)
    with pytest.raises(ValueError, match="all skips"):
        summarize(raw, expected_sha256=digest)


def test_py38_skip_audit_requires_all_unique_nodes_to_complete():
    nodes = [f"test_{i}" for i in range(2617)]
    marker = {"finished": True, "exit_code": 0, "completed": nodes, "items": nodes}
    def encode(value):
        return ("SKIPPED [115] test.py:3: could not import 'nose': absent\n"
                "2492 passed, 115 skipped, 10 xfailed in 1.0s\n"
                "P07_PY38_REPRO=" + json.dumps(value) + "\n").encode()
    raw = encode(marker)
    assert summarize(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), py38=True)["completed"] == 2617
    marker["completed"] = nodes[:-1]
    raw = encode(marker)
    with pytest.raises(ValueError, match="inventory"):
        summarize(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), py38=True)
