"""Simple, medium and hard request flows with per-request audit artifacts."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from atomic_flow.activation.activate import ActivationIndex, SearchResult, materialize
from atomic_flow.backends.base import ChatResult, LLMBackend
from atomic_flow.budget import SpendLedger
from atomic_flow.config import AtomicConfig, ConversationConfig, is_trusted, user_dir
from atomic_flow.errors import ResponseFormatError
from atomic_flow.llm.history import RequestHistory
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.llm.schemas import Answer, AnswerCritique, AnswerResult, ContextPlan, QueryIntent
from atomic_flow.llm.verify import grounding_score, unknown_identifiers
from atomic_flow.tree.builder import load_tree, refresh_tree
from atomic_flow.tree.node import TreeNode

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")          # request_id becomes a directory name
_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_CACHE_LOCK = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_INDEX_CACHE: OrderedDict[tuple[str, str], ActivationIndex] = OrderedDict()
_ACTION_VERBS = re.compile(
    r"\b("
    r"rename|renaming|renamed|"
    r"add|adding|added|insert|inserting|"
    r"remove|removing|removed|delete|deleting|deleted|"
    r"change|changing|changed|modify|modifying|modified|"
    r"update|updating|updated|patch|patching|patched|"
    r"fix|fixing|fixed|refactor|refactoring|refactored|"
    r"implement|implementing|implemented|"
    r"write|writing|written|"
    r"переимен|добав|удал|измени|обнови|исправ|рефактор|реализ"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_action(query: str) -> bool:
    """Deterministic action detector used as a floor for the LLM-based Parser."""
    return bool(_ACTION_VERBS.search(query))


def project_lock(project: Path) -> threading.Lock:
    """Return the lock shared by refreshes for one project."""
    key = str(project)
    with _CACHE_LOCK:
        return _PROJECT_LOCKS.setdefault(key, threading.Lock())


def choose_mode(query: str, configured_mode: str, *, explicit_mode: str | None = None,
                auto_mode: bool = True, file_scope: str | None = None) -> str:
    """Choose an initial mode before asking the user to approve a costly hard run."""
    if explicit_mode:
        return explicit_mode
    if not auto_mode:
        return configured_mode
    text = query.casefold()
    if re.search(
        r"\b(refactor\w*|migrat\w*|trace\b|review|audit\w*|"
        r"рефактор\w*|миграц\w*|трассир\w*|аудит\w*)\b|across files|cross-file",
        text,
    ):
        return "hard"
    if file_scope and any(term in text for term in ("explain", "what does", "что делает", "объясни")):
        return "simple"
    return configured_mode


# --------------------------------------------------------------------------- parsing helpers

def parse_json(content: str, schema: type[M]) -> M:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    value: Any = None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):                 # tolerate prose before/after the object
            try:
                value, _ = decoder.raw_decode(text, match.start())
                break
            except json.JSONDecodeError:
                continue
        if value is None:
            raise ResponseFormatError(f"invalid JSON for {schema.__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResponseFormatError(f"{schema.__name__} must be a JSON object")
    try:
        return schema.model_validate(value)
    except ValidationError as exc:
        raise ResponseFormatError(f"{schema.__name__} validation failed: {exc.errors(include_url=False)[:3]}") from exc


def render_user(query: str, role: str, data: dict[str, Any], context: str | None) -> str:
    """Code goes in as raw text. Inside a JSON string every newline/quote is escaped, which inflates
    tokens and makes the code harder for the model to read."""
    parts = [f"<role>{role}</role>", f"<question>\n{query}\n</question>"]
    if context:
        parts.append("<project_context>\n" + context.replace("</project_context>", "<\\/project_context>") + "\n</project_context>")
    if data:
        parts.append("<state>\n" + json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n</state>")
    return "\n".join(parts)


class _Aliases:
    """Short ids (n12) instead of 80-character node ids: fewer tokens, far fewer mis-copied citations."""

    def __init__(self, nodes: list[TreeNode]):
        self.alias_of = {n.node_id: f"n{i}" for i, n in enumerate(nodes, 1)}
        self.id_of = {a: nid for nid, a in self.alias_of.items()}
        self.by_label = {n.label: n.node_id for n in nodes}

    def resolve(self, ref: Any) -> str | None:
        if not isinstance(ref, str):
            return None
        ref = ref.strip().strip("`'\"[]")
        ref = re.sub(r"^NODE\s+", "", ref, flags=re.I)
        if ref.lower() in self.id_of:
            return self.id_of[ref.lower()]
        if ref in self.alias_of:
            return ref
        return self.by_label.get(ref)


def normalize_scope(scope: str) -> str:
    value = scope.replace("\\", "/").strip()
    while value.startswith("./"):                             # NOT lstrip("./"): that also eats ".github", ".env"
        value = value[2:]
    return value.strip("/")


def scope_ids(nodes: list[TreeNode], scope: str) -> set[str]:
    wanted = normalize_scope(scope)
    ids = {n.node_id for n in nodes
           if n.path == wanted or n.path.endswith("/" + wanted) or n.path.startswith(wanted + "/")}
    if not ids:
        raise ValueError(f"No indexed project file matches --file {scope!r}.")
    return ids


def _get_index(project: Path, tree: dict, nodes: list[TreeNode]) -> ActivationIndex:
    key = (str(project), tree.get("fingerprint", ""))
    with _CACHE_LOCK:
        if key in _INDEX_CACHE:
            _INDEX_CACHE.move_to_end(key)
            return _INDEX_CACHE[key]
    index = ActivationIndex(nodes)
    with _CACHE_LOCK:
        _INDEX_CACHE[key] = index
        while len(_INDEX_CACHE) > 8:
            _INDEX_CACHE.popitem(last=False)
    return index


# --------------------------------------------------------------------------- per-request state

class _Run:
    def __init__(self, backend: LLMBackend, registry: PromptRegistry, ledger: SpendLedger, query: str,
                 artifact_dir: Path | None, tool_registry=None, tool_ctx=None, role_tools=None, conversation=None):
        self.backend, self.registry, self.ledger, self.query, self.dir = backend, registry, ledger, query, artifact_dir
        self.tool_registry = tool_registry
        self.tool_ctx = tool_ctx
        self.role_tools = role_tools or {}
        self.cancel_check = lambda: None
        self.intent: QueryIntent | None = None
        self.tool_calls: list[dict[str, Any]] = []
        self.roles: list[str] = []
        self.tokens_in = self.tokens_out = self.latency_ms = 0
        self.cost = 0.0
        self.cost_missing = False
        self.conversation = conversation or ConversationConfig()

    def write(self, name: str, content: str) -> None:
        if self.dir:
            (self.dir / name).write_text(content, encoding="utf-8")

    def write_json(self, name: str, data: object) -> None:
        self.write(name, json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")

    def _account(self, response: ChatResult) -> None:
        self.tokens_in += response.tokens_in
        self.tokens_out += response.tokens_out
        self.latency_ms += response.latency_ms
        if response.cost_usd is None:
            self.cost_missing = True
        else:
            self.cost += response.cost_usd

    def invoke(self, role: str, schema: type[M], data: dict[str, Any], context: str | None = None) -> M:
        allowed = self.role_tools.get(role, [])
        if not allowed or self.tool_registry is None or self.tool_ctx is None:
            return self._invoke_single(role, schema, data, context)
        return self._invoke_with_tools(role, schema, data, context, allowed)

    def _invoke_single(self, role: str, schema: type[M], data: dict[str, Any], context: str | None = None) -> M:
        system = self.registry.compose(role, schema.__name__)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": render_user(self.query, role, data, context)}]
        step = len(self.roles)
        self.write(f"{step:02d}_{role}_prompt.txt", system + "\n\n" + messages[1]["content"])
        history = RequestHistory(self.conversation)
        for attempt in range(3):                                # two repair rounds for occasionally truncated JSON
            self.ledger.check(0.0 if getattr(self.backend, "accounts_spend", False) else self.cost)
            self.cancel_check()
            request = history.prepare(messages)
            self.write_json(f"{step:02d}_{role}_context.json", history.stats())
            response = self.backend.chat(request, json_mode=True)
            self._account(response)
            self.cancel_check()
            try:
                parsed = parse_json(response.content, schema)
                break
            except ResponseFormatError as exc:
                if attempt == 2:
                    self.write(f"{step:02d}_{role}_raw.txt", response.content)
                    raise
                hint = " Your reply was cut off by the output limit; be much shorter." if response.truncated else ""
                if attempt == 1:
                    hint += " Return ONLY one JSON object, without markdown fences. Keep it under 500 tokens and summarize long fields."
                messages = [*messages, {"role": "assistant", "content": response.content or "{}"},
                            {"role": "user", "content": f"Rejected: {exc}.{hint} Return ONLY one corrected JSON object."}]
        self.roles.append(role)
        self.write_json(f"{step:02d}_{role}.json", parsed.model_dump())
        return parsed

    def _invoke_with_tools(self, role: str, schema: type[M], data: dict[str, Any], context: str | None,
                           tool_names: list[str]) -> M:
        from atomic_flow.agents.loop import (
            TOOL_LOOP_MAX_TURNS,
            execute_tool,
            looks_like_tool_call,
            render_tools_block,
        )

        base_system = self.registry.compose(role, schema.__name__)
        tools_block = render_tools_block(self.tool_registry, tool_names)
        system = base_system + ("\n\n" + tools_block if tools_block else "")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": render_user(self.query, role, data, context)}]
        step = len(self.roles)
        self.write(f"{step:02d}_{role}_prompt.txt", system + "\n\n" + messages[1]["content"])
        is_action = (
            self.intent is not None
            and self.intent.task_type in {"modify", "refactor"}
        )

        history = RequestHistory(self.conversation)
        for turn in range(TOOL_LOOP_MAX_TURNS + 1):
            self.ledger.check(0.0 if getattr(self.backend, "accounts_spend", False) else self.cost)
            self.cancel_check()
            request = history.prepare(messages)
            self.write_json(f"{step:02d}_{role}_context.json", history.stats())
            response = self.backend.chat(request, json_mode=True)
            self._account(response)
            self.cancel_check()
            try:
                parsed_any: Any = json.loads(response.content)
            except json.JSONDecodeError:
                parsed_any = None

            if looks_like_tool_call(parsed_any):
                history.check_tool(parsed_any, self.tool_ctx.revision)
                result, record = execute_tool(parsed_any, self.tool_registry, self.tool_ctx,
                                               allowed_tools=tool_names, role=role)
                self.tool_calls.append(record)
                self.write_json(f"{step:02d}_{role}_tool_{turn}.json", {"call": record, "result": result})
                messages.append({"role": "assistant", "content": response.content})
                history.append_result(messages, result)
                continue

            if is_action and turn == 0 and role == "answerer":
                messages.append({"role": "assistant", "content": response.content or "{}"})
                messages.append({"role": "user", "content": (
                    "Rejected: this request asks to MODIFY code, but you returned prose instead of a tool call. "
                    "You must return exactly one JSON object of the form "
                    '{"tool": "edit_node", "args": {"node_id": "<id>", "new_source": "<full new body>"}}. '
                    "If you need the full body first, call read_node with zoom=2. "
                    "Do NOT return the change as text."
                )})
                continue

            try:
                final = parse_json(response.content, schema)
            except Exception as exc:  # noqa: BLE001 - retry the model's malformed final response
                if turn == TOOL_LOOP_MAX_TURNS:
                    raise
                messages.append({"role": "assistant", "content": response.content or "{}"})
                messages.append({"role": "user", "content":
                                 f"Rejected: {exc}. Return ONLY one valid JSON object, no tool call."})
                continue

            self.roles.append(role)
            self.write_json(f"{step:02d}_{role}.json", final.model_dump())
            return final

        raise ResponseFormatError(f"{role}: tool loop exceeded {TOOL_LOOP_MAX_TURNS} turns")


@dataclass
class _Attempt:
    answer: Answer
    verdict: str
    coverage: float
    grounding: float
    unknown: list[str]
    dropped: int

    @property
    def score(self) -> tuple:
        return (self.verdict == "ok", self.grounding, self.coverage, _CONF_RANK[self.answer.confidence])


class Orchestrator:
    def __init__(self, config: AtomicConfig, project: str | Path, backend: LLMBackend,
                 approval_callback=None, *, settings_project: str | Path | None = None, cancel_check=None):
        self.config = config
        self.project = Path(project).expanduser().resolve()
        self.backend = backend
        self.approval_callback = approval_callback
        self.settings_project = Path(settings_project).resolve() if settings_project else self.project
        self.cancel_check = cancel_check or (lambda: None)

    def answer(self, query: str, *, mode: str | None = None, budget_tokens: int | None = None,
               role_prompts: dict[str, str] | None = None, request_id: str | None = None,
               file_scope: str | None = None, retrieval_query: str | None = None,
               approve_edits: bool = False) -> AnswerResult:
        mode = mode or self.config.mode
        if mode not in {"simple", "medium", "hard"}:
            raise ValueError("mode must be simple, medium, or hard")
        req_id = request_id or "req_" + uuid.uuid4().hex[:12]
        if not REQUEST_ID_RE.fullmatch(req_id):
            raise ValueError("request_id may contain only letters, digits, '_' and '-' (max 64).")
        artifact_dir: Path | None = None
        if self.config.artifacts.save:
            artifact_dir = user_dir() / "runs" / req_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
        registry = PromptRegistry(self.settings_project, role_prompts)
        run = _Run(self.backend, registry, SpendLedger(self.config.budget.daily_limit_usd), query, artifact_dir,
                   conversation=self.config.conversation)
        run.write_json("request.json", {"query": query, "project": str(self.project), "mode": mode,
                                        "created_at": datetime.now(UTC).isoformat()})
        try:
            return self._answer(run, registry, req_id, query, retrieval_query or query, mode,
                                budget_tokens or self.config.context_budget_tokens, file_scope, approve_edits)
        except Exception as exc:
            run.write_json("error.json", {"type": type(exc).__name__, "message": str(exc), "roles_done": run.roles})
            raise
        finally:
            if not getattr(self.backend, "accounts_spend", False):
                run.ledger.record(request_id=req_id, backend=self.backend.name, model=self.backend.config.model,
                                  mode=mode, tokens_in=run.tokens_in, tokens_out=run.tokens_out,
                                  cost_usd=run.cost if not run.cost_missing or run.cost else None)

    # ------------------------------------------------------------------ pipeline
    def _answer(self, run: _Run, registry: PromptRegistry, req_id: str, query: str, rq: str, mode: str,
                limit: int, file_scope: str | None, approve_edits: bool = False) -> AnswerResult:
        tree = load_tree(self.project)
        if self.config.tree.auto_refresh:
            with project_lock(self.project):
                tree, changed = refresh_tree(self.project, self.config.tree.ignore,
                                             respect_gitignore=self.config.tree.respect_gitignore)
            if changed:
                log.info("Project index refreshed before answering.")
        nodes = [TreeNode.model_validate(item) for item in tree.get("nodes", [])]
        if not nodes:
            raise ValueError(f"Project tree is empty. Run `atomic tree build` in {self.project}.")
        aliases = _Aliases(nodes)
        by_id = {n.node_id: n for n in nodes}
        index = _get_index(self.project, tree, nodes)
        from atomic_flow.agents.context import ToolContext
        from atomic_flow.agents.policy import PolicyEngine
        from atomic_flow.agents.tools import ROLE_TOOLS, default_registry
        from atomic_flow.agents.undo import UndoStack

        edits_enabled = approve_edits or os.getenv("ATOMIC_FLOW_ALLOW_EDITS", "").lower() in {"1", "true", "yes"}
        trusted = is_trusted(self.settings_project)
        policy = PolicyEngine.load(self.settings_project) if trusted else PolicyEngine()
        run.tool_registry = default_registry()
        run.tool_ctx = ToolContext(project_root=self.project, nodes_by_id=by_id,
                       aliases=aliases.alias_of, alias_to_id=aliases.id_of, index=index,
                       edits_enabled=edits_enabled,
                       undo_stack=UndoStack(user_dir() / "edits"), policy=policy,
                       trusted=trusted, approval_callback=self.approval_callback,
                       tree_ignore=self.config.tree.ignore, respect_gitignore=self.config.tree.respect_gitignore,
                       execution=self.config.execution, cancel_check=self.cancel_check)
        run.role_tools = ROLE_TOOLS
        run.cancel_check = self.cancel_check
        scope = scope_ids(nodes, file_scope) if file_scope else None
        search: SearchResult = index.search(rq, limit=80, scope=scope)
        intent = QueryIntent()
        run.intent = intent
        warnings: list[str] = []

        def view(pairs: list[tuple[TreeNode, float]]) -> list[dict]:
            return [{"id": aliases.alias_of[n.node_id], "path": n.path, "symbol": n.symbol, "kind": n.kind,
                     "score": round(s, 3)} for n, s in pairs]

        def reparse(extra: dict[str, Any] | None = None) -> None:
            nonlocal intent, search
            if extra is None:
                paths = list(dict.fromkeys(n.path for n, s in search.ranked[:20] if s > 0.1))[:15]
                intent = run.invoke("parser", QueryIntent, {"candidate_paths": paths})
            else:
                intent = run.invoke("reparser", QueryIntent, extra)
            if intent.task_type in {"explain", "other"} and _looks_like_action(rq):
                intent = intent.model_copy(update={"task_type": "modify"})
                run.write_json("00_intent_patched.json", intent.model_dump())
            run.intent = intent
            search = index.search(rq, extra_terms=intent.focus_terms + intent.target_scope, limit=80, scope=scope)

        if mode != "simple" or search.weak:        # simple mode only pays for a parser call when BM25 found nothing
            reparse()

        selected: set[str] | None = None
        if mode == "hard":
            plan = run.invoke("refiner", ContextPlan, {"candidates": view(search.ranked[:50])})
            selected = {nid for ref in plan.node_ids if (nid := aliases.resolve(ref)) and nid in by_id}
            if not selected:
                selected = {n.node_id for n, _ in search.ranked[:8]}
            run.write_json("context_plan.json", plan.model_dump())

        def build_context() -> tuple[str, list[dict]]:
            pairs = list(search.ranked)
            floor: dict[str, int] = {}
            if selected is not None:
                have = {n.node_id for n, _ in pairs}
                pairs += [(by_id[nid], 0.6) for nid in selected - have if nid in by_id]
                pairs.sort(key=lambda p: -p[1])
                floor = {nid: 2 for nid in selected}                           # the LLM asked for it: not just a signature
            return materialize(self.project, pairs, budget_tokens=limit, include_skeleton=(mode != "simple"),
                               selected_ids=selected, skeleton_nodes=nodes, aliases=aliases.alias_of, min_zoom=floor)

        context, meta = build_context()
        context_revision = run.tool_ctx.revision

        def sync_context() -> str:
            nonlocal nodes, index, search, scope, context, meta, context_revision, best
            if context_revision != run.tool_ctx.revision:
                best = None  # answers from a previous source revision cannot win this run
                nodes = list(by_id.values())
                aliases.by_label = {n.label: n.node_id for n in nodes}
                index = run.tool_ctx.index
                scope = scope_ids(nodes, file_scope) if file_scope else None
                search = index.search(rq, extra_terms=intent.focus_terms + intent.target_scope, limit=80, scope=scope)
                context, meta = build_context()
                context_revision = run.tool_ctx.revision
                run.write("focus.txt", context)
            extra = "\n\n".join(f"READ NODE {aliases.alias_of[nid]}\n{by_id[nid].label}\n{text}"
                                   for nid, text in run.tool_ctx.evidence.items() if nid in by_id)
            return context + ("\n\n" + extra if extra else "")

        run.write("focus.txt", context)
        if search.weak:
            warnings.append("Retrieval is weak: few query terms occur in the best-matching code.")

        ledger: list[dict] = []
        best: _Attempt | None = None
        critique: AnswerCritique | None = None
        max_attempts = {"simple": 1, "medium": 2}.get(mode, self.config.max_attempts)
        attempts = 0
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            evidence_context = sync_context()
            valid = {m["node_id"] for m in meta} | set(run.tool_ctx.evidence)
            valid_aliases = sorted(aliases.alias_of[i] for i in valid)
            data = {"intent": intent.model_dump(), "retrieval_weak": search.weak, "ledger": ledger,
                    "valid_node_ids": valid_aliases}
            if critique is not None:
                data["prior_critique"] = critique.model_dump()
            answer = run.invoke("answerer", Answer, data, context=evidence_context)
            evidence_context = sync_context()
            valid = {m["node_id"] for m in meta} | set(run.tool_ctx.evidence)
            valid_aliases = sorted(aliases.alias_of[i] for i in valid)
            kept, seen, dropped = [], set(), 0
            for citation in answer.citations:
                nid = aliases.resolve(citation.node_id)
                if nid and nid in valid:
                    if nid not in seen:
                        seen.add(nid)
                        kept.append(citation.model_copy(update={"node_id": nid}))
                else:
                    dropped += 1
            answer.citations = kept
            unknown, checked = unknown_identifiers(answer.text, evidence_context)
            grounding = grounding_score(unknown, checked)
            if mode == "simple":
                best = _Attempt(answer, "unverified", 0.0, grounding, unknown, dropped)
                break
            shown = answer.model_dump()
            shown["citations"] = [{**c.model_dump(), "node_id": aliases.alias_of[c.node_id]} for c in answer.citations]
            critique = run.invoke("critic", AnswerCritique, {"answer": shown, "valid_node_ids": valid_aliases,
                                                             "identifiers_not_in_context": unknown,
                                                             "citations_dropped_as_invalid": dropped}, context=evidence_context)
            verdict = critique.verdict
            if unknown:                                        # deterministic evidence beats the critic's opinion
                critique.unsupported_claims += [f"`{u}` does not occur in the project context" for u in unknown]
                if verdict == "ok":
                    verdict = "needs_retry"
                    critique.verdict = verdict
                    critique.feedback = (critique.feedback + f" Remove or justify identifiers absent from context: {unknown}.").strip()
            current = _Attempt(answer, verdict, critique.citation_coverage, grounding, unknown, dropped)
            if best is None or current.score > best.score:
                best = current
            ledger.append({"attempt": attempt, "verdict": verdict, "coverage": critique.citation_coverage,
                           "grounding": grounding, "feedback": critique.feedback})
            run.write_json(f"attempt_{attempt}_answer.json", answer.model_dump())
            run.write_json(f"attempt_{attempt}_critique.json", critique.model_dump())
            if verdict == "ok" or attempt == max_attempts:
                break
            if mode == "hard" and selected is not None:
                if verdict == "wrong_answer":
                    reparse({"intent": intent.model_dump(), "critique": critique.model_dump()})
                if verdict in {"needs_more_context", "wrong_answer"}:
                    pool = [(n, s) for n, s in search.ranked if n.node_id not in selected][:40]
                    pool += [(n, 0.5) for n in index.neighbors(selected)][:20]       # graph neighbours of what we already show
                    expanded = run.invoke("expander", ContextPlan, {"critique": critique.model_dump(),
                                                                    "selected_node_ids": sorted(aliases.alias_of[i] for i in selected),
                                                                    "candidates": view(pool)})
                    fresh = {nid for ref in expanded.node_ids if (nid := aliases.resolve(ref)) and nid in by_id} - selected
                    if fresh:
                        selected |= fresh
                        context, meta = build_context()
                        run.write("focus.txt", context)
                        ledger[-1]["added_node_ids"] = sorted(aliases.alias_of[i] for i in fresh)
        assert best is not None
        if best.unknown:
            warnings.append(f"Identifiers not found in the supplied context: {', '.join(best.unknown)}")
        if best.dropped:
            warnings.append(f"{best.dropped} citation(s) referred to nodes that were not in the context and were removed.")
        failed_tools = [call for call in run.tool_calls
                        if call.get("result_summary", {}).get("outcome") in {"error", "denied"}]
        if failed_tools:
            warnings.append(f"{len(failed_tools)} tool call(s) failed or were denied; inspect tool_calls for host-verified outcomes.")
            if best.verdict == "ok":
                best.verdict = "needs_retry"
        run.write_json("ledger.json", ledger)
        result = AnswerResult(
            text=best.answer.text, citations=best.answer.citations, confidence=best.answer.confidence,
            missing_context=best.answer.missing_context, follow_ups=best.answer.follow_ups, mode_used=mode,  # type: ignore[arg-type]
            roles_invoked=run.roles, prompts_source=registry.sources, attempts=attempts, ledger_id=req_id,
            artifacts_path=str(run.dir) if run.dir else None, tokens_in=run.tokens_in, tokens_out=run.tokens_out,
            cost_usd=run.cost if (run.cost or not run.cost_missing) else None, cost_complete=not run.cost_missing,
            latency_ms=run.latency_ms, backend_used=self.backend.name, model_used=self.backend.config.model,
            verdict=best.verdict, citation_coverage=best.coverage, grounding=best.grounding,
            unknown_identifiers=best.unknown, dropped_citations=best.dropped, retrieval_weak=search.weak,
            warnings=warnings, tool_calls=run.tool_calls)
        run.write("final.md", best.answer.text + "\n")
        run.write_json("meta.json", result.model_dump())
        return result
