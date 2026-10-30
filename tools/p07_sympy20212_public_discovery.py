"""Compare SymPy20212 public test candidates to native discovery in bounded Docker.

Runs only target import and discovery inside the immutable isolated image. Replaces
in-memory test execution with a recorder; no public test body is invoked. This is
not a full collection, ordinary execution, TaskRunner admission or model result.
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

from p07_pytest6116_collection_recon import VERIFY_SOURCE
from p07_sympy20212_public_inventory import BASE, BLACKLIST, TREE

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'benchmarks/stage4/p07-sympy20212-public-tests-v1.json'
MANIFEST_SHA = '8c6aba97d378414cce18426daaa58fd34cfd9cbf4ce265c400b8918519fdb52d'
IMAGE = 'swebench/sweb.eval.x86_64.sympy_1776_sympy-20212@sha256:10acbbc49c1905919809c0d8ca6305ee0d5e546fa541246c18fca3e222f79626'
PYTHON = '/opt/miniconda3/envs/testbed/bin/python'
ENGINE = '/usr/bin/docker'
RUNNER = [
    {'path': 'bin/test', 'sha': '3a4713e38f94f1e0ae280d5a8119f86181560303', 'size': 3991},
    {'path': 'sympy/testing/runtests.py', 'sha': 'a280fc52cd83b94eddaf4874f3672d08f8101c35', 'size': 88919},
]
SOURCE = r'''
import json,os,sys
sys.dont_write_bytecode=True
if any(os.environ.get(name) for name in ('TRAVIS_BUILD_NUMBER','PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONINSPECT','PYTHONUSERBASE')):
    raise SystemExit(125)
if os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python' or not sys.flags.isolated:
    raise SystemExit(125)
sys.path.insert(0,'/testbed')
import sympy
from sympy.testing import runtests
if runtests.ON_TRAVIS:
    raise SystemExit(125)
if os.path.realpath(sympy.__file__)!='/testbed/sympy/__init__.py' or os.path.realpath(runtests.__file__)!='/testbed/sympy/testing/runtests.py':
    raise SystemExit(125)
result=[]
def record(self,**kwargs):
    result.extend(os.path.relpath(os.path.realpath(path),'/testbed').replace(os.sep,'/') for path in self._testfiles)
    return True
runtests.SymPyTests.test=record
code=runtests._test(colors=False,seed=1)
print('P07_SYMPY_DISCOVERY='+json.dumps({'files':result,'exit_code':code,'python':list(sys.version_info[:3]),
 'sympy_version':sympy.__version__,'on_travis':bool(runtests.ON_TRAVIS),'test_bodies_executed':False},sort_keys=True),flush=True)
raise SystemExit(code)
'''


def manifest_data(raw):
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError('fixed public manifest hash differs')
    value = json.loads(raw)
    rows = value.get('test_candidates', [])
    if (value.get('base_commit') != BASE or value.get('root_tree') != TREE
            or value.get('default_blacklist_substring') != BLACKLIST or len(rows) != 543
            or sum(row['native_default_blacklisted'] for row in rows) != 14):
        raise ValueError('fixed public inventory differs')
    return rows


def compare_discovery(stdout, exit_code, rows):
    lines = [line.removeprefix('P07_SYMPY_DISCOVERY=') for line in stdout.splitlines()
             if line.startswith('P07_SYMPY_DISCOVERY=')]
    if len(lines) != 1:
        raise ValueError('native discovery marker missing or duplicate')
    data = json.loads(lines[0])
    files = data.get('files')
    if (type(files) is not list or any(type(x) is not str for x in files)
            or len(files) != len(set(files)) or data.get('exit_code') != exit_code
            or exit_code != 0 or data.get('python') != [3, 9, 20] or data.get('on_travis') is not False
            or data.get('test_bodies_executed') is not False):
        raise ValueError('native discovery result incomplete')
    expected = {row['path'] for row in rows if not row['native_default_blacklisted']}
    actual = set(files)
    return {'observed': len(files), 'expected': len(expected), 'missing': sorted(expected - actual),
            'unexpected': sorted(actual - expected), 'sympy_version': data.get('sympy_version')}


def verify_container(item, cid, image_id):
    """Require the exact owned worker configuration before running target code."""
    host, config = item.get('HostConfig', {}), item.get('Config', {})
    if (item.get('Id') != cid or item.get('Image') != image_id or host.get('NetworkMode') != 'none'
            or host.get('ReadonlyRootfs') is not True or host.get('Init') is not True or host.get('Privileged')
            or host.get('CapDrop') != ['ALL'] or host.get('CapAdd') or 'no-new-privileges' not in host.get('SecurityOpt', [])
            or host.get('Memory') != 1024**3 or host.get('NanoCpus') != 2_000_000_000 or host.get('PidsLimit') != 128
            or config.get('User') != '65534:65534' or config.get('WorkingDir') != '/testbed'
            or host.get('Binds') or host.get('VolumesFrom') or item.get('Mounts')
            or host.get('Tmpfs') != {'/tmp': 'rw,noexec,nosuid,size=134217728,mode=1777'}):
        raise ValueError('worker isolation differs')


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh Linux output required')
    rows = manifest_data(MANIFEST.read_bytes())
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'public file discovery only', 'provider_calls': 0,
              'ordinary_acceptance': False, 'test_bodies_executed': False, 'hidden_oracle': 'not_run',
              'manifest_sha256': MANIFEST_SHA, 'image': IMAGE}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    with tempfile.TemporaryDirectory(prefix='p07-sympy-discovery-') as tmp:
        home, cid = Path(tmp), None
        cidfile = home / 'cid'

        def control(args, timeout=60):
            return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)

        try:
            commit = control(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
            report['tested_commit'] = commit.stdout.decode().strip()
            if commit.returncode or not re.fullmatch(r'[a-f0-9]{40}', report['tested_commit']):
                raise ValueError('tested commit unavailable')
            pull = control([ENGINE, 'pull', IMAGE], 300)
            (output / 'pull.stdout').write_bytes(pull.stdout)
            (output / 'pull.stderr').write_bytes(pull.stderr)
            if pull.returncode:
                raise ValueError('image pull failed')
            inspect_image = control([ENGINE, 'image', 'inspect', IMAGE])
            image = json.loads(inspect_image.stdout)[0] if not inspect_image.returncode else {}
            if (IMAGE not in image.get('RepoDigests', []) or image.get('Os') != 'linux'
                    or image.get('Architecture') != 'amd64' or not re.fullmatch(r'sha256:[a-f0-9]{64}', image.get('Id', ''))):
                raise ValueError('image identity differs')
            (output / 'image-inspect.json').write_bytes(inspect_image.stdout)
            created = control([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777',
                '--workdir=/testbed', '--env=HOME=/tmp', '--env=TRAVIS_BUILD_NUMBER=', '--entrypoint=/bin/sleep', image['Id'], 'infinity'])
            if created.returncode:
                raise ValueError('worker creation failed')
            cid = cidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'[a-f0-9]{64}', cid) or control([ENGINE, 'start', cid]).returncode:
                raise ValueError('owned worker start failed')
            inspected = control([ENGINE, 'inspect', cid])
            item = json.loads(inspected.stdout)[0] if not inspected.returncode else {}
            verify_container(item, cid, image['Id'])
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            public = [{'path': row['path'], 'sha': row['sha'], 'size': row['size']} for row in rows] + RUNNER
            verify = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, PYTHON, '-I', '-B',
                               '-c', VERIFY_SOURCE, json.dumps(public, separators=(',', ':'))],
                              home=home, timeout=90, limit=1_000_000, check=lambda: None)
            (output / 'source-verification.stdout').write_text(verify.stdout, encoding='utf-8')
            (output / 'source-verification.stderr').write_text(verify.stderr, encoding='utf-8')
            if verify.returncode != 0 or verify.timed_out or verify.stdout_truncated or verify.stderr_truncated:
                raise ValueError('source byte audit incomplete')
            audit = json.loads(verify.stdout)
            report['source_audit'] = audit
            if audit.get('checked') != len(public) or audit.get('differences'):
                raise ValueError('image public bytes differ')
            result = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, PYTHON, '-I', '-B', '-c', SOURCE],
                              home=home, timeout=120, limit=1_000_000, check=lambda: None)
            (output / 'discovery.stdout').write_text(result.stdout, encoding='utf-8')
            (output / 'discovery.stderr').write_text(result.stderr, encoding='utf-8')
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                          stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest())
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('native discovery stream incomplete')
            observed = compare_discovery(result.stdout, result.returncode, rows)
            report['discovery'] = observed
            report['status'] = 'native_file_discovery_matches_static_inventory' if not observed['missing'] and not observed['unexpected'] else 'native_file_discovery_differs'
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            if cid is None and cidfile.is_file():
                cid = cidfile.read_text(encoding='ascii').strip()
            if cid and re.fullmatch(r'[a-f0-9]{64}', cid):
                removed = control([ENGINE, 'rm', '-f', cid])
                absent = control([ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                report['cleanup'] = {'container_id': cid, 'remove_exit': removed.returncode,
                                     'confirmed': absent.returncode == 0 and not absent.stdout.strip()}
            else:
                report['cleanup'] = {'container_id': cid, 'confirmed': False}
            if not report['cleanup']['confirmed']:
                report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    outcome = run(parser.parse_args().output)
    print(json.dumps(outcome, sort_keys=True))
    raise SystemExit(0 if outcome['status'] == 'native_file_discovery_matches_static_inventory' else 1)
