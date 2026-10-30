"""Reconcile fixed SymPy file partitions without granting runtime authority."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sympy20212_partition_contract as c  # noqa: E402


def fixture():
    rows = c.manifest_data(c.MANIFEST.read_bytes())
    files = sorted(r['path'] for r in rows if not r['native_default_blacklisted'])
    partition = c.plan(files)
    expected = [f + '::test_fixture' for f in files]
    identities = {'source_revision': 'a' * 64, 'environment_digest': 'b' * 64, 'runner_digest': 'c' * 64}
    runs = [{'batch_index': i, 'plan_sha256': partition['plan_sha256'], **identities, 'files': list(batch),
             'node_statuses': {f + '::test_fixture': 'passed' for f in batch}}
            for i, batch in enumerate(partition['batches'])]
    return partition, expected, runs, identities


def test_complete_529_file_partition_is_deterministic_and_never_authorizes():
    partition, expected, runs, identities = fixture()
    assert [len(x) for x in partition['batches']] == [256, 256, 17]
    assert c.plan(list(reversed(partition['files']))) == partition
    result = c.aggregate(partition, expected, runs, **identities)
    assert result['all_declared_passed'] and result['nodes'] == result['files'] == 529
    assert result['ordinary_acceptance'] is result['can_publish'] is False
    assert result['runtime_receipts_authenticated'] is result['node_inventory_authenticated'] is False


def test_selector_must_not_match_another_native_file(monkeypatch):
    rows = [{'path': 'sympy/test_a.py', 'native_default_blacklisted': False},
            {'path': 'sympy/test_a.py2.py', 'native_default_blacklisted': False}]
    monkeypatch.setattr(c, 'manifest_data', lambda raw: rows)
    with pytest.raises(ValueError, match='selector'):
        c.plan([row['path'] for row in rows])


def test_requires_exact_file_inventory_and_fixed_partition():
    partition, expected, runs, identities = fixture()
    files = partition['files']
    for bad in (files[:-1], files + [files[0]], files + ['sympy/extra/test_bad.py'], files + [[]]):
        with pytest.raises(ValueError):
            c.plan(bad)
    tampered = copy.deepcopy(partition)
    tampered['batches'][0].pop()
    tampered['plan_sha256'] = c.digest({k: v for k, v in tampered.items() if k != 'plan_sha256'})
    with pytest.raises(ValueError):
        c.aggregate(tampered, expected, runs, **identities)


def test_rejects_missing_nodes_wrong_identity_and_extra_outcomes():
    partition, expected, runs, identities = fixture()
    mutations = [lambda r: r.pop(),
                 lambda r: r[0]['node_statuses'].pop(expected[0]),
                 lambda r: r[0]['node_statuses'].update({'sympy/extra/test_bad.py::test_bad': 'passed'}),
                 lambda r: r[0].update(source_revision='f' * 64),
                 lambda r: r[0].update(batch_index=1),
                 lambda r: r[0]['files'].pop(),
                 lambda r: r[0]['node_statuses'].update({expected[0]: 'success'})]
    for change in mutations:
        altered = copy.deepcopy(runs)
        change(altered)
        with pytest.raises(ValueError):
            c.aggregate(partition, expected, altered, **identities)
    with pytest.raises(ValueError):
        c.aggregate(partition, expected[:-1], runs, **identities)
    with pytest.raises(ValueError):
        c.aggregate(partition, expected + [expected[0]], runs, **identities)


def test_nonpassing_is_preserved_and_never_hidden():
    partition, expected, runs, identities = fixture()
    runs[0]['node_statuses'][expected[0]] = 'skipped'
    result = c.aggregate(partition, expected, runs, **identities)
    assert result['counts']['skipped'] == 1 and not result['all_declared_passed']
    assert result['ordinary_acceptance'] is False
