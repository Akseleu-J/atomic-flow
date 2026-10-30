"""Observe the two public lsof tests in a byte-audited optional environment.

This focused diagnostic is not a full suite, task-runner admission or model
evaluation. A later public full-suite run must include the same two nodes.
"""
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
import urllib.request
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_lsof_probe as lsof
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as py38

from atomic_flow.check_requests_execution import _bounded

NODES = ('testing/test_capture.py::TestStdCaptureFD::test_many',
         'testing/test_capture.py::TestFDCapture::test_simple_many_check_open_files')


def test_source():
    focused = ("'testing/code/test_source.py::test_isparseable',\n "
               "'testing/code/test_source.py::TestAccesses::test_getrange',\n "
               "'testing/code/test_source.py::test_getfslineno'")
    if py38.TARGET.count(focused) != 1:
        raise ValueError('fixed selector replacement source differs')
    return py38.TARGET.replace(focused, ',\n '.join(repr(node) for node in NODES))


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on Linux required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'two public lsof tests only',
              'ordinary_acceptance': False, 'provider_calls': 0, 'hidden_oracle': 'not_run',
              'publisher_signature_verified': False, 'generated_console_script_contents_verified': False}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)
    with tempfile.TemporaryDirectory(prefix='p07-lsof-focused-') as temp:
        tmp = Path(temp)
        cidfile, cid = tmp / 'cid', None
        try:
            wheelhouse = tmp / 'wheelhouse'
            receipt = optional.prepare(wheelhouse, with_pexpect=True)
            (output / 'wheel-preparation.json').write_bytes((wheelhouse / 'preparation.json').read_bytes())
            profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            profile['wheels'].extend(json.loads(optional.PEXPECT_PROFILE.read_bytes())['wheels'])
            if len(profile['wheels']) != 25 or profile['source_image'] != recon.IMAGE or profile['python_image'] != lsof.BASE:
                raise ValueError('fixed images or 25 wheel closure differs')
            report.update(base_profile_sha256=receipt['base_profile_sha256'], optional_profile_sha256=receipt['profile_sha256'])
            for item in profile['wheels']:
                shutil.copyfile(wheelhouse / item['filename'], tmp / item['filename'])
            with urllib.request.urlopen(lsof.PACKAGE, timeout=60) as response:
                package = response.read(1_000_001)
            if len(package) != 318268 or lsof.digest(package) != lsof.PACKAGE_SHA256:
                raise ValueError('Debian package bytes differ')
            (tmp / 'lsof.deb').write_bytes(package)
            payload = command(['dpkg-deb', '--fsys-tarfile', str(tmp / 'lsof.deb')])
            if payload.returncode or len(payload.stdout) > 8_000_000:
                raise ValueError('Debian package payload unavailable')
            binary = lsof.publisher_binary(payload.stdout)
            report.update(lsof_package_sha256=lsof.digest(package), lsof_binary_sha256=lsof.digest(binary))
            for name, image in (('python', profile['python_image']), ('source', profile['source_image'])):
                pull = command([recon.ENGINE, 'pull', image], timeout=240)
                (output / (name + '-pull.stdout')).write_bytes(pull.stdout)
                (output / (name + '-pull.stderr')).write_bytes(pull.stderr)
                if pull.returncode:
                    raise ValueError('pinned ' + name + ' image pull failed')
                inspect = command([recon.ENGINE, 'image', 'inspect', image])
                image_data = json.loads(inspect.stdout)[0]
                if inspect.returncode or image not in image_data.get('RepoDigests', []) or image_data.get('Architecture') != 'amd64':
                    raise ValueError('pinned image identity differs')
            scheme = ("import pip,sys,sysconfig; p=sysconfig.get_paths(); "
                      "x={'python':sys.executable,'version':list(sys.version_info[:3]),'pip':pip.__version__,"
                      "'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']}; "
                      'assert x==' + repr(py38.FIXED_SCHEME))
            inject = ("from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                      "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")")
            dockerfile = ('FROM ' + profile['source_image'] + ' AS target\nFROM ' + profile['python_image']
                          + '\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\n'
                          + 'COPY lsof.deb /tmp/lsof.deb\nRUN --network=none dpkg -i /tmp/lsof.deb\n'
                          + 'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(scheme) + '\n'
                          + 'RUN --network=none ' + py38.PYTHON + ' -I -B -m pip --isolated install '
                            '--no-index --no-deps --no-compile /opt/p07-wheels/*.whl\n'
                          + 'RUN --network=none ' + py38.PYTHON + ' -I -B -c ' + shlex.quote(inject) + '\n')
            (tmp / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            (output / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            build = command([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(tmp)], timeout=300)
            (output / 'build.stdout').write_bytes(build.stdout)
            (output / 'build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('offline image build failed')
            iid = (tmp / 'iid').read_text(encoding='ascii').strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('image ID missing')
            report['image_id'] = iid
            created = command([recon.ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never',
                '--network=none', '--read-only', '--user=65534:65534', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--memory=1g', '--cpus=2', '--pids-limit=128',
                '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777', '--workdir=/testbed',
                '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
            if created.returncode:
                raise ValueError('container creation failed')
            cid = cidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'[0-9a-f]{64}', cid):
                raise ValueError('container ID missing')
            inspected = command([recon.ENGINE, 'inspect', cid])
            item = json.loads(inspected.stdout)[0]
            host = item['HostConfig']
            if (item['Image'] != iid or item['State']['Running'] is not False or item['Config']['User'] != '65534:65534'
                    or host['NetworkMode'] != 'none' or not host['ReadonlyRootfs'] or host['Privileged']
                    or host['CapDrop'] != ['ALL'] or host.get('CapAdd') or host.get('Binds') or host.get('VolumesFrom')
                    or item.get('Mounts') or host['Init'] is not True or host['Memory'] != 1024**3
                    or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
                    or 'no-new-privileges' not in host.get('SecurityOpt', [])):
                raise ValueError('container controls differ')
            (output / 'container-inspect.json').write_bytes(inspected.stdout)
            copied = command([recon.ENGINE, 'cp', cid + ':/usr/bin/lsof', str(tmp / 'installed-lsof')])
            if copied.returncode or (tmp / 'installed-lsof').read_bytes() != binary:
                raise ValueError('installed lsof differs from publisher binary')
            verified, installed, extras, wrappers = py38.audit_relocated_wheels(command, cid, tmp, profile, tmp / 'installed')
            if verified['publisher_files_verified'] != 796 or len(wrappers) != 5:
                raise ValueError('publisher or generated wrapper inventory differs')
            (output / 'installed-inventory.json').write_text(json.dumps(installed, sort_keys=True, indent=2) + '\n')
            (output / 'generated-console-scripts.json').write_text(json.dumps(wrappers, sort_keys=True, indent=2) + '\n')
            report.update(installed_publisher_files=796, generated_console_scripts_observed=len(wrappers),
                          pip_metadata_extras=extras, installed_lsof_verified=True)
            if command([recon.ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            tree = json.loads(recon.PUBLIC_TREE.read_bytes())
            if tree.get('base_commit') != recon.BASE or len(tree.get('blobs', [])) != 474:
                raise ValueError('public tree inventory differs')
            verify = command([recon.ENGINE, 'exec', '--user=65534:65534', cid, py38.PYTHON, '-I', '-B', '-c',
                              recon.VERIFY_SOURCE, json.dumps(tree['blobs'], separators=(',', ':'))], timeout=90)
            (output / 'source-verification.stdout').write_bytes(verify.stdout)
            if verify.returncode or json.loads(verify.stdout) != {'checked': 474, 'differences': []}:
                raise ValueError('public source bytes differ')
            report['source_blobs_verified'] = 474
            for name, source, expected in (('scheme', py38.SCHEME_PROBE, py38.FIXED_SCHEME),
                    ('markupsafe', py38.MARKUPSAFE_PROBE, {'module': py38.relocated.SITE + '/markupsafe/_speedups.cpython-38-x86_64-linux-gnu.so', 'escape_ok': True})):
                probe = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, py38.PYTHON, '-I', '-B', '-c', source])
                (output / (name + '.stdout')).write_bytes(probe.stdout)
                (output / (name + '.stderr')).write_bytes(probe.stderr)
                if probe.returncode or probe.stderr or json.loads(probe.stdout) != expected:
                    raise ValueError(name + ' probe differs')
            source_probe = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, py38.PYTHON, '-I', '-B', '-c',
                                    "import os,pytest,_pytest; print(os.path.realpath(pytest.__file__)); print(os.path.realpath(_pytest.__file__))"])
            if source_probe.returncode or source_probe.stdout.splitlines() != [b'/testbed/src/pytest.py', b'/testbed/src/_pytest/__init__.py']:
                raise ValueError('subprocess source identity differs')
            (output / 'subprocess-source.stdout').write_bytes(source_probe.stdout)
            capability = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, py38.PYTHON, '-I', '-B', '-c', lsof.PROBE], timeout=60)
            (output / 'capability.stdout').write_bytes(capability.stdout)
            (output / 'capability.stderr').write_bytes(capability.stderr)
            capability_result = json.loads(capability.stdout)
            if (capability.returncode or capability.stderr or not capability_result.get('fd_check')
                    or not all(capability_result[x]['exit'] == 0 and capability_result[x]['target_visible']
                               and capability_result[x]['regular'] > 0 for x in ('first', 'second'))):
                raise ValueError('lsof capability not established')
            result = _bounded([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, py38.PYTHON,
                               '-I', '-B', '-c', test_source()], home=tmp, timeout=120, limit=1_000_000, check=lambda: None)
            (output / 'stdout').write_bytes(result.stdout.encode())
            (output / 'stderr').write_bytes(result.stderr.encode())
            report.update(exit_code=result.returncode, timed_out=result.timed_out, stdout_truncated=result.stdout_truncated,
                          stderr_truncated=result.stderr_truncated, stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                          stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest())
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('focused diagnostic output incomplete')
            markers = [line.removeprefix('P07_PY38_REPRO=') for line in result.stdout.splitlines() if line.startswith('P07_PY38_REPRO=')]
            if len(markers) != 1:
                raise ValueError('exact focused marker required')
            observation = json.loads(markers[0])
            (output / 'observation.json').write_text(json.dumps(observation, indent=2, sort_keys=True) + '\n')
            if (not observation['finished'] or observation['exit_code'] != result.returncode
                    or len(observation['items']) != 2 or set(observation['items']) != set(NODES)
                    or set(observation['completed']) != set(NODES) or observation['collection_errors']
                    or observation['collection_skips']):
                raise ValueError('focused test inventory incomplete')
            report.update(status='focused_diagnostic_completed', outcomes=observation['outcomes'],
                          nonpassing=observation['nonpassing'])
        except Exception as exc:
            report.update(error_type=type(exc).__name__, error=str(exc)[:500])
        finally:
            if cid is None and cidfile.exists():
                cid = cidfile.read_text(encoding='ascii').strip()
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
    outcome = run(parser.parse_args().output)
    raise SystemExit(0 if outcome['status'] == 'focused_diagnostic_completed' and outcome['exit_code'] == 0
                     and not outcome['nonpassing'] and outcome['outcomes'].get('call:passed') == 2 else 1)
