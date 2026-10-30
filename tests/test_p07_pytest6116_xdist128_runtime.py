"""Refuse partial or misleading prospective xdist focused receipts."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_xdist128_runtime as runtime  # noqa: E402


def valid():
    n = len(runtime.NODES)
    return {'finished': True, 'exit_code': 0, 'nodes': list(runtime.NODES), 'completed': list(runtime.NODES),
            'errors': [], 'plugins': {'xdist': True, 'pytest_forked': True, 'xdist.looponfail': True, 'xdist.boxed': False},
            'outcomes': {'setup:passed': n, 'call:passed': n, 'teardown:passed': n}, 'nonpassing': [],
            'xdist': '/usr/local/lib/python3.8/site-packages/xdist/__init__.py',
            'boxed': '/usr/local/lib/python3.8/site-packages/xdist/boxed.py',
            'execnet': '/usr/local/lib/python3.8/site-packages/execnet/__init__.py',
            'forked': '/usr/local/lib/python3.8/site-packages/pytest_forked/__init__.py'}


def test_fixed_inputs_and_selection_include_six_previous_failures():
    assert hashlib.sha256(runtime.ADAPTER.read_bytes()).hexdigest() == runtime.ADAPTER_SHA
    assert len(runtime.NODES) == len(set(runtime.NODES)) == 19
    assert set(runtime.focus.NODES).issubset(runtime.NODES)
    compile(runtime.SMOKE, '<container-smoke>', 'exec')


def test_incomplete_node_receipts_fail_closed():
    base = valid()
    assert runtime.verify_observation(base, 0)['status'] == 'focused_smoke_passed'
    for mutate in (lambda x: x['nodes'].pop(), lambda x: x['completed'].pop(),
                   lambda x: x['nodes'].__setitem__(0, x['nodes'][1]), lambda x: x['errors'].append('failed')):
        changed = copy.deepcopy(base)
        mutate(changed)
        with pytest.raises(ValueError):
            runtime.verify_observation(changed, 0)


def test_unexpected_outcomes_plugins_paths_and_exit_are_not_passing():
    base = valid()
    for mutate in (lambda x: x['nonpassing'].append({'outcome': 'skipped'}),
                   lambda x: x['plugins'].__setitem__('xdist.boxed', True),
                   lambda x: x['outcomes'].__setitem__('call:passed', 18),
                   lambda x: x.__setitem__('boxed', '/unexpected')):
        changed = copy.deepcopy(base)
        mutate(changed)
        assert runtime.verify_observation(changed, 0)['status'] == 'focused_smoke_failed'
    changed = copy.deepcopy(base)
    changed['exit_code'] = 1
    assert runtime.verify_observation(changed, 1)['status'] == 'focused_smoke_failed'


def test_fixed_tree_and_container_mount_constraints():
    assert hashlib.sha256(runtime.recon.PUBLIC_TREE.read_bytes()).hexdigest() == runtime.TREE_SHA
    container = {'HostConfig': {'Mounts': None}, 'Config': {'Volumes': None}, 'Mounts': []}
    assert runtime.valid_mounts(container)
    container['Mounts'] = [{'Type': 'tmpfs', 'Destination': '/tmp', 'RW': True, 'Source': ''}]
    assert runtime.valid_mounts(container)
    for mutate in (
        lambda x: x['HostConfig'].__setitem__('Mounts', [{'Type': 'bind'}]),
        lambda x: x['Config'].__setitem__('Volumes', {'/testbed': {}}),
        lambda x: x['Mounts'][0].__setitem__('Destination', '/testbed'),
        lambda x: x['Mounts'][0].__setitem__('Type', 'bind'),
        lambda x: x['Mounts'][0].__setitem__('Source', '/host'),
        lambda x: x['Mounts'].append({'Type': 'bind'}),
    ):
        changed = copy.deepcopy(container)
        mutate(changed)
        assert not runtime.valid_mounts(changed)
