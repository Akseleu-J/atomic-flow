"""Extract and verify five pip wrappers in a stopped bounded container.

This diagnostic runs no public tests, imports no target packages on the host,
and grants no ordinary admission. It does not verify installed pip source.
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
from pathlib import Path

import p07_pytest6116_collection_recon as recon
import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_py38_repro as runtime
import p07_pytest6116_wheel_install_inventory as inventory
import p07_pytest6116_wrapper_verify as wrappers


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_copied_wrappers(destination, merged):
    """Compare bytes only after the caller verifies safe stopped extraction."""
    observed = {}
    for name in wrappers.ENTRY_POINTS:
        file = destination / name.lstrip('/')
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 65536:
            raise ValueError('wrapper is missing, linked or oversized')
        observed[name] = file.read_bytes()
    result = wrappers.verify_wrapper_bytes(merged['generated_console_scripts'], observed)
    return result, observed


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh Linux output required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'five stopped-container wrapper bytes only',
              'ordinary_acceptance': False, 'full_test_profile_qualified': False,
              'pip_installed_source_verified': False, 'provider_calls': 0}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)
    with tempfile.TemporaryDirectory(prefix='p07-wrapper-extract-') as temp:
        tmp = Path(temp)
        cidfile, cid = tmp / 'cid', None
        try:
            wheelhouse = tmp / 'wheelhouse'
            prepared = optional.prepare(wheelhouse, with_pexpect=True)
            (output / 'wheel-preparation.json').write_bytes((wheelhouse / 'preparation.json').read_bytes())
            profile = json.loads(optional.PEXPECT_BASE.read_bytes())
            profile['wheels'].extend(json.loads(optional.PEXPECT_PROFILE.read_bytes())['wheels'])
            if len(profile['wheels']) != 25 or profile['source_image'] != recon.IMAGE:
                raise ValueError('fixed wheel closure or source image differs')
            report.update(base_profile_sha256=prepared['base_profile_sha256'], optional_profile_sha256=prepared['profile_sha256'])
            plans = [inventory.publisher_wheel_plan((wheelhouse / item['filename']).read_bytes(), item)
                     for item in profile['wheels']]
            merged = inventory.merge_plans(plans)
            wrappers.expected_plan(merged['generated_console_scripts'])
            for item in profile['wheels']:
                shutil.copyfile(wheelhouse / item['filename'], tmp / item['filename'])
            for name, image in (('python', profile['python_image']), ('source', profile['source_image'])):
                pull = command([recon.ENGINE, 'pull', image], timeout=240)
                (output / (name + '-pull.stdout')).write_bytes(pull.stdout)
                (output / (name + '-pull.stderr')).write_bytes(pull.stderr)
                if pull.returncode:
                    raise ValueError('pinned ' + name + ' image pull failed')
                inspection = command([recon.ENGINE, 'image', 'inspect', image])
                data = json.loads(inspection.stdout)[0]
                if inspection.returncode or image not in data.get('RepoDigests', []) or data.get('Architecture') != 'amd64':
                    raise ValueError('pinned image identity differs')
            scheme = ("import pip,sys,sysconfig; p=sysconfig.get_paths(); "
                      "x={'python':sys.executable,'version':list(sys.version_info[:3]),'pip':pip.__version__,"
                      "'data':p['data'],'scripts':p['scripts'],'purelib':p['purelib'],'platlib':p['platlib']}; "
                      'assert x==' + repr(runtime.FIXED_SCHEME))
            inject = ("from pathlib import Path; p=Path('/usr/local/lib/python3.8/site-packages/p07-public-source.pth'); "
                      "assert not p.exists(); p.write_text(\"import sys; sys.path.insert(0, '/testbed/src')\\n\")")
            dockerfile = ('FROM ' + profile['source_image'] + ' AS target\nFROM ' + profile['python_image']
                          + '\nCOPY --from=target /testbed /testbed\nCOPY *.whl /opt/p07-wheels/\n'
                          + 'RUN --network=none ' + runtime.PYTHON + ' -I -B -c ' + shlex.quote(scheme) + '\n'
                          + 'RUN --network=none ' + runtime.PYTHON + ' -I -B -m pip --isolated install '
                            '--no-index --no-deps --no-compile /opt/p07-wheels/*.whl\n'
                          + 'RUN --network=none ' + runtime.PYTHON + ' -I -B -c ' + shlex.quote(inject) + '\n')
            (tmp / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            (output / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            build = command([recon.ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(tmp / 'iid'), str(tmp)], timeout=300)
            (output / 'build.stdout').write_bytes(build.stdout)
            (output / 'build.stderr').write_bytes(build.stderr)
            if build.returncode:
                raise ValueError('offline candidate image build failed')
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
            verified, installed, extras, _ = runtime.audit_relocated_wheels(command, cid, wheelhouse, profile, tmp / 'installed')
            if verified['publisher_files_verified'] != 796:
                raise ValueError('publisher wheel inventory differs')
            (output / 'installed-inventory.json').write_text(json.dumps(installed, indent=2, sort_keys=True) + '\n')
            result, raw_files = check_copied_wrappers(tmp / 'installed', merged)
            raw_dir = output / 'wrapper-bytes'
            raw_dir.mkdir()
            for name, raw in raw_files.items():
                (raw_dir / Path(name).name).write_bytes(raw)
            (output / 'wrapper-assessment.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
            report.update(wrappers_verified=result['wrappers_verified'], wrapper_sha256=result['sha256'],
                          installed_publisher_files=verified['publisher_files_verified'], pip_metadata_extras=extras)
            if command([recon.ENGINE, 'start', cid]).returncode:
                raise ValueError('container start failed')
            tree = json.loads(recon.PUBLIC_TREE.read_bytes())
            if tree.get('base_commit') != recon.BASE or len(tree.get('blobs', [])) != 474:
                raise ValueError('public source tree differs')
            source = command([recon.ENGINE, 'exec', '--user=65534:65534', cid, runtime.PYTHON, '-I', '-B', '-c',
                              recon.VERIFY_SOURCE, json.dumps(tree['blobs'], separators=(',', ':'))], timeout=90)
            (output / 'source-verification.stdout').write_bytes(source.stdout)
            if source.returncode or json.loads(source.stdout) != {'checked': 474, 'differences': []}:
                raise ValueError('public source bytes differ')
            report['source_blobs_verified'] = 474
            probe = command([recon.ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', cid, runtime.PYTHON, '-I', '-B', '-c', runtime.SCHEME_PROBE])
            (output / 'scheme.stdout').write_bytes(probe.stdout)
            (output / 'scheme.stderr').write_bytes(probe.stderr)
            if probe.returncode or probe.stderr or json.loads(probe.stdout) != runtime.FIXED_SCHEME:
                raise ValueError('fixed pip or scheme differs')
            report['status'] = 'wrapper_bytes_observed'
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
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'wrapper_bytes_observed' else 1)
