"""Layered, deny-first approval rules for model-invoked tools."""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
    path_matches: str | None = None


@dataclass
class Decision:
    action: Action
    rule: Rule | None = None
    reason: str = ""
    source: str = "default"


READ_ONLY_TOOLS = {"read_node", "find_callers", "search_tree", "search_text", "read_result", "read_file", "find_files", "read_many", "tool_catalog",
                   "notebook_read", "workspace_read", "lsp_definition", "lsp_references", "lsp_diagnostics", "docs_fetch", "docs_search"}


class PolicyEngine:
    """Every matching denial wins across every layer, regardless of rule order.

    Otherwise the first matching rule in each layer applies, and ask beats auto
    across layers. Host defaults apply if no configured layer matches. Project
    rules cannot weaken an explicit host/user rule.
    """

    def __init__(self, rules: list[Rule] | None = None, *, host_rules: list[Rule] | None = None,
                 user_rules: list[Rule] | None = None, project_rules: list[Rule] | None = None,
                 session_rules: list[Rule] | None = None):
        self.layers = {
            "host": [Rule("run_command", "forbid", command_matches=r"rm\s+-rf|sudo|curl|wget|chmod")]
                    + list(host_rules or []),
            "user": list(user_rules or []) + list(rules or []),
            "project": list(project_rules or []),
            "session": list(session_rules or []),
        }
        self.rules = [rule for ruleset in self.layers.values() for rule in ruleset]

    @staticmethod
    def _default_rules() -> list[Rule]:
        return [
            Rule(tool="|".join(sorted(READ_ONLY_TOOLS)), action="auto"),
            Rule(tool="ask_user", action="auto"),
            Rule(tool="edit_node", when="trusted", lines_lt=20, action="auto"),
            Rule(tool="edit_node", action="ask"),
            Rule(tool="run_command", command_matches=r"^(pytest|ruff|mypy|py_compile)(\s|$)", action="auto"),
        ]

    @staticmethod
    def _read_rules(path: Path) -> list[Rule]:
        if not path.is_file():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
                raise ValueError("policy must contain a rules list")
            rules = []
            for raw in data.get("rules", []):
                if not isinstance(raw, dict) or not isinstance(raw.get("match", {}), dict):
                    raise ValueError("policy rule must be an object")
                match = raw.get("match", {})
                action = raw.get("action", "ask")
                if action not in {"auto", "ask", "forbid"}:
                    raise ValueError("invalid policy action")
                pattern = match.get("command_matches")
                if pattern is not None:
                    re.compile(pattern)
                when = match.get("when")
                if when not in {None, "trusted", "untrusted"}:
                    raise ValueError("invalid trust condition")
                lines = match.get("lines_lt")
                if lines is not None and (type(lines) is not int or lines < 0):
                    raise ValueError("invalid line limit")
                scope = match.get("path_matches")
                if scope is not None and (not isinstance(scope, str) or not scope):
                    raise ValueError("invalid path scope")
                tool = match.get("tool", "")
                if not isinstance(tool, str) or not tool:
                    raise ValueError("invalid tool match")
                rules.append(Rule(tool, action, when, lines, pattern, scope))
            return rules
        except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, re.error):
            # A broken policy must not silently turn a denial into permission.
            return [Rule("*", "forbid")]

    @classmethod
    def load(cls, project_root: Path, *, user_path: Path | None = None,
             host_rules: list[Rule] | None = None, session_rules: list[Rule] | None = None,
             project_trusted: bool = True) -> PolicyEngine:
        return cls(host_rules=host_rules, session_rules=session_rules,
                   user_rules=cls._read_rules(user_path) if user_path else [],
                   project_rules=cls._read_rules(project_root / ".atomic_flow" / "policy.yaml") if project_trusted else [])

    def decide(self, tool_name: str, args: dict[str, Any], *, project_trusted: bool) -> Decision:
        decision = self._decide(tool_name, args, project_trusted=project_trusted)
        if tool_name == "patch_files" and isinstance(args.get("patches"), list) and 1 <= len(args["patches"]) <= 16:
            # A batch cannot bypass existing explicit single-file deny/ask.
            # An explicit batch allow may supersede only the single-file default.
            items = [self._decide("patch_file", patch, project_trusted=project_trusted)
                     for patch in args["patches"] if isinstance(patch, dict)]
            decisions = [decision, *(item for item in items if item.source != "default")]
            return next((item for item in decisions if item.action == "forbid"),
                        next((item for item in decisions if item.action == "ask"), decision))
        return decision

    def _decide(self, tool_name: str, args: dict[str, Any], *, project_trusted: bool) -> Decision:
        matches: list[tuple[str, Rule]] = []
        for source, rules in self.layers.items():
            matching = [rule for rule in rules if self._tool_matches(rule.tool, tool_name)
                        and self._context_matches(rule, args, project_trusted)]
            for rule in matching:
                if rule.action == "forbid":
                    return self._decision(source, rule)
            if matching:
                matches.append((source, matching[0]))
        if matches:
            source, rule = next(((source, rule) for source, rule in matches if rule.action == "ask"), matches[0])
            return self._decision(source, rule)
        for rule in self._default_rules():
            if self._tool_matches(rule.tool, tool_name) and self._context_matches(rule, args, project_trusted):
                return self._decision("default", rule)
        return Decision("ask", reason="default: no matching rule; approval required", source="default")

    def explain(self, tool_name: str, args: dict[str, Any], *, project_trusted: bool) -> dict:
        """Offline decision trace; never invokes a callback or an actual tool."""
        decision = self.decide(tool_name, args, project_trusted=project_trusted)
        matches = []
        selected = False
        for source, rules in [*self.layers.items(), ("default", self._default_rules())]:
            for index, rule in enumerate(rules):
                if self._tool_matches(rule.tool, tool_name) and self._context_matches(rule, args, project_trusted):
                    choose = not selected and source == decision.source and rule == decision.rule
                    matches.append({"source": source, "index": index, "tool": rule.tool, "action": rule.action,
                                    "selected": choose})
                    selected |= choose
        result = {"tool": tool_name, "project_trusted": project_trusted, "action": decision.action,
                  "source": decision.source, "reason": decision.reason, "matches": matches,
                  "semantics": "All matching forbids win; otherwise first per layer, ask wins across layers. No tool was run."}
        if tool_name == "patch_files" and isinstance(args.get("patches"), list) and 1 <= len(args["patches"]) <= 16:
            result["batch_items"] = [{"path": patch.get("path"), **self.explain("patch_file", patch, project_trusted=project_trusted)}
                                     for patch in args["patches"] if isinstance(patch, dict)]
            result["semantics"] += " Batch decisions also preserve every explicit single-file deny/ask."
        return result

    def lint(self) -> list[dict]:
        """Conservative diagnostics only for identical predicates, not regex satisfiability."""
        findings = []
        previous = []
        for source, rules in self.layers.items():
            for index, rule in enumerate(rules):
                for old_source, old_index, old in previous:
                    if (old.when, old.lines_lt, old.command_matches, old.path_matches) != (
                        rule.when, rule.lines_lt, rule.command_matches, rule.path_matches
                    ):
                        continue
                    old_tools, tools = set(old.tool.split("|")), set(rule.tool.split("|"))
                    subset = "*" in old_tools or tools <= old_tools
                    overlap = subset or "*" in tools or bool(old_tools & tools)
                    code = None
                    if old_source == source and subset and old.action == rule.action:
                        code = "duplicate_or_redundant"
                    elif overlap and "forbid" in {old.action, rule.action} and old.action != rule.action:
                        code = "deny_overrides_allow"
                    elif old_source == source and subset and rule.action != "forbid":
                        code = "shadowed_by_first_match"
                    if code:
                        findings.append({"code": code, "source": source, "index": index,
                                         "other_source": old_source, "other_index": old_index})
                previous.append((source, index, rule))
        return findings

    @staticmethod
    def _decision(source: str, rule: Rule) -> Decision:
        scope = f" (paths: {rule.path_matches})" if rule.path_matches else ""
        return Decision(rule.action, rule, f"{source}: matched {rule.tool} -> {rule.action}{scope}", source)

    @staticmethod
    def _tool_matches(pattern: str, tool_name: str) -> bool:
        return bool(pattern) and any(part.strip() in {"*", tool_name} for part in pattern.split("|"))

    @staticmethod
    def _context_matches(rule: Rule, args: dict[str, Any], trusted: bool) -> bool:
        if rule.when == "trusted" and not trusted or rule.when == "untrusted" and trusted:
            return False
        patches = args.get("patches")
        if patches is not None and (not isinstance(patches, list) or not 1 <= len(patches) <= 16
                                    or any(not isinstance(patch, dict) or not isinstance(patch.get("path"), str)
                                           or not patch["path"] for patch in patches)):
            return rule.action == "forbid"
        if rule.lines_lt is not None:
            lines = (sum(len(str(patch.get("new_text", "")).splitlines()) for patch in patches)
                     if patches is not None else len(str(args.get("new_source", args.get("new_text", ""))).splitlines()))
            if lines >= rule.lines_lt:
                return False
        if rule.command_matches is not None:
            try:
                if not re.search(rule.command_matches, str(args.get("command", "")), re.IGNORECASE):
                    return False
            except (re.error, TypeError):
                return rule.action == "forbid"
        if rule.path_matches is not None:
            paths = [str(args[key]).replace("\\", "/") for key in (
                "path", "source", "destination", "source_path", "destination_path", "old_path", "new_path", "target"
            ) if isinstance(args.get(key), str) and args[key]]
            if patches is not None:
                paths.extend(patch["path"].replace("\\", "/") for patch in patches)
            if not paths:
                return False

            def safe(path: str) -> bool:
                return not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts and ":" not in path

            pattern = rule.path_matches.casefold() if os.name == "nt" else rule.path_matches
            matched = [safe(path) and fnmatch.fnmatchcase(path.casefold() if os.name == "nt" else path, pattern)
                       for path in paths]
            if not (any(matched) if rule.action == "forbid" else all(matched)):
                return False
        return True
