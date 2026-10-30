"""Fail-closed checks for the prospective Pylint GitPython wheel closure."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pylint6506_gitpython_prepare as candidate  # noqa: E402


def test_pinned_profile_and_no_overwrite(tmp_path, monkeypatch):
    raw = candidate.PROFILE.read_bytes()
    profile = candidate.check_profile(raw)
    assert len(profile['wheels']) == 3
    with pytest.raises(ValueError, match='digest'):
        candidate.check_profile(raw + b' ')
    target = tmp_path / 'existing'
    target.mkdir()
    monkeypatch.setattr(candidate.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('unexpected download'))
    with pytest.raises(ValueError, match='fresh'):
        candidate.prepare(target)


def test_closure_evaluates_python39_markers_and_refuses_incompatible_edges():
    rows = [{'name': 'gitpython', 'version': '3.1.27', 'requires_dist': [
        'gitdb (<5,>=4.0.1)', 'typing-extensions (>=3.7.4.3) ; python_version < "3.8"']},
            {'name': 'gitdb', 'version': '4.0.9', 'requires_dist': ['smmap (<6,>=3.0.1)']},
            {'name': 'smmap', 'version': '5.0.0', 'requires_dist': []}]
    assert len(candidate.closure(rows)) == 2
    for change in ('missing', 'incompatible', 'extra', 'url', 'unknown'):
        edited = json.loads(json.dumps(rows))
        if change == 'missing':
            edited.pop()
        elif change == 'incompatible':
            edited[0]['requires_dist'] = ['gitdb>=8']
        elif change == 'extra':
            edited[1]['requires_dist'] = ['smmap[unknown]>=3']
        elif change == 'url':
            edited[1]['requires_dist'] = ['smmap @ https://example.com/a.whl']
        else:
            edited[2]['requires_dist'] = ['nonexistent']
        with pytest.raises(ValueError):
            candidate.closure(edited)
