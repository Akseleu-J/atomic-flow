"""Tool protocol and registry.

Tools are functions the model can invoke during a role's turn. They receive a
ToolContext rather than raw project paths and return JSON-serializable data.
"""
from __future__ import annotations

import copy
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from atomic_flow.agents.context import ToolContext


@dataclass(frozen=True)
class ToolContract:
    """Host declarations, never model-supplied capabilities.

    Deadlines are cooperative: tools must call ``ctx.cancel_check`` while doing
    long work; subprocesses must use the managed execution runner. No retry is
    performed automatically, even for an idempotent tool.
    """

    effects: tuple[str, ...] = ("read",)
    timeout_seconds: float = 120
    max_output_bytes: int = 262144
    interaction: bool = False
    idempotent: bool = True
    retryable: bool = False
    cancellation: str = "cooperative"

    def __post_init__(self) -> None:
        if not self.effects or set(self.effects) - {"read", "write", "execute", "interact"}:
            raise ValueError("tool contract has invalid effects")
        if not 0 < self.timeout_seconds <= 3600 or not 256 <= self.max_output_bytes <= 8_388_608:
            raise ValueError("tool contract limits are out of range")
        if self.cancellation != "cooperative":
            raise ValueError("only cooperative tool cancellation is supported")
        if self.retryable and not self.idempotent:
            raise ValueError("retryable tools must declare idempotence")


# Preserve the existing tool subclasses while making their effects explicit.
# Newly registered tools should set their own contract on the subclass.
_BUILTIN_CONTRACTS = {
    "edit_node": ToolContract(effects=("read", "write", "execute"), idempotent=False),
    "run_command": ToolContract(effects=("execute",), idempotent=False),
    "ask_user": ToolContract(effects=("interact",), interaction=True, idempotent=False),
}


class Tool(ABC):
    """A model-invokable operation with a validated argument schema."""

    name: ClassVar[str]
    description: ClassVar[str]
    args_schema: ClassVar[type[BaseModel]]
    contract: ClassVar[ToolContract | None] = None
    result_schema: ClassVar[type[BaseModel] | None] = None

    def host_contract(self) -> ToolContract:
        return self.contract or _BUILTIN_CONTRACTS.get(self.name, ToolContract())

    @abstractmethod
    def run(self, args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        """Return a JSON-serializable result; operational errors become ``error``."""


@dataclass(frozen=True)
class ToolSpecification:
    name: str
    description: str
    args_schema: type[BaseModel]
    contract: ToolContract

    def host_contract(self):
        return self.contract


class ToolRegistry:
    """Registry used to expose a role's allowed tools to the model."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._specs: dict[str, ToolSpecification] = {}
        self._factories: dict = {}
        self._active_schemas: set[str] = set()
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> None:
        if tool.name in self._specs:
            raise ValueError(f"tool already registered: {tool.name}")
        if not isinstance(tool.host_contract(), ToolContract):
            raise TypeError("tool contract must be a ToolContract")
        self._tools[tool.name] = tool
        self._specs[tool.name] = ToolSpecification(tool.name, tool.description, tool.args_schema, tool.host_contract())
        self._active_schemas.add(tool.name)

    def register_lazy(self, name: str, description: str, args_schema: type[BaseModel], contract: ToolContract, factory) -> None:
        """Register host-trusted metadata; factory is not imported/invoked for discovery."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", name) or not callable(factory):
            raise ValueError("Invalid lazy tool registration")
        if not isinstance(contract, ToolContract) or not isinstance(description, str) or not 1 <= len(description) <= 2000:
            raise ValueError("Invalid lazy tool metadata")
        if not isinstance(args_schema, type) or not issubclass(args_schema, BaseModel):
            raise TypeError("Tool arguments require a Pydantic model")
        with self._lock:
            if name in self._specs:
                raise ValueError(f"tool already registered: {name}")
            self._specs[name] = ToolSpecification(name, description, args_schema, contract)
            self._factories[name] = factory

    def specification(self, name: str) -> ToolSpecification | None:
        return self._specs.get(name)

    def get(self, name: str) -> Tool | None:
        with self._lock:
            if name in self._tools or name not in self._factories:
                return self._tools.get(name)
            spec = self._specs[name]
            tool = self._factories[name]()
            if (not isinstance(tool, Tool) or tool.name != spec.name or tool.description != spec.description
                    or tool.host_contract() != spec.contract or tool.args_schema.model_json_schema() != spec.args_schema.model_json_schema()):
                raise ValueError("Loaded tool does not match the host-registered metadata")
            self._tools[name] = tool
            return tool

    def schemas(self, names: list[str], *, include_deferred: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "name": self._specs[name].name,
                "description": self._specs[name].description,
                "input_schema": copy.deepcopy(self._specs[name].args_schema.model_json_schema()),
                "host_contract": asdict(self._specs[name].host_contract()),
            }
            for name in names
            if name in self._specs and (include_deferred or name in self._active_schemas)
        ]

    def discover(self, query: str, names: list[str], *, limit=5, include_schema=False) -> list[dict]:
        if not 1 <= limit <= 20 or len(query) > 200:
            raise ValueError("Invalid discovery limits")
        terms = query.casefold().split()
        candidates = []
        for name in sorted(set(names) & self._specs.keys()):
            spec = self._specs[name]
            text = (name + " " + spec.description).casefold()
            score = sum(term in text for term in terms) + 4 * (query.casefold() == name.casefold())
            if not terms or score:
                candidates.append((-score, name))
        selected = [name for _, name in sorted(candidates)[:limit]]
        if include_schema:
            self._active_schemas.update(selected)
            return self.schemas(selected)
        return [{"name": name, "description": self._specs[name].description,
                 "schema_active": name in self._active_schemas, "implementation_loaded": name in self._tools}
                for name in selected]
