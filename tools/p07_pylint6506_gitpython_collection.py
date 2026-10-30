"""Offline GitPython overlay and unchanged pylint6506 public collection diagnostic.

This is not ordinary admission. Candidate wheels are installed only into an
isolated Docker image, without dependency resolution or target source changes.
Additional source files, historical environment equivalence and generated pip
metadata content remain unverified; original source mode differences persist.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

import p07_pylint6506_collection_recon as recon
import p07_pylint6506_gitpython_prepare as candidate
import p07_pytest6116_derived_collection as inventory

from atomic_flow.check_requests_execution import _bounded

PREFIX = '/opt/p07-pylint6506-gitpython'
ENGINE = '/usr/bin/docker'
LABEL = 'atomic.p07.pylint6506.gitpython.profile'
TMPFS = {'/tmp': 'rw,noexec,nosuid,size=134217728,mode=1777'}


def fixed_collection_source():
    anchor = 'import pytest,pylint'
    if recon.SOURCE.count(anchor) != 1:
        raise ValueError('reviewed collection source differs')
    return recon.SOURCE.replace(anchor, "sys.path.insert(0,'" + PREFIX + "')\n" + anchor)


def verify_lineage(base, derived, iid):
    before = base.get('RootFS', {}).get('Layers')
    after = derived.get('RootFS', {}).get('Layers')
    if (recon.IMAGE not in base.get('RepoDigests', []) or base.get('Architecture') != 'amd64'
            or base.get('Os') != 'linux' or derived.get('Architecture') != 'amd64' or derived.get('Id') != iid
            or not isinstance(before, list) or not before or not isinstance(after, list)
            or len(after) <= len(before) or after[:len(before)] != before
            or derived.get('Config', {}).get('Labels', {}).get(LABEL) != candidate.PROFILE_SHA):
        raise ValueError('derived image identity or lineage differs')


def verify_container(item, cid, iid, *, running):
    host, config = item['HostConfig'], item['Config']
    mounts = item.get('Mounts')
    if (item['Id'] != cid or item['Image'] != iid or host['NetworkMode'] != 'none'
            or not host['ReadonlyRootfs'] or host['Init'] is not True or host['Privileged']
            or host['CapDrop'] != ['ALL'] or host.get('CapAdd')
            or 'no-new-privileges' not in host.get('SecurityOpt', [])
            or host['Memory'] != 1024**3 or host['NanoCpus'] != 2_000_000_000 or host['PidsLimit'] != 128
            or config['User'] != '65534:65534' or host.get('Binds') or host.get('VolumesFrom')
            or host.get('Mounts') or config.get('Volumes') or host.get('Tmpfs') != TMPFS
            or not isinstance(mounts, list) or len(mounts) > 1
            or any(m.get('Type') != 'tmpfs' or m.get('Destination') != '/tmp' or m.get('Source') for m in mounts)
            or item.get('State', {}).get('Running') is not running):
        raise ValueError('inspection or runtime container differs')


def collection_summary(observation, tree, exit_code):
    nodes = [r['nodeid'] for r in observation['items']]
    if len(nodes) != len(set(nodes)):
        raise ValueError('duplicate collected nodes')
    paths = {r['path'] for r in observation['items']}
    nonpublic = sorted(paths - {r['path'] for r in tree['blobs']})
    success = (exit_code == 0 and bool(nodes) and not observation['collection_errors']
               and not observation['collection_skips'] and not nonpublic)
    return {'status': 'derived_collection_observed' if success else 'derived_collection_incomplete',
            'collected': len(nodes), 'collected_files': len(paths), 'nonpublic_collected_files': nonpublic,
            'collection_errors': observation['collection_errors'], 'collection_skips': observation['collection_skips']}


def run(output):
    if output.exists() or os.name != 'posix':
        raise ValueError('fresh output and Linux Docker host required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'derived public collection diagnostic only',
              'base_commit': recon.BASE, 'base_image': recon.IMAGE, 'provider_calls': 0,
              'hidden_oracle': 'not_run', 'ordinary_acceptance': False, 'test_execution': False,
              'tox_environment_equivalence_verified': False, 'additional_source_files_audited': False,
              'pip_generated_metadata_content_verified': False}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}
    def command(args, timeout=120):
        return subprocess.run(args, env=env, capture_output=True, timeout=timeout, check=False)

    with tempfile.TemporaryDirectory(prefix='p07-pylint-gitpython-') as temp:
        temp = Path(temp)
        active = None
        cidfile = temp / 'container.cid'
        cleanup = []

        def remove(cid):
            removed = command([ENGINE, 'rm', '-f', cid])
            absent = command([ENGINE, 'container', 'ls', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}'])
            ok = removed.returncode == 0 and absent.returncode == 0 and not absent.stdout.strip()
            cleanup.append({'container_id': cid, 'confirmed': ok})
            return ok

        try:
            profile = candidate.check_profile(candidate.PROFILE.read_bytes())
            tree_raw = recon.PUBLIC_TREE.read_bytes()
            if recon.sha(tree_raw) != recon.PUBLIC_TREE_SHA256:
                raise ValueError('public tree digest differs')
            tree = json.loads(tree_raw)
            if tree.get('repo') != 'pylint-dev/pylint' or tree.get('base_commit') != recon.BASE or len(tree.get('blobs', [])) != 2563:
                raise ValueError('public tree identity differs')
            revision = command(['git', '-C', str(recon.ROOT), 'rev-parse', 'HEAD'])
            report['tested_commit'] = revision.stdout.decode().strip()
            if revision.returncode or not re.fullmatch(r'[a-f0-9]{40}', report['tested_commit']):
                raise ValueError('tested commit unavailable')
            wheelhouse = temp / 'wheelhouse'
            prepared = candidate.prepare(wheelhouse)
            (output / 'wheel-preparation.json').write_bytes((wheelhouse / 'preparation.json').read_bytes())
            if prepared['profile_sha256'] != candidate.PROFILE_SHA:
                raise ValueError('wheelhouse identity differs')
            expected = inventory.expected_wheel_files(wheelhouse, profile)
            pulled = command([ENGINE, 'pull', recon.IMAGE], timeout=300)
            (output / 'pull.stdout').write_bytes(pulled.stdout)
            (output / 'pull.stderr').write_bytes(pulled.stderr)
            if pulled.returncode:
                raise ValueError('pinned base pull failed')
            base_result = command([ENGINE, 'image', 'inspect', recon.IMAGE])
            base = json.loads(base_result.stdout)[0] if not base_result.returncode else {}
            if (recon.IMAGE not in base.get('RepoDigests', []) or base.get('Architecture') != 'amd64'
                    or base.get('Os') != 'linux'):
                raise ValueError('base image identity differs')
            context = temp / 'context'
            context.mkdir()
            for item in profile['wheels']:
                shutil.copyfile(wheelhouse / item['filename'], context / item['filename'])
            dockerfile = ('FROM ' + recon.IMAGE + '\nCOPY *.whl /opt/p07-wheels/\n'
                          'RUN --network=none ' + recon.PYTHON + ' -I -B -m pip --isolated install '
                          '--no-index --no-deps --no-compile --disable-pip-version-check --target ' + PREFIX + ' '
                          + ' '.join('/opt/p07-wheels/' + x['filename'] for x in profile['wheels'])
                          + ' && chmod -R a+rX ' + PREFIX + '\nLABEL ' + LABEL + '=' + candidate.PROFILE_SHA + '\n')
            (context / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            (output / 'Dockerfile').write_text(dockerfile, encoding='ascii')
            iidfile = temp / 'image.iid'
            built = command([ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(iidfile), str(context)], timeout=300)
            (output / 'build.stdout').write_bytes(built.stdout)
            (output / 'build.stderr').write_bytes(built.stderr)
            if built.returncode:
                raise ValueError('offline image build failed')
            iid = iidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('derived image id unavailable')
            derived_result = command([ENGINE, 'image', 'inspect', iid])
            derived = json.loads(derived_result.stdout)[0] if not derived_result.returncode else {}
            verify_lineage(base, derived, iid)
            (output / 'base-image-inspect.json').write_bytes(base_result.stdout)
            (output / 'image-inspect.json').write_bytes(derived_result.stdout)
            report['derived_image_id'] = iid

            def create_container():
                if cidfile.exists():
                    cidfile.unlink()
                made = command([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                    '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777',
                    '--workdir=/testbed', '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
                if made.returncode:
                    raise ValueError('isolated container creation failed')
                cid = cidfile.read_text(encoding='ascii').strip()
                if not re.fullmatch(r'[a-f0-9]{64}', cid):
                    raise ValueError('invalid container id')
                return cid

            active = create_container()
            inspected = command([ENGINE, 'inspect', active])
            item = json.loads(inspected.stdout)[0] if not inspected.returncode else {}
            verify_container(item, active, iid, running=False)
            (output / 'inventory-container-inspect.json').write_bytes(inspected.stdout)
            copied = command([ENGINE, 'cp', active + ':' + PREFIX, str(temp / 'installed')])
            if copied.returncode:
                raise ValueError('stopped container inventory unavailable')
            actual = inventory.installed_files(temp / 'installed')
            extras = inventory.validate_installed(expected, actual)
            (output / 'installed-inventory.json').write_text(json.dumps(actual, sort_keys=True, indent=2) + '\n')
            report.update(installed_publisher_files_verified=len(expected), pip_generated_metadata_files=extras)
            if not remove(active):
                raise ValueError('inspection container cleanup uncertain')
            active = None
            active = create_container()
            if command([ENGINE, 'start', active]).returncode:
                raise ValueError('runtime start failed')
            inspected = command([ENGINE, 'inspect', active])
            runtime = json.loads(inspected.stdout)[0] if not inspected.returncode else {}
            verify_container(runtime, active, iid, running=True)
            (output / 'runtime-container-inspect.json').write_bytes(inspected.stdout)
            compressed = base64.b64encode(zlib.compress(json.dumps(tree['blobs'], separators=(',', ':')).encode())).decode()
            verification = command([ENGINE, 'exec', '--user=65534:65534', active, recon.PYTHON, '-I', '-B', '-c', recon.VERIFY_SOURCE, compressed])
            (output / 'source-verification.stdout').write_bytes(verification.stdout)
            (output / 'source-verification.stderr').write_bytes(verification.stderr)
            source = json.loads(verification.stdout) if not verification.returncode else {}
            differences = source.get('differences', [])
            if source.get('checked') != 2563 or any(x.get('reason') != 'executable_mode_differs' for x in differences):
                raise ValueError('source contents differ')
            report['source_mode_differences'] = len(differences)
            report['source_executable_modes_match'] = not differences
            deps_code = ("import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'],d.version) "
                         "for d in m.distributions() if d.metadata.get('Name'))))")
            deps = command([ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', active, recon.PYTHON, '-I', '-B', '-c', deps_code])
            (output / 'base-dependencies.stdout').write_bytes(deps.stdout)
            (output / 'base-dependencies.stderr').write_bytes(deps.stderr)
            if deps.returncode or hashlib.sha256(deps.stdout).hexdigest() != candidate.BASE_INVENTORY_SHA:
                raise ValueError('base distribution inventory differs')
            git = command([ENGINE, 'exec', '--user=65534:65534', '-w', '/tmp', active, 'git', '--version'])
            (output / 'git-version.stdout').write_bytes(git.stdout)
            (output / 'git-version.stderr').write_bytes(git.stderr)
            report['git_version_exit'] = git.returncode
            result = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', active, recon.PYTHON, '-I', '-B', '-c', fixed_collection_source()],
                              home=temp, timeout=300, limit=8_000_000, check=lambda: None)
            stdout, stderr = result.stdout.encode(), result.stderr.encode()
            (output / 'collection.stdout').write_bytes(stdout)
            (output / 'collection.stderr').write_bytes(stderr)
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=hashlib.sha256(stdout).hexdigest(), stderr_sha256=hashlib.sha256(stderr).hexdigest())
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('collection output incomplete')
            observed = recon.parse_collection(result.stdout, result.returncode)
            (output / 'collection.json').write_text(json.dumps(observed, indent=2, sort_keys=True) + '\n')
            report.update(collection_summary(observed, tree, result.returncode))
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            malformed_cid = False
            if active is None and cidfile.exists():
                candidate_cid = cidfile.read_text(encoding='ascii').strip()
                if re.fullmatch(r'[a-f0-9]{64}', candidate_cid) and not any(x['container_id'] == candidate_cid for x in cleanup):
                    active = candidate_cid
                elif not re.fullmatch(r'[a-f0-9]{64}', candidate_cid):
                    malformed_cid = True
            if active and re.fullmatch(r'[a-f0-9]{64}', active):
                remove(active)
            report['containers'] = cleanup
            if malformed_cid or any(not row['confirmed'] for row in cleanup):
                report['status'] = 'cleanup_uncertain'
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'derived_collection_observed' else 1)
