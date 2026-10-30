"""Prospective trial closure is bound to the target OS and refuses missing edges."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_trial_prepare as trial  # noqa: E402


def row(name, version, requires=(), requires_python=None):
    return {'name': name, 'version': version, 'requires_dist': list(requires), 'requires_python': requires_python}


def test_windows_marker_is_not_evaluated_using_host_os():
    rows = [row('twisted', '21.2.0', ['win-extra; platform_system == "Windows"',
                                       'dep>=1; platform_system == "Linux"']), row('dep', '1.1')]
    assert trial.closure(rows) == [{'from': 'twisted', 'requirement': 'dep>=1; platform_system == "Linux"', 'resolved': '1.1'}]


def test_missing_dependency_compatibility_and_unfixed_markers_fail_closed():
    for rows in ([row('a', '1', ['missing>=1'])],
                 [row('a', '1', ['dep>=2']), row('dep', '1')],
                 [row('a', '1', ['dep; platform_release == "x"']), row('dep', '1')],
                 [row('a', '1', ['dep[extra]']), row('dep', '1')],
                 [row('a', '1'), row('A', '2')],
                 [row('a', '1', requires_python='>=3.9')]):
        with pytest.raises(ValueError):
            trial.closure(rows)


def test_profile_tampering_fails_before_network(tmp_path, monkeypatch):
    profile = tmp_path / 'candidate.json'
    profile.write_text('{}')
    monkeypatch.setattr(trial, 'PROFILE', profile)
    with pytest.raises(ValueError, match='profile bytes'):
        trial.prepare(tmp_path / 'out')
