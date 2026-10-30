"""Combined pytest6116 diagnostic preserves full public node and phase outcomes."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_xdist134_combined as combined  # noqa: E402


def example():
    n = 'testing/test_example.py::test_one'
    phases = [{'nodeid': n, 'phase': phase, 'outcome': 'passed', 'wasxfail': None}
              for phase in ('setup', 'call', 'teardown')]
    obs = {'finished': True, 'exit_code': 0, 'items': [n], 'completed': [n], 'phase_reports': phases,
           'outcomes': {phase + ':passed': 1 for phase in ('setup', 'call', 'teardown')},
           'plugins': {'xdist': True, 'pytest_forked': True, 'xdist.boxed': False, 'xdist.looponfail': True},
           'nonpassing': [], 'xpasses': [], 'collection_errors': [], 'collection_skips': []}
    return obs, [n], {'testing/test_example.py'}


def test_baseline_and_source_are_fixed_and_full():
    raw = combined.NODE_MANIFEST.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == combined.NODE_SHA
    assert len(combined.load_baseline(raw)) == 2617
    with pytest.raises(ValueError, match='digest'):
        combined.load_baseline(raw + b' ')
    code = combined.combined_source()
    compile(code, '<target>', 'exec')
    assert "'testing'" in code and 'phase_reports' in code and "'reason':str(report.longrepr)" in code


def test_full_inventory_and_phase_receipt_and_skip_preservation():
    obs, baseline, paths = example()
    assert combined.assess_observation(obs, baseline, 0, paths)['status'] == 'combined_public_diagnostic_observed'
    obs['phase_reports'][1]['outcome'] = 'skipped'
    obs['phase_reports'][1]['wasxfail'] = 'source xfail'
    obs['outcomes'] = {'setup:passed': 1, 'call:skipped': 1, 'teardown:passed': 1}
    obs['nonpassing'] = [{'nodeid': baseline[0], 'phase': 'call', 'outcome': 'skipped', 'wasxfail': 'source xfail'}]
    result = combined.assess_observation(obs, baseline, 0, paths)
    assert result['nonpassing'][0]['wasxfail'] == 'source xfail'
    assert result['status'] == 'combined_public_diagnostic_observed'


def test_missing_or_duplicate_phase_and_changed_nodes_fail_closed():
    obs, baseline, paths = example()
    for change in ('phase_missing', 'phase_duplicate', 'outcome_mismatch'):
        alt = copy.deepcopy(obs)
        if change == 'phase_missing':
            alt['phase_reports'].pop()
        if change == 'phase_duplicate':
            alt['phase_reports'].append(dict(alt['phase_reports'][0]))
        if change == 'outcome_mismatch':
            alt['outcomes']['call:passed'] = 2
        with pytest.raises(ValueError):
            combined.assess_observation(alt, baseline, 0, paths)
    assert combined.assess_observation(obs, ['testing/test_example.py::different'], 0, paths)['status'] == 'combined_public_diagnostic_incomplete'
    with pytest.raises(ValueError):
        combined.assess_observation(obs, baseline, 0, set())


def test_setup_skip_and_missing_call_are_valid_nonpassing_but_not_lost():
    obs, baseline, paths = example()
    obs['phase_reports'][0]['outcome'] = 'skipped'
    obs['phase_reports'].pop(1)
    obs['outcomes'] = {'setup:skipped': 1, 'teardown:passed': 1}
    obs['nonpassing'] = [{'nodeid': baseline[0], 'phase': 'setup', 'outcome': 'skipped', 'wasxfail': None}]
    assert combined.assess_observation(obs, baseline, 0, paths)['nonpassing'] == obs['nonpassing']
    obs['phase_reports'][0]['outcome'] = 'passed'
    obs['outcomes'] = {'setup:passed': 1, 'teardown:passed': 1}
    obs['nonpassing'] = []
    with pytest.raises(ValueError, match='causality'):
        combined.assess_observation(obs, baseline, 0, paths)
