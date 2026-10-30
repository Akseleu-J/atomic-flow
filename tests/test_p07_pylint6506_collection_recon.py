"""Public collection evidence stays source-bound and fails closed on malformed output."""
import base64
import json
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pylint6506_collection_recon as recon  # noqa: E402


def test_frozen_tree_and_exec_payload():
    raw = recon.PUBLIC_TREE.read_bytes()
    tree = json.loads(raw)
    assert recon.sha(raw) == recon.PUBLIC_TREE_SHA256
    rows = tree['blobs']
    assert tree['base_commit'] == recon.BASE and len(rows) == len({x['path'] for x in rows}) == 2563
    assert sum(x['mode'] == '120000' for x in rows) == 2
    assert len(base64.b64encode(zlib.compress(json.dumps(rows, separators=(',', ':')).encode()))) < 131000
    compile(recon.VERIFY_SOURCE, '<verification>', 'exec')
    compile(recon.SOURCE, '<collection>', 'exec')


def test_partial_or_mismatched_collection_marker_is_refused():
    value = {'finished': True, 'exit_code': 2, 'items': [], 'collection_errors': ['tests/foo'], 'collection_skips': []}
    text = 'P07_PUBLIC_COLLECTION=' + json.dumps(value)
    assert recon.parse_collection(text, 2) == value
    with pytest.raises(ValueError):
        recon.parse_collection(text, 0)
    with pytest.raises(ValueError):
        recon.parse_collection(text + '\n' + text, 2)
