"""Default tool registry wired to the shared tool implementations."""
from __future__ import annotations

from atomic_flow.agents.base import ToolRegistry
from atomic_flow.agents.tools.ask_user import AskUserTool
from atomic_flow.agents.tools.edit_node import EditNodeTool
from atomic_flow.agents.tools.file_ops import CreateFileTool, DeleteFileTool, MoveFileTool, PatchFileTool
from atomic_flow.agents.tools.find_callers import FindCallersTool
from atomic_flow.agents.tools.read_file import ReadFileTool
from atomic_flow.agents.tools.read_node import ReadNodeTool
from atomic_flow.agents.tools.read_result import ReadResultTool
from atomic_flow.agents.tools.run_command import RunCommandTool
from atomic_flow.agents.tools.search_text import SearchTextTool
from atomic_flow.agents.tools.search_tree import SearchTreeTool


def default_registry() -> ToolRegistry:
	registry = ToolRegistry()
	for tool in (ReadNodeTool(), FindCallersTool(), SearchTreeTool(), EditNodeTool(), RunCommandTool(),
                 SearchTextTool(), ReadFileTool(), PatchFileTool(), CreateFileTool(), DeleteFileTool(), MoveFileTool(),
                 AskUserTool(), ReadResultTool()):
		registry.register(tool)
	return registry


ROLE_TOOLS: dict[str, list[str]] = {
	"parser": [],
	"refiner": ["search_tree", "read_node", "read_file", "search_text"],
	"answerer": ["read_node", "find_callers", "search_tree", "edit_node", "run_command", "search_text",
                 "patch_file", "create_file", "delete_file", "move_file", "read_file", "ask_user", "read_result"],
	"critic": ["read_node", "run_command", "read_file", "search_text"],
	"expander": ["search_tree", "read_node", "find_callers", "read_file", "search_text"],
	"reparser": [],
}

for _role in ("refiner", "critic", "expander"):
    ROLE_TOOLS[_role].append("read_result")
