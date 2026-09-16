"""Smoke tests — verify pipeline end-to-end on a tiny fixture."""

from __future__ import annotations

import textwrap
from pathlib import Path

from atomic_flow.config import Settings
from atomic_flow.core.encoder import BM25FallbackEncoder
from atomic_flow.tree.builder import build_tree
from atomic_flow.tree.node import NodeKind


FIXTURE = textwrap.dedent(
    '''
    """Tiny fixture module."""


    class Session:
        """A session."""

        def validate(self, token: str) -> bool:
            return bool(token)

        def refresh(self) -> None:
            pass


    def login_handler(user: str) -> bool:
        return True
    '''
).strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "session.py").write_text(FIXTURE)
    return repo


def test_build_tree_finds_nodes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    tree = build_tree(repo, Settings())

    kinds = {n.kind for n in tree.nodes}
    assert NodeKind.FILE in kinds
    assert NodeKind.CLASS in kinds
    assert NodeKind.METHOD in kinds
    assert NodeKind.FUNCTION in kinds

    names = {n.name for n in tree.nodes}
    assert {"session.py", "Session", "validate", "refresh", "login_handler"} <= names


def test_encoder_returns_matrix(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    tree = build_tree(repo, Settings())

    enc = BM25FallbackEncoder(dim=256)
    texts = [n.signature or n.name for n in tree.nodes]
    matrix = enc.encode(texts)

    assert matrix.shape == (len(tree.nodes), 256)
    # Each row is L2-normalized (or zero for empty text).
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1)
    assert np.all((norms == 0) | (abs(norms - 1.0) < 1e-5))
