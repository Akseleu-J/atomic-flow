"""The xdist candidate fails closed on changes to its plugin and module surface."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_xdist134_prepare as candidate  # noqa: E402


def fake_xdist(boxed=False, plugin='xdist.plugin'):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('xdist/__init__.py', '')
        if boxed:
            archive.writestr('xdist/boxed.py', '')
        archive.writestr('pytest_xdist-1.34.0.dist-info/entry_points.txt',
                         f'[pytest11]\nxdist = {plugin}\nxdist.looponfail = xdist.looponfail\n')
    return output.getvalue()


def test_candidate_rejects_unexpected_boxed_module_or_plugin(tmp_path, monkeypatch):
    item = json.loads(candidate.PROFILE.read_bytes())['wheels'][0]
    monkeypatch.setattr(candidate.audit, 'inspect_wheel', lambda raw, row: {'name': row['name']})
    with pytest.raises(ValueError, match='boxed'):
        candidate.inspect_new_wheel(fake_xdist(boxed=True), item, tmp_path)
    with pytest.raises(ValueError, match='plugin surface'):
        candidate.inspect_new_wheel(fake_xdist(plugin='other.plugin'), item, tmp_path)
    result = candidate.inspect_new_wheel(fake_xdist(), item, tmp_path)
    assert result['pytest11'] == candidate.PLUGIN_MAPS['pytest-xdist']


def test_profile_and_existing_output_fail_before_download(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='fresh'):
        candidate.prepare(tmp_path)
    bad = tmp_path / 'bad.json'
    bad.write_text('{}')
    monkeypatch.setattr(candidate, 'PROFILE', bad)
    with pytest.raises(ValueError, match='profile digest'):
        candidate.prepare(tmp_path / 'new')
