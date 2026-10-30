"""Static public tree reconciliation refuses missing or tampered entries."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_sympy20212_public_inventory import reconcile_tree  # noqa: E402


def oid(kind, data):
    return hashlib.sha1(f'{kind} {len(data)}\0'.encode() + data).hexdigest()


def tree_blob(name, digest):
    return b'100644 ' + name.encode() + b'\0' + bytes.fromhex(digest)


def sample():
    blob = oid('blob', b'def test_a(): pass\n')
    sub = oid('tree', tree_blob('test_a.py', blob))
    root = oid('tree', b'40000 sympy\0' + bytes.fromhex(sub))
    return {'sha': root, 'truncated': False, 'tree': [
        {'path': 'sympy', 'mode': '040000', 'type': 'tree', 'sha': sub},
        {'path': 'sympy/test_a.py', 'mode': '100644', 'type': 'blob', 'sha': blob, 'size': 19},
    ]}, root


def test_valid_tree_and_bound_candidate():
    data, root = sample()
    result = reconcile_tree(data, root)
    assert result['candidate_count'] == result['default_candidate_count'] == 1
    assert result['test_candidates'][0]['path'] == 'sympy/test_a.py'
    assert result['ordinary_acceptance'] is False


def test_refuses_incomplete_or_modified_tree():
    for modify in (lambda d: d.update(truncated=True), lambda d: d['tree'].pop(),
                   lambda d: d['tree'][1].update(sha='0' * 40),
                   lambda d: d['tree'].append(dict(d['tree'][1]))):
        data, root = sample()
        modify(data)
        with pytest.raises(ValueError):
            reconcile_tree(data, root)
