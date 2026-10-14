"""Tool protocol and registry.

Tools are functions the model can invoke during a role's turn. They receive a
ToolContext rather than raw project paths and return JSON-serializable data.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from atomic_flow.agents.context import ToolContext


class Tool(ABC):
    """A model-invokable operation with a validated argument schema."""

    name: ClassVar[str]
    description: ClassVar[str]
    args_schema: ClassVar[type[BaseModel]]

    @abstractmethod
    def run(self, args: BaseModel, ctx: ToolContext) -> dict[str, Any]:
        """Return a JSON-serializable result; operational errors become ``error``."""


class ToolRegistry:
    """Registry used to expose a role's allowed tools to the model."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "name": self._tools[name].name,
                "description": self._tools[name].description,
                "input_schema": self._tools[name].args_schema.model_json_schema(),
            }
            for name in names
            if name in self._tools
        ]
