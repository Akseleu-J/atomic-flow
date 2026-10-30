"""Execute frozen Sphinx 7738 public tests in a restricted diagnostic image.

Read-only diagnostic only: no target repairs, hidden oracle, provider calls or
TaskRunner admission. This invokes pytest directly with the native public
configuration; it does not claim exact equivalence to tox's full environment.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import p07_pytest6116_collection_recon as common

from atomic_flow.check_requests_execution import _bounded

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-7738@sha256:908d80e3f2cb27da3fa10152c4da53d3be5389d39468339aad5563fda8f8e65d'
BASE = 'c087d717f6ed183dd422359bf91210dc59689d63'
TREE = ROOT / 'benchmarks/stage4/p07-sphinx7738-public-tree-v1.json'
TREE_SHA = '4db470eb97a3038dbc0b516b0d56ce676aac23c7459a4968c7c6faa492b30ce3'
ARCHIVE_URL = 'https://codeload.github.com/sphinx-doc/sphinx/tar.gz/' + BASE
ARCHIVE_SHA = 'edf40c2790c0f832faca73f4d1d9701c8406d36fb3b6dfeb6b5db795a4e8a092'
ENGINE = '/usr/bin/docker'
PYTHON = '/opt/miniconda3/envs/testbed/bin/python'
NODES_MANIFEST = ROOT / 'benchmarks/stage4/p07-sphinx7738-collection-nodes-v1.json'
NODES_SHA = 'c0aa3b9370f070f667fbdd84ec4ebf2f2362ea5f08d9175aefb2e0479d8105b7'
BASELINE_IMAGE = 'sha256:f496eccea1c7a9613b69f5d5265a3e24cb628fd26d66463bb983183949eb23b8'
BASELINE_DEPENDENCIES_SHA = '99caea8298df70d28c4637ac3c930fa825b509ce9fa66843b3b42469255e07c1'
SOURCE = r'''
import json,os,sys
sys.dont_write_bytecode=True
import pytest,sphinx
if (os.path.realpath(os.getcwd())!='/testbed' or sys.executable!='/opt/miniconda3/envs/testbed/bin/python'
    or list(sys.version_info[:3])!=[3,9,20] or os.path.realpath(sphinx.__file__)!='/testbed/sphinx/__init__.py'):
    raise SystemExit(125)
class Recorder:
    def __init__(self):self.items=[];self.collection_errors=[];self.collection_skips=[];self.finished=False;self.phases=[]
    def pytest_collection_finish(self,session):
        self.items=[{'nodeid':item.nodeid,'path':os.path.relpath(str(item.fspath),'/testbed').replace(os.sep,'/')} for item in session.items]
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)
        if report.skipped:self.collection_skips.append(report.nodeid)
    def pytest_runtest_logreport(self,report):
        reason=str(report.longrepr) if report.outcome!='passed' else ''
        self.phases.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome,
          'wasxfail':str(report.wasxfail) if hasattr(report,'wasxfail') else None,
          'reason':reason,'reason_truncated':False})
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
r=Recorder()
code=int(pytest.main(['-q','--tb=line','--durations','25'],plugins=[r]))
result={'runner':'sphinx7738_public_execution_diagnostic','python_version':list(sys.version_info[:3]),
        'pytest_version':pytest.__version__,'pytest_module':os.path.realpath(pytest.__file__),
        'sphinx_module':os.path.realpath(sphinx.__file__),'items':r.items,'phases':r.phases,
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


def extract_verified_source(raw, rows, destination):
    """Copy only byte-verified regular public files; never extract archive paths."""
    if len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError('source archive digest differs')
    expected = {row['path']: row for row in rows}
    prefix = 'sphinx-' + BASE + '/'
    found = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            if member.name == prefix[:-1] and member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError('source archive prefix differs')
            path = member.name[len(prefix):].rstrip('/')
            if not path or any(p in ('', '.', '..') for p in path.split('/')) or '\\' in path:
                raise ValueError('unsafe source archive path')
            if member.isdir():
                continue
            if not member.isfile() or path not in expected or path in found:
                raise ValueError('unexpected or duplicate archive file')
            row = expected[path]
            if member.size != row['size'] or member.size > 4_194_304:
                raise ValueError('source archive member size differs')
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError('source archive member unavailable')
            data = stream.read(member.size + 1)
            sha = hashlib.sha1(('blob ' + str(len(data)) + '\0').encode('ascii') + data).hexdigest()
            if len(data) != row['size'] or sha != row['sha']:
                raise ValueError('source archive blob differs')
            found[path] = data
    if set(found) != set(expected):
        raise ValueError('source archive inventory differs')
    destination.mkdir()
    for path, data in found.items():
        target = destination.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if expected[path]['mode'] == '100755' else 0o644)
    return len(found)


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


def verify_image_lineage(base, derived, iid):
    base_layers = base.get('RootFS', {}).get('Layers')
    derived_layers = derived.get('RootFS', {}).get('Layers')
    if (IMAGE not in base.get('RepoDigests', []) or base.get('Architecture') != 'amd64'
            or derived.get('Architecture') != 'amd64' or derived.get('Id') != iid
            or not isinstance(base_layers, list) or not base_layers or not isinstance(derived_layers, list)
            or len(derived_layers) <= len(base_layers) or derived_layers[:len(base_layers)] != base_layers):
        raise ValueError('derived image lineage differs')


def verify_baseline(raw):
    if hashlib.sha256(raw).hexdigest() != NODES_SHA:
        raise ValueError('fixed node manifest digest differs')
    baseline = json.loads(raw)
    rows = baseline.get('items', [])
    if (baseline.get('source_ci') != 41001700454 or baseline.get('schema_version') != 1
            or baseline.get('artifact_sha256') != '0fa24c3f7af3dca472d7cacb453dd8ca9856d45619205084965a90087eb23108'
            or len(rows) != 1502 or len({r['nodeid'] for r in rows}) != 1502):
        raise ValueError('fixed baseline inventory differs')
    return rows


def assess_execution(observation, tree, exit_code, baseline):
    items = observation['items']
    nodes = [r['nodeid'] for r in items]
    if len(nodes) != len(set(nodes)):
        raise ValueError('duplicate collected nodes')
    paths = {r['path'] for r in items}
    nonpublic = sorted(paths - {r['path'] for r in tree['blobs']})
    expected = {r['nodeid']: r['path'] for r in baseline}
    actual = {r['nodeid']: r['path'] for r in items}
    phases = observation.get('phases')
    if not isinstance(phases, list):
        raise ValueError('phase reports unavailable')
    counts = {}
    seen = set()
    setup_outcomes = {}
    for phase in phases:
        if (not isinstance(phase, dict) or phase.get('nodeid') not in actual
                or phase.get('phase') not in ('setup', 'call', 'teardown')
                or phase.get('outcome') not in ('passed', 'failed', 'skipped')
                or type(phase.get('reason_truncated')) is not bool
                or (phase['nodeid'], phase['phase']) in seen):
            raise ValueError('invalid, duplicate or nonpublic phase report')
        seen.add((phase['nodeid'], phase['phase']))
        if phase['phase'] == 'setup':
            setup_outcomes[phase['nodeid']] = phase['outcome']
        key = phase['phase'] + ':' + phase['outcome']
        if phase.get('wasxfail') is not None:
            key += ':xfail' if phase['outcome'] == 'skipped' else ':xpass' if phase['outcome'] == 'passed' else ':wasxfail'
        counts[key] = counts.get(key, 0) + 1
    incomplete = sorted(node for node in nodes if (node, 'setup') not in seen or (node, 'teardown') not in seen)
    not_run = sorted(node for node in nodes if (node, 'call') not in seen)
    inconsistent_calls = sorted(node for node in nodes if node in setup_outcomes
                                and ((setup_outcomes[node] == 'passed') != ((node, 'call') in seen)))
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed_paths = sorted(n for n in set(expected) & set(actual) if expected[n] != actual[n])
    complete = (bool(nodes) and not incomplete and not inconsistent_calls and not missing and not unexpected and not changed_paths and not nonpublic
                and not observation['collection_errors'] and not observation['collection_skips'])
    nonpassing = [p for p in phases if p['outcome'] != 'passed' or p.get('wasxfail') is not None]
    status = ('execution_observed_clean' if complete and exit_code == 0 and not nonpassing else
              'execution_observed_with_nonpassing' if complete else 'execution_incomplete')
    return {'status': status, 'collected': len(nodes), 'collected_files': len(paths), 'phases': len(phases),
            'outcomes': counts, 'nonpassing_phase_count': len(nonpassing), 'no_call_nodes': not_run,
            'incomplete_nodes': incomplete, 'inconsistent_call_nodes': inconsistent_calls, 'missing_baseline_nodes': missing, 'unexpected_nodes': unexpected,
            'changed_paths': changed_paths, 'nonpublic_collected_files': nonpublic,
            'collection_errors': observation['collection_errors'], 'collection_skips': observation['collection_skips']}


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh output on a Linux Docker host required')
    output.mkdir(parents=True)
    report = {'status': 'not_qualified', 'scope': 'full public execution diagnostic only',
              'task_id': 'sphinx-doc__sphinx-7738', 'base_commit': BASE, 'image': IMAGE,
              'provider_calls': 0, 'test_execution': True, 'hidden_oracle': 'not_run',
              'ordinary_acceptance': False, 'tox_environment_equivalence_verified': False,
              'extra_testbed_files_audited': False, 'source_overlay': 'verified public base only'}
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'LANG': 'C', 'LC_ALL': 'C'}

    def command(args, timeout=90):
        return subprocess.run(args, env=env, capture_output=True, check=False, timeout=timeout)

    with tempfile.TemporaryDirectory(prefix='p07-sphinx7738-execution-') as temporary:
        home = Path(temporary)
        cidfile, cid = home / 'container.cid', None
        try:
            tree = verify_tree(TREE.read_bytes())
            baseline = verify_baseline(NODES_MANIFEST.read_bytes())
            report['baseline_nodes_sha256'] = NODES_SHA
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
            base_inspect = command([ENGINE, 'image', 'inspect', IMAGE])
            base = json.loads(base_inspect.stdout)[0] if not base_inspect.returncode else {}
            if IMAGE not in base.get('RepoDigests', []) or base.get('Architecture') != 'amd64':
                raise ValueError('base image digest differs')
            (output / 'base-image-inspect.json').write_bytes(base_inspect.stdout)
            request = urllib.request.Request(ARCHIVE_URL, headers={'User-Agent': 'atomic-flow-p07-sphinx7738/1'})
            with urllib.request.urlopen(request, timeout=90) as response:
                archive_raw = response.read(20_000_001)
            context = home / 'context'
            context.mkdir()
            report['archive_blobs_verified'] = extract_verified_source(archive_raw, tree['blobs'], context / 'public')
            report['source_archive_sha256'] = hashlib.sha256(archive_raw).hexdigest()
            (output / 'public-source.tar.gz').write_bytes(archive_raw)
            dockerfile = 'FROM ' + IMAGE + '\nCOPY public/ /testbed/\n'
            (context / 'Dockerfile').write_text(dockerfile)
            (output / 'Dockerfile').write_text(dockerfile)
            iidfile = home / 'image.iid'
            built = command([ENGINE, 'build', '--pull=false', '--network=none', '--iidfile', str(iidfile), str(context)], timeout=300)
            (output / 'build.stdout').write_bytes(built.stdout)
            (output / 'build.stderr').write_bytes(built.stderr)
            if built.returncode:
                raise ValueError('public-source overlay build failed')
            iid = iidfile.read_text(encoding='ascii').strip()
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', iid):
                raise ValueError('derived image identity unavailable')
            report['derived_image_id'] = iid
            report['image_id_matches_collection'] = iid == BASELINE_IMAGE
            created = command([ENGINE, 'create', '--cidfile', str(cidfile), '--init', '--pull=never', '--network=none',
                '--read-only', '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                '--memory=1g', '--cpus=2', '--pids-limit=128', '--tmpfs=/tmp:rw,noexec,nosuid,size=268435456,mode=1777',
                '--workdir=/testbed', '--env=HOME=/tmp', '--entrypoint=/bin/sleep', iid, 'infinity'])
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
            inspected_image = command([ENGINE, 'image', 'inspect', iid])
            image_item = json.loads(inspected_image.stdout)[0] if not inspected_image.returncode else {}
            if image_item.get('Id') != item['Image'] or image_item.get('Id') != iid:
                raise ValueError('actual immutable image differs')
            verify_image_lineage(base, image_item, iid)
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
            report['dependency_inventory_matches_collection'] = report['dependency_inventory_sha256'] == BASELINE_DEPENDENCIES_SHA
            result = _bounded([ENGINE, 'exec', '--user=65534:65534', '-w', '/testbed', cid, PYTHON, '-I', '-B', '-c', SOURCE],
                              home=home, timeout=1500, limit=24_000_000, check=lambda: None)
            stdout, stderr = result.stdout.encode(), result.stderr.encode()
            (output / 'execution.stdout').write_bytes(stdout)
            (output / 'execution.stderr').write_bytes(stderr)
            report.update(exit_code=result.returncode, timed_out=result.timed_out,
                          stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
                          stdout_sha256=hashlib.sha256(stdout).hexdigest(), stderr_sha256=hashlib.sha256(stderr).hexdigest())
            if result.timed_out or result.stdout_truncated or result.stderr_truncated:
                raise ValueError('execution output incomplete')
            observation = common.parse_collection(result.stdout, result.returncode)
            (output / 'execution.json').write_text(json.dumps(observation, indent=2, sort_keys=True) + '\n')
            report.update(assess_execution(observation, tree, result.returncode, baseline))
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
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'execution_observed_clean' else 1)
