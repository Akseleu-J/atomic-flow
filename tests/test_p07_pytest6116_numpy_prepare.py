"""The wheel audit rejects byte and RECORD substitutions without target imports."""
import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_numpy_prepare as audit  # noqa: E402


def wheel(*, corrupt=False, unsafe=False, plugin=False):
    files = {'numpy/__init__.py': b'publisher bytes',
             'numpy-1.21.6.dist-info/METADATA': b'Name: numpy\nVersion: 1.21.6\nRequires-Python: >=3.7,<3.11\n\n',
             'numpy-1.21.6.dist-info/WHEEL': b'Root-Is-Purelib: false\nTag: cp38-cp38-manylinux_2_12_x86_64\n\n',
             'numpy-1.21.6.dist-info/entry_points.txt': b'[pytest11]\nx = y\n' if plugin else b'[console_scripts]\nf2py = numpy.f2py:main\n'}
    if unsafe:
        files['../escape'] = b'bad'
    entries = []
    for name, value in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=')
        entries.append((name, 'sha256=' + digest, str(len(value))))
    entries.append(('numpy-1.21.6.dist-info/RECORD', '', ''))
    record = io.StringIO(newline='')
    csv.writer(record).writerows(entries)
    if corrupt:
        files['numpy/__init__.py'] = b'changed'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for name, value in files.items():
            z.writestr(name, value)
        z.writestr('numpy-1.21.6.dist-info/RECORD', record.getvalue())
    return output.getvalue()


def bind(monkeypatch, data):
    monkeypatch.setattr(audit, 'SIZE', len(data))
    monkeypatch.setattr(audit, 'WHEEL_SHA256', hashlib.sha256(data).hexdigest())


def test_accepts_verified_publisher_bytes_and_refuses_unbound_changes(monkeypatch):
    raw = wheel()
    bind(monkeypatch, raw)
    result = audit.inspect(raw)
    assert result['publisher_file_count'] == 4
    assert result['ordinary_acceptance'] is False
    assert result['generated_scripts_content_verified'] is False
    with pytest.raises(ValueError, match='digest'):
        audit.inspect(raw + b'x')


@pytest.mark.parametrize('variant, message', [('corrupt', 'RECORD member'), ('unsafe', 'unsafe'), ('plugin', 'pytest plugin')])
def test_refuses_malformed_publisher_claims(monkeypatch, variant, message):
    raw = wheel(**{variant: True})
    bind(monkeypatch, raw)
    with pytest.raises(ValueError, match=message):
        audit.inspect(raw)
