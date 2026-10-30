"""Focused replay must bind the six nodes, complete output and cleanup."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_six_failure_focus as focus  # noqa: E402


def observation():
    return {'finished': True, 'items': list(focus.NODES), 'completed': list(focus.NODES), 'exit_code': 1,
            'nonpassing': [{'nodeid': node, 'phase': 'call'} for node in focus.NODES],
            'plugins': {'xdist': True, 'xdist.boxed': True, 'xdist.looponfail': True},
            'collection_errors': [], 'collection_skips': []}


def report():
    return {'exit_code': 1, 'timed_out': False, 'stdout_truncated': False,
            'stderr_truncated': False, 'cleanup_confirmed': True}


def test_focus_keeps_six_exact_nodes_and_untruncated_traceback():
    code = focus.focused_source()
    compile(code, '<focused-container>', 'exec')
    for node in focus.NODES:
        assert code.count(repr(node)) == 1
    assert "'--tb=long','--show-capture=all'" in code
    assert "'reason':str(report.longrepr)[:500]" not in code


def test_result_requires_unique_nodes_phases_and_complete_streams():
    assert focus.verify_observation(observation(), report()) == {'collected': 6, 'completed': 6}
    changed = observation()
    changed['completed'].pop()
    with pytest.raises(ValueError, match='incomplete'):
        focus.verify_observation(changed, report())
    changed = observation()
    changed['nonpassing'].append(changed['nonpassing'][0])
    with pytest.raises(ValueError, match='incomplete'):
        focus.verify_observation(changed, report())
    changed_report = {**report(), 'stdout_truncated': True}
    with pytest.raises(ValueError, match='incomplete'):
        focus.verify_observation(observation(), changed_report)
    with pytest.raises(ValueError, match='incomplete'):
        focus.verify_observation(observation(), {**report(), 'cleanup_confirmed': False})


def test_prior_archive_must_match_hash_commit_and_failed_full_receipt(monkeypatch):
    summary = {'image_id': focus.PRIOR_IMAGE, 'status': 'combined_public_diagnostic_failed',
               'cleanup_confirmed': True, 'collected': 2617, 'completed': 2617, 'exit_code': 1,
               'stdout_truncated': False, 'timed_out': False}
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('tested-commit.txt', focus.PRIOR_COMMIT)
        archive.writestr('diagnostic/summary.json', json.dumps(summary))
        archive.writestr('diagnostic/image-inspect.json', json.dumps([{'Id': focus.PRIOR_IMAGE}]))
        for name in ('installed-inventory.json', 'numpy-inventory.json', 'verified-wrappers.json', 'Dockerfile'):
            archive.writestr('diagnostic/' + name, '{}')
    raw = data.getvalue()
    monkeypatch.setattr(focus, 'PRIOR_ARCHIVE_SHA', hashlib.sha256(raw).hexdigest())
    assert focus.read_prior(raw)['tested-commit.txt'].decode() == focus.PRIOR_COMMIT
    with pytest.raises(ValueError, match='digest differs'):
        focus.read_prior(raw + b'changed')
