"""Audit pinned xdist source and build requirements without executing target code.

This module creates a prospective, bytes-only wheelhouse. It does not extract
the source, build, install, import xdist, or grant any execution admission.
"""
from __future__ import annotations

import configparser
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_prepare_dependencies as audit
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-xdist-build-v1.json'
PROFILE_SHA256 = 'aca7d9c419f6a307d1fc0e77a7143803f251cb85a892650375c219739ad3de73'
SOURCE_SHA256 = '10468377901b80255cf192c4603a94ffe8b1f071f5c912868da5f5cb91170dae'
MAX_ARCHIVE = 4 * 1024 * 1024
PREFIX = 'pytest-xdist-1.18.2/'


def inspect_source(raw: bytes):
    if len(raw) > MAX_ARCHIVE or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('xdist publisher archive digest differs')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or len(members) > 500 or sum(m.size for m in members) > 2 * 1024 * 1024:
            raise ValueError('duplicate or excessive source members')
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.name.startswith(PREFIX) and member.name != PREFIX.rstrip('/')) or path.is_absolute() or '..' in path.parts:
                raise ValueError('source path escapes fixed root')
            if not (member.isfile() or member.isdir()) or member.size > 512 * 1024:
                raise ValueError('links, special files or oversized source members are refused')
            if '\\' in member.name or any(part in ('', '.', '..') for part in member.name.split('/')):
                raise ValueError('noncanonical source path')
        def read(relative):
            member = archive.getmember(PREFIX + relative)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError('source member is not regular')
            return stream.read()
        boxed = read('xdist/boxed.py')
        version = read('xdist/_version.py')
        if b"version = '1.18.2'" not in version:
            raise ValueError('source version differs')
        requires = [line.strip() for line in read('pytest_xdist.egg-info/requires.txt').decode('utf8').splitlines() if line.strip()]
        if requires != ['execnet>=1.1', 'pytest>=3.0.0']:
            raise ValueError('source runtime requirements differ')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(read('pytest_xdist.egg-info/entry_points.txt').decode('utf8'))
        plugins = dict(parser.items('pytest11'))
        if plugins != {'xdist': 'xdist.plugin', 'xdist.boxed': 'xdist.boxed', 'xdist.looponfail': 'xdist.looponfail'}:
            raise ValueError('source plugin entry points differ')
        return {'members': len(members), 'regular_files': sum(m.isfile() for m in members),
                'boxed_sha256': hashlib.sha256(boxed).hexdigest(), 'requirements': requires,
                'pytest11': plugins, 'build_requires_declared_in_setup': ['setuptools_scm'],
                'target_imports': False, 'source_extracted': False}


def prepare(output: Path):
    if output.exists():
        raise ValueError('fresh output directory required')
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA256:
        raise ValueError('fixed profile digest differs')
    profile = json.loads(raw)
    if (profile.get('task_id') != 'pytest-dev__pytest-6116' or profile.get('python_version') != '3.8.20'
            or profile.get('source', {}).get('sha256') != SOURCE_SHA256
            or [r['name'] for r in profile.get('build_wheels', [])] != ['setuptools', 'wheel', 'setuptools-scm']
            or [r['name'] for r in profile.get('runtime_wheels', [])] != ['execnet']):
        raise ValueError('fixed source and requirements profile required')
    output.mkdir(parents=True)
    def get(item):
        if not item['url'].startswith('https://files.pythonhosted.org/') or not item['url'].endswith('/' + item['filename']):
            raise ValueError('publisher URL differs')
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'atomic-flow-p07-xdist-audit/1'})
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read(audit.MAX_WHEEL + 1)
    source = get(profile['source'])
    source_result = inspect_source(source)
    (output / profile['source']['filename']).write_bytes(source)
    rows = []
    for item in profile['build_wheels'] + profile['runtime_wheels']:
        optional.validate_tags(item)
        wheel = get(item)
        row = audit.inspect_wheel(wheel, item)
        (output / item['filename']).write_bytes(wheel)
        rows.append(row)
    edges = optional.closure(rows)
    versions = {row['name']: Version(row['version']) for row in rows}
    versions['pytest'] = Version('5.2.2')
    for requirement in source_result['requirements']:
        req = Requirement(requirement)
        if req.name not in versions or versions[req.name] not in req.specifier:
            raise ValueError('source runtime dependency unresolved')
    result = {'status': 'prospective_sources_verified_no_execution', 'profile_sha256': PROFILE_SHA256,
              'source': source_result, 'wheel_receipts': rows, 'active_wheel_edges': edges,
              'source_dependency_versions': {req: str(versions[req]) for req in ('execnet', 'pytest')},
              'wheel_built': False, 'runtime_tested': False, 'ordinary_acceptance': False, 'provider_calls': 0}
    (output / 'preparation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': prepare(args.output)['status']}))
