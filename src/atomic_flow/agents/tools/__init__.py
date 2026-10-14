"""Default tool registry wired to the shared tool implementations."""
from __future__ import annotations

from atomic_flow.agents.base import ToolRegistry
from atomic_flow.agents.tools.find_callers import FindCallersTool
from atomic_flow.agents.tools.read_node import ReadNodeTool
from atomic_flow.agents.tools.search_tree import SearchTreeTool


def default_registry() -> ToolRegistry:
	registry = ToolRegistry()
	for tool in (ReadNodeTool(), FindCallersTool(), SearchTreeTool()):
		registry.register(tool)
	return registry


ROLE_TOOLS: dict[str, list[str]] = {
	"parser": [],
	"refiner": ["search_tree", "read_node"],
	"answerer": ["read_node", "find_callers", "search_tree"],
	"critic": ["read_node"],
	"expander": ["search_tree", "read_node", "find_callers"],
	"reparser": [],
}
