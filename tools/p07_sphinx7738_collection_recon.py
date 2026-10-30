"""Collect the frozen Sphinx 7738 public suite in a restricted image.

Read-only diagnostic only: no target repairs, hidden oracle, provider calls or
TaskRunner admission. This invokes pytest directly with the native collection
configuration; it does not claim exact equivalence to tox's full environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import p07_pytest6116_collection_recon as common

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-7738@sha256:908d80e3f2cb27da3fa10152c4da53d3be5389d39468339aad5563fda8f8e65d'
BASE = 'c087d717f6ed183dd422359bf91210dc59689d63'
TREE = ROOT / 'benchmarks/stage4/p07-sphinx7738-public-tree-v1.json'
TREE_SHA = '4db470eb97a3038dbc0b516b0d56ce676aac23c7459a4968c7c6faa492b30ce3'
ENGINE = '/usr/bin/docker'
PYTHON = '/opt/miniconda3/envs/testbed/bin/python'
SOURCE = r'''
import json,os,sys
sys.dont_write_bytecode=True
import pytest,sphinx
if (os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python'
    or list(sys.version_info[:3])!=[3,9,20] or os.path.realpath(sphinx.__file__)!='/testbed/sphinx/__init__.py'):
    raise SystemExit(125)
class Recorder:
    def __init__(self):self.items=[];self.collection_errors=[];self.collection_skips=[];self.finished=False
    def pytest_collection_finish(self,session):
        self.items=[{'nodeid':item.nodeid,'path':os.path.relpath(str(item.fspath),'/testbed').replace(os.sep,'/')} for item in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
code=int(pytest.main(['--collect-only','-q','--durations','25'],plugins=[r]))
result={'runner':'sphinx7738_public_collection','python_version':list(sys.version_info[:3]),
        'pytest_version':pytest.__version__,'pytest_module':os.path.realpath(pytest.__file__),
        'sphinx_module':os.path.realpath(sphinx.__file__),'items':r.items,
        'collection_errors':r.collection_errors,'collection_skips':r.collection_skips,
        'finished':r.finished,'exit_code':code}
print('\nP07_PUBLIC_COLLECTION='+json.dumps(result,sort_keys=True),flush=True)
raise SystemExit(code)
'''


def verify_tree(raw):
    if hashlib.sha256(raw).hexdigest() != TREE_SHA:
        raise ValueError('public tree digest differs')
    tree = json.loads(raw)
    rows = tree.get('blobs', [])
    if (tree.get('base_commit') != BASE or tree.get('repo') != 'sphinx-doc/sphinx' or len(rows) != 1416
            or len({x['path'] for x in rows}) != len(rows)
            or any(x.get('mode') not in ('100644', '100755') or x.get('size', 4_194_305) > 4_194_304 for x in rows)):
        raise ValueError('public tree structure differs')
    return tree


def verify_container(item, cid):
    host, config = item['HostConfig'], item['Config']
    tmpfs = {'/tmp': 'rw,noexec,nosuid,size=268435456,mode=1777'}
    mounts = item.get('Mounts')
    if (item['Id'] != cid or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs']
            or host['Init'] is not True or host['Privileged'] or host['CapDrop'] != ['ALL']
            or host.get('CapAdd') or 'no-new-privileges' not in host.get('SecurityOpt', [])
            or host['Memory'] != 1024**3 or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
            or config['User'] != '65534:65534' or host.get('Binds') or host.get('VolumesFrom')
            or host.get('Mounts') or config.get('Volumes') or host.get('Tmpfs') != tmpfs
            or not isinstance(mounts, list) or len(mounts) > 1
            or any(m.get('Type') != 'tmpfs' or m.get('Destination') != '/tmp' or m.get('Source') for m in mounts)):
        raise ValueError('container isolation differs')


def assess_collection(observation, tree, exit_code):
    rows = observation['items']
    nodes = [r['nodeid'] for r in rows]
    if len(nodes) != len(set(nodes)):
        raise ValueError('duplicate collected nodes')
    paths = {r['path'] for r in rows}
    nonpublic = sorted(paths - {r['path'] for r in tree['blobs']})
    acceptable = (exit_code == 0 and bool(nodes) and not observation['collection_errors']
                  and not observation['collection_skips'] and not nonpublic)
    return {'status': 'collection_observed' if acceptable else 'collection_observed_with_errors',
            'collected': len(nodes), 'collected_files': len(paths), 'nonpublic_collected_files': nonpublic,
            'collection_errors': observation['collection_errors'], 'collection_skips': observation['collection_skips']}


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on a Linux Docker host required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'public collection reconnaissance only',
              'task_id': 'sphinx-doc__sphinx-7738', 'base_commit': BASE, 'image': IMAGE,
              'provider_calls': 0, 'test_execution': False, 'hidden_oracle': 'not_run',
              'ordinary_acceptance': False, 'tox_environment_equivalence_verified': False,
              'extra_testbed_files_audited': False}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}

    def command(args, timeout=90):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix='p07-sphinx7738-collection-') as temporary:
        home = Path(temporary)
        cidfile, cid = home / 'container.cid', None
        try:
            tree = verify_tree(TREE.read_bytes())
            report['public_tree_sha256'] = TREE_SHA
            commit = command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
            revision = commit.stdout.decode('ascii').strip()
            if commit.returncode or not re.fullmatch(r'[a-f0-9]{40}', revision):
                raise ValueError('tested commit unavailable')
            report['tested_commit'] = revision
            pulled = command([ENGINE, 'pull', IMAGE], timeout=300)
            (output / 'pull.stdout').write_bytes(pulled.stdout)
            (output / 'pull.stderr').write_bytes(pulled.stderr)
            if pulled.returncode:
                raise ValueError('pinned image pull failed')
            created = command([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=268435456,mode=1777',
                '--workdir=/testbed', '--env=HOME=/tmp', '--entrypoint=/bin/sleep', IMAGE, 'infinity'])
            if created.returncode:
                raise ValueError('isolated container creation failed')
            cid = cidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'[a-f0-9]{64}', cid) or command([ENGINE, 'start', cid]).returncode:
                raise ValueError('owned container start failed')
            inspected = command([ENGINE, 'inspect', cid])
            if inspected.returncode:
                raise ValueError('container inspection failed')
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            item = json.loads(inspected.stdout)[0]
            verify_container(item, cid)
            inspected_image = command([ENGINE, 'image', 'inspect', IMAGE])
            image_item = json.loads(inspected_image.stdout)[0] if not inspected_image.returncode else {}
            if image_item.get('Id') != item['Image'] or IMAGE not in image_item.get('RepoDigests', []):
                raise ValueError('actual immutable image differs')
            (output / 'image-inspect.json').write_bytes(inspected_image.stdout)
            checks = []
            # Chunked argv avoids Linux's per-argument size limit; every row is checked once.
            for offset in range(0, len(tree['blobs']), 100):
                rows = tree['blobs'][offset:offset + 100]
                verified = command([ENGINE, 'exec', '--user=65534:65534', cid, PYTHON, '-I', '-B', '-c',
                                    common.VERIFY_SOURCE, json.dumps(rows, separators=(',', ':'))])
                if verified.returncode:
                    raise ValueError('source byte audit failed')
                part = json.loads(verified.stdout)
                if part.get('checked') != len(rows) or part.get('differences'):
                    (output / 'source-differences.json').write_text(json.dumps(part, indent=2) + '\n')
                    raise ValueError('public source bytes differ')
                checks.append(part)
            (output / 'source-verification.json').write_text(json.dumps(checks, indent=2, sort_keys=True) + '\n')
            report['source_blobs_verified'] = sum(x['checked'] for x in checks)
            deps_code = ("import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'],d.version) "
                         "for d in m.distributions() if d.metadata.get('Name'))))")
            deps = command([ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, PYTHON, '-I', '-B', '-c', deps_code])
            (output / 'dependencies.stdout').write_bytes(deps.stdout)
            (output / 'dependencies.stderr').write_bytes(deps.stderr)
            if deps.returncode:
                raise ValueError('dependency inventory unavailable')
            report['dependency_inventory_sha256'] = hashlib.sha256(deps.stdout).hexdigest()
            result = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, PYTHON, '-I', '-B', '-c', SOURCE],
                              home=home, timeout=300, limit=8_000_000, check=lambda: None)
            stdout, stderr = result.stdout.encode(), result.stderr.encode()
            (output / 'collection.stdout').write_bytes(stdout)
            (output / 'collection.stderr').write_bytes(stderr)
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=hashlib.sha256(stdout).hexdigest(), stderr_sha256=hashlib.sha256(stderr).hexdigest())
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('collection output incomplete')
            observation = common.parse_collection(result.stdout, result.returncode)
            (output / 'collection.json').write_text(json.dumps(observation, indent=2, sort_keys=True) + '\n')
            report.update(assess_collection(observation, tree, result.returncode))
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            if cid is None and cidfile.is_file():
                cid = cidfile.read_text(encoding='ascii').strip()
            if cid and re.fullmatch(r'[a-f0-9]{64}', cid):
                removed = command([ENGINE, 'rm', '-f', cid])
                absent = command([ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                report['cleanup'] = {'container_id': cid, 'remove_exit': removed.returncode,
                                     'confirmed': absent.returncode == 0 and not absent.stdout.strip()}
            else:
                report['cleanup'] = {'container_id': cid, 'confirmed': False}
            if not report['cleanup']['confirmed']:
                report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'collection_observed' else 1)
