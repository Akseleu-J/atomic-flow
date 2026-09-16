"""Node — a single element of the project tree.

Nodes represent files, classes, functions, and methods. Each node carries
positional info (file, lines), structural info (parent, children), and
derived metrics (fan_in, fan_out, embedding).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


class NodeKind(str, Enum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MODULE = "module"


@dataclass
class Node:
    """A node in the project tree.

    Attributes:
        id: Stable hash-based identifier.
        kind: Node type (file / class / function / method).
        name: Node name (e.g. ``validate`` or ``session.py``).
        file: Relative path to source file.
        start_line: 1-based start line.
        end_line: 1-based end line (inclusive).
        parent_id: Parent node id (None for top-level).
        signature: Source signature (for functions/methods).
        embedding: Optional numpy vector from atomic-core.
        fan_in: How many nodes reference this node.
        fan_out: How many nodes this node references.
        churn_30d: Git churn over last 30 days (0 if unavailable).
    """

    id: str
    kind: NodeKind
    name: str
    file: str
    start_line: int
    end_line: int
    parent_id: str | None = None
    signature: str = ""
    embedding: "np.ndarray | None" = None
    fan_in: int = 0
    fan_out: int = 0
    churn_30d: int = 0

    @classmethod
    def make_id(cls, file: str, path: str) -> str:
        """Build a stable id from file + AST path."""
        raw = f"{file}::{path}".encode()
        return hashlib.sha1(raw).hexdigest()[:16]

    @property
    def span(self) -> int:
        return max(1, self.end_line - self.start_line + 1)

    def __repr__(self) -> str:
        return (
            f"Node({self.kind.value} {self.name} "
            f"@{self.file}:{self.start_line}-{self.end_line})"
        )
