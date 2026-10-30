"""Wrapper extraction accepts exact bytes only, with no runtime admission."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_wrapper_extract import check_copied_wrappers, run  # noqa: E402
from p07_pytest6116_wrapper_verify import ENTRY_POINTS, expected_bytes  # noqa: E402


def plan():
    return {'generated_console_scripts': {name: {'entry_point': ep, 'kind': 'pip_generated_console_script',
                                                  'expected_sha256': None} for name, ep in ENTRY_POINTS.items()}}


def test_exact_extracted_wrapper_bytes_are_required(tmp_path):
    for name, entry in ENTRY_POINTS.items():
        target = tmp_path / name.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected_bytes(entry))
    result, data = check_copied_wrappers(tmp_path, plan())
    assert result['wrappers_verified'] == 5
    assert result['ordinary_acceptance'] is False
    assert len(data) == 5
    target = tmp_path / 'usr/local/bin/pytest'
    target.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='wrapper bytes differ'):
        check_copied_wrappers(tmp_path, plan())
    target.unlink()
    with pytest.raises(ValueError, match='missing'):
        check_copied_wrappers(tmp_path, plan())


def test_no_existing_output_directory():
    with pytest.raises(ValueError, match='fresh Linux output'):
        run(Path.cwd())
