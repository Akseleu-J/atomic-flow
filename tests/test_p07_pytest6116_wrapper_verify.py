"""Exact wrapper bytes and incomplete hash-only evidence remain distinct."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_wrapper_verify import (  # noqa: E402
    ENTRY_POINTS,
    compare_reported_hashes,
    expected_bytes,
    expected_plan,
    verify_wrapper_bytes,
)


def manifest():
    return {name: {'entry_point': value, 'kind': 'pip_generated_console_script', 'expected_sha256': None}
            for name, value in ENTRY_POINTS.items()}


def test_fixed_wrapper_bytes_and_aliases_are_strict():
    generated = manifest()
    observed = {name: expected_bytes(value) for name, value in ENTRY_POINTS.items()}
    result = verify_wrapper_bytes(generated, observed)
    assert result['wrappers_verified'] == 5
    assert result['ordinary_acceptance'] is False
    assert result['pip_installed_source_verified'] is False
    assert observed['/usr/local/bin/pytest'] == observed['/usr/local/bin/py.test']
    observed['/usr/local/bin/pytest'] += b'#changed\n'
    with pytest.raises(ValueError, match='bytes differ'):
        verify_wrapper_bytes(generated, observed)


def test_receipt_matches_are_not_raw_byte_attestation():
    generated = manifest()
    hashes = expected_plan(generated)
    receipt = {name: {'entry_point': value, 'sha256': hashes[name], 'content_verified': False}
               for name, value in ENTRY_POINTS.items()}
    assert compare_reported_hashes(generated, receipt)['wrapper_bytes_verified'] is False
    receipt['/usr/local/bin/nosetests']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='reported wrapper differs'):
        compare_reported_hashes(generated, receipt)


def test_refuse_extra_or_missing_wrapper_and_changed_entry_point():
    generated = manifest()
    generated['/usr/local/bin/new'] = generated['/usr/local/bin/pytest']
    with pytest.raises(ValueError, match='inventory differs'):
        expected_plan(generated)
    generated = manifest()
    generated['/usr/local/bin/pytest']['entry_point'] = 'different:main'
    with pytest.raises(ValueError, match='entry point differs'):
        expected_plan(generated)
    with pytest.raises(ValueError, match='unreviewed'):
        expected_bytes('unsafe:main')
