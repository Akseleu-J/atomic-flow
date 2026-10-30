"""Full selection and source-bound wrapper verification are fail-closed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_combined_public as combined  # noqa: E402


def test_full_source_keeps_public_root_and_records_xpass_and_plugins(monkeypatch):
    code = combined.combined_source()
    compile(code, '<combined-container>', 'exec')
    assert "'-q','--tb=line','-o','cache_dir=/tmp/p07-pytest-cache',\n 'testing']" in code
    assert 'testing/code/test_source.py::test_isparseable' not in code
    assert "'plugins':r.plugins,'xpasses':r.xpasses" in code
    monkeypatch.setattr(combined.py38, 'target_source', lambda _: 'unreviewed')
    with pytest.raises(ValueError, match='fixed full public source'):
        combined.combined_source()


def test_five_wrappers_are_compared_as_raw_bytes_and_entrypoints(tmp_path, monkeypatch):
    expected = combined.wrapper_verify.ENTRY_POINTS
    generated = {name: {'entry_point': target, 'kind': 'pip_generated_console_script', 'expected_sha256': None}
                 for name, target in expected.items()}
    monkeypatch.setattr(combined.relocated, 'publisher_wheel_plan', lambda raw, row: {})
    monkeypatch.setattr(combined.relocated, 'merge_plans', lambda plans: {'generated_console_scripts': generated})
    context = tmp_path / 'context'
    context.mkdir()
    (context / 'example.whl').write_bytes(b'wheel')
    installed = tmp_path / 'installed'
    for name, target in expected.items():
        path = installed / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(combined.wrapper_verify.expected_bytes(target))
    profile = {'wheels': [{'filename': 'example.whl'}]}
    result = combined.verify_fixed_wrappers(context, profile, installed)
    assert result['wrappers_verified'] == 5
    assert result['pip_installed_source_verified'] is False
    (installed / '/usr/local/bin/pytest'.lstrip('/')).write_bytes(b'different')
    with pytest.raises(ValueError, match='wrapper bytes differ'):
        combined.verify_fixed_wrappers(context, profile, installed)
    generated.pop('/usr/local/bin/nosetests')
    with pytest.raises(ValueError, match='inventory differs'):
        combined.verify_fixed_wrappers(context, profile, installed)
