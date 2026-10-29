"""The complete diagnostic must run the public suite, not only focused probes."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_py38_repro import FULL_PROFILE, PEXPECT_PROFILE, TARGET, target_source  # noqa: E402


def test_full_diagnostic_selects_public_test_root():
    assert target_source(False) == TARGET
    full = target_source(True)
    assert "'-q','--tb=line','-o','cache_dir=/tmp/p07-pytest-cache',\n 'testing']" in full
    assert 'testing/code/test_source.py::test_isparseable' not in full
    assert 'PYTEST_ADDOPTS' not in full
    assert "'nonpassing':r.nonpassing" in full
    compile(full, '<public-test-diagnostic>', 'exec')


def test_pexpect_profile_only_adds_the_locked_dependency_pair():
    base = json.loads(FULL_PROFILE.read_bytes())
    candidate = json.loads(PEXPECT_PROFILE.read_bytes())
    assert candidate['python_image'] == base['python_image']
    assert candidate['source_image'] == base['source_image']
    assert candidate['wheels'][:len(base['wheels'])] == base['wheels']
    assert [(x['name'], x['version']) for x in candidate['wheels'][len(base['wheels']):]] == [
        ('pexpect', '4.7.0'), ('ptyprocess', '0.6.0')
    ]
