"""Source archives and dependency preparation do not execute xdist on host."""
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_xdist_prepare as xdist  # noqa: E402


def archive(extra=None):
    files = {'xdist/boxed.py': b'def sample(): pass\n', 'xdist/_version.py': b"version = '1.18.2'\n",
             'pytest_xdist.egg-info/requires.txt': b'execnet>=1.1\npytest>=3.0.0\n',
             'pytest_xdist.egg-info/entry_points.txt': b'[pytest11]\nxdist = xdist.plugin\n'
             b'xdist.boxed = xdist.boxed\nxdist.looponfail = xdist.looponfail\n'}
    if extra:
        files.update(extra)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as stream:
        for name, data in files.items():
            entry = tarfile.TarInfo(xdist.PREFIX + name)
            entry.size = len(data)
            stream.addfile(entry, io.BytesIO(data))
    return out.getvalue()


def test_exact_source_digest_and_runtime_metadata_are_bound(monkeypatch):
    raw = archive()
    monkeypatch.setattr(xdist, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest())
    result = xdist.inspect_source(raw)
    assert result['requirements'] == ['execnet>=1.1', 'pytest>=3.0.0']
    assert result['pytest11']['xdist.boxed'] == 'xdist.boxed'
    assert result['target_imports'] is False and result['source_extracted'] is False
    with pytest.raises(ValueError, match='digest'):
        xdist.inspect_source(raw + b'tampered')


def test_source_path_escape_is_rejected_even_if_digest_matches(monkeypatch):
    raw = archive({'../escape': b'bad'})
    monkeypatch.setattr(xdist, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='escapes'):
        xdist.inspect_source(raw)


def test_link_member_is_rejected_even_if_digest_matches(monkeypatch):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as stream:
        member = tarfile.TarInfo(xdist.PREFIX + 'link')
        member.type = tarfile.SYMTYPE
        member.linkname = '../outside'
        stream.addfile(member)
    raw = out.getvalue()
    monkeypatch.setattr(xdist, 'SOURCE_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='links'):
        xdist.inspect_source(raw)


def test_existing_output_fails_without_network(tmp_path):
    with pytest.raises(ValueError, match='fresh output'):
        xdist.prepare(tmp_path)
