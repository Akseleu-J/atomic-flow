"""The lsof probe validates bytes and cannot claim historical test acceptance."""
import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_lsof_probe import PROBE, publisher_binary, run  # noqa: E402


def archive(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as stream:
        for name, body in entries:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            stream.addfile(info, io.BytesIO(body))
    return result.getvalue()


def test_debian_payload_demands_exactly_one_regular_publisher_binary():
    assert publisher_binary(archive([('./usr/bin/lsof', b'publisher')])) == b'publisher'
    with pytest.raises(ValueError, match='exact bounded'):
        publisher_binary(archive([('./usr/bin/other', b'x')]))
    with pytest.raises(ValueError, match='exact bounded'):
        publisher_binary(archive([('./usr/bin/lsof', b'x'), ('usr/bin/lsof', b'y')]))


def test_probe_requires_fresh_output_and_compiles_without_running_target_code(tmp_path):
    compile(PROBE, '<isolated-lsof-probe>', 'exec')
    with pytest.raises(ValueError, match='fresh Linux output'):
        run(tmp_path)
