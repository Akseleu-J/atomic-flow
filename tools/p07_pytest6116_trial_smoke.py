"""Isolated Twisted installation and ABI observation; no test admission or inference."""
from __future__ import annotations

import argparse
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


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on Linux required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'Twisted trial installation and ABI smoke only',
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
            scheme = ("import pip,sys,sysconfig; p=sysconfig.get_paths(); "
                      "x={'python':sys.executable,'version':list(sys.version_info[:3]),'pip':pip.__version__,"
                      "'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']}; "
                      'assert x==' + repr(py38.FIXED_SCHEME))
            dockerfile = ('FROM ' + IMAGE + '\nCOPY *.whl /opt/p07-wheels/\n'
                          'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(scheme) + '\n'
                          'RUN --network=none ' + py38.PYTHON + ' -I -B -m pip --isolated install '
                          '--no-index --no-deps --no-compile --force-reinstall /opt/p07-wheels/*.whl\n')
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
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result['status'] == 'trial_install_and_abi_smoke_passed' else 1)
