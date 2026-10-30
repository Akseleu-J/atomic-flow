"""Host-only behavioral checks for the unadmitted boxed compatibility adapter."""
import importlib.util
import marshal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools/p07_pytest6116_boxed_adapter_v1.py'


class ExitCalled(Exception):
    pass


class ForkExit(BaseException):
    pass


def load(monkeypatch):
    fake_py = SimpleNamespace(process=SimpleNamespace())
    fake_forked = SimpleNamespace(serialize_report=lambda x: x, report_process_crash=lambda item, result: ('crash', item, result.signal))
    fake_runner = SimpleNamespace(TestReport=lambda **x: x)
    fake_pytest = SimpleNamespace(exit=lambda message: (_ for _ in ()).throw(ExitCalled(message)))
    fake_pytest.exit.Exception = type('TargetExit', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'py', fake_py)
    monkeypatch.setitem(sys.modules, 'pytest', fake_pytest)
    monkeypatch.setitem(sys.modules, 'pytest_forked', fake_forked)
    monkeypatch.setitem(sys.modules, '_pytest', SimpleNamespace(runner=fake_runner))
    spec = importlib.util.spec_from_file_location('p07_boxed_test_adapter', PATH)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter, fake_py, fake_forked, fake_runner, fake_pytest


def execute_in_fake_fork(adapter, fake_py, monkeypatch):
    def run(fn):
        try:
            result = SimpleNamespace(retval=fn(), exitstatus=0, signal=0)
            return SimpleNamespace(waitfinish=lambda: result)
        except ForkExit as exc:
            result = SimpleNamespace(retval=None, exitstatus=exc.args[0], signal=0)
            return SimpleNamespace(waitfinish=lambda: result)
        except Exception:
            result = SimpleNamespace(retval=None, exitstatus=3, signal=0)
            return SimpleNamespace(waitfinish=lambda: result)
    fake_py.process.ForkedFunc = run
    monkeypatch.setattr(adapter.os, '_exit', lambda code: (_ for _ in ()).throw(ForkExit(code)))


def test_reports_preserve_all_phases(monkeypatch):
    adapter, py, _, runner, _ = load(monkeypatch)
    execute_in_fake_fork(adapter, py, monkeypatch)
    calls = []
    def runtest(item, log):
        calls.append((item, log))
        return [{'when': 'setup', 'outcome': 'passed'}, {'when': 'call', 'outcome': 'failed'},
                {'when': 'teardown', 'outcome': 'passed'}]
    runner.runtestprotocol = runtest
    assert adapter.forked_run_report('node') == runtest('node', False)
    assert calls == [('node', False), ('node', False)]


@pytest.mark.parametrize('error', ['target_exit', 'keyboard_interrupt'])
def test_target_exit_propagates_from_fork(monkeypatch, error):
    adapter, py, _, runner, target_pytest = load(monkeypatch)
    execute_in_fake_fork(adapter, py, monkeypatch)
    def runtest(item, log):
        raise target_pytest.exit.Exception() if error == 'target_exit' else KeyboardInterrupt()
    runner.runtestprotocol = runtest
    with pytest.raises(ExitCalled, match='forked test item node raised Exit'):
        adapter.forked_run_report('node')


def test_crash_is_delegated_with_original_result(monkeypatch):
    adapter, py, _, _, _ = load(monkeypatch)
    result = SimpleNamespace(retval=None, exitstatus=0, signal=15)
    py.process.ForkedFunc = lambda fn: SimpleNamespace(waitfinish=lambda: result)
    assert adapter.forked_run_report('node') == [('crash', 'node', 15)]


def test_result_payload_uses_forked_serializer(monkeypatch):
    adapter, py, forked, runner, _ = load(monkeypatch)
    execute_in_fake_fork(adapter, py, monkeypatch)
    runner.runtestprotocol = lambda item, log: [object()]
    forked.serialize_report = lambda report: {'when': 'call', 'outcome': 'passed'}
    assert adapter.forked_run_report('node') == [{'when': 'call', 'outcome': 'passed'}]
    assert marshal.loads(marshal.dumps({'when': 'call'})) == {'when': 'call'}


def test_unrelated_exception_is_not_misreported_as_pytest_exit(monkeypatch):
    adapter, py, _, runner, _ = load(monkeypatch)
    execute_in_fake_fork(adapter, py, monkeypatch)
    def runtest(item, log):
        raise ValueError('unexpected')
    runner.runtestprotocol = runtest
    assert adapter.forked_run_report('node') == [('crash', 'node', 0)]