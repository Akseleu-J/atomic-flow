"""Declarative approval rules for model-invoked tools."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Action = Literal["auto", "ask", "forbid"]


@dataclass
class Rule:
    tool: str
    action: Action
    when: str | None = None
    lines_lt: int | None = None
    command_matches: str | None = None


@dataclass
class Decision:
    action: Action
    rule: Rule | None = None
    reason: str = ""


READ_ONLY_TOOLS = {"read_node", "find_callers", "search_tree"}


class PolicyEngine:
    def __init__(self, rules: list[Rule] | None = None):
        self.rules = rules if rules is not None else self._default_rules()

    @staticmethod
    def _default_rules() -> list[Rule]:
        return [
            Rule(tool="|".join(sorted(READ_ONLY_TOOLS)), action="auto"),
            Rule(tool="edit_node", when="trusted", lines_lt=20, action="auto"),
            Rule(tool="edit_node", when="trusted", action="ask"),
            Rule(tool="edit_node", action="ask"),
            Rule(tool="run_command", command_matches="pytest|ruff|mypy|py_compile", action="auto"),
            Rule(tool="run_command", command_matches="rm -rf|sudo|curl|wget|chmod", action="forbid"),
            Rule(tool="run_command", action="ask"),
        ]

    @classmethod
    def load(cls, project_root: Path) -> "PolicyEngine":
        path = project_root / ".atomic_flow" / "policy.yaml"
        if not path.is_file():
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, UnicodeError, yaml.YAMLError):
            return cls()
        rules: list[Rule] = []
        for raw in data.get("rules", []):
            if not isinstance(raw, dict):
                continue
            match = raw.get("match", {}) or {}
            if not isinstance(match, dict):
                continue
            action = raw.get("action", "ask")
            if action not in {"auto", "ask", "forbid"}:
                action = "ask"
            lines_lt = match.get("lines_lt")
            try:
                lines_lt = int(lines_lt) if lines_lt is not None else None
            except (TypeError, ValueError):
                lines_lt = None
            rules.append(Rule(tool=str(match.get("tool", "")), action=action,
                              when=match.get("when"), lines_lt=lines_lt,
                              command_matches=match.get("command_matches")))
        return cls(rules)

    def decide(self, tool_name: str, args: dict[str, Any], *, project_trusted: bool) -> Decision:
        for rule in self.rules:
            if not self._tool_matches(rule.tool, tool_name):
                continue
            if not self._context_matches(rule, args, project_trusted):
                continue
            return Decision(rule.action, rule, f"matched rule: {rule.tool} -> {rule.action}")
        if tool_name in READ_ONLY_TOOLS:
            return Decision("auto", reason="read-only default")
        return Decision("ask", reason="no matching rule; safe default")

    @staticmethod
    def _tool_matches(pattern: str, tool_name: str) -> bool:
        return bool(pattern) and tool_name in {part.strip() for part in pattern.split("|")}

    @staticmethod
    def _context_matches(rule: Rule, args: dict[str, Any], trusted: bool) -> bool:
        if rule.when == "trusted" and not trusted:
            return False
        if rule.when == "untrusted" and trusted:
            return False
        if rule.lines_lt is not None and len(str(args.get("new_source", "")).splitlines()) >= rule.lines_lt:
            return False
        if rule.command_matches is not None:
            if not re.search(rule.command_matches, str(args.get("command", "")), re.IGNORECASE):
                return False
        return True
