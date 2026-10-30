"""Audit a prospective GitPython closure for pylint6506 without executing it.

Pins are prospective choices, not a recovered historical lock. This creates a
wheelhouse only; it does not install, import, run Git, collect tests or admit a
runtime. Git availability and any Git operations remain unverified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_prepare_dependencies as audit
from packaging.requirements import Requirement
from packaging.tags import Tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pylint6506-gitpython-v1.json'
PROFILE_SHA = '734a86c5910668a06283cf8d2f8a10106737bccec606c4104a8b34e5b1ee583b'
BASE_INVENTORY_SHA = 'a71daad5a9da1d666caa9c99319fac7263f2ca0410cab15c5021f34ab8f68bb1'
EXPECTED = [('gitpython', '3.1.27'), ('gitdb', '4.0.9'), ('smmap', '5.0.0')]


def check_profile(raw):
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA:
        raise ValueError('candidate profile digest differs')
    profile = json.loads(raw)
    if (profile.get('task_id') != 'pylint-dev__pylint-6506'
            or profile.get('base_commit') != '0a4204fd7555cfedd43f43017c94d24ef48244a5'
            or profile.get('python_version') != '3.9.20' or profile.get('platform') != 'linux_x86_64'
            or profile.get('observed_base_inventory_sha256') != BASE_INVENTORY_SHA
            or [(canonicalize_name(x['name']), x['version']) for x in profile.get('wheels', [])] != EXPECTED):
        raise ValueError('candidate profile identity differs')
    for item in profile['wheels']:
        name, version, _, tags = parse_wheel_filename(item['filename'])
        if (canonicalize_name(name) != canonicalize_name(item['name']) or version != Version(item['version'])
                or Tag('py3', 'none', 'any') not in tags
                or not item['url'].startswith('https://files.pythonhosted.org/')
                or not item['url'].endswith('/' + item['filename'])):
            raise ValueError('wheel filename, tag or publisher URL differs')
    return profile


def closure(rows):
    versions = {row['name']: Version(row['version']) for row in rows}
    if len(versions) != len(rows) or list(versions.items()) != [(n, Version(v)) for n, v in EXPECTED]:
        raise ValueError('candidate wheel inventory differs')
    edges = []
    for row in rows:
        for text in row['requires_dist']:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate(audit.ENVIRONMENT):
                continue
            name = canonicalize_name(requirement.name)
            if requirement.url or requirement.extras or name not in versions or versions[name] not in requirement.specifier:
                raise ValueError('active dependency unavailable or incompatible: ' + name)
            edges.append({'from': row['name'], 'requirement': str(requirement), 'resolved': str(versions[name])})
    if {(x['from'], canonicalize_name(Requirement(x['requirement']).name)) for x in edges} != {
            ('gitpython', 'gitdb'), ('gitdb', 'smmap')} or len(edges) != 2:
        raise ValueError('active candidate dependency closure differs')
    return edges


def prepare(output):
    if output.exists():
        raise ValueError('fresh output directory required')
    profile = check_profile(PROFILE.read_bytes())
    output.mkdir(parents=True)
    rows = []
    for item in profile['wheels']:
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'atomic-flow-p07-pylint6506/1'})
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(audit.MAX_WHEEL + 1)
        row = audit.inspect_wheel(raw, item)
        target = output / item['filename']
        target.write_bytes(raw)
        paths, scripts, plugins = optional.inspect_data_paths(target)
        if paths or scripts or plugins:
            raise ValueError('unexpected relocation, console scripts or pytest plugin')
        row.update(wheel_data_paths=paths, generates_console_scripts=scripts, pytest11=plugins)
        rows.append(row)
    receipt = {'status': 'prospective_wheelhouse_verified_no_execution', 'profile_sha256': PROFILE_SHA,
               'observed_base_inventory_sha256': BASE_INVENTORY_SHA, 'python_version': '3.9.20',
               'wheels': rows, 'active_edges': closure(rows), 'target_imports': False, 'image_built': False,
               'ordinary_acceptance': False, 'provider_calls': 0, 'hidden_oracle': 'not_run',
               'limitations': ['Selected releases are candidates, not a historical lock.',
                               'No package was installed or imported; active base conflicts and installed-byte provenance need checking.',
                               'Git executable availability and GitPython operations have not been qualified.',
                               'Collection, full public execution, source modes and TaskRunner remain open.']}
    (output / 'preparation.json').write_bytes((json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode())
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps({'status': prepare(parser.parse_args().output)['status']}))
