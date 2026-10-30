"""A source-skip review cannot silently accept altered evidence."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_source_skip_review as review  # noqa: E402


def fixture():
    ids = list(review.EXPECTED) + [f'testing/other.py::test_{i}' for i in range(2611)]
    reports = [{'nodeid': node, 'phase': 'setup', 'outcome': 'skipped', 'wasxfail': None,
                'reason': expected[2]} for node, expected in review.EXPECTED.items()]
    obs = {'finished': True, 'exit_code': 0, 'items': ids, 'completed': ids, 'nonpassing': reports}
    tree = {'base_commit': 'e670ff76cbad80108bde9bab616b66771b8653cf',
            'blobs': [{'path': name, 'sha': digest} for name, digest in review.FILES.items()]}
    return obs, tree


def run(obs, tree):
    raw = json.dumps(obs).encode()
    return review.review(raw, json.dumps(tree).encode(), expected_observation=hashlib.sha256(raw).hexdigest())


def test_review_binds_nodes_source_and_preserves_non_admission():
    obs, tree = fixture()
    result = run(obs, tree)
    assert len(result['rows']) == 6 and result['ordinary_acceptance'] is False
    assert result['runtime_predicates_independently_verified'] is False
    obs['nonpassing'][0]['phase'] = 'call'
    with pytest.raises(ValueError, match='phase or reason'):
        run(obs, tree)
    obs, tree = fixture()
    tree['blobs'][0]['sha'] = '0' * 40
    with pytest.raises(ValueError, match='source inventory'):
        run(obs, tree)
    with pytest.raises(ValueError, match='observation digest'):
        review.review(b'{}', b'{}')
