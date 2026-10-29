"""A NumPy smoke cannot promote modified or extra installed bytes."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_numpy_smoke as smoke  # noqa: E402


def test_installed_publisher_inventory_rejects_tampering_and_extras(tmp_path):
    root = tmp_path / 'numpy'
    root.mkdir()
    (root / '__init__.py').write_bytes(b'publisher')
    info = tmp_path / 'numpy-1.21.6.dist-info'
    info.mkdir()
    (info / 'RECORD').write_bytes(b'generated')
    observed = smoke.installed_hashes(tmp_path)
    expected = {smoke.SITE + '/numpy/__init__.py': hashlib.sha256(b'publisher').hexdigest()}
    assert smoke.compare(expected, observed) == [smoke.SITE + '/numpy-1.21.6.dist-info/RECORD']
    with pytest.raises(ValueError, match='publisher bytes'):
        smoke.compare({**expected, smoke.SITE + '/numpy/__init__.py': '0' * 64}, observed)
    (root / 'extra.py').write_bytes(b'extra')
    with pytest.raises(ValueError, match='unexpected'):
        smoke.compare(expected, smoke.installed_hashes(tmp_path))


def test_inventory_rejects_oversized_file_and_native_probe_is_compilable(tmp_path, monkeypatch):
    (tmp_path / 'file').write_bytes(b'12')
    monkeypatch.setattr(smoke, 'MAX_FILE', 1)
    with pytest.raises(ValueError, match='oversized'):
        smoke.installed_hashes(tmp_path)
    compile(smoke.NATIVE_PROBE, '<native-numpy-probe>', 'exec')
