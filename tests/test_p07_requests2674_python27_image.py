import ast
import copy
import json
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import p07_python27_support as runtime  # noqa:E402
import p07_requests2674_python27_image as image  # noqa:E402


def test_launcher_is_fixed_private_python2_and_confines_import_search_before_pip():
    parts=shlex.split(runtime.LAUNCHER_SOURCE.split('exec ',1)[1])
    assert parts[-1]=='$@'
    assert '-I' not in parts
    assert parts[3]=='/opt/p07-python27/bin/python2.7'
    assert parts[4:9]==['-E','-s','-S','-B','-c']
    ast.parse(parts[9])
    assert "sys.path[:]=['/opt/p07-python27/" in parts[9]
    assert "args[1]=='pip'" in parts[9]


def test_own_task_and_native_abi_wheels_remain_distinct():
    raw,target,dep,profile=image.frozen_task('psf__requests-2674')
    assert target['base_commit']=='0be38a0c37c59c4b66ce908731da15b401655113'
    assert target['oracle_obligations']['required_count']==155
    assert profile['installed_inventory']['count']==244
    assert 'cp27-cp27mu' in next(x['filename'] for x in profile['wheels'] if x['name']=='MarkupSafe')
    assert 'py27-none-any' in next(x['filename'] for x in profile['wheels'] if x['name']=='PySocks')
    assert runtime.sha(raw)==image.PROFILE_SHA
    assert json.loads(dep)['requested_runtime']['version']=='2.7.18'
    assert runtime.DONOR in image.dockerfile(target)
    assert target['official_base_image'] in image.dockerfile(target)
    assert 'python27' in image.dockerfile(target) and 'python36' not in image.dockerfile(target)


@pytest.mark.parametrize('task',['psf__requests-863','psf__requests-3362','other'])
def test_other_task_cannot_borrow_python2_receipt(task):
    with pytest.raises(ValueError):
        image.frozen_task(task)


def test_generated_inventory_and_certificate_programs_are_host_fixed_source_only():
    inventory=runtime.inventory_source({'some/file.py':'a'*64})
    certificate=image.certificate_source({image.CERT_ROOT+'/'+name:'b'*64 for name in image.CERT_NAMES})
    ast.parse(inventory)
    ast.parse(certificate)
    assert 'import requests' not in inventory+certificate
    assert 'pathlib' not in inventory+certificate
    assert 'python2.7/site-packages' in inventory


def test_python2_marker_dependencies_cannot_be_dropped_or_reuse_python3_closure():
    _, _, _, profile = image.frozen_task('psf__requests-2674')
    wheels = profile['wheels']
    edges = runtime.closure(profile, wheels)
    assert ('pytest-mock', 'mock', '') in edges
    assert any(source == 'mock' and target == 'funcsigs' for source, target, _ in edges)
    missing = copy.deepcopy(profile)
    missing['pins'].pop('mock')
    with pytest.raises(ValueError, match='cardinality'):
        runtime.closure(missing, wheels)
    stale = copy.deepcopy(profile)
    stale['closure']['active_edges'] = [
        edge for edge in stale['closure']['active_edges'] if edge['from'] != 'mock'
    ]
    with pytest.raises(ValueError, match='closure differs'):
        runtime.closure(stale, wheels)


def test_dns_certificates_keep_chain_hostname_and_wrongca_contract():
    files=image.create_certificates()
    metadata=image.validate_certificates(files)
    assert metadata['cert.pem']['subject']=='CN=httpbin.org'
    assert metadata['cert.pem']['issuer']==metadata['cacert.pem']['subject']
    assert metadata['wrongca.pem']['sha256']!=metadata['cacert.pem']['sha256']
