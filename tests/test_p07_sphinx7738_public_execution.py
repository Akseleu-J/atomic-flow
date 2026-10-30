"""Sphinx full execution receipts must preserve exact nodes and every phase."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sphinx7738_public_execution as runner  # noqa: E402


def setup():
    row = {'nodeid': 'tests/test_example.py::test_one', 'path': 'tests/test_example.py'}
    phases = [{'nodeid': row['nodeid'], 'phase': phase, 'outcome': 'passed', 'wasxfail': None,
               'reason': '', 'reason_truncated': False} for phase in ('setup', 'call', 'teardown')]
    return {'items': [row], 'phases': phases, 'collection_errors': [], 'collection_skips': []}, {'blobs': [{'path': row['path']}]}, [row]


def test_fixed_baseline_is_full_and_bound():
    raw = runner.NODES_MANIFEST.read_bytes()
    assert len(runner.verify_baseline(raw)) == 1502
    with pytest.raises(ValueError, match='digest'):
        runner.verify_baseline(raw + b' ')
    compile(runner.SOURCE, '<sphinx-source>', 'exec')
    assert "pytest.main(['-q','--tb=line','--durations','25']" in runner.SOURCE


def test_missing_call_with_passing_setup_and_missing_phase_are_incomplete():
    obs, tree, baseline = setup()
    assert runner.assess_execution(obs, tree, 0, baseline)['status'] == 'execution_observed_clean'
    for phase in ('call', 'setup', 'teardown'):
        changed = copy.deepcopy(obs)
        changed['phases'] = [x for x in changed['phases'] if x['phase'] != phase]
        assert runner.assess_execution(changed, tree, 0, baseline)['status'] == 'execution_incomplete'


def test_source_declared_xfail_skip_and_failed_phases_are_preserved():
    obs, tree, baseline = setup()
    obs['phases'][1].update({'outcome': 'skipped', 'wasxfail': 'known', 'reason': 'expected failure'})
    result = runner.assess_execution(obs, tree, 0, baseline)
    assert result['status'] == 'execution_observed_with_nonpassing'
    assert result['outcomes']['call:skipped:xfail'] == 1
    assert result['nonpassing_phase_count'] == 1
    obs['phases'][1].update({'outcome': 'failed', 'wasxfail': None})
    result = runner.assess_execution(obs, tree, 1, baseline)
    assert result['outcomes']['call:failed'] == 1


def test_skipped_setup_is_a_completed_nonpassing_node_without_call():
    obs, tree, baseline = setup()
    obs['phases'][0]['outcome'] = 'skipped'
    obs['phases'] = [p for p in obs['phases'] if p['phase'] != 'call']
    result = runner.assess_execution(obs, tree, 0, baseline)
    assert result['status'] == 'execution_observed_with_nonpassing'
    assert result['no_call_nodes'] == [baseline[0]['nodeid']]


def test_changed_node_and_duplicate_phase_refused():
    obs, tree, baseline = setup()
    changed = copy.deepcopy(obs)
    changed['items'][0]['nodeid'] = 'tests/test_example.py::other'
    changed['phases'] = []
    assert runner.assess_execution(changed, tree, 0, baseline)['status'] == 'execution_incomplete'
    obs['phases'].append(dict(obs['phases'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        runner.assess_execution(obs, tree, 0, baseline)
