"""Native node reconnaissance retains partial failures and never grants admission."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_sympy20212_node_recon as recon  # noqa: E402


def event(kind, **fields):
    return 'P07_NODE_RECON=' + json.dumps({'kind': kind, **fields})


def test_complete_raw_events_and_partial_events_are_distinct():
    path = 'sympy/core/tests/test_basic.py'
    lines = [event('file_started', path=path), event('candidate', path=path, name='test_basic'),
             event('file_finished', path=path), event('finished', started=1, completed=1, candidates=1,
                   python=[3, 9, 20], on_travis=False)]
    data = recon.summarize_events('\n'.join(lines), [path])
    assert data['started'] == data['finished'] == data['candidate_events'] == 1
    assert data['final_event_present'] is True and data['missing_finished'] == []
    partial = recon.summarize_events('\n'.join(lines[:2]), [path])
    assert partial['final_event_present'] is False and partial['missing_finished'] == [path]


def test_duplicate_invalid_or_out_of_batch_events_fail_closed():
    p = 'sympy/core/tests/test_basic.py'
    for lines in ([event('file_started', path=p)] * 2,
                  [event('file_finished', path=p)],
                  [event('candidate', path='sympy/bad.py', name='test_bad')],
                  [event('finished', started=1, completed=1, candidates=1, python=[3, 9, 20], on_travis=False)],
                  [event('bad_kind')]):
        with pytest.raises(ValueError):
            recon.summarize_events('\n'.join(lines), [p])


def test_run_requires_fresh_linux_output(tmp_path, monkeypatch):
    monkeypatch.setattr(recon.os, 'name', 'nt')
    with pytest.raises(ValueError):
        recon.run(tmp_path / 'fresh')
