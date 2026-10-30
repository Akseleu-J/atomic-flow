"""Verify the five fixed pip 23.0.1 wrapper bytes, without importing target code.

This is a narrow Linux/Python3.8 candidate, not a general pip implementation.
Raw wrapper bytes are required for a byte-verification claim; saved hash-only
reports can only be compared as a consistency check. Runtime, source, pip
provenance and ordinary-test admission are independent gates.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PIP_WHEEL_SHA256 = '236bcb61156d76c4b8a05821b988c7b8c35bf0da28a4b614e8d6ab5212c25c6f'
DISTLIB_SCRIPTS_SHA256 = '0669132a68939389b6723fa2b9e9626adc33deeb7ff52b000415b9d6f9d09d95'
ENTRY_POINTS = {
    '/usr/local/bin/chardetect': 'chardet.cli.chardetect:main',
    '/usr/local/bin/nosetests': 'nose:run_exit',
    '/usr/local/bin/nosetests-3.4': 'nose:run_exit',
    '/usr/local/bin/py.test': 'pytest:main',
    '/usr/local/bin/pytest': 'pytest:main',
}


def expected_bytes(entry_point):
    if entry_point not in set(ENTRY_POINTS.values()):
        raise ValueError('unreviewed entry point')
    module, function = entry_point.split(':')
    source = ("#!/usr/local/bin/python\n# -*- coding: utf-8 -*-\nimport re\nimport sys\n"
              f"from {module} import {function}\nif __name__ == '__main__':\n"
              "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
              f"    sys.exit({function}())\n")
    return source.encode('ascii')


def expected_plan(generated):
    """Require the exact audited entry-point list before considering bytes."""
    if type(generated) is not dict or set(generated) != set(ENTRY_POINTS):
        raise ValueError('generated wrapper inventory differs')
    for name, target in ENTRY_POINTS.items():
        value = generated[name]
        if value != {'entry_point': target, 'kind': 'pip_generated_console_script', 'expected_sha256': None}:
            raise ValueError('publisher wheel entry point differs')
    return {name: hashlib.sha256(expected_bytes(target)).hexdigest() for name, target in ENTRY_POINTS.items()}


def verify_wrapper_bytes(generated, observed):
    """Compare already safely copied regular-file bytes to the fixed template."""
    hashes = expected_plan(generated)
    if type(observed) is not dict or set(observed) != set(ENTRY_POINTS):
        raise ValueError('observed wrapper inventory differs')
    for name, raw in observed.items():
        if type(raw) is not bytes or raw != expected_bytes(ENTRY_POINTS[name]):
            raise ValueError('wrapper bytes differ: ' + name)
    return {'scope': 'fixed candidate wrapper bytes only', 'wrappers_verified': len(hashes),
            'sha256': hashes, 'pip_installed_source_verified': False, 'ordinary_acceptance': False}


def compare_reported_hashes(generated, observations):
    """Compare an earlier worker's hash-only receipt without claiming byte extraction."""
    hashes = expected_plan(generated)
    if type(observations) is not dict or set(observations) != set(hashes):
        raise ValueError('reported wrapper inventory differs')
    for name, value in observations.items():
        if (type(value) is not dict or set(value) != {'entry_point', 'sha256', 'content_verified'}
                or value.get('entry_point') != ENTRY_POINTS[name]
                or value.get('sha256') != hashes[name] or value.get('content_verified') is not False):
            raise ValueError('reported wrapper differs: ' + name)
    return {'scope': 'worker reported hashes only; raw wrapper bytes not independently extracted',
            'reported_hashes_match': len(hashes), 'wrapper_bytes_verified': False, 'ordinary_acceptance': False}


def historical_report(path: Path, generated):
    """Load a retained receipt; the caller separately verifies its artifact hash."""
    return compare_reported_hashes(generated, json.loads(path.read_bytes()))
