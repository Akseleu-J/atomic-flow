"""Bounded pylint6506 public execution diagnostic; never an ordinary admission.

Uses the independently audited GitPython collection builder, replacing only its
collection command, recorder, output bound, and observation assessment. The
public tests and pytest configuration remain intact. This may execute external
project tests under network=none; failures and skips are evidence, not excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import p07_pylint6506_gitpython_collection as base
import p07_sphinx7738_public_execution as assessment

ROOT = Path(__file__).resolve().parents[1]
NODES = ROOT / 'benchmarks/stage4/p07-pylint6506-collection-nodes-v1.json'
NODES_SHA = '493bb253f7e669c34fe5fef50fa32151a846804d5155ca9de36fa709b87d90c3'
ARCHIVE_SHA = 'bc84750ca9587772413d0ebd0236c7158ed06827a74c817409b796263793ea42'


def baseline(raw):
    if hashlib.sha256(raw).hexdigest() != NODES_SHA:
        raise ValueError('fixed baseline digest differs')
    data = json.loads(raw)
    rows = data.get('items', [])
    if (data.get('source_ci') != 41002231450 or data.get('artifact_sha256') != ARCHIVE_SHA
            or data.get('tested_commit') != '993500a888131e1409e134f5c023a5682bb2e6d4'
            or len(rows) != 1954 or len({r['nodeid'] for r in rows}) != len(rows)):
        raise ValueError('baseline inventory differs')
    return rows


def execution_source():
    source = base.fixed_collection_source()
    changes = {
        'self.collection_skips=[]; self.finished=False': 'self.collection_skips=[]; self.finished=False; self.phases=[]',
        '    def pytest_sessionfinish(self,session,exitstatus):self.finished=True': (
            '    def pytest_runtest_logreport(self,report):\n'
            "        self.phases.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome,"
            "'wasxfail':str(report.wasxfail) if hasattr(report,'wasxfail') else None,"
            "'reason':str(report.longrepr) if report.outcome!='passed' else '', 'reason_truncated':False})\n"
            '    def pytest_sessionfinish(self,session,exitstatus):self.finished=True'),
        "['--collect-only','-q','--benchmark-disable','tests']": "['-q','--tb=line','--benchmark-disable','tests']",
        "'items':r.items,'collection_errors'": "'items':r.items,'phases':r.phases,'collection_errors'",
    }
    for old, new in changes.items():
        if source.count(old) != 1:
            raise ValueError('reviewed collection source changed')
        source = source.replace(old, new)
    return source


def run(output):
    reference = baseline(NODES.read_bytes())
    source = execution_source()
    original_source, original_summary, original_bounded = base.fixed_collection_source, base.collection_summary, base._bounded

    def bounded(*args, **kwargs):
        if kwargs.get('timeout') != 300 or kwargs.get('limit') != 8_000_000:
            raise ValueError('reviewed execution bounds changed')
        kwargs.update(timeout=1500, limit=24_000_000)
        return original_bounded(*args, **kwargs)

    def summarize(observation, tree, exit_code):
        return assessment.assess_execution(observation, tree, exit_code, reference)

    try:
        base.fixed_collection_source = lambda: source
        base.collection_summary = summarize
        base._bounded = bounded
        result = base.run(output)
    finally:
        base.fixed_collection_source, base.collection_summary, base._bounded = original_source, original_summary, original_bounded
    result.update(scope='full public execution diagnostic only', baseline_nodes_sha256=NODES_SHA,
                  baseline_ci=41002231450, test_execution=(output / 'collection.stdout').exists(),
                  ordinary_acceptance=False, hidden_oracle='not_run', provider_calls=0)
    (output / 'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if run(parser.parse_args().output)['status'] == 'execution_observed_clean' else 1)
