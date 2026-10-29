"""Run three public compatibility probes in a pinned, isolated Python 3.8 image.

This is a diagnostic, not a full test run, ordinary admission or model evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_derived_collection as derived
import p07_pytest6116_prepare_dependencies as audit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-repro-v1.json'
FULL_PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-full-v1.json'
PEXPECT_PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-pexpect-v1.json'
PYTHON = '/usr/local/bin/python'
TARGET = r'''
import json,os,sys
sys.dont_write_bytecode=True
os.environ['HYPOTHESIS_STORAGE_DIRECTORY']='/tmp/p07-hypothesis'
sys.path.insert(0,'/testbed/src')
import pytest,_pytest
if (sys.version_info[:3]!=(3,8,20) or os.path.realpath(pytest.__file__)!='/testbed/src/pytest.py'
    or os.path.realpath(_pytest.__file__)!='/testbed/src/_pytest/__init__.py'):
    raise SystemExit(125)
class Recorder:
    def __init__(self): self.items=[]; self.completed=[]; self.outcomes={}; self.finished=False; self.collection_errors=[]; self.collection_skips=[]; self.nonpassing=[]
    def pytest_collection_finish(self,session): self.items=[i.nodeid for i in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_runtest_logreport(self,report):
        k=report.when+':'+report.outcome; self.outcomes[k]=self.outcomes.get(k,0)+1
        if report.outcome!='passed':
            self.nonpassing.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome,
                'wasxfail':getattr(report,'wasxfail',None),'reason':str(report.longrepr)[:500]})
        if report.when=='teardown': self.completed.append(report.nodeid)
    def pytest_sessionfinish(self,session,exitstatus): self.finished=True
r=Recorder()
code=int(pytest.main(['-q','--tb=short','-o','cache_dir=/tmp/p07-pytest-cache',
 'testing/code/test_source.py::test_isparseable',
 'testing/code/test_source.py::TestAccesses::test_getrange',
 'testing/code/test_source.py::test_getfslineno'],plugins=[r]))
print('\nP07_PY38_REPRO='+json.dumps({'finished':r.finished,'exit_code':code,'items':r.items,
 'completed':r.completed,'outcomes':r.outcomes,'python':list(sys.version_info[:3]),
 'pytest_file':os.path.realpath(pytest.__file__),'collection_errors':r.collection_errors,
 'collection_skips':r.collection_skips,'nonpassing':r.nonpassing},sort_keys=True),flush=True)
raise SystemExit(code)
'''


def prepare_wheels(profile, directory):
    rows = []
    for item in profile['wheels']:
        if not item['url'].startswith('https://files.pythonhosted.org/') or not item['url'].endswith('/' + item['filename']):
            raise ValueError('publisher wheel URL differs')
        with urllib.request.urlopen(item['url'], timeout=45) as response:
            raw = response.read(audit.MAX_WHEEL + 1)
        row = audit.inspect_wheel(raw, item)
        if row['requires_python'] and Version('3.8.20') not in SpecifierSet(row['requires_python']):
            raise ValueError('wheel is incompatible with Python 3.8')
        rows.append(row)
        (directory / item['filename']).write_bytes(raw)
    versions = {row['name']: Version(row['version']) for row in rows}
    environment = {**audit.ENVIRONMENT, 'python_version': '3.8', 'python_full_version': '3.8.20',
                   'implementation_version': '3.8.20'}
    for row in rows:
        for value in row['requires_dist']:
            req = Requirement(value)
            if req.marker and not req.marker.evaluate(environment):
                continue
            name = canonicalize_name(req.name)
            if name not in versions or versions[name] not in req.specifier:
                raise ValueError('unresolved active dependency: ' + name)
    return rows


def target_source(full_public):
    if not full_public:
        return TARGET
    focused = "'testing/code/test_source.py::test_isparseable',\n 'testing/code/test_source.py::TestAccesses::test_getrange',\n 'testing/code/test_source.py::test_getfslineno'"
    if TARGET.count(focused) != 1:
        raise ValueError('fixed full-suite source replacement failed')
    return TARGET.replace("'-q','--tb=short'", "'-q','--tb=line'").replace(focused, "'testing'")


def audit_installed_wheels(command, cid, wheelhouse, profile, destination):
    """Copy only publisher-owned roots from a stopped container; reject extra files."""
    expected = derived.expected_wheel_files(wheelhouse, profile)
    if not expected:
        raise ValueError('empty installed wheel inventory')
    roots = sorted({name.split('/', 1)[0] for name in expected})
    destination.mkdir()
    for name in roots:
        result = command([recon.ENGINE, 'cp', cid + ':/usr/local/lib/python3.8/site-packages/' + name,
                          str(destination / name)])
        if result.returncode:
            raise ValueError('installed wheel root unavailable: ' + name)
    actual = derived.installed_files(destination)
    extras = derived.validate_installed(expected, actual)
    return expected, actual, extras


def run(output, *, full_public=False, source_for_subprocesses=False, with_pexpect=False):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on Linux required')
    if with_pexpect and not full_public:
        raise ValueError('pexpect candidate requires the full public suite')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'full public test diagnostic' if full_public else 'three-test public compatibility diagnostic',
              'ordinary_acceptance': False, 'provider_calls': 0, 'hidden_oracle': 'not_run'}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)
    with tempfile.TemporaryDirectory(prefix='p07-py38-') as temp:
        tmp = Path(temp)
        cidfile = tmp / 'cid'
        cid = None
        try:
            profile_raw = (PEXPECT_PROFILE if with_pexpect else FULL_PROFILE if full_public else PROFILE).read_bytes()
            profile = json.loads(profile_raw)
            report['profile_sha256'] = hashlib.sha256(profile_raw).hexdigest()
            if (len(profile['wheels']) != (13 if with_pexpect else 11 if full_public else 8) or profile['source_image'] != recon.IMAGE
                    or profile['python_image'] != 'python@sha256:1d52838af602b4b5a831beb13a0e4d073280665ea7be7f69ce2382f29c5a613f'):
                raise ValueError('fixed images and wheels required')
            rows = prepare_wheels(profile, tmp)
            (output / 'wheels.json').write_text(json.dumps(rows, indent=2, sort_keys=True) + '\n')
            for name, image in (('python', profile['python_image']), ('source', profile['source_image'])):
                pull = command([recon.ENGINE, 'pull', image], timeout=240)
                (output / (name + '-pull.stdout')).write_bytes(pull.stdout)
                (output / (name + '-pull.stderr')).write_bytes(pull.stderr)
                if pull.returncode:
                    raise ValueError('pinned ' + name + ' image pull failed')
                inspected = command([recon.ENGINE, 'image', 'inspect', image])
                item = json.loads(inspected.stdout)[0]
                if inspected.returncode or image not in item.get('RepoDigests', []) or item.get('Architecture') != 'amd64':
                    raise ValueError('image identity differs')
            dockerfile = ('FROM ' + profile['source_image'] + ' AS target\nFROM ' + profile['python_image']
                          + '\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\n'
                          + 'RUN --network=none /usr/local/bin/python -I -B -m pip --isolated install '
                            '--no-index --no-deps --no-compile /opt/p07-wheels/*.whl\n')
            if source_for_subprocesses:
                inject = ("from pathlib import Path; "
                          "p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                          "assert not p.exists(); "
                          "p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")")
                dockerfile += 'RUN --network=none ' + PYTHON + ' -I -B -c ' + shlex.quote(inject) + '\n'
                report['subprocess_source_policy'] = 'image site hook inserts audited public source into sys.path'
            (tmp / 'Dockerfile').write_text(dockerfile)
            (output / 'Dockerfile').write_text(dockerfile)
            build = command([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(tmp)], timeout=300)
            (output / 'build.stdout').write_bytes(build.stdout)
            (output / 'build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('diagnostic image build failed')
            iid = (tmp / 'iid').read_text().strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('image ID missing')
            report['image_id'] = iid
            create = command([recon.ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never',
                '--network=none', '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=128',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777', '--workdir=/testbed',
                '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
            if create.returncode:
                raise ValueError('container creation failed')
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', cid):
                raise ValueError('container ID missing')
            inspected = command([recon.ENGINE, 'inspect', cid])
            container = json.loads(inspected.stdout)[0]
            host = container['HostConfig']
            if (container['Image'] != iid or container['Config']['User'] != '65534:65534'
                    or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs'] or host['Privileged']
                    or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds')
                    or host.get('VolumesFrom') or container.get('Mounts') or host['Init'] is not True
                    or host['Memory'] != 1024**3 or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
                    or 'no-new-privileges' not in host.get('SecurityOpt', []) or container['State']['Running'] is not False):
                raise ValueError('container controls differ')
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            expected, installed, extras = audit_installed_wheels(command, cid, tmp, profile, tmp / 'installed')
            (output / 'installed-inventory.json').write_text(json.dumps(installed, indent=2, sort_keys=True) + '\n')
            report.update(installed_publisher_files=len(expected), installed_total_files=len(installed), pip_metadata_extras=extras)
            if command([recon.ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            tree = json.loads(recon.PUBLIC_TREE.read_bytes())
            if tree.get('base_commit') != recon.BASE or tree.get('repo') != 'pytest-dev/pytest' or len(tree.get('blobs', [])) != 474:
                raise ValueError('public tree inventory differs')
            verify = command([recon.ENGINE, 'exec', '--user=65534:65534', cid, PYTHON, '-I', '-B', '-c',
                              recon.VERIFY_SOURCE, json.dumps(tree['blobs'], separators=(',', ':'))], timeout=90)
            (output / 'source-verification.stdout').write_bytes(verify.stdout)
            if verify.returncode or json.loads(verify.stdout) != {'checked': 474, 'differences': []}:
                raise ValueError('public source bytes differ')
            if source_for_subprocesses:
                probe = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, PYTHON, '-I', '-B', '-c',
                                 "import os,pytest,_pytest; print(os.path.realpath(pytest.__file__)); print(os.path.realpath(_pytest.__file__))"])
                (output / 'subprocess-source.stdout').write_bytes(probe.stdout)
                (output / 'subprocess-source.stderr').write_bytes(probe.stderr)
                if probe.returncode or probe.stdout.splitlines() != [b'/testbed/src/pytest.py', b'/testbed/src/_pytest/__init__.py']:
                    raise ValueError('isolated subprocess does not import audited source')
            source = target_source(full_public)
            result = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, PYTHON,
                               '-I', '-B', '-c', source], home=tmp, timeout=360 if full_public else 120, limit=1000000, check=lambda: None)
            (output / 'stdout').write_bytes(result.stdout.encode())
            (output / 'stderr').write_bytes(result.stderr.encode())
            report.update(exit_code=result.returncode, timed_out=result.timed_out, stdout_truncated=result.stdout_truncated,
                          stderr_truncated=result.stderr_truncated, stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                          stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest(), source_blobs_verified=474)
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('diagnostic output incomplete')
            markers = [line.removeprefix('P07_PY38_REPRO=') for line in result.stdout.splitlines() if line.startswith('P07_PY38_REPRO=')]
            if len(markers) != 1:
                raise ValueError('diagnostic marker missing')
            marker = json.loads(markers[0])
            (output / 'observation.json').write_text(json.dumps(marker, indent=2, sort_keys=True) + '\n')
            if (not marker['finished'] or marker['exit_code'] != result.returncode or not marker['items']
                    or (not full_public and len(marker['items']) != 3)
                    or len(set(marker['items'])) != len(marker['items'])
                    or set(marker['completed']) != set(marker['items'])
                    or len(marker['nonpassing']) != sum(value for key, value in marker['outcomes'].items()
                                                        if key.endswith(':failed') or key.endswith(':skipped'))):
                raise ValueError('diagnostic inventory incomplete')
            public_paths = {row['path'] for row in tree['blobs']}
            nonpublic = sorted({node.split('::', 1)[0] for node in marker['items']} - public_paths)
            if nonpublic:
                raise ValueError('collected nonpublic paths: ' + repr(nonpublic))
            report.update(status='full_diagnostic_completed' if full_public else 'focused_diagnostic_completed',
                          outcomes=marker['outcomes'], collected=len(marker['items']),
                          collection_errors=marker['collection_errors'], collection_skips=marker['collection_skips'])
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text().strip()
            if cid and re.fullmatch(r'[0-9a-f]{64}', cid):
                removed = command([recon.ENGINE, 'rm', '-f', cid])
                absent = command([recon.ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
                report['cleanup_confirmed'] = not removed.returncode and not absent.returncode and not absent.stdout.strip()
                if not report['cleanup_confirmed']:
                    report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--full-public', action='store_true')
    parser.add_argument('--source-for-subprocesses', action='store_true')
    parser.add_argument('--with-pexpect', action='store_true')
    options = parser.parse_args()
    result = run(options.output, full_public=options.full_public, source_for_subprocesses=options.source_for_subprocesses,
                 with_pexpect=options.with_pexpect)
    expected = 'full_diagnostic_completed' if options.full_public else 'focused_diagnostic_completed'
    raise SystemExit(0 if result['status'] == expected and result['exit_code'] == 0 else 1)
