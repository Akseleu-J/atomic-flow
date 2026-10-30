"""Fail-closed, non-authorizing SymPy20212 partition and aggregation contract.

Inputs are caller-supplied declarations, not trusted receipts. This module never
runs target code, validates an image, authenticates an executor, or grants
ordinary admission. The expected node inventory must itself be independently
collected and authenticated before any downstream policy may rely on it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re

from p07_sympy20212_public_discovery import MANIFEST, manifest_data

PATH = re.compile(r'sympy/[A-Za-z0-9_./-]+\.py\Z')
NODE = re.compile(r'(sympy/[A-Za-z0-9_./-]+\.py)::(test[A-Za-z0-9_]+)\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')
OUTCOMES = frozenset({'passed', 'failed', 'exceptions', 'skipped', 'xfailed', 'xpassed'})
LIMIT = 256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def plan(observed_files):
    """Derive fixed batches only from a complete declared discovery set.

    Model the pinned native `_test` selector: path substring OR fnmatch against
    the basename. Test each selector against the *entire* discovered set.
    """
    rows = manifest_data(MANIFEST.read_bytes())
    required = sorted(row['path'] for row in rows if not row['native_default_blacklisted'])
    if (type(observed_files) is not list
            or any(type(p) is not str or not PATH.fullmatch(p) or '//' in p
                   or any(part in ('', '.', '..') for part in p.split('/')) for p in observed_files)
            or len(observed_files) != len(set(observed_files)) or set(observed_files) != set(required)):
        raise ValueError('complete exact observed file set required')
    for selector in required:
        matched = [path for path in observed_files
                   if selector in path or fnmatch.fnmatchcase(path.rsplit('/', 1)[-1], selector)]
        if matched != [selector]:
            raise ValueError('native selector is not a unique exact file')
    batches = [required[start:start + LIMIT] for start in range(0, len(required), LIMIT)]
    result = {'schema_version': 1, 'scope': 'untrusted static partition declaration only',
              'limit': LIMIT, 'files': required, 'batches': batches,
              'runtime_receipts_authenticated': False, 'node_inventory_authenticated': False,
              'ordinary_acceptance': False}
    return {**result, 'plan_sha256': digest(result)}


def aggregate(partition, expected_nodes, runs, *, source_revision, environment_digest, runner_digest):
    """Reconcile exact paths and exact expected nodes from caller declarations.

    Every batch binds to one explicit source/environment/runner identity. A
    passing structural result is still not evidence of actual execution or
    complete node discovery; it cannot authorize publication or a model claim.
    """
    if (type(partition) is not dict or type(partition.get('files')) is not list
            or partition != plan(partition['files'])):
        raise ValueError('partition is not the fixed canonical plan')
    if any(type(v) is not str or not SHA256.fullmatch(v) for v in
           (source_revision, environment_digest, runner_digest)):
        raise ValueError('fixed source, environment and runner digests required')
    files = set(partition['files'])
    if type(expected_nodes) is not list or not expected_nodes or any(type(n) is not str for n in expected_nodes):
        raise ValueError('nonempty expected node inventory required')
    if len(expected_nodes) != len(set(expected_nodes)):
        raise ValueError('duplicate expected node')
    expected_files = set()
    for node in expected_nodes:
        match = NODE.fullmatch(node)
        if not match or match[1] not in files:
            raise ValueError('invalid or nonpublic expected node')
        expected_files.add(match[1])
    if expected_files != files:
        raise ValueError('expected node inventory omits one or more files')
    if type(runs) is not list or len(runs) != len(partition['batches']):
        raise ValueError('one result per batch required')
    results = {}
    for index, (batch, run) in enumerate(zip(partition['batches'], runs, strict=True)):
        if (type(run) is not dict or set(run) != {'batch_index', 'plan_sha256', 'source_revision',
                'environment_digest', 'runner_digest', 'files', 'node_statuses'}
                or type(run['batch_index']) is not int or run['batch_index'] != index
                or run['plan_sha256'] != partition['plan_sha256'] or run['source_revision'] != source_revision
                or run['environment_digest'] != environment_digest or run['runner_digest'] != runner_digest
                or run['files'] != batch or type(run['node_statuses']) is not dict):
            raise ValueError('batch identity or file coverage differs')
        expected = {node for node in expected_nodes if node.split('::', 1)[0] in set(batch)}
        statuses = run['node_statuses']
        if (set(statuses) != expected or any(type(v) is not str or v not in OUTCOMES for v in statuses.values())
                or results.keys() & statuses.keys()):
            raise ValueError('batch node inventory or outcomes differ')
        results.update(statuses)
    if set(results) != set(expected_nodes):
        raise ValueError('aggregate inventory differs')
    counts = {status: sum(value == status for value in results.values()) for status in sorted(OUTCOMES)}
    return {'schema_version': 1, 'status': 'declared_partition_structurally_reconciled',
            'plan_sha256': partition['plan_sha256'], 'expected_node_inventory_sha256': digest(sorted(expected_nodes)),
            'node_statuses_sha256': digest(results), 'files': len(files), 'nodes': len(results), 'counts': counts,
            'all_declared_passed': counts['passed'] == len(results), 'source_revision': source_revision,
            'environment_digest': environment_digest, 'runner_digest': runner_digest,
            'runtime_receipts_authenticated': False, 'node_inventory_authenticated': False,
            'ordinary_acceptance': False, 'can_publish': False}
