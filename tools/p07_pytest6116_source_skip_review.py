"""Extract six source-declared skip observations; never admit a skip."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

OBSERVATION_SHA256 = 'bfe13b99f74a19852dc29c733392955fbff24131091116df285f4c1afb2c7097'
FILES = {
    'testing/test_capture.py': '85b0b05aeeb1f0dc33f0ab2b19415d31062fb444',
    'testing/test_tmpdir.py': '0ebed22ac45c7159647b6e12e7f4aef2a5f073d8',
    'testing/test_conftest.py': '0374db0b351e2c36b335c6577ae16439267f6b42',
    'testing/test_warnings.py': 'c4af14dac0e5f1ebc471a435dc9d51823e40bcba',
}
EXPECTED = {
    'testing/test_capture.py::test_py36_windowsconsoleio_workaround_non_standard_streams':
        ('testing/test_capture.py', 'not sys.platform.startswith("win") and sys.version_info[:2] >= (3, 6)', 'only py3.6+ on windows'),
    'testing/test_tmpdir.py::test_get_user': ('testing/test_tmpdir.py', 'not sys.platform.startswith("win")', 'win only'),
    'testing/test_conftest.py::test_conftest_badcase': ('testing/test_conftest.py', 'os.path.normcase("x") != os.path.normcase("X")', 'only relevant for case insensitive file systems'),
    **{f'testing/test_warnings.py::test_deprecation_warning_as_error[{param}]':
       ('testing/test_warnings.py', 'pytest.mark.skip', 'This test should be enabled again before pytest 6.0 is released')
       for param in ('None', 'ini', 'cmdline')},
}


def review(observation_bytes, tree_bytes, *, expected_observation=OBSERVATION_SHA256):
    if hashlib.sha256(observation_bytes).hexdigest() != expected_observation:
        raise ValueError('observation digest differs')
    tree = json.loads(tree_bytes)
    if tree.get('base_commit') != 'e670ff76cbad80108bde9bab616b66771b8653cf':
        raise ValueError('public base differs')
    blobs = {item['path']: item['sha'] for item in tree['blobs']}
    if any(blobs.get(file) != sha for file, sha in FILES.items()):
        raise ValueError('public source inventory differs')
    observation = json.loads(observation_bytes)
    if not observation.get('finished') or observation.get('exit_code') != 0:
        raise ValueError('diagnostic did not finish successfully')
    items = observation.get('items', [])
    completed = observation.get('completed', [])
    if len(items) != 2617 or len(set(items)) != 2617 or set(items) != set(completed):
        raise ValueError('diagnostic inventory differs')
    reports = observation.get('nonpassing', [])
    rows = []
    for node, (file, predicate, reason) in EXPECTED.items():
        matches = [entry for entry in reports if entry.get('nodeid') == node]
        if (len(matches) != 1 or matches[0].get('outcome') != 'skipped'
                or matches[0].get('phase') != 'setup' or matches[0].get('wasxfail') is not None
                or reason not in matches[0].get('reason', '')):
            raise ValueError('exact node, phase or reason differs: ' + node)
        rows.append({'nodeid': node, 'phase': 'setup', 'public_file': file,
                     'source_git_blob': FILES[file], 'source_predicate': predicate,
                     'observed_reason': matches[0]['reason']})
    return {'scope': 'source-bound observations only; no skip admission', 'run': 40930969344,
            'observation_sha256': expected_observation, 'rows': rows,
            'runtime_predicates_independently_verified': False, 'ordinary_acceptance': False, 'provider_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observation', type=Path)
    parser.add_argument('tree', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = review(args.observation.read_bytes(), args.tree.read_bytes())
    with args.output.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')
