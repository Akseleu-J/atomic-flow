"""Capture full tracebacks for six observed public failures in a bounded rebuild.

The original image was not exported; this repeats the exact combined builder
and compares the rebuilt image and installed inventories to the retained CI
artifact. A different image ID is explicitly not an identical-image claim.
No public test file is changed. This is not a full suite or admission.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import p07_pytest6116_combined_public as combined
import p07_pytest6116_xdist_runtime as xdist

PRIOR_ARTIFACT = 12950448399
PRIOR_ARCHIVE_SHA = 'eb637e827ffb41a2ba603b38cd7ff5d37678c2fce7878f1d5111dd2e3ff516f1'
PRIOR_COMMIT = 'f24a59c3076e72a3d12d92f04590bc8bdb21480d'
PRIOR_IMAGE = 'sha256:0a5bc42ef13c306d450e3249f366fe4238eb81750e1bd98767336ac9fd7477f1'
PRIOR_URL = f'https://api.github.com/repos/Akseleu-J/atomic-flow/actions/artifacts/{PRIOR_ARTIFACT}/zip'
NODES = (
    'testing/test_runner.py::TestExecutionForked::test_exit_propagates',
    'testing/test_runner.py::TestExecutionForked::test_suicide',
    'testing/test_terminal.py::TestProgressOutputStyle::test_xdist_normal',
    'testing/test_terminal.py::TestProgressOutputStyle::test_xdist_normal_count',
    'testing/test_terminal.py::TestProgressWithTeardown::test_xdist_normal',
    'testing/acceptance_test.py::test_pdb_can_be_rewritten',
)
_FULL_SOURCE = combined.combined_source


def focused_source():
    """Fix the six exact public nodes and retain untruncated failure reprs."""
    code = _FULL_SOURCE()
    selection = "'-q','--tb=line','-o','cache_dir=/tmp/p07-pytest-cache',\n 'testing'"
    reason = "'reason':str(report.longrepr)[:500]"
    initialization = 'self.nonpassing=[]; self.xpasses=[]'
    phase_hook = "        k=report.when+':'+report.outcome; self.outcomes[k]=self.outcomes.get(k,0)+1"
    marker = "'plugins':r.plugins,'xpasses':r.xpasses"
    if any(code.count(value) != 1 for value in (selection, reason, initialization, phase_hook, marker)):
        raise ValueError('reviewed full diagnostic source differs')
    replacement = "'-vv','--tb=long','--show-capture=all','-o','cache_dir=/tmp/p07-pytest-cache',\n " + ',\n '.join(map(repr, NODES))
    code = code.replace(selection, replacement).replace(reason, "'reason':str(report.longrepr)")
    code = code.replace(initialization, initialization + '; self.phases=[]')
    code = code.replace(phase_hook, phase_hook + "\n        self.phases.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome})")
    return code.replace(marker, marker + ",'phase_reports':r.phases")


def read_prior(raw):
    if len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != PRIOR_ARCHIVE_SHA:
        raise ValueError('prior artifact digest differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [entry.filename for entry in archive.infolist()]
        required = ['tested-commit.txt', 'diagnostic/summary.json', 'diagnostic/image-inspect.json',
                    'diagnostic/installed-inventory.json', 'diagnostic/numpy-inventory.json',
                    'diagnostic/verified-wrappers.json', 'diagnostic/Dockerfile']
        if len(names) != len(set(names)) or len(names) > 100 or any(
                name not in names or archive.getinfo(name).file_size > 10_000_000 for name in required):
            raise ValueError('prior artifact members differ')
        values = {name: archive.read(name) for name in required}
    prior = json.loads(values['diagnostic/summary.json'])
    inspect = json.loads(values['diagnostic/image-inspect.json'])[0]
    if (values['tested-commit.txt'].decode('ascii').strip() != PRIOR_COMMIT
            or prior.get('image_id') != PRIOR_IMAGE or inspect.get('Id') != PRIOR_IMAGE
            or prior.get('status') != 'combined_public_diagnostic_failed'
            or prior.get('cleanup_confirmed') is not True or prior.get('collected') != 2617
            or prior.get('completed') != 2617 or prior.get('exit_code') != 1
            or prior.get('stdout_truncated') is not False or prior.get('timed_out') is not False):
        raise ValueError('prior build receipt differs')
    return values


def download_prior():
    """Reuse the GitHub downloader that strips credentials across redirects."""
    before = xdist.ARTIFACT_URL
    try:
        xdist.ARTIFACT_URL = PRIOR_URL
        return xdist.download_artifact()
    finally:
        xdist.ARTIFACT_URL = before


def verify_observation(observation, original_report):
    """Refuse missing, duplicated or nonpublic nodes and incomplete streams."""
    nodes = observation.get('items', [])
    completed = observation.get('completed', [])
    nonpassing = observation.get('nonpassing', [])
    phase_keys = [(entry['nodeid'], entry['phase']) for entry in nonpassing]
    phases = observation.get('phase_reports', [])
    all_phases = [(entry['nodeid'], entry['phase']) for entry in phases]
    if (not observation.get('finished') or len(nodes) != len(NODES) or set(nodes) != set(NODES)
            or len(set(nodes)) != len(NODES) or len(completed) != len(NODES) or set(completed) != set(NODES)
            or len(phase_keys) != len(set(phase_keys)) or any(node not in set(NODES) for node, _ in phase_keys)
            or len(all_phases) != len(set(all_phases)) or any(node not in set(NODES) or phase not in ('setup', 'call', 'teardown')
                                                               for node, phase in all_phases)
            or any((node, phase) not in set(all_phases) for node in NODES for phase in ('setup', 'teardown'))
            or any(entry.get('outcome') not in ('passed', 'failed', 'skipped') for entry in phases)
            or observation.get('plugins') != {'xdist': True, 'xdist.boxed': True, 'xdist.looponfail': True}
            or observation.get('exit_code') != original_report.get('exit_code')
            or observation.get('collection_errors') or observation.get('collection_skips')
            or original_report.get('timed_out') is not False or original_report.get('stdout_truncated') is not False
            or original_report.get('stderr_truncated') is not False or original_report.get('cleanup_confirmed') is not True):
        raise ValueError('six-node, stream or cleanup evidence incomplete')
    return {'collected': len(nodes), 'completed': len(completed)}


def run(output):
    if os.name != 'posix' or output.exists():
        raise ValueError('fresh Linux output required')
    try:
        prior = read_prior(download_prior())
    except Exception as exc:
        output.mkdir(parents=True)
        report = {'status': 'not_qualified', 'scope': 'six public failure diagnostics only',
                  'error_type': type(exc).__name__, 'error': str(exc)[:500],
                  'ordinary_acceptance': False, 'provider_calls': 0}
        (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        return report
    # This standalone process temporarily substitutes only the diagnostic test
    # selection and logging, not source files, wheel inputs or container bounds.
    original = combined.combined_source
    try:
        combined.combined_source = focused_source
        original_report = combined.run(output)
    finally:
        combined.combined_source = original
    (output / 'combined-rebuild-summary.json').write_text(json.dumps(original_report, indent=2, sort_keys=True) + '\n')
    report = {'status': 'not_qualified', 'scope': 'six public failure diagnostics only',
              'prior_artifact': PRIOR_ARTIFACT, 'prior_archive_sha256': PRIOR_ARCHIVE_SHA,
              'prior_image_id': PRIOR_IMAGE, 'logging': 'pytest -vv --tb=long --show-capture=all; full failure repr',
              'ordinary_acceptance': False, 'provider_calls': 0, 'hidden_oracle': 'not_run'}
    try:
        required = ['image-inspect.json', 'installed-inventory.json', 'numpy-inventory.json',
                    'verified-wrappers.json', 'Dockerfile', 'observation.json', 'stdout', 'stderr']
        if any(not (output / name).is_file() for name in required):
            raise ValueError('rebuild or diagnostic evidence incomplete')
        before = json.loads(prior['diagnostic/image-inspect.json'])[0]
        after = json.loads((output / 'image-inspect.json').read_bytes())[0]
        observation = json.loads((output / 'observation.json').read_bytes())
        report.update(image_id=after.get('Id'), image_id_matches_prior=after.get('Id') == PRIOR_IMAGE,
                      rootfs_layers_match_prior=before.get('RootFS', {}).get('Layers') == after.get('RootFS', {}).get('Layers'),
                      dockerfile_matches_prior=(output / 'Dockerfile').read_bytes() == prior['diagnostic/Dockerfile'],
                      wheel_inventory_matches_prior=json.loads((output / 'installed-inventory.json').read_bytes())
                      == json.loads(prior['diagnostic/installed-inventory.json']),
                      numpy_inventory_matches_prior=json.loads((output / 'numpy-inventory.json').read_bytes())
                      == json.loads(prior['diagnostic/numpy-inventory.json']),
                      wrapper_receipt_matches_prior=json.loads((output / 'verified-wrappers.json').read_bytes())
                      == json.loads(prior['diagnostic/verified-wrappers.json']),
                      rebuilt_cleanup_confirmed=original_report.get('cleanup_confirmed'),
                      stdout_sha256=hashlib.sha256((output / 'stdout').read_bytes()).hexdigest(),
                      stderr_sha256=hashlib.sha256((output / 'stderr').read_bytes()).hexdigest())
        counts = verify_observation(observation, original_report)
        if not all(report[name] for name in ('dockerfile_matches_prior', 'wheel_inventory_matches_prior',
                                             'numpy_inventory_matches_prior', 'wrapper_receipt_matches_prior')):
            raise ValueError('rebuild input or installed inventory differs from prior')
        report.update(**counts, outcomes=observation['outcomes'],
                      nonpassing=observation['nonpassing'], xpasses=observation['xpasses'],
                      status='six_node_diagnostic_complete_exact_image' if report['image_id_matches_prior']
                      else 'six_node_diagnostic_complete_rebuilt_image_only')
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    (output / 'summary.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = run(parser.parse_args().output)
    raise SystemExit(0 if result['status'] == 'six_node_diagnostic_complete_exact_image' else 1)
