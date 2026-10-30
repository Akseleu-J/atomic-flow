"""Boundary tests for the Pylint GitPython collection diagnostic."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pylint6506_gitpython_collection as worker  # noqa: E402


def test_fixed_collection_keeps_entire_original_selection(monkeypatch):
    source = worker.fixed_collection_source()
    assert source.replace("sys.path.insert(0,'" + worker.PREFIX + "')\n", '') == worker.recon.SOURCE
    assert "['--collect-only','-q','--benchmark-disable','tests']" in source
    monkeypatch.setattr(worker.recon, 'SOURCE', 'unknown')
    with pytest.raises(ValueError):
        worker.fixed_collection_source()


def test_collection_refuses_errors_skips_nonpublic_empty_or_duplicate():
    tree = {'blobs': [{'path': 'tests/test_a.py'}]}
    obs = {'items': [{'path': 'tests/test_a.py', 'nodeid': 'tests/test_a.py::test_a'}],
           'collection_skips': [], 'collection_errors': []}
    assert worker.collection_summary(obs, tree, 0)['status'] == 'derived_collection_observed'
    for change in ('empty', 'skip', 'error', 'nonpublic', 'exit'):
        item = copy.deepcopy(obs)
        code = 0
        if change == 'empty':
            item['items'] = []
        elif change == 'skip':
            item['collection_skips'] = ['tests/test_a.py']
        elif change == 'error':
            item['collection_errors'] = ['tests/test_a.py']
        elif change == 'nonpublic':
            item['items'][0]['path'] = 'tests/extra.py'
        else:
            code = 2
        assert worker.collection_summary(item, tree, code)['status'] == 'derived_collection_incomplete'
    obs['items'].append(dict(obs['items'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        worker.collection_summary(obs, tree, 0)


def test_image_and_container_guards_reject_provenance_or_isolation_changes():
    iid = 'sha256:' + 'a' * 64
    base = {'RepoDigests': [worker.recon.IMAGE], 'Architecture': 'amd64', 'Os': 'linux', 'RootFS': {'Layers': ['a']}}
    derived = {'Id': iid, 'Architecture': 'amd64', 'RootFS': {'Layers': ['a', 'b']},
               'Config': {'Labels': {worker.LABEL: worker.candidate.PROFILE_SHA}}}
    worker.verify_lineage(base, derived, iid)
    for change in ('digest', 'layers', 'label'):
        b, d = copy.deepcopy(base), copy.deepcopy(derived)
        if change == 'digest':
            b['RepoDigests'] = []
        elif change == 'layers':
            d['RootFS']['Layers'] = ['wrong', 'b']
        else:
            d['Config']['Labels'] = {}
        with pytest.raises(ValueError):
            worker.verify_lineage(b, d, iid)
    cid = 'c' * 64
    item = {'Id': cid, 'Image': iid, 'Mounts': [], 'State': {'Running': False},
            'Config': {'User': '65534:65534', 'Volumes': None},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Init': True, 'Privileged': False,
                           'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'],
                           'Memory': 1024**3, 'NanoCpus': 2_000_000_000, 'PidsLimit': 128,
                           'Tmpfs': dict(worker.TMPFS)}}
    worker.verify_container(item, cid, iid, running=False)
    for change in ('network', 'tmpfs', 'bind', 'running'):
        amended = copy.deepcopy(item)
        if change == 'network':
            amended['HostConfig']['NetworkMode'] = 'bridge'
        elif change == 'tmpfs':
            amended['HostConfig']['Tmpfs'] = {}
        elif change == 'bind':
            amended['Mounts'] = [{'Type': 'bind', 'Destination': '/testbed', 'Source': '/host'}]
        else:
            amended['State']['Running'] = True
        with pytest.raises(ValueError):
            worker.verify_container(amended, cid, iid, running=False)
