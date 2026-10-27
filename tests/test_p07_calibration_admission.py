import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from evaluation_p07_source_pilot import admit_calibration  # noqa:E402

INVENTORY = json.loads((ROOT / 'validation/p07-budget20/calibration-inventory.json').read_bytes())
TASKS = {task['instance_id']: task for task in json.loads((ROOT / 'benchmarks/stage4/corpus-p07-v3.json').read_bytes())['tasks']}


def test_calibration_inventory_covers_every_current_task_once_without_silent_downgrades():
    rows = INVENTORY['rows']
    assert len(rows) == len(TASKS) == INVENTORY['accepted'] == INVENTORY['total'] == 30
    assert {row['instance_id'] for row in rows} == set(TASKS)
    assert all(row['status'] == 'accepted' for row in rows)


@pytest.mark.parametrize('record', [row for row in INVENTORY['rows'] if row['status'] == 'accepted'],
                         ids=lambda row: row['instance_id'])
def test_every_current_accepted_report_contains_all_current_obligations(record):
    report = json.loads((ROOT / record['accepted_evidence']).read_bytes())
    admit_calibration(report, TASKS[record['instance_id']])


@pytest.mark.parametrize('mutation', ['omit_requirement', 'gold_failure', 'regression_failure', 'different_patch',
                                    'reorder', 'missing_row_ancestry', 'contradictory_aggregate_ancestry',
                                    'different_protected_tests', 'unknown_calibration_corpus'])
def test_old_success_flags_cannot_mask_missing_or_changed_task_evidence(mutation):
    record = next(row for row in INVENTORY['rows'] if row['instance_id'] == 'psf__requests-2674')
    report = copy.deepcopy(json.loads((ROOT / record['accepted_evidence']).read_bytes()))
    task = copy.deepcopy(TASKS['psf__requests-2674'])
    if mutation == 'omit_requirement':
        del report['rows'][1]['tests']['test_requests.py::test_urllib3_pool_connection_closed']
    elif mutation == 'gold_failure':
        report['rows'][1]['tests'][task['FAIL_TO_PASS'][0]] = 'FAILED'
    elif mutation == 'regression_failure':
        report['rows'][0]['tests'][task['PASS_TO_PASS'][0]] = 'FAILED'
    elif mutation == 'different_patch':
        report['rows'][1]['patch_sha256'] = '0' * 64
    elif mutation == 'missing_row_ancestry':
        del report['rows'][1]['base_verified']
    elif mutation == 'contradictory_aggregate_ancestry':
        report['base_commit'] = '0' * 40
    elif mutation == 'different_protected_tests':
        task['test_patch'] += '\n'
    elif mutation == 'unknown_calibration_corpus':
        report['manifest_sha256'] = '0' * 64
    else:
        report['rows'][0]['label'] = 'wrong'
    with pytest.raises(ValueError, match='calibration'):
        admit_calibration(report, task)
