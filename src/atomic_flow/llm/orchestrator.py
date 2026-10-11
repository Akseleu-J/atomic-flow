"""Simple, medium, and hard request flows with per-request audit artifacts."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from atomic_flow.activation.activate import activate, materialize
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig, user_dir
from atomic_flow.errors import BackendError
from atomic_flow.llm.prompts import PromptRegistry
from atomic_flow.llm.schemas import Answer, AnswerCritique, AnswerResult, ContextPlan, QueryIntent
from atomic_flow.tree.builder import load_tree
from atomic_flow.tree.node import TreeNode

M = TypeVar("M", bound=BaseModel)


def choose_mode(query: str, configured_mode: str, *, explicit_mode: str | None = None,
               auto_mode: bool = True, file_scope: str | None = None) -> str:
    """Choose an initial mode before asking the user to approve a costly hard run."""
    if explicit_mode:
        return explicit_mode
    if not auto_mode:
        return configured_mode
    text = query.casefold()
    hard_terms = ("refactor", "migration", "migrate", "trace", "review", "audit", "рефактор", "миграц", "трассир", "аудит", "across files", "cross-file")
    if any(term in text for term in hard_terms):
        return "hard"
    explain_terms = ("explain", "what does", "что делает", "объясни")
    if file_scope and any(term in text for term in explain_terms):
        return "simple"
    return configured_mode


def _parse_json(content: str, schema: type[M]) -> M:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                raise BackendError(f"Backend returned invalid JSON for {schema.__name__}: {exc}") from exc
        else:
            raise BackendError(f"Backend returned invalid JSON for {schema.__name__}: {exc}") from exc
    try:
        return schema.model_validate(value)
    except ValidationError as exc:
        raise BackendError(f"Backend response failed {schema.__name__} validation: {exc}") from exc


def _save_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class Orchestrator:
    def __init__(self, config: AtomicConfig, project: str | Path, backend: OpenAICompatibleBackend):
        self.config = config
        self.project = Path(project).expanduser().resolve()
        self.backend = backend

    def answer(self, query: str, *, mode: str | None = None, budget_tokens: int | None = None,
               role_prompts: dict[str, str] | None = None, request_id: str | None = None,
               file_scope: str | None = None) -> AnswerResult:
        mode = mode or self.config.mode
        if mode not in {"simple", "medium", "hard"}:
            raise ValueError("mode must be simple, medium, or hard")
        req_id = request_id or "req_" + uuid.uuid4().hex[:12]
        artifact_dir = user_dir() / "runs" / req_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "request.json").write_text(json.dumps({"query": query, "project": str(self.project), "mode": mode, "created_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")

        tree_data = load_tree(self.project)
        nodes = [TreeNode.model_validate(item) for item in tree_data.get("nodes", [])]
        if not nodes:
            raise ValueError(f"Project tree is empty. Run `atomic tree build` in {self.project}.")
        by_id = {node.node_id: node for node in nodes}
        registry = PromptRegistry(self.project, role_prompts)
        roles: list[str] = []
        calls: list[dict] = []
        total_in = total_out = 0
        total_cost = 0.0
        cost_known = True
        total_latency_ms = 0
        ledger: list[dict] = []
        limit = budget_tokens or self.config.context_budget_tokens
        intent = QueryIntent()

        def invoke(role: str, prompt: str, data: dict, schema: type[M]) -> M:
            nonlocal total_in, total_out, total_cost, cost_known, total_latency_ms
            system = registry.get(role)
            user_content = json.dumps({"query": query, **data}, ensure_ascii=False, indent=2)
            calls.append({"role": role, "system": system, "user": user_content})
            (artifact_dir / f"{len(roles):02d}_{role}_prompt.txt").write_text(system + "\n\n" + prompt + "\n\n" + user_content, encoding="utf-8")
            response = self.backend.chat([{"role": "system", "content": system + "\n\n" + prompt}, {"role": "user", "content": user_content}], json_mode=True)
            total_in += response.tokens_in
            total_out += response.tokens_out
            if response.cost_usd is None:
                cost_known = False
            else:
                total_cost += response.cost_usd
            total_latency_ms += response.latency_ms
            parsed = _parse_json(response.content, schema)
            roles.append(role)
            _save_json(artifact_dir / f"{len(roles)-1:02d}_{role}.json", parsed.model_dump())
            return parsed

        def rank(extra: list[str] | None = None):
            eligible = nodes
            if file_scope:
                wanted = file_scope.replace("\\", "/").lstrip("./")
                eligible = [node for node in nodes if node.path == wanted or node.path.endswith("/" + wanted)]
                if not eligible:
                    raise ValueError(f"No indexed project file matches --file {file_scope!r}.")
            return activate(query, eligible, extra_terms=extra, limit=min(80, len(eligible)))

        def answer_once(context: str, prior: dict | None = None) -> Answer:
            data = {"context": context, "intent": intent.model_dump(), "ledger": ledger, "prior_critique": prior}
            result = invoke("answerer", "Return one JSON object matching this Answer schema: " + json.dumps(Answer.model_json_schema()), data, Answer)
            valid = {item["node_id"] for item in context_meta}
            result.citations = [citation for citation in result.citations if citation.node_id in valid]
            return result

        candidates = rank()
        if mode != "simple":
            intent = invoke("parser", "Return one JSON object matching this QueryIntent schema: " + json.dumps(QueryIntent.model_json_schema()), {"candidate_paths": [node.path for node, score in candidates[:20] if score > 0.1]}, QueryIntent)
            _save_json(artifact_dir / "intent.json", intent.model_dump())
            candidates = rank(intent.focus_terms + intent.target_scope)

        selected_ids: set[str] | None = None
        if mode == "hard":
            plan = invoke("refiner", "Return one JSON object matching this ContextPlan schema: " + json.dumps(ContextPlan.model_json_schema()), {"candidates": [{"node_id": n.node_id, "path": n.path, "symbol": n.symbol, "kind": n.kind, "score": round(s, 3)} for n, s in candidates[:50]]}, ContextPlan)
            selected_ids = {node_id for node_id in plan.node_ids if node_id in by_id}
            if not selected_ids:
                selected_ids = {node.node_id for node, _ in candidates[:8]}
            _save_json(artifact_dir / "context_plan.json", plan.model_dump())
        full_skeleton = [(node, 0.0) for node in nodes]
        context, context_meta = materialize(self.project, candidates, budget_tokens=limit, include_skeleton=(mode != "simple"), selected_ids=selected_ids, skeleton_nodes=full_skeleton)
        (artifact_dir / "skeleton.txt").write_text("\n".join(line for line in context.splitlines() if not line.startswith("NODE ")), encoding="utf-8")
        (artifact_dir / "focus.txt").write_text(context, encoding="utf-8")

        if mode == "simple":
            answer = answer_once(context)
            verdict, coverage, attempts = "unverified", 0.0, 1
        else:
            answer = answer_once(context)
            attempts = 1
            valid_ids = {item["node_id"] for item in context_meta}
            critique = invoke("critic", "Return one JSON object matching this AnswerCritique schema: " + json.dumps(AnswerCritique.model_json_schema()), {"context": context, "answer": answer.model_dump(), "valid_node_ids": sorted(valid_ids)}, AnswerCritique)
            coverage = critique.citation_coverage
            verdict = critique.verdict
            best_answer = answer
            best_verdict = verdict
            best_score = (coverage, {"high": 3, "medium": 2, "low": 1}[answer.confidence])
            ledger.append({"attempt": attempts, "verdict": verdict, "coverage": coverage, "feedback": critique.feedback, "missing_node_ids": critique.missing_node_ids})
            _save_json(artifact_dir / "attempt_0_answer.json", answer.model_dump())
            _save_json(artifact_dir / "attempt_0_critique.json", critique.model_dump())
            if mode == "medium" and verdict in {"needs_retry", "needs_more_context", "wrong_answer"}:
                attempts += 1
                answer = answer_once(context, critique.model_dump())
                critique = invoke("critic", "Return one JSON object matching this AnswerCritique schema: " + json.dumps(AnswerCritique.model_json_schema()), {"context": context, "answer": answer.model_dump(), "valid_node_ids": sorted(valid_ids), "previous_critique": critique.model_dump()}, AnswerCritique)
                coverage, verdict = critique.citation_coverage, critique.verdict
                ledger.append({"attempt": attempts, "verdict": verdict, "coverage": coverage, "feedback": critique.feedback})
            elif mode == "hard":
                seen = {item["node_id"] for item in context_meta}
                for iteration in range(1, self.config.max_attempts):
                    if verdict == "ok":
                        break
                    if verdict == "wrong_answer":
                        intent = invoke("reparser", "Return one JSON object matching this QueryIntent schema: " + json.dumps(QueryIntent.model_json_schema()), {"intent": intent.model_dump(), "critique": critique.model_dump()}, QueryIntent)
                    expanded = invoke("expander", "Return one JSON object matching this ContextPlan schema: " + json.dumps(ContextPlan.model_json_schema()), {"critique": critique.model_dump(), "selected_node_ids": sorted(seen), "candidates": [{"node_id": n.node_id, "path": n.path, "symbol": n.symbol, "kind": n.kind, "score": round(s, 3)} for n, s in candidates[:50]]}, ContextPlan)
                    fresh = {item for item in expanded.node_ids if item in by_id} - seen
                    if not fresh:
                        break
                    seen.update(fresh)
                    context, context_meta = materialize(self.project, candidates, budget_tokens=limit, include_skeleton=True, selected_ids=seen, skeleton_nodes=full_skeleton)
                    attempts += 1
                    answer = answer_once(context, critique.model_dump())
                    valid_ids = {item["node_id"] for item in context_meta}
                    critique = invoke("critic", "Return one JSON object matching this AnswerCritique schema: " + json.dumps(AnswerCritique.model_json_schema()), {"context": context, "answer": answer.model_dump(), "valid_node_ids": sorted(valid_ids)}, AnswerCritique)
                    verdict, coverage = critique.verdict, critique.citation_coverage
                    score = (coverage, {"high": 3, "medium": 2, "low": 1}[answer.confidence])
                    if score > best_score:
                        best_answer, best_verdict, best_score = answer, verdict, score
                    ledger.append({"attempt": attempts, "verdict": verdict, "coverage": coverage, "feedback": critique.feedback, "added_node_ids": sorted(fresh)})
                    _save_json(artifact_dir / f"attempt_{attempts-1}_answer.json", answer.model_dump())
                    _save_json(artifact_dir / f"attempt_{attempts-1}_critique.json", critique.model_dump())
                if verdict != "ok" and best_score > (coverage, {"high": 3, "medium": 2, "low": 1}[answer.confidence]):
                    answer, verdict, coverage = best_answer, best_verdict, best_score[0]

        _save_json(artifact_dir / "ledger.json", ledger)
        result = AnswerResult(text=answer.text, citations=answer.citations, confidence=answer.confidence,
                              missing_context=answer.missing_context, follow_ups=answer.follow_ups,
                              mode_used=mode, roles_invoked=roles, prompts_source=registry.sources,
                              attempts=attempts, ledger_id=req_id, artifacts_path=str(artifact_dir),
                              tokens_in=total_in, tokens_out=total_out, backend_used=self.backend.name,
                              model_used=self.backend.config.model, cost_usd=total_cost if cost_known else None,
                              latency_ms=total_latency_ms, verdict=verdict, citation_coverage=coverage)
        (artifact_dir / "final.md").write_text(answer.text + "\n", encoding="utf-8")
        _save_json(artifact_dir / "meta.json", result.model_dump())
        return result
