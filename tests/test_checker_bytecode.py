"""A same-size/same-mtime edit must not reuse a previous project's bytecode."""
import os
import py_compile
from pathlib import Path

import pytest

from atomic_flow.agents.execution import run_python
from atomic_flow.config import ExecutionConfig


@pytest.mark.parametrize("cached,current,expected_exit", [(1, 2, 0), (2, 1, 1)])
def test_pytest_checks_current_source_despite_valid_timestamp_bytecode(tmp_path, cached, current, expected_exit):
    source = tmp_path / "value.py"
    source.write_text(f"VALUE = {cached}\n", encoding="utf-8")
    before = source.stat()
    cache = py_compile.compile(str(source), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    cache_bytes = Path(cache).read_bytes()
    source.write_text(f"VALUE = {current}\n", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    (tmp_path / "test_value.py").write_text("from value import VALUE\ndef test_value():\n    assert VALUE == 2\n", encoding="utf-8")
    result = run_python(["-m", "pytest", "-q", "test_value.py"], tmp_path, config=ExecutionConfig(mode="local"))
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert Path(cache).read_bytes() == cache_bytes
