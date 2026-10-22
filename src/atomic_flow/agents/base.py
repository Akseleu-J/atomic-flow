"""Tool protocol and registry.

Tools are functions the model can invoke during a role's turn. They receive a
ToolContext rather than raw project paths and return JSON-serializable data.
"""
from __future__ import annotations

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


class ToolRegistry:
    """Registry used to expose a role's allowed tools to the model."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        if not isinstance(tool.host_contract(), ToolContract):
            raise TypeError("tool contract must be a ToolContract")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "name": self._tools[name].name,
                "description": self._tools[name].description,
                "input_schema": self._tools[name].args_schema.model_json_schema(),
                "host_contract": asdict(self._tools[name].host_contract()),
            }
            for name in names
            if name in self._tools
        ]
