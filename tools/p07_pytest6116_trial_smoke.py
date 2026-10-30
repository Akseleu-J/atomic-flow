"""Isolated Twisted installation and ABI observation; no test admission or inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as py38
import p07_pytest6116_trial_prepare as trial

from atomic_flow.check_requests_execution import _bounded

IMAGE = 'python@sha256:1d52838af602b4b5a831beb13a0e4d073280665ea7be7f69ce2382f29c5a613f'
PROBE = r'''
import json,os,sys,importlib.metadata as metadata
from zope.interface import Interface,implementer
from zope.interface.verify import verifyObject
import zope.interface._zope_interface_coptimizations as native
import twisted.trial.unittest as trial
class IExample(Interface):
    def value():
        pass
@implementer(IExample)
class Example:
    def value(self):return 7
assert sys.version_info[:3]==(3,8,20)
assert metadata.version('setuptools')=='57.5.0'
assert metadata.version('Twisted')=='21.2.0'
assert verifyObject(IExample,Example()) and Example().value()==7
site='/usr/local/lib/python3.8/site-packages/'
nativepath=os.path.realpath(native.__file__)
trialpath=os.path.realpath(trial.__file__)
assert nativepath.startswith(site+'zope/interface/_zope_interface_coptimizations.') and nativepath.endswith('.so')
assert trialpath==site+'twisted/trial/unittest.py'
print(json.dumps({'twisted':metadata.version('Twisted'),'setuptools':metadata.version('setuptools'),
                  'native':nativepath,'trial':trialpath,'interface_ok':True},sort_keys=True))
'''
TRIAL_NODES = frozenset('testing/test_unittest.py::TestTrialUnittest::' + name for name in (
    'test_trial_testcase_runtest_not_collected', 'test_trial_exceptions_with_skips', 'test_trial_error',
    'test_trial_testcase_skip_property', 'test_trial_testfunction_skip_property',
    'test_trial_testcase_todo_property', 'test_trial_testfunction_todo_property', 'test_trial_pdb'))
FOCUSED = r'''
import json,os,sys
sys.dont_write_bytecode=True
sys.path.insert(0,'/testbed/src')
import pytest,_pytest
assert sys.version_info[:3]==(3,8,20) and os.path.realpath(pytest.__file__)=='/testbed/src/pytest.py'
assert os.path.realpath(_pytest.__file__)=='/testbed/src/_pytest/__init__.py'
class Recorder:
    def __init__(self):self.items=[];self.reports=[];self.finished=False;self.collection_errors=[];self.collection_skips=[]
    def pytest_collection_finish(self,session):self.items=[item.nodeid for item in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_runtest_logreport(self,report):
        self.reports.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome,
                             'wasxfail':getattr(report,'wasxfail',None)})
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
code=int(pytest.main(['-q','--tb=short','-o','cache_dir=/tmp/p07-pytest-cache',
                      'testing/test_unittest.py::TestTrialUnittest'],plugins=[r]))
print('\nP07_TRIAL_FOCUSED='+json.dumps({'items':r.items,'reports':r.reports,'finished':r.finished,
      'exit_code':code,'collection_errors':r.collection_errors,'collection_skips':r.collection_skips},sort_keys=True),flush=True)
raise SystemExit(code)
'''


def parse_focused(stdout, exit_code):
    prefix = 'P07_TRIAL_FOCUSED='
    markers = [line.removeprefix(prefix) for line in stdout.splitlines() if line.startswith(prefix)]
    if len(markers) != 1:
        raise ValueError('exactly one trial diagnostic marker required')
    obs = json.loads(markers[0])
    if (obs.get('finished') is not True or obs.get('exit_code') != exit_code
            or type(obs.get('items')) is not list or len(obs['items']) != len(TRIAL_NODES)
            or set(obs['items']) != TRIAL_NODES or len(set(obs['items'])) != len(obs['items'])
            or obs.get('collection_errors') or obs.get('collection_skips') or type(obs.get('reports')) is not list):
        raise ValueError('focused trial collection differs or is incomplete')
    reports = obs['reports']
    pairs = [(r.get('nodeid'), r.get('phase')) for r in reports]
    if (len(set(pairs)) != len(pairs) or any(node not in TRIAL_NODES or phase not in ('setup','call','teardown')
                                              for node, phase in pairs)
            or any((node, 'teardown') not in pairs for node in TRIAL_NODES)):
        raise ValueError('focused trial phase inventory incomplete')
    return obs


def run(output, *, focused=False):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on Linux required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'eight public trial nodes' if focused else 'Twisted trial installation and ABI smoke only',
              'ordinary_acceptance': False, 'provider_calls': 0, 'full_test_suite': 'not_run'}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)
    with tempfile.TemporaryDirectory(prefix='p07-trial-smoke-') as temp:
        tmp = Path(temp)
        cidfile, cid = tmp / 'cid', None
        try:
            wheelhouse = tmp / 'verified'
            receipt = trial.prepare(wheelhouse)
            (output / 'wheel-preparation.json').write_bytes((wheelhouse / 'preparation.json').read_bytes())
            (output / 'base-preparation.json').write_bytes((wheelhouse / 'base/preparation.json').read_bytes())
            if receipt['total_distributions'] != 32 or receipt['profile_sha256'] != trial.PROFILE_SHA256:
                raise ValueError('fixed wheel closure differs')
            base_profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            optional_profile = json.loads(optional.PEXPECT_PROFILE.read_bytes())
            trial_profile = json.loads(trial.PROFILE.read_bytes())
            wheels = base_profile['wheels'] + optional_profile['wheels'] + trial_profile['wheels']
            profile = {'wheels': wheels}
            context = tmp / 'context'
            context.mkdir()
            for item in wheels:
                source = wheelhouse / item['filename']
                if not source.is_file():
                    source = wheelhouse / 'base' / item['filename']
                shutil.copyfile(source, context / item['filename'])
            pull = command([recon.ENGINE, 'pull', IMAGE], timeout=240)
            (output / 'pull.stdout').write_bytes(pull.stdout)
            (output / 'pull.stderr').write_bytes(pull.stderr)
            if pull.returncode:
                raise ValueError('pinned image pull failed')
            base_result = command([recon.ENGINE, 'image', 'inspect', IMAGE])
            base_image = json.loads(base_result.stdout)[0] if not base_result.returncode else {}
            if IMAGE not in base_image.get('RepoDigests', []) or base_image.get('Architecture') != 'amd64':
                raise ValueError('pinned image identity differs')
            if focused:
                source_pull = command([recon.ENGINE, 'pull', recon.IMAGE], timeout=240)
                (output / 'source-pull.stdout').write_bytes(source_pull.stdout)
                (output / 'source-pull.stderr').write_bytes(source_pull.stderr)
                if source_pull.returncode:
                    raise ValueError('pinned source image pull failed')
                source_inspect = command([recon.ENGINE, 'image', 'inspect', recon.IMAGE])
                source_image = json.loads(source_inspect.stdout)[0] if not source_inspect.returncode else {}
                if recon.IMAGE not in source_image.get('RepoDigests', []) or source_image.get('Architecture') != 'amd64':
                    raise ValueError('pinned source image identity differs')
                (output / 'source-image-inspect.json').write_bytes(source_inspect.stdout)
            scheme = ("import pip,sys,sysconfig; p=sysconfig.get_paths(); "
                      "x={'python':sys.executable,'version':list(sys.version_info[:3]),'pip':pip.__version__,"
                      "'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']}; "
                      'assert x==' + repr(py38.FIXED_SCHEME))
            dockerfile = (('FROM ' + recon.IMAGE + ' AS target\n') if focused else '') + ('FROM ' + IMAGE + '\n'
                          + ('COPY --from=target /testbed /testbed\n' if focused else '')
                          + 'COPY *.whl /opt/p07-wheels/\n'
                          'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(scheme) + '\n'
                          'RUN --network=none ' + py38.PYTHON + ' -I -B -m pip --isolated install '
                          '--no-index --no-deps --no-compile --force-reinstall /opt/p07-wheels/*.whl\n')
            if focused:
                inject = ("from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                          "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")")
                dockerfile += 'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(inject) + '\n'
            (context / 'Dockerfile').write_text(dockerfile)
            (output / 'Dockerfile').write_text(dockerfile)
            build = command([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(context)], timeout=300)
            (output / 'build.stdout').write_bytes(build.stdout)
            (output / 'build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('trial image build failed')
            iid = (tmp / 'iid').read_text().strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('derived image ID missing')
            image_result = command([recon.ENGINE, 'image', 'inspect', iid])
            image = json.loads(image_result.stdout)[0] if not image_result.returncode else {}
            layers = base_image.get('RootFS', {}).get('Layers', [])
            derived_layers = image.get('RootFS', {}).get('Layers', [])
            if image.get('Id') != iid or not layers or derived_layers[:len(layers)] != layers or len(derived_layers) <= len(layers):
                raise ValueError('derived image ancestry differs')
            (output / 'base-image-inspect.json').write_bytes(base_result.stdout)
            (output / 'derived-image-inspect.json').write_bytes(image_result.stdout)
            report['image_id'] = iid
            create = command([recon.ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never',
                '--network=none', '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=128',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777', '--workdir=/tmp',
                '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
            if create.returncode:
                raise ValueError('container creation failed')
            cid = cidfile.read_text().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', cid):
                raise ValueError('container ID missing')
            result = command([recon.ENGINE, 'inspect', cid])
            item = json.loads(result.stdout)[0]
            host = item['HostConfig']
            if (item['Id'] != cid or item['Image'] != iid or item['State']['Running'] is not False
                    or item['Config']['User'] != '65534:65534' or host['NetworkMode'] != 'none'
                    or host['ReadonlyRootfs'] is not True or host['Privileged'] is not False
                    or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds') or host.get('VolumesFrom')
                    or item.get('Mounts') or host['Init'] is not True or host['Memory'] != 1024**3
                    or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
                    or 'no-new-privileges' not in host.get('SecurityOpt', [])):
                raise ValueError('container controls differ')
            (output / 'container-inspect.json').write_bytes(result.stdout)
            verified, actual, extras, wrappers = py38.audit_relocated_wheels(command, cid, context, profile, tmp / 'installed')
            (output / 'installed-inventory.json').write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
            (output / 'generated-wrappers.json').write_text(json.dumps(wrappers, indent=2, sort_keys=True) + '\n')
            report.update(installed_publisher_files=verified['publisher_files_verified'], installed_total_files=len(actual),
                          pip_metadata_extras=extras, generated_wrappers_content_verified=False,
                          generated_wrappers_count=len(wrappers))
            if command([recon.ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            if focused:
                tree = json.loads(recon.PUBLIC_TREE.read_bytes())
                if tree.get('base_commit') != recon.BASE or tree.get('repo') != 'pytest-dev/pytest' or len(tree.get('blobs', [])) != 474:
                    raise ValueError('public source inventory differs')
                verify = command([recon.ENGINE, 'exec', '--user=65534:65534', cid, py38.PYTHON, '-I', '-B', '-c',
                                  recon.VERIFY_SOURCE, json.dumps(tree['blobs'], separators=(',', ':'))], timeout=90)
                (output / 'source-verification.stdout').write_bytes(verify.stdout)
                (output / 'source-verification.stderr').write_bytes(verify.stderr)
                if verify.returncode or json.loads(verify.stdout) != {'checked': 474, 'differences': []}:
                    raise ValueError('public source bytes differ')
                report['source_blobs_verified'] = 474
            for name, code in (('scheme', py38.SCHEME_PROBE), ('native', PROBE)):
                run = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid,
                                py38.PYTHON, '-I', '-B', '-c', code], home=tmp, timeout=60, limit=100000, check=lambda: None)
                (output / (name + '.stdout')).write_bytes(run.stdout.encode())
                (output / (name + '.stderr')).write_bytes(run.stderr.encode())
                if run.returncode or run.timed_out or run.stdout_truncated or run.stderr_truncated or run.stderr:
                    raise ValueError(name + ' probe failed or incomplete')
                observed = json.loads(run.stdout)
                if name == 'scheme' and observed != py38.FIXED_SCHEME:
                    raise ValueError('Python or pip installation scheme differs')
                if name == 'native' and (observed.get('twisted') != '21.2.0'
                        or observed.get('setuptools') != '57.5.0' or observed.get('interface_ok') is not True
                        or observed.get('native') not in actual or observed.get('trial') not in actual):
                    raise ValueError('trial/native result differs from audited inventory')
                report[name] = observed
            if focused:
                result = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid,
                                   py38.PYTHON, '-I', '-B', '-c', FOCUSED], home=tmp, timeout=180, limit=500000, check=lambda: None)
                (output / 'focused.stdout').write_bytes(result.stdout.encode())
                (output / 'focused.stderr').write_bytes(result.stderr.encode())
                report.update(focused_stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                              focused_stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest(),
                              focused_timed_out=result.timed_out, focused_stdout_truncated=result.stdout_truncated,
                              focused_stderr_truncated=result.stderr_truncated)
                if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                    raise ValueError('focused trial diagnostic incomplete')
                obs = parse_focused(result.stdout, result.returncode)
                (output / 'focused-observation.json').write_text(json.dumps(obs, indent=2, sort_keys=True) + '\n')
                report.update(focused_exit_code=result.returncode, focused_nodes=len(obs['items']),
                              focused_reports=obs['reports'])
                expected = {(node, phase) for node in TRIAL_NODES for phase in ('setup', 'call', 'teardown')}
                actual = {(row['nodeid'], row['phase']) for row in obs['reports']}
                report['status'] = ('focused_trial_passed' if result.returncode == 0 and actual == expected
                                    and all(row['outcome'] == 'passed' and row['wasxfail'] is None for row in obs['reports'])
                                    else 'focused_trial_observed_nonpassing')
            else:
                report['status'] = 'trial_install_and_abi_smoke_passed'
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
    parser.add_argument('--focused', action='store_true')
    options = parser.parse_args()
    result = run(options.output, focused=options.focused)
    expected = 'focused_trial_passed' if options.focused else 'trial_install_and_abi_smoke_passed'
    raise SystemExit(0 if result['status'] == expected else 1)
