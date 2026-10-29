"""Prepare a hash-bound prospective Python 3.8 optional dependency wheelhouse.

Target packages are downloaded and inspected as bytes, never imported or
executed. The receipt does not admit the image, tests, skips, or historical
environment. Both the base and optional profile are validated as one closure.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

import p07_pytest6116_prepare_dependencies as audit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-optional-v1.json'
BASE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-full-v1.json'
PEXPECT_PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-optional-pexpect-v1.json'
PEXPECT_BASE = ROOT / 'benchmarks/stage4/p07-pytest6116-py38-pexpect-v1.json'
PROFILE_SHA256 = '783422b46e728303af9c1775be09967aa83b16b4351cbbc19be29ee9b6187565'
BASE_SHA256 = '393eabaf9f173cf6ffdef15e6984d6ffed2b7f0d0c30457909f6106a51fe3ea0'
PEXPECT_PROFILE_SHA256 = '82a142fdd6e1ac6c5aee6b59cef0e415fa171f66be3b6f6edc8afaf935f2072d'
PEXPECT_BASE_SHA256 = '383cd9e40dcb1a33fe2146f353fd9d8f396cb66d3ae5c51d81e05abb4fb4a460'
ENVIRONMENT = {**audit.ENVIRONMENT, 'python_version': '3.8', 'python_full_version': '3.8.20',
               'implementation_version': '3.8.20'}
TARGET_TAGS = (set(cpython_tags(python_version=(3, 8), abis=['cp38'],
                               platforms=['manylinux2010_x86_64', 'linux_x86_64']))
               | set(compatible_tags(python_version=(3, 8), interpreter='cp38',
                                     platforms=['manylinux2010_x86_64', 'linux_x86_64'])))


def validate_tags(item):
    name, version, _, tags = parse_wheel_filename(item['filename'])
    if (canonicalize_name(name) != canonicalize_name(item['name']) or version != Version(item['version'])
            or not TARGET_TAGS.intersection(tags)):
        raise ValueError('wheel filename or target compatibility differs: ' + item['name'])


def closure(rows):
    versions = {}
    for row in rows:
        name = canonicalize_name(row['name'])
        if name in versions:
            raise ValueError('duplicate distribution in combined profile: ' + name)
        versions[name] = Version(row['version'])
        if row['requires_python'] and Version('3.8.20') not in SpecifierSet(row['requires_python']):
            raise ValueError('distribution excludes target Python: ' + name)
    edges = []
    for row in rows:
        for raw in row['requires_dist']:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate(ENVIRONMENT):
                continue
            name = canonicalize_name(req.name)
            # Extras would activate additional dependency markers on the target
            # distribution; this candidate has none, so do not silently omit them.
            if req.url or req.extras or name not in versions or versions[name] not in req.specifier:
                raise ValueError('unresolved active dependency: ' + name)
            edges.append({'from': row['name'], 'requirement': str(req), 'resolved': str(versions[name])})
    return edges


def inspect_data_paths(file, *, allow_pytest11=False):
    """List relocation paths and entry points; installation is not verified here."""
    with zipfile.ZipFile(file) as archive:
        names = archive.namelist()
        paths = sorted(name for name in names if '.data/' in name and not name.endswith('/'))
        entries = [name for name in names if name.endswith('.dist-info/entry_points.txt')]
        if len(entries) > 1:
            raise ValueError('ambiguous entry point metadata')
        text = archive.read(entries[0]).decode('utf8') if entries else ''
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        pytest11 = dict(parser.items('pytest11')) if parser.has_section('pytest11') else {}
        if pytest11 and not allow_pytest11:
            raise ValueError('candidate unexpectedly introduces pytest11 plugin')
        return paths, parser.has_section('console_scripts'), pytest11


def prepare(output: Path, *, with_pexpect: bool = False):
    if output.exists():
        raise ValueError('fresh output directory required')
    profile_path, base_path = (PEXPECT_PROFILE, PEXPECT_BASE) if with_pexpect else (PROFILE, BASE)
    profile_raw, base_raw = profile_path.read_bytes(), base_path.read_bytes()
    expected_profile = PEXPECT_PROFILE_SHA256 if with_pexpect else PROFILE_SHA256
    expected_base = PEXPECT_BASE_SHA256 if with_pexpect else BASE_SHA256
    if hashlib.sha256(profile_raw).hexdigest() != expected_profile or hashlib.sha256(base_raw).hexdigest() != expected_base:
        raise ValueError('fixed candidate or byte-bound base digest differs')
    profile, base = json.loads(profile_raw), json.loads(base_raw)
    if (profile.get('task_id') != 'pytest-dev__pytest-6116' or profile.get('python_version') != '3.8.20'
            or profile.get('base_profile_sha256') != hashlib.sha256(base_raw).hexdigest()
            or len(profile.get('wheels', [])) != 12 or len(base.get('wheels', [])) != (13 if with_pexpect else 11)
            or profile.get('direct_requested') != ['nose', 'mock', 'decorator', 'jinja2', 'requests', 'argcomplete']):
        raise ValueError('fixed candidate and byte-bound base required')
    items = base['wheels'] + profile['wheels']
    if len({canonicalize_name(item['name']) for item in items}) != len(items):
        raise ValueError('duplicate distribution in combined profile')
    if len({item['filename'] for item in items}) != len(items):
        raise ValueError('duplicate wheel filename')
    output.mkdir(parents=True)
    rows = []
    for index, item in enumerate(items):
        validate_tags(item)
        if not item['url'].startswith('https://files.pythonhosted.org/') or not item['url'].endswith('/' + item['filename']):
            raise ValueError('publisher wheel URL differs')
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'atomic-flow-p07-optional-wheel-audit/1'})
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(audit.MAX_WHEEL + 1)
        row = audit.inspect_wheel(raw, item)
        file = output / item['filename']
        with file.open('xb') as stream:
            stream.write(raw)
        data_paths, console_scripts, pytest11 = inspect_data_paths(file, allow_pytest11=index < len(base['wheels']))
        row.update(wheel_data_paths=data_paths, generates_console_scripts=console_scripts, pytest11=pytest11)
        rows.append(row)
    edges = closure(rows)
    receipt = {'status': 'prospective_combined_wheelhouse_verified_no_execution',
               'profile_sha256': hashlib.sha256(profile_raw).hexdigest(),
               'base_profile_sha256': hashlib.sha256(base_raw).hexdigest(),
               'python_version': '3.8.20', 'platform': 'linux_x86_64', 'wheels': rows,
               'active_edges': edges, 'target_imports': False, 'image_built': False,
               'ordinary_acceptance': False, 'provider_calls': 0,
               'limits': ['Native MarkupSafe extension has not been imported or dynamically linked.',
                          'Wheel data paths and generated console scripts require relocation-aware independent installed-byte checks.',
                          'No plugin, shell integration, network, target tests, historical exactness or isolation claims.']}
    (output / 'preparation.json').write_bytes((json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-pexpect', action='store_true')
    args = parser.parse_args()
    print(json.dumps({'status': prepare(args.output, with_pexpect=args.with_pexpect)['status']}))
