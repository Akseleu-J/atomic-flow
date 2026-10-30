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

@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX executable mode semantics')
def test_source_verifier_distinguishes_modes_from_content(tmp_path):
    import hashlib
    import os
    import subprocess

    root = tmp_path / 'source'
    root.mkdir()
    target = root / 'test.py'
    raw = b'original\n'
    target.write_bytes(raw)
    target.chmod(0o755)
    digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    rows = [{'path': 'test.py', 'sha': digest, 'size': len(raw), 'mode': '100644'}]
    payload = base64.b64encode(zlib.compress(json.dumps(rows).encode())).decode()
    code = recon.VERIFY_SOURCE.replace("os.path.join('/testbed',relative)", f"os.path.join({str(root)!r},relative)")
    def observe():
        out = subprocess.check_output([sys.executable, '-c', code, payload], env=os.environ.copy())
        return json.loads(out)['differences']
    assert observe()[0]['reason'] == 'executable_mode_differs'
    target.write_bytes(b'corrupt\n')
    assert observe()[0]['reason'] == 'content_differs'
