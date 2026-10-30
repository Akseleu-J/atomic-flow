"""Fail-closed scope and public tree checks for the Sphinx collection diagnostic."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sphinx7738_collection_recon as recon  # noqa: E402


def test_frozen_tree_and_manifest_tamper():
    raw = recon.TREE.read_bytes()
    tree = recon.verify_tree(raw)
    assert len(tree['blobs']) == 1416
    assert {'setup.cfg', 'tox.ini', 'sphinx/__init__.py'} <= {row['path'] for row in tree['blobs']}
    with pytest.raises(ValueError, match='digest'):
        recon.verify_tree(raw + b' ')


def test_collection_is_public_and_does_not_claim_tox_equivalence():
    assert "pytest.main(['--collect-only','-q','--durations','25']" in recon.SOURCE
    assert "os.path.realpath(sphinx.__file__)!='/testbed/sphinx/__init__.py'" in recon.SOURCE
    assert 'P07_PUBLIC_COLLECTION=' in recon.SOURCE
    result = {'items': [], 'collection_errors': ['tests/example.py'], 'collection_skips': [],
              'finished': True, 'exit_code': 2}
    assert recon.common.parse_collection('P07_PUBLIC_COLLECTION=' + json.dumps(result), 2) == result
    with pytest.raises(ValueError):
        recon.common.parse_collection('P07_PUBLIC_COLLECTION=' + json.dumps(result), 0)


def test_collection_gate_rejects_empty_skipped_errored_nonpublic_and_duplicates():
    tree = {'blobs': [{'path': 'tests/test_public.py'}]}
    obs = {'items': [{'nodeid': 'tests/test_public.py::test_ok', 'path': 'tests/test_public.py'}],
           'collection_errors': [], 'collection_skips': []}
    assert recon.assess_collection(obs, tree, 0)['status'] == 'collection_observed'
    for change in ('empty', 'skip', 'error', 'nonpublic', 'exit'):
        candidate = copy.deepcopy(obs)
        exit_code = 0
        if change == 'empty':
            candidate['items'] = []
        if change == 'skip':
            candidate['collection_skips'] = ['tests/test_public.py']
        if change == 'error':
            candidate['collection_errors'] = ['tests/test_public.py']
        if change == 'nonpublic':
            candidate['items'][0]['path'] = 'tests/extra.py'
        if change == 'exit':
            exit_code = 2
        assert recon.assess_collection(candidate, tree, exit_code)['status'] == 'collection_observed_with_errors'
    obs['items'].append(dict(obs['items'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        recon.assess_collection(obs, tree, 0)


def test_inspected_container_requires_exact_tmpfs_and_refuses_other_mounts():
    cid = 'a' * 64
    item = {'Id': cid, 'Mounts': [], 'Config': {'User': '65534:65534', 'Volumes': None},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Init': True, 'Privileged': False,
                           'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'],
                           'Memory': 1024**3, 'NanoCpus': 2_000_000_000, 'PidsLimit': 128,
                           'Binds': None, 'VolumesFrom': None, 'Mounts': None,
                           'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=268435456,mode=1777'}}}
    recon.verify_container(item, cid)
    for mutate in (
        lambda x: x['HostConfig']['Tmpfs'].update({'/tmp': 'rw'}),
        lambda x: x['HostConfig']['Tmpfs'].update({'/other': 'rw'}),
        lambda x: x['HostConfig'].update({'Binds': ['/host:/testbed']}),
        lambda x: x['HostConfig'].update({'Mounts': [{'Target': '/testbed'}]}),
        lambda x: x.update({'Mounts': [{'Type': 'bind', 'Destination': '/testbed', 'Source': '/host'}]}),
        lambda x: x['Config'].update({'Volumes': {'/data': {}}}),
    ):
        candidate = copy.deepcopy(item)
        mutate(candidate)
        with pytest.raises(ValueError, match='isolation'):
            recon.verify_container(candidate, cid)
