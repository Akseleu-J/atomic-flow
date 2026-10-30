"""Reconcile the immutable SymPy20212 public tree and native test-file candidates.

Bytes and metadata only: no target import, collection, Docker execution, TaskRunner,
model or hidden oracle. The native runner's default blacklist is a candidate policy,
not proof of dynamic discovery or permission to skip public tests.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

BASE = 'a106f4782a9dbe7f8fd16030f15401d977e03ae9'
TREE = '6d5f0f379a0b28d6c83718311890449280202d0e'
BLACKLIST = 'sympy/integrals/rubi/rubi_tests/tests'
SHA1 = re.compile(r'[0-9a-f]{40}\Z')


def reconcile_tree(payload, root_sha=TREE):
    """Recompute every Git tree object, refusing partial or inconsistent metadata."""
    if not isinstance(payload, dict) or payload.get('truncated') is not False or payload.get('sha') != root_sha:
        raise ValueError('recursive tree response differs or is truncated')
    rows = payload.get('tree')
    if not isinstance(rows, list) or not rows or len(rows) > 20000:
        raise ValueError('tree entry count outside bound')
    entries, directories = {}, {'': root_sha}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid tree entry')
        path, mode, kind, sha = (row.get(k) for k in ('path', 'mode', 'type', 'sha'))
        if (not isinstance(path, str) or not path or '\\' in path or '\0' in path
                or PurePosixPath(path).as_posix() != path or any(x in ('', '.', '..') for x in path.split('/'))
                or path in entries or not isinstance(sha, str) or not SHA1.fullmatch(sha)
                or (mode, kind) not in {('040000', 'tree'), ('100644', 'blob'), ('100755', 'blob'),
                                        ('120000', 'blob'), ('160000', 'commit')}):
            raise ValueError('invalid or duplicate tree entry')
        size = row.get('size')
        if kind == 'blob' and (type(size) is not int or not 0 <= size <= 100_000_000):
            raise ValueError('invalid blob size')
        entries[path] = row
        if kind == 'tree':
            directories[path] = sha
    children = {name: [] for name in directories}
    for path, row in entries.items():
        parent, _, leaf = path.rpartition('/')
        if parent not in children:
            raise ValueError('missing parent directory')
        children[parent].append((leaf, row))
    for directory in sorted(directories, key=lambda x: x.count('/'), reverse=True):
        members = sorted(children[directory], key=lambda item: item[0].encode() + (b'/' if item[1]['type'] == 'tree' else b''))
        body = b''.join((format(int(row['mode'], 8), 'o').encode() + b' ' + leaf.encode() + b'\0' + bytes.fromhex(row['sha']))
                        for leaf, row in members)
        actual = hashlib.sha1(b'tree ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        if actual != directories[directory]:
            raise ValueError('Git tree digest mismatch')
    tests = []
    for path, row in sorted(entries.items()):
        name = path.rsplit('/', 1)[-1]
        if path.startswith('sympy/') and name.startswith('test_') and name.endswith('.py'):
            if row['type'] != 'blob' or row['mode'] not in ('100644', '100755'):
                raise ValueError('test candidate is not regular Git blob')
            tests.append({'path': path, 'sha': row['sha'], 'size': row['size'],
                          'native_default_blacklisted': BLACKLIST in path})
    if not tests:
        raise ValueError('no public candidates found')
    return {'schema_version': 1, 'repo': 'sympy/sympy', 'base_commit': BASE, 'root_tree': root_sha,
            'scope': 'static public Git tree; native runtime discovery and ordinary execution not established',
            'default_blacklist_substring': BLACKLIST, 'test_candidates': tests,
            'candidate_count': len(tests), 'default_candidate_count': sum(not r['native_default_blacklisted'] for r in tests),
            'blacklisted_candidate_count': sum(r['native_default_blacklisted'] for r in tests),
            'provider_calls': 0, 'ordinary_acceptance': False}


def download_tree():
    """Fetch public tree metadata only; root identity is fixed in this module."""
    import urllib.request

    request = urllib.request.Request(
        f'https://api.github.com/repos/sympy/sympy/git/trees/{TREE}?recursive=1',
        headers={'User-Agent': 'atomic-flow-p07-sympy-public-tree/1'},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError('public response exceeds bound')
    return reconcile_tree(json.loads(raw))


if __name__ == '__main__':
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('fresh output required')
    result = download_tree()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({key: result[key] for key in ('candidate_count', 'default_candidate_count', 'blacklisted_candidate_count')}))
