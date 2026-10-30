"""The lsof diagnostic selects exactly two public nodes and grants no admission."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from p07_pytest6116_lsof_focused import NODES, run  # noqa: E402
from p07_pytest6116_lsof_focused import test_source as focused_source


def test_selector_is_fixed_and_complete_suite_is_not_claimed():
    source = focused_source()
    compile(source, '<lsof-focused>', 'exec')
    for node in NODES:
        assert source.count(node) == 1
    assert 'testing/code/test_source.py::test_isparseable' not in source


def test_requires_fresh_linux_output(tmp_path):
    with pytest.raises(ValueError, match='fresh output'):
        run(tmp_path)
