"""Host-supplied graph security namespace; graph IDs are local within this scope."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from atomic_flow.graph.schema import GraphContractError


@dataclass(frozen=True)
class GraphIdentity:
    principal_id: str
    workspace_id: str
    project_id: str
    root_id: str
    policy_version: str

    def __post_init__(self):
        for value in self.__dict__.values():
            if not isinstance(value, str) or not value or len(value) > 256 or any(c in value for c in "\x00\r\n"):
                raise GraphContractError("invalid host graph identity")

    def namespace(self):
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class GraphReadContext:
    identity: GraphIdentity
    grants: frozenset[tuple[str, str, str]]

    def __post_init__(self):
        if (not isinstance(self.identity, GraphIdentity) or not isinstance(self.grants, frozenset)
                or any(not isinstance(g, tuple) or len(g) != 3 or any(not isinstance(p, str) or not p for p in g)
                       for g in self.grants)):
            raise GraphContractError("graph grants require immutable trusted root tuples")

    def require(self, expected: GraphIdentity):
        if not isinstance(self.identity, GraphIdentity) or self.identity != expected:
            raise GraphContractError("graph principal/project/root/policy binding differs")
        if (expected.workspace_id, expected.project_id, expected.root_id) not in self.grants:
            raise GraphContractError("graph root is outside host grants")
