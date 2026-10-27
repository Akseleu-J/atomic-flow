"""Native runner contracts/counting only; actual SymPy runs require Docker CI."""
import ast
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from atomic_flow import check_execution as checks
from atomic_flow.agents import execution
from atomic_flow.agents.verify import check_source_revision
from atomic_flow.check_obligation import CheckObligation, CheckObligationError
from atomic_flow.check_spec import SYMPY_RUNNER_PATHS, CheckSpec, SymPyRunnerIdentity
from atomic_flow.config import ExecutionConfig

NONCE = 'a' * 32
PATH = 'sympy/core/tests/test_example.py'
BODY = ('================ test process starts ================\n'
        + PATH + '[2] \n'
        'test_ok ok\n'
        'test_known_failure f                          [OK]\n\n'
        '====== tests finished: 1 passed, 1 expected to fail, in 0.01 seconds ======\n')


@pytest.fixture
def admitted(tmp_path):
    for path in (*SYMPY_RUNNER_PATHS, PATH):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'# fixed synthetic source fixture, never executed\n')
    hashes = tuple((path, hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()) for path in SYMPY_RUNNER_PATHS)
    identity = SymPyRunnerIdentity((3, 9, 20), '1.8.dev', hashes)
    spec = CheckSpec('sympy__sympy-example', 'a' * 40, 'b' * 40, check_source_revision(tmp_path),
                     'example/sympy@sha256:' + 'c' * 64, '/opt/miniconda3/envs/testbed/bin/python',
                     'sympy-native', (PATH,), sympy_identity=identity)
    return spec, tmp_path


def config(spec):
    return ExecutionConfig(mode='docker', profile='checks', image=spec.image,
                           output_bytes=spec.resources.output_bytes_per_stream)


def result(spec, *, body=BODY, exit_code=0, **changes):
    summary = checks._parse_sympy_output(body, spec.test_paths, exit_code)
    receipt = {'nonce': NONCE, 'root': spec.target_root, 'interpreter': spec.interpreter,
               'runner': 'sympy-native', 'sympy_module': spec.target_root + '/sympy/__init__.py',
               'sympy_version': spec.sympy_identity.sympy_version,
               'python_version': list(spec.sympy_identity.python_version),
               'native_source_sha256': dict(spec.sympy_identity.source_sha256),
               'native_argv': [spec.target_root + '/bin/test', '-C', '--verbose', *spec.test_paths],
               'native_stdout_sha256': hashlib.sha256(body.encode()).hexdigest(),
               'finished': True, 'exit_code': exit_code, **summary, **changes}
    output = 'ATOMIC_SYMPY_BEGIN:' + NONCE + '\n' + body + 'ATOMIC_SYMPY_END:' + NONCE + '\n'
    return execution.CommandResult(exit_code, output + checks._MARKER + json.dumps(receipt) + '\n', '')


def test_native_argv_and_bootstrap_preserve_original_runner_and_existing_security_bounds(admitted):
    spec, root = admitted
    assert spec.argv == (spec.interpreter, 'bin/test', '-C', '--verbose', PATH)
    assert checks.admit_check_spec(spec, root, config(spec), 180, list(spec.argv[1:])) == spec.source_revision
    args = checks.checker_args(spec, NONCE)
    assert args[:2] == ['-I', '-c']
    ast.parse(args[2])
    assert 'runpy' not in args[2] and 'pytest' not in args[2]
    assert "process=subprocess.Popen(native" in args[2]
    assert "'-C','--verbose'" in args[2]
    command = execution.docker_command('docker', spec.image, root, 'native-check', list(spec.argv[1:]),
                                      host_spec=spec, host_spec_nonce=NONCE)
    assert '--memory=1073741824' in command and '--pids-limit=128' in command and '--cpus=2' in command
    for control in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pull=never'):
        assert control in command


def test_native_source_identity_is_checked_before_launch_without_target_import(admitted):
    spec, root = admitted
    (root / 'bin/test').write_bytes(b'# different native runner\n')
    changed = replace(spec, source_revision=check_source_revision(root))
    with pytest.raises(execution.ExecutionUnavailable, match='runner source'):
        checks.admit_check_spec(changed, root, config(changed), 180, list(changed.argv[1:]))


def test_obligation_automatically_seals_every_native_runner_source(admitted):
    spec, root = admitted
    obligation = CheckObligation.seal(spec, root, environment_digest='e' * 64, deadline_at=1000, now=lambda: 1)
    assert set(spec.sympy_identity.source_sha256) <= set(obligation.protected_files)
    assert obligation.derive(root, now=lambda: 2).sympy_identity == spec.sympy_identity
    (root / 'bin/test').write_bytes(b'# silently changed native runner\n')
    with pytest.raises(CheckObligationError, match='protected ordinary-test'):
        obligation.derive(root, now=lambda: 3)
    with pytest.raises(CheckObligationError, match='runner source'):
        CheckObligation.seal(replace(spec, source_revision=check_source_revision(root)), root,
                             environment_digest='e' * 64, deadline_at=1000, now=lambda: 1)


def test_direct_obligation_construction_cannot_omit_native_runner_protection(admitted):
    spec, root = admitted
    obligation = CheckObligation.seal(spec, root, environment_digest='e' * 64, deadline_at=1000, now=lambda: 1)
    with pytest.raises(CheckObligationError, match='native SymPy runner'):
        replace(obligation, protected_files=tuple(item for item in obligation.protected_files if item[0] != 'bin/test'))


