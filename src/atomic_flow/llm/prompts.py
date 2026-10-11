"""Built-in role prompts and layered prompt resolution."""

from __future__ import annotations

from pathlib import Path

from atomic_flow.config import project_dir, user_dir

BUILTINS = {
    "parser": "Classify the developer's request. Return JSON matching the schema. Use task_type, target_scope, focus_terms, and answer_format.",
    "refiner": "Select useful node IDs from the supplied candidate list. Return JSON matching the schema. Never invent IDs.",
    "answerer": "Answer using only the project context. Return JSON matching the schema. Cite project-specific claims with supplied node_ids. State when evidence is missing.",
    "critic": "Check each claim against the supplied context and valid node IDs. Return JSON matching the schema. Mark unsupported claims and request missing context where needed.",
    "expander": "Select additional candidate node IDs that address the critique. Return JSON matching the schema. Do not repeat selected IDs.",
    "reparser": "Reconsider the request using the critique. Return JSON matching the schema and do not repeat an explicitly rejected interpretation.",
}


class PromptRegistry:
    """Resolve request > project > user > builtin prompt text."""

    def __init__(self, project: str | Path, request_prompts: dict[str, str] | None = None):
        self.project = Path(project).expanduser().resolve()
        self.request_prompts = request_prompts or {}
        self.sources: dict[str, str] = {}

    def get(self, role: str) -> str:
        if role not in BUILTINS:
            raise ValueError(f"Unknown prompt role: {role}")
        if role in self.request_prompts:
            self.sources[role] = "request"
            return self.request_prompts[role]
        for base, label in ((project_dir(self.project) / "prompts", "project"), (user_dir() / "prompts", "user")):
            for suffix in (".md", ".txt"):
                path = base / f"{role}{suffix}"
                if path.is_file():
                    self.sources[role] = label
                    return path.read_text(encoding="utf-8").strip()
        self.sources[role] = "builtin"
        return BUILTINS[role]

    @staticmethod
    def roles_for_mode(mode: str) -> list[str]:
        return {"simple": ["answerer"], "medium": ["parser", "answerer", "critic"],
                "hard": ["parser", "refiner", "answerer", "critic", "expander", "reparser"]}[mode]
