"""Build the verified xdist sdist twice offline in isolated throwaway containers.

This is an experiment in byte reproducibility, not runtime admission. Target
build code is executed only inside owned, network-disabled Docker containers.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_derived_collection as derived
import p07_pytest6116_prepare_dependencies as audit
import p07_pytest6116_xdist_prepare as source

from atomic_flow.check_requests_execution import _bounded

TOOLS = '/opt/p07-buildtools'
ARCHIVE = '/opt/p07-input/pytest-xdist-1.18.2.tar.gz'
EXPECTED_BOXED = '33bc94a90555dbf1b3995c4696b68942b9b37430337acfde51c05726fa6cfac5'
WHEEL = 'pytest_xdist-1.18.2-py2.py3-none-any.whl'
EXTRACT_AND_BUILD = r'''
import hashlib,os,pathlib,runpy,sys,tarfile
source='/opt/p07-input/pytest-xdist-1.18.2.tar.gz'
with open(source,'rb') as f: assert hashlib.sha256(f.read()).hexdigest()=='10468377901b80255cf192c4603a94ffe8b1f071f5c912868da5f5cb91170dae'
root=pathlib.Path('/tmp/p07-build'); root.mkdir(mode=0o700)
with tarfile.open(source,'r:gz') as archive:
    seen=set()
    for m in archive.getmembers():
        parts=m.name.split('/')
        if (m.name in seen or not parts or parts[0]!='pytest-xdist-1.18.2' or any(x in ('','.','..') for x in parts)
                or not (m.isdir() or m.isfile()) or m.size>524288 or '\\' in m.name): raise ValueError('invalid archive')
        seen.add(m.name)
        dest=root.joinpath(*parts)
        if m.isdir(): dest.mkdir(parents=True,exist_ok=True)
        else:
            dest.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(m) as src,open(dest,'xb') as out:
                raw=src.read(524289)
                if len(raw)!=m.size: raise ValueError('bad archive file')
                out.write(raw)
os.chdir(root/'pytest-xdist-1.18.2')
os.umask(0o022)
sys.path.insert(0,'/opt/p07-buildtools')
sys.argv=['setup.py','bdist_wheel','--dist-dir','/tmp/p07-output']
runpy.run_path('setup.py',run_name='__main__')
'''


def check_wheel(raw):
    """Validate every member against RECORD and bind boxed code and plugins."""
    digest = hashlib.sha256(raw).hexdigest()
    item = {'name': 'pytest-xdist', 'version': '1.18.2', 'filename': WHEEL, 'sha256': digest}
    result = audit.inspect_wheel(raw, item)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if hashlib.sha256(z.read('xdist/boxed.py')).hexdigest() != EXPECTED_BOXED:
            raise ValueError('built boxed.py differs from publisher source')
        entries = [n for n in z.namelist() if n.endswith('.dist-info/entry_points.txt')]
        if len(entries) != 1:
            raise ValueError('wheel plugin metadata missing')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(z.read(entries[0]).decode('utf8'))
        if dict(parser.items('pytest11')) != {'xdist': 'xdist.plugin', 'xdist.boxed': 'xdist.boxed',
                                              'xdist.looponfail': 'xdist.looponfail'}:
            raise ValueError('wheel plugin metadata differs')
        if sorted(result['requires_dist']) != sorted(['execnet (>=1.1)', 'pytest (>=3.0.0)']):
            raise ValueError('wheel runtime requirements differ')
    return result


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on Linux required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'isolated offline double build only',
              'runtime_acceptance': False, 'ordinary_acceptance': False, 'provider_calls': 0, 'attempts': []}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}

    def cmd(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix='p07-xdist-build-') as tmp_name:
        tmp = Path(tmp_name)
        active = None
        try:
            wheelhouse = tmp / 'inputs'
            prepared = source.prepare(wheelhouse)
            profile = json.loads(source.PROFILE.read_bytes())
            (output / 'preparation.json').write_bytes((wheelhouse / 'preparation.json').read_bytes())
            if prepared['profile_sha256'] != source.PROFILE_SHA256:
                raise ValueError('source profile changed')
            image = profile['python_image']
            pulled = cmd([recon.ENGINE, 'pull', image], timeout=240)
            (output / 'pull.stdout').write_bytes(pulled.stdout)
            (output / 'pull.stderr').write_bytes(pulled.stderr)
            if pulled.returncode:
                raise ValueError('base image pull failed')
            base = cmd([recon.ENGINE, 'image', 'inspect', image])
            inspected = json.loads(base.stdout)[0] if not base.returncode else {}
            if image not in inspected.get('RepoDigests', []) or inspected.get('Architecture') != 'amd64':
                raise ValueError('base image identity differs')
            expected = derived.expected_wheel_files(wheelhouse, {'wheels': profile['build_wheels']})
            context = tmp / 'context'
            context.mkdir()
            for item in [profile['source'], *profile['build_wheels']]:
                (context / item['filename']).write_bytes((wheelhouse / item['filename']).read_bytes())
            files = ' '.join('/opt/p07-input/' + item['filename'] for item in profile['build_wheels'])
            dockerfile = ('FROM ' + image + '\nCOPY . /opt/p07-input/\n'
                          'RUN --network=none /usr/local/bin/python -I -B -m pip --isolated install '
                          '--no-index --no-deps --no-compile --target ' + TOOLS + ' ' + files
                          + ' && chmod -R a+rX ' + TOOLS + '\n')
            (context / 'Dockerfile').write_text(dockerfile)
            (output / 'Dockerfile').write_text(dockerfile)
            build = cmd([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(context)], timeout=300)
            (output / 'image-build.stdout').write_bytes(build.stdout)
            (output / 'image-build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('builder image build failed')
            iid = (tmp / 'iid').read_text().strip()
            derived_image = cmd([recon.ENGINE, 'image', 'inspect', iid])
            info = json.loads(derived_image.stdout)[0] if not derived_image.returncode else {}
            base_layers = inspected.get('RootFS', {}).get('Layers', [])
            layers = info.get('RootFS', {}).get('Layers', [])
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid) or info.get('Id') != iid or not base_layers or layers[:len(base_layers)] != base_layers:
                raise ValueError('builder image ancestry differs')
            (output / 'base-inspect.json').write_bytes(base.stdout)
            (output / 'image-inspect.json').write_bytes(derived_image.stdout)
            report['builder_image_id'] = iid
            for index in (1, 2):
                attempt = {'index': index, 'status': 'not_qualified', 'cleanup_confirmed': False}
                report['attempts'].append(attempt)
                cidfile = tmp / ('cid' + str(index))
                try:
                    created = cmd([recon.ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never',
                        '--network=none', '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                        '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=128',
                        '--tmpfs=/tmp:rw,noexec,nosuid,size=268435456,mode=1777', '--workdir=/tmp',
                        '--env=HOME=/tmp', '--env=LANG=C', '--env=LC_ALL=C', '--env=TZ=UTC',
                        '--env=PYTHONHASHSEED=0', '--env=SOURCE_DATE_EPOCH=1500000000',
                        '--entrypoint=/bin/sleep', iid, 'infinity'])
                    if created.returncode:
                        raise ValueError('build container creation failed')
                    active = cidfile.read_text().strip()
                    if not re.fullmatch(r'[0-9a-f]{64}', active):
                        raise ValueError('invalid container id')
                    details = cmd([recon.ENGINE, 'inspect', active])
                    container = json.loads(details.stdout)[0]
                    host = container['HostConfig']
                    if (container['Id'] != active or container['Image'] != iid or container['State']['Running'] is not False
                            or container['Config']['User'] != '65534:65534' or host['NetworkMode'] != 'none'
                            or host['ReadonlyRootfs'] is not True or host['Init'] is not True or host['Privileged']
                            or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds') or host.get('VolumesFrom')
                            or container.get('Mounts') or host['Memory'] != 1024**3 or host['NanoCpus'] != 2_000_000_000
                            or host['PidsLimit'] != 128 or 'no-new-privileges' not in host.get('SecurityOpt', [])):
                        raise ValueError('build container controls differ')
                    (output / f'container-{index}.json').write_bytes(details.stdout)
                    copied = cmd([recon.ENGINE, 'cp', active + ':' + TOOLS, str(tmp / f'tools-{index}')])
                    if copied.returncode:
                        raise ValueError('build tools copy failed')
                    actual = derived.installed_files(tmp / f'tools-{index}')
                    if 'bin/wheel' not in actual:
                        raise ValueError('expected generated wheel script missing')
                    generated = {'bin/wheel': actual['bin/wheel']}
                    extras = derived.validate_installed(expected, {k: v for k, v in actual.items() if k not in generated})
                    (output / f'build-tools-{index}.json').write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
                    attempt.update(installed_publisher_files=len(expected), permitted_pip_metadata=extras,
                                   generated_scripts_unverified=generated)
                    if cmd([recon.ENGINE, 'start', active]).returncode:
                        raise ValueError('build container start failed')
                    probe = cmd([recon.ENGINE, 'exec', '--user=65534:65534', active, '/usr/local/bin/python', '-I', '-B', '-c',
                                 'import sys; print(sys.version_info[:3]); print(sys.executable)'])
                    if probe.returncode or probe.stderr or probe.stdout.splitlines() != [b'(3, 8, 20)', b'/usr/local/bin/python']:
                        raise ValueError('build interpreter differs')
                    (output / f'python-{index}.stdout').write_bytes(probe.stdout)
                    scheme = cmd([recon.ENGINE, 'exec', '--user=65534:65534', active, '/usr/local/bin/python', '-I', '-B', '-c',
                                  "import json,pip,sysconfig; p=sysconfig.get_paths(); print(json.dumps({'pip':pip.__version__,'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']},sort_keys=True))"])
                    expected_scheme = {'pip': '23.0.1', 'data': '/usr/local', 'scripts': '/usr/local/bin',
                                       'purelib': '/usr/local/lib/python3.8/site-packages', 'platlib': '/usr/local/lib/python3.8/site-packages'}
                    if scheme.returncode or scheme.stderr or json.loads(scheme.stdout) != expected_scheme:
                        raise ValueError('build pip or installation scheme differs')
                    (output / f'scheme-{index}.stdout').write_bytes(scheme.stdout)
                    run = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', active,
                        '/usr/local/bin/python', '-B', '-c', EXTRACT_AND_BUILD], home=tmp, timeout=120, limit=1000000, check=lambda: None)
                    (output / f'build-{index}.stdout').write_bytes(run.stdout.encode())
                    (output / f'build-{index}.stderr').write_bytes(run.stderr.encode())
                    if run.returncode or run.timed_out or run.stdout_truncated or run.stderr_truncated:
                        raise ValueError('offline build failed, timed out or output truncated')
                    listing = cmd([recon.ENGINE, 'exec', '--user=65534:65534', active, '/bin/ls', '-1', '/tmp/p07-output'])
                    if listing.returncode or listing.stdout.splitlines() != [WHEEL.encode()]:
                        raise ValueError('unexpected build output')
                    read_code = ("import base64,os,stat,sys; p='/tmp/p07-output/" + WHEEL + "'; "
                                 "fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); st=os.fstat(fd); "
                                 "assert stat.S_ISREG(st.st_mode) and 0<st.st_size<=16777216; "
                                 "b=os.read(fd,st.st_size+1); assert len(b)==st.st_size; "
                                 "sys.stdout.write(base64.b64encode(b).decode('ascii'))")
                    copied = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', active, '/usr/local/bin/python',
                                       '-I', '-B', '-c', read_code], home=tmp, timeout=30, limit=24000000, check=lambda: None)
                    if copied.returncode or copied.timed_out or copied.stdout_truncated or copied.stderr_truncated or copied.stderr:
                        raise ValueError('safe wheel extraction failed')
                    raw = base64.b64decode(copied.stdout, validate=True)
                    if not 1 <= len(raw) <= audit.MAX_WHEEL:
                        raise ValueError('unsafe wheel size')
                    (output / f'build-{index}.whl').write_bytes(raw)
                    attempt.update(status='built', wheel_sha256=hashlib.sha256(raw).hexdigest(), wheel=check_wheel(raw))
                finally:
                    if active is None and cidfile.exists():
                        active = cidfile.read_text().strip()
                    if active and re.fullmatch(r'[0-9a-f]{64}', active):
                        rm = cmd([recon.ENGINE, 'rm', '-f', active])
                        absent = cmd([recon.ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + active, '--format', '{{.ID}}'])
                        attempt['cleanup_confirmed'] = not rm.returncode and not absent.returncode and not absent.stdout.strip()
                        active = None
                    if not attempt['cleanup_confirmed']:
                        raise ValueError('container cleanup uncertain')
            if (output / 'build-1.whl').read_bytes() != (output / 'build-2.whl').read_bytes():
                raise ValueError('independent wheel bytes differ')
            report['status'] = 'offline_double_build_byte_identical'
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result['status'] == 'offline_double_build_byte_identical' else 1)
