"""Inspect a fixed Twisted trial wheel candidate, without target execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_prepare_dependencies as audit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-trial-v1.json'
PROFILE_SHA256 = '5d74e445d3ee64c9b62c721e6fbab6d5d772ed61f1c62d5f65cd67ddd679f4db'
EXPECTED = {('twisted', '21.2.0'), ('zope-interface', '5.5.2'), ('constantly', '15.1.0'),
            ('incremental', '21.3.0'), ('automat', '20.2.0'), ('hyperlink', '21.0.0'), ('setuptools', '57.5.0')}
ENV = {**optional.ENVIRONMENT, 'platform_system': 'Linux'}


def closure(rows):
    """Resolve active Python 3.8 Linux edges; never use the Windows host marker."""
    versions = {}
    for row in rows:
        name = canonicalize_name(row['name'])
        if name in versions or (row['requires_python'] and Version('3.8.20') not in SpecifierSet(row['requires_python'])):
            raise ValueError('duplicate or Python-incompatible distribution: ' + name)
        versions[name] = Version(row['version'])
    edges = []
    for row in rows:
        for text in row['requires_dist']:
            requirement = Requirement(text)
            if requirement.marker:
                # platform_release and platform_version vary by host and are not fixed by this candidate.
                marker = str(requirement.marker)
                if 'platform_release' in marker or 'platform_version' in marker:
                    raise ValueError('unbound target marker')
                if not requirement.marker.evaluate(ENV):
                    continue
            name = canonicalize_name(requirement.name)
            if requirement.url or requirement.extras or name not in versions or versions[name] not in requirement.specifier:
                raise ValueError('unresolved active dependency: ' + name)
            edges.append({'from': row['name'], 'requirement': str(requirement), 'resolved': str(versions[name])})
    return edges


def prepare(output: Path):
    if output.exists():
        raise ValueError('fresh output directory required')
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA256:
        raise ValueError('trial profile bytes differ')
    profile = json.loads(raw)
    if (profile.get('task_id') != 'pytest-dev__pytest-6116' or profile.get('python_version') != '3.8.20'
            or profile.get('base_optional_profile_sha256') != optional.PEXPECT_PROFILE_SHA256
            or {(canonicalize_name(x['name']), x['version']) for x in profile['wheels']} != EXPECTED
            or len(profile['wheels']) != len(EXPECTED)):
        raise ValueError('fixed trial wheel set differs')
    output.mkdir(parents=True)
    # Reinspect the complete previously fixed 25-wheel candidate, not assumed metadata.
    base_dir = output / 'base'
    base = optional.prepare(base_dir, with_pexpect=True)
    if base['profile_sha256'] != optional.PEXPECT_PROFILE_SHA256:
        raise ValueError('base optional profile mismatch')
    rows = list(base['wheels'])
    added = []
    for item in profile['wheels']:
        optional.validate_tags(item)
        if not item['url'].startswith('https://files.pythonhosted.org/') or not item['url'].endswith('/' + item['filename']):
            raise ValueError('publisher wheel URL differs')
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'atomic-flow-p07-trial-wheel-audit/1'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(audit.MAX_WHEEL + 1)
        entry = audit.inspect_wheel(data, item)
        dest = output / item['filename']
        with dest.open('xb') as stream:
            stream.write(data)
        paths, scripts, plugins = optional.inspect_data_paths(dest)
        entry.update(wheel_data_paths=paths, generates_console_scripts=scripts, pytest11=plugins)
        rows.append(entry)
        added.append(entry)
    edges = closure(rows)
    result = {'status': 'prospective_trial_wheels_verified_no_execution', 'profile_sha256': PROFILE_SHA256,
              'base_optional_profile_sha256': base['profile_sha256'], 'wheels_added': added,
              'total_distributions': len(rows), 'active_edges': edges, 'target_imports': False,
              'ordinary_acceptance': False, 'provider_calls': 0,
              'limitations': ['Setuptools 57.5.0 is a prospective replacement of the base installation.',
                              'Native zope.interface ABI, installed bytes, scripts and actual trial behavior unverified.',
                              'No Docker image, test execution, plugin acceptance or historical exactness claim.']}
    (output / 'preparation.json').write_bytes((json.dumps(result, indent=2, sort_keys=True) + '\n').encode())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps({'status': prepare(parser.parse_args().output)['status']}))