def test_expected_native_failures_keep_their_status_and_counts(admitted):
    spec, _ = admitted
    receipt = checks._runner_receipt(result(spec), spec, NONCE)
    assert receipt['collected'] == 2 and receipt['counts']['passed'] == 1 and receipt['counts']['xfailed'] == 1
    assert receipt['test_statuses'][PATH + '::test_known_failure'] == 'xfailed'


@pytest.mark.parametrize('body', [
    '', BODY.replace('[2]', '[3]'), BODY.replace('[OK]', ''), BODY.replace('test_ok ok\n', ''),
    BODY.replace('test_known_failure f', 'test_ok f'), BODY + BODY,
    BODY.replace(PATH, 'sympy/other/test_not_selected.py'), BODY.replace('1 passed', '2 passed'),
    BODY.replace('test_known_failure f', 'test_known_failure strange'),
    BODY.replace('test_known_failure f', 'test_known_failure Slow w'),
    BODY.replace('test_known_failure f', 'test_known_failure E'),
])
def test_empty_partial_ambiguous_or_false_zero_native_output_refuses(body):
    with pytest.raises(ValueError):
        checks._parse_sympy_output(body, (PATH,), 0)


@pytest.mark.parametrize('changes', [
    {'nonce': 'b' * 32}, {'root': '/wrong'}, {'runner': 'pytest'}, {'finished': False},
    {'sympy_module': '/testbed/sympy/__init__.py'}, {'sympy_version': 'different'},
    {'python_version': [3, 6, 15]}, {'native_source_sha256': {}}, {'native_argv': ['bin/test']},
    {'collected': 99}, {'collection': []}, {'native_stdout_sha256': '0' * 64},
])
def test_native_receipt_cannot_change_runtime_inventory_or_observations(admitted, changes):
    spec, _ = admitted
    assert checks._runner_receipt(result(spec, **changes), spec, NONCE) is None


def test_native_receipt_rejects_boolean_exit_code_even_when_numeric_comparison_matches(admitted):
    spec, _ = admitted
    observed = result(spec)
    prefix, raw = observed.stdout.rsplit(checks._MARKER, 1)
    receipt = json.loads(raw)
    receipt['exit_code'] = False
    observed.stdout = prefix + checks._MARKER + json.dumps(receipt) + '\n'
    assert checks._runner_receipt(observed, spec, NONCE) is None


@pytest.mark.parametrize('body', [BODY + 'test_extra F [FAIL]\n', BODY + 'Traceback: incomplete child failure\n',
                                BODY.replace('[OK]', '[FAIL]')])
def test_native_log_cannot_append_unaccounted_observations_or_contradict_its_file_verdict(body):
    with pytest.raises(ValueError):
        checks._parse_sympy_output(body, (PATH,), 0)


def test_only_the_real_failure_trailer_is_allowed_after_failed_native_summary():
    failed = BODY.replace('test_known_failure f', 'test_known_failure F').replace('[OK]', '[FAIL]').replace(
        '1 expected to fail', '1 failed')
    assert checks._parse_sympy_output(failed + 'DO *NOT* COMMIT!\n', (PATH,), 1)['counts']['failed'] == 1
    with pytest.raises(ValueError):
        checks._parse_sympy_output(BODY + 'DO *NOT* COMMIT!\n', (PATH,), 0)


@pytest.mark.parametrize('field', ['timed_out', 'stdout_truncated', 'stderr_truncated'])
def test_native_timeout_or_output_truncation_never_passes(admitted, field):
    spec, _ = admitted
    observed = result(spec)
    setattr(observed, field, True)
    assert checks._runner_receipt(observed, spec, NONCE) is None


def test_actual_completed_native_failure_is_retained_but_never_a_pass(admitted, monkeypatch):
    spec, root = admitted
    body = BODY.replace('test_ok ok', 'test_ok F').replace('[OK]', '[FAIL]').replace('1 passed', '0 passed, 1 failed')
    observed = result(spec, body=body, exit_code=1)

    def execute(args, project, **options):
        return execution.CommandResult(1, observed.stdout.replace(NONCE, options['host_spec_nonce']), '')

    monkeypatch.setattr(execution, 'run_python', execute)
    receipt = checks.run_check_spec(spec, root)
    assert not receipt.check_passed and not receipt.verified_success
    assert receipt.runner_receipt['counts']['failed'] == 1


def test_native_success_uses_existing_lifecycle_and_keeps_independent_success_false(admitted, monkeypatch):
    spec, root = admitted
    calls = []

    def execute(args, project, **options):
        calls.append(options)
        observed = result(spec)
        return execution.CommandResult(0, observed.stdout.replace(NONCE, options['host_spec_nonce']), '')

    monkeypatch.setattr(execution, 'run_python', execute)
    receipt = checks.run_check_spec(spec, root)
    assert receipt.check_passed and not receipt.verified_success
    assert receipt.source_before == receipt.source_after == spec.source_revision
    assert calls[0]['host_spec'] is spec and calls[0]['config'].profile == 'checks'


def test_identity_is_immutable_and_cannot_select_private_or_older_runtime(admitted):
    spec, _ = admitted
    identity = spec.sympy_identity
    with pytest.raises(FrozenInstanceError):
        identity.sympy_version = 'changed'
    for fields in ({'python_version': (3, 6, 15)}, {'python_version': [3, 9, 20]}, {'python_version': (True, 9, 20)},
                   {'source_sha256': identity.source_sha256[:-1]}, {'source_sha256': tuple(reversed(identity.source_sha256))},
                   {'sympy_version': 'version; shell'}, {'source_sha256': tuple((path, 'x' * 64) for path in SYMPY_RUNNER_PATHS)}):
        with pytest.raises(ValueError):
            replace(identity, **fields)
    with pytest.raises(ValueError, match='only to a native'):
        replace(spec, runner='pytest')
