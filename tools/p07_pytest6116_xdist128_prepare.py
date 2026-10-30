"""Audit a prospective xdist 1.28/forked wheel closure without executing it.

This is a byte and metadata check, not a compatibility adapter, runtime test,
ordinary admission or a historical environment claim. In particular, directly
re-exporting forked_run_report does not establish pytest.exit propagation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import p07_pytest6116_optional_prepare as optional
import p07_pytest6116_prepare_dependencies as audit
import p07_pytest6116_xdist_prepare as old

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'benchmarks/stage4/p07-pytest6116-xdist128-candidate-v1.json'
PROFILE_SHA256 = '889266e2701f9512235e44dc45858a62a2e0ad935e186a1f174316a86821724f'
PLUGIN_MAPS = {
    'pytest-xdist': {'xdist': 'xdist.plugin', 'xdist.looponfail': 'xdist.looponfail'},
    'pytest-forked': {'pytest_forked': 'pytest_forked'},
}


def inspect_new_wheel(raw, item, output):
    """Enforce publisher bytes, RECORD, target tags and known plugin surface."""
    optional.validate_tags(item)
    result = audit.inspect_wheel(raw, item)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if item['name'] == 'pytest-xdist' and 'xdist/boxed.py' in names:
            raise ValueError('unexpected historical boxed module')
        if item['name'] == 'pytest-forked' and 'pytest_forked/__init__.py' not in names:
            raise ValueError('forked implementation missing')
    target = output / item['filename']
    target.write_bytes(raw)
    paths, scripts, plugins = optional.inspect_data_paths(target, allow_pytest11=True)
    if paths or scripts or plugins != PLUGIN_MAPS[item['name']]:
        raise ValueError('candidate relocation or plugin surface differs')
    result.update(wheel_data_paths=paths, generates_console_scripts=scripts, pytest11=plugins)
    return result


def download(item):
    if not item['url'].startswith('https://files.pythonhosted.org/') or not item['url'].endswith('/' + item['filename']):
        raise ValueError('publisher wheel URL differs')
    req = urllib.request.Request(item['url'], headers={'User-Agent': 'atomic-flow-p07-xdist128-audit/1'})
    with urllib.request.urlopen(req, timeout=45) as response:
        return response.read(audit.MAX_WHEEL + 1)


def prepare(output):
    if output.exists():
        raise ValueError('fresh output directory required')
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA256:
        raise ValueError('fixed candidate profile digest differs')
    profile = json.loads(raw)
    if (profile.get('task_id') != 'pytest-dev__pytest-6116' or profile.get('python_version') != '3.8.20'
            or profile.get('optional_pexpect_profile_sha256') != optional.PEXPECT_PROFILE_SHA256
            or profile.get('execnet_profile_sha256') != old.PROFILE_SHA256
            or [(x['name'], x['version']) for x in profile.get('wheels', [])]
            != [('pytest-xdist', '1.28.0'), ('pytest-forked', '1.0.2')]):
        raise ValueError('fixed candidate profile differs')
    old_raw = old.PROFILE.read_bytes()
    if hashlib.sha256(old_raw).hexdigest() != old.PROFILE_SHA256:
        raise ValueError('fixed execnet profile differs')
    execnet = json.loads(old_raw)['runtime_wheels']
    if len(execnet) != 1 or (execnet[0]['name'], execnet[0]['version']) != ('execnet', '1.9.0'):
        raise ValueError('execnet candidate differs')
    output.mkdir(parents=True)
    base = optional.prepare(output / 'base', with_pexpect=True)
    rows = list(base['wheels'])
    row = execnet[0]
    optional.validate_tags(row)
    data = download(row)
    info = audit.inspect_wheel(data, row)
    (output / row['filename']).write_bytes(data)
    rows.append(info)
    rows.extend(inspect_new_wheel(download(item), item, output) for item in profile['wheels'])
    edges = optional.closure(rows)
    result = {'status': 'prospective_wheels_verified_no_execution', 'profile_sha256': PROFILE_SHA256,
              'wheels': rows, 'active_edges': edges, 'target_imports': False,
              'adapter_implemented': False, 'runtime_tested': False, 'ordinary_acceptance': False,
              'provider_calls': 0, 'hidden_oracle': 'not_run',
              'limitations': ['xdist.boxed is absent; a simple forked re-export does not establish pytest.exit propagation.',
                              'Installed wheel bytes, adapter, plugin behavior and public test outcomes remain unverified.']}
    (output / 'preparation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps({'status': prepare(parser.parse_args().output)['status']}))
