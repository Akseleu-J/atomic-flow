"""Test source-bound full Pylint execution diagnostics without target imports."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pylint6506_public_execution as execution  # noqa: E402


def test_fixed_baseline_and_execution_source():
    raw = execution.NODES.read_bytes()
    assert len(execution.baseline(raw)) == 1954
    with pytest.raises(ValueError, match='digest'):
        execution.baseline(raw + b' ')
    source = execution.execution_source()
    compile(source, '<target-diagnostic>', 'exec')
    assert "'--collect-only'" not in source
    assert "'--benchmark-disable','tests'" in source
    assert 'pytest_runtest_logreport' in source and "'reason_truncated':False" in source


def test_phase_and_baseline_refuse_missing_nodes_and_preserve_skips():
    item = {'nodeid': 'tests/test_one.py::test_one', 'path': 'tests/test_one.py'}
    tree = {'blobs': [{'path': item['path']}]}
    phases = [{'nodeid': item['nodeid'], 'phase': p, 'outcome': 'passed', 'wasxfail': None,
               'reason': '', 'reason_truncated': False} for p in ('setup', 'call', 'teardown')]
    obs = {'items': [item], 'phases': phases, 'collection_errors': [], 'collection_skips': []}
    assert execution.assessment.assess_execution(obs, tree, 0, [item])['status'] == 'execution_observed_clean'
    altered = copy.deepcopy(obs)
    altered['phases'][1]['outcome'] = 'skipped'
    assert execution.assessment.assess_execution(altered, tree, 0, [item])['status'] == 'execution_observed_with_nonpassing'
    altered = copy.deepcopy(obs)
    altered['phases'].pop()
    assert execution.assessment.assess_execution(altered, tree, 0, [item])['status'] == 'execution_incomplete'
    assert json.loads(execution.NODES.read_bytes())['source_ci'] == 41002231450
