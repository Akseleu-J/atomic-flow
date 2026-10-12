import os
import time

from atomic_flow.activation.text import split_identifier, stem, terms
from atomic_flow.textutil import split_lines
from atomic_flow.tree.builder import build_tree, diff_trees, load_tree, refresh_tree, save_tree

SRC = '''"""mod a"""
from b import helper
import pkg.sub as sub


class K:
    @property
    def x(self): return 1

    @x.setter
    def x(self, v): pass

    def run(self,
            a: int,
            b: str = "z") -> int:
        return helper(a)


def get(): pass
'''


def make(tmp_path):
    (tmp_path / "a.py").write_text(SRC)
    (tmp_path / "b.py").write_text("def helper(a):\n    return a\n\ndef get():\n    return 1\n")
    return tmp_path


def by_id(tree):
    return {n["node_id"]: n for n in tree["nodes"]}


def test_identifier_splitting_and_stemming():
    assert split_identifier("is_refundable") == ["is", "refundable"]
    assert split_identifier("parseJSONFile") == ["parse", "json", "file"]
    assert stem("refundable") == stem("refunds") == stem("refund")
    assert "is_refundable" in terms("is_refundable") and stem("refundable") in terms("is_refundable")
    assert "the" not in terms("the discount")


def test_splitlines_matches_python_tokenizer():
    text = "a = 1\x0c\nb = 2\n"                       # form feed: str.splitlines() would give 3 lines
    assert len(text.splitlines()) == 3 and split_lines(text) == ["a = 1\x0c", "b = 2"]


def test_signature_property_setters_and_edges(tmp_path):
    tree = build_tree(make(tmp_path))
    nodes = by_id(tree)
    assert "a.py::K.run" in nodes and nodes["a.py::K.run"]["signature"].startswith("def run(self, a: int, b: str='z')")
    assert "a.py::K.x" in nodes and "a.py::K.x#2" in nodes          # no id collision
    assert nodes["a.py::K.run"]["callees"] == ["b.py::helper"]      # cross-file edge via the import
    assert nodes["b.py::helper"]["fan_in"] == 1
    assert nodes["a.py::get"]["fan_in"] == 0                         # ambiguous bare name `get` makes no edge


def test_incremental_refresh_sees_body_edits(tmp_path):
    root = make(tmp_path)
    save_tree(root, build_tree(root))
    old = load_tree(root)
    time.sleep(0.01)
    (root / "b.py").write_text("def helper(a):\n    return a + 1\n\ndef get():\n    return 1\n")
    os.utime(root / "b.py", None)
    new, changed = refresh_tree(root)
    assert changed and new["stats"]["parsed"] == 1 and new["stats"]["reused"] == 1
    assert diff_trees(old, new)["modified"] == ["b.py"]              # node-id diff (v0.1) would say "0 changed"


def test_gitignore_and_defaults(tmp_path):
    root = make(tmp_path)
    (root / ".gitignore").write_text("secret.py\n")
    (root / "secret.py").write_text("x = 1\n")
    (root / "poetry.lock").write_text("x")
    paths = {n["path"] for n in build_tree(root)["nodes"]}
    assert "secret.py" not in paths and "poetry.lock" not in paths


def test_unparseable_python_is_kept_as_file(tmp_path):
    (tmp_path / "bad.py").write_text("def (:\n")
    tree = build_tree(tmp_path)
    assert tree["nodes"][0]["summary"].startswith("(unparseable")
