"""Strict parsing for bounded native public file discovery."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sympy20212_public_discovery as discovery  # noqa: E402


def test_source_bound_inventory_and_discovery():
    rows = discovery.manifest_data(discovery.MANIFEST.read_bytes())
    files = [row['path'] for row in rows if not row['native_default_blacklisted']]
    observation = {'files': files, 'exit_code': 0, 'python': [3, 9, 20], 'on_travis': False, 'test_bodies_executed': False}
    marker = 'P07_SYMPY_DISCOVERY=' + json.dumps(observation)
    assert discovery.compare_discovery(marker, 0, rows)['missing'] == []
    observation['files'].pop()
    assert len(discovery.compare_discovery('P07_SYMPY_DISCOVERY=' + json.dumps(observation), 0, rows)['missing']) == 1
    observation['files'].append(observation['files'][0])
    with pytest.raises(ValueError):
        discovery.compare_discovery('P07_SYMPY_DISCOVERY=' + json.dumps(observation), 0, rows)
    with pytest.raises(ValueError):
        discovery.manifest_data(discovery.MANIFEST.read_bytes() + b' ')


def test_refuses_missing_duplicate_or_incomplete_marker():
    rows = discovery.manifest_data(discovery.MANIFEST.read_bytes())
    with pytest.raises(ValueError):
        discovery.compare_discovery('', 0, rows)
    raw = 'P07_SYMPY_DISCOVERY=' + json.dumps({'files': [], 'exit_code': 0, 'python': [3, 9, 20], 'on_travis': False, 'test_bodies_executed': False})
    with pytest.raises(ValueError):
        discovery.compare_discovery(raw + '\n' + raw, 0, rows)
    with pytest.raises(ValueError):
        discovery.compare_discovery(raw, 1, rows)


def test_worker_refuses_extra_mounts_and_incorrect_tmpfs():
    item = {'Id': 'cid', 'Image': 'image', 'Mounts': [],
            'Config': {'User': '65534:65534', 'WorkingDir': '/testbed'},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Init': True,
                'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': None,
                'SecurityOpt': ['no-new-privileges'], 'Memory': 1024**3, 'NanoCpus': 2_000_000_000,
                'PidsLimit': 128, 'Binds': None, 'VolumesFrom': None,
                'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=134217728,mode=1777'}}}
    discovery.verify_container(item, 'cid', 'image')
    item['Mounts'] = [{'Source': '/host', 'Destination': '/untrusted'}]
    with pytest.raises(ValueError):
        discovery.verify_container(item, 'cid', 'image')
    item['Mounts'] = []
    item['HostConfig']['Tmpfs']['/tmp'] = 'rw,size=134217728'
    with pytest.raises(ValueError):
        discovery.verify_container(item, 'cid', 'image')
