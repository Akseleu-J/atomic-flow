import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def module():
    path = Path(__file__).resolve().parents[1] / 'tools' / 'evaluation_provenance.py'
    spec = importlib.util.spec_from_file_location('evaluation_provenance_test', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_provenance_detects_same_size_source_change_and_records_dirty_state(tmp_path, monkeypatch):
    value = module()
    (tmp_path / 'src').mkdir()
    (tmp_path / 'tools').mkdir()
    source = tmp_path / 'src' / 'sample.py'
    source.write_bytes(b'x=1\n')
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="test"\n')
    def git(command, **kwargs):
        return SimpleNamespace(stdout='a' * 40 if 'rev-parse' in command else ' M src/sample.py\n')
    monkeypatch.setattr(value.subprocess, 'run', git)
    before = value.source_state(tmp_path)
    source.write_bytes(b'x=2\n')
    after = value.source_state(tmp_path)
    assert before['git_head'] == after['git_head'] == 'a' * 40
    assert before['tracked_runtime_dirty'] and after['tracked_runtime_dirty']
    assert before['source_manifest_sha256'] != after['source_manifest_sha256']
    assert before['source_files_sha256']['src/sample.py'] != after['source_files_sha256']['src/sample.py']


def test_runtime_metadata_does_not_capture_credentials(monkeypatch):
    value = module()
    monkeypatch.setenv('MODEL_PROXY_API_KEY', 'sensitive-test-value-never-record')
    monkeypatch.setenv('GITHUB_TOKEN', 'another-sensitive-test-value')
    result = value.runtime_state()
    encoded = json.dumps(result)
    assert 'sensitive-test-value' not in encoded
    assert not result['environment_values_recorded']
    assert result['python_version'] and result['packages']
