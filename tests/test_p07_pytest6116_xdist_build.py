"""The offline build receipt refuses changed source bytes or plugin metadata."""
import base64
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_xdist_build as build  # noqa: E402


def wheel(*, boxed=b'public boxed', plugins=None, tamper=False):
    info = 'pytest_xdist-1.18.2.dist-info'
    if plugins is None:
        plugins = '[pytest11]\nxdist = xdist.plugin\nxdist.boxed = xdist.boxed\nxdist.looponfail = xdist.looponfail\n'
    files = {'xdist/boxed.py': boxed, info + '/entry_points.txt': plugins.encode(),
             info + '/METADATA': b'Name: pytest-xdist\nVersion: 1.18.2\nRequires-Dist: execnet (>=1.1)\nRequires-Dist: pytest (>=3.0.0)\n\n'}
    rows = [(name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), str(len(data)))
            for name, data in files.items()]
    rows.append((info + '/RECORD', '', ''))
    out = io.StringIO(newline='')
    csv.writer(out).writerows(rows)
    if tamper:
        files['xdist/boxed.py'] = b'tampered'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr(info + '/RECORD', out.getvalue())
    return buf.getvalue()


def test_wheel_binds_source_plugin_and_every_record_member(monkeypatch):
    monkeypatch.setattr(build, 'EXPECTED_BOXED', hashlib.sha256(b'public boxed').hexdigest())
    assert build.check_wheel(wheel())['version'] == '1.18.2'
    with pytest.raises(ValueError, match='boxed.py'):
        build.check_wheel(wheel(boxed=b'other'))
    with pytest.raises(ValueError, match='plugin metadata'):
        build.check_wheel(wheel(plugins='[pytest11]\nxdist = xdist.plugin\n'))
    with pytest.raises(ValueError, match='RECORD'):
        build.check_wheel(wheel(tamper=True))


def test_target_source_is_statically_compilable_and_pinned():
    compile(build.EXTRACT_AND_BUILD, '<isolated-build>', 'exec')
    assert build.source.SOURCE_SHA256 in build.EXTRACT_AND_BUILD
    assert '/opt/p07-buildtools' in build.EXTRACT_AND_BUILD
