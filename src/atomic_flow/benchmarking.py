"""Reproducible local evaluation: free retrieval metrics + end-to-end grounded-QA runs."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from atomic_flow.activation.activate import ActivationIndex
from atomic_flow.backends.router import make_backend
from atomic_flow.config import AtomicConfig, user_dir
from atomic_flow.errors import BudgetExceeded, ConfigurationError
from atomic_flow.evaluation_meter import EvaluationMeter, usage_summary
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.qa_rubric import RUBRIC_VERSION, grade_propositions
from atomic_flow.tree.builder import build_tree, save_tree
from atomic_flow.tree.node import TreeNode


def benchmark_root() -> Path:
    return Path(__file__).resolve().parent / "benchmark_data"


def benchmark_cases() -> list[dict[str, Any]]:
    return json.loads((benchmark_root() / "cases.json").read_text(encoding="utf-8"))


@contextmanager
def fixture_project() -> Iterator[tuple[Path, list[TreeNode]]]:
    with tempfile.TemporaryDirectory(prefix="atomic-flow-benchmark-") as temp:
        project = Path(temp) / "fixture"
        shutil.copytree(benchmark_root() / "fixtures", project)
        tree = build_tree(project)
        save_tree(project, tree)
        yield project, [TreeNode.model_validate(n) for n in tree["nodes"]]


def run_retrieval_benchmark(*, k: int = 5) -> dict[str, Any]:
    """Zero-cost: no model calls. Use it to iterate on tokenization/ranking before spending quota."""
    rows: list[dict[str, Any]] = []
    with fixture_project() as (_, nodes):
        index = ActivationIndex(nodes)
        for case in benchmark_cases():
            expected = case.get("expected_nodes", [])
            result = index.search(case["query"], limit=max(k, 10))
            ids = [n.node_id for n, _ in result.ranked]
            ranks = [ids.index(e) + 1 if e in ids else None for e in expected]
            rows.append({"case_id": case["id"], "skipped": bool(case.get("skip_retrieval") or not expected),
                         "recall_at_k": (sum(1 for r in ranks if r and r <= k) / len(expected)) if expected else None,
                         "hit_all_at_k": all(r and r <= k for r in ranks) if expected else None,
                         "mrr": (1 / min(r for r in ranks if r)) if any(ranks) else 0.0 if expected else None,
                         "weak": result.weak, "top": ids[:k]})
    scored = [r for r in rows if not r["skipped"]]
    return {"k": k, "summary": {
        "cases": len(scored),
        "hit_all_at_k": round(mean(r["hit_all_at_k"] for r in scored), 3) if scored else None,
        "mean_recall_at_k": round(mean(r["recall_at_k"] for r in scored), 3) if scored else None,
        "mrr": round(mean(r["mrr"] for r in scored), 3) if scored else None}, "results": rows}


def _evaluate(case: dict[str, Any], answer, target_ids: list[str]) -> dict[str, Any]:
    text = answer.text
    cited = {c.node_id for c in answer.citations}
    checks = {pattern: bool(re.search(pattern, text)) for pattern in case.get("must_match", [])}
    forbidden = {pattern: not re.search(pattern, text) for pattern in case.get("must_not_match", [])}
    hits = sum(1 for target in target_ids if target in cited)
    min_required = case.get("min_citations", len(target_ids))
    citation_hit = hits >= min_required
    citation_strict = hits == len(target_ids)
    semantic = grade_propositions(case, text)
    unknown_identifiers = getattr(answer, "unknown_identifiers", [])
    # Legacy keyword checks remain diagnostics; bilingual proposition checks own
    # automatic success. Valid IDs establish citation identity, not entailment.
    passed = (citation_hit and all(forbidden.values()) and semantic["semantic_status"] == "matched"
              and not unknown_identifiers)
    return {"citation_hit": citation_hit, "citation_strict": citation_strict, "required_patterns": checks, "forbidden_patterns": forbidden,
            "rubric_version": RUBRIC_VERSION, "grading_scope": "finite_calibrated_fixture_answers; other wording requires independent review",
            **semantic, "identifier_grounded": not unknown_identifiers,
            "grounded": not unknown_identifiers, "passed": passed}


def run_benchmark(config: AtomicConfig, *, model: str | None = None, mode: str | None = None, budget_tokens: int = 5000,
                  limit: int | None = None, output: str | Path | None = None, backend_name: str = "auto",
                  repeats: int = 1) -> dict[str, Any]:
    cases = benchmark_cases()[:limit]
    if not cases or type(repeats) is not int or repeats < 1:
        raise ValueError("The benchmark case list is empty.")
    target = Path(output).expanduser() if output else user_dir() / "benchmarks" / "runs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    attempt_path = target.with_name(target.name + ".attempts.json")
    if attempt_path.exists():
        raise ValueError("evaluation attempt ledger already exists; choose a fresh output path (no automatic replay)")
    def checkpoint(calls):
        temporary = attempt_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "calls": calls, **usage_summary(calls)}, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(attempt_path)
    backend = make_backend(config, backend_name, model)
    # Hidden transport retries would make the observed attempt count incomplete.
    backend.config = backend.config.model_copy(update={"max_retries": 0})
    meter = EvaluationMeter(backend, on_update=checkpoint)
    results: list[dict[str, Any]] = []
    with fixture_project() as (project, nodes):
        known = {n.node_id for n in nodes}
        for case in cases:
            targets = case.get("expected_nodes", [])
            missing = [t for t in targets if t not in known]
            if missing:
                raise ValueError(f"Benchmark case {case['id']} points to missing node(s): {missing}")
            for repeat in range(repeats):
                meter.trial_start = len(meter.calls)
                started = time.monotonic()
                try:
                    case_mode = mode or case.get("mode", "medium")  # CLI --mode forces, else per-case
                    answer = Orchestrator(config, project, meter).answer(case["query"], mode=case_mode, budget_tokens=budget_tokens)
                    result = {"case_id": case["id"], "repeat": repeat, "mode": case_mode, "text": answer.text,
                              "expected_nodes": targets, "cited": [c.node_id for c in answer.citations],
                              "verdict": answer.verdict, "confidence": answer.confidence, "grounding": answer.grounding,
                              "latency_ms": answer.latency_ms, "request_id": answer.ledger_id,
                              **_evaluate(case, answer, targets)}
                except Exception as exc:  # noqa: BLE001 - one flaky case must not hide the rest
                    result = {"case_id": case["id"], "repeat": repeat, "mode": mode, "passed": False,
                              "error_type": type(exc).__name__}
                calls = [dict(call) for call in meter.calls[meter.trial_start:]]
                measured = usage_summary(calls)
                result.update(calls=calls, **measured, cost_usd=measured["reported_cost_usd"],
                              wall_seconds=round(time.monotonic() - started, 3))
                results.append(result)
                if not measured["cost_complete"] or result.get("error_type") in {ConfigurationError.__name__, BudgetExceeded.__name__}:
                    break
            if results and (not results[-1]["cost_complete"] or results[-1].get("error_type") in
                            {ConfigurationError.__name__, BudgetExceeded.__name__}):
                break
    total = len(results)
    passed = sum(bool(r["passed"]) for r in results)
    measured = usage_summary(meter.calls)
    per_case: dict[str, list[bool]] = {}
    for r in results:
        per_case.setdefault(r["case_id"], []).append(bool(r["passed"]))
    report = {
        "suite": "atomic-flow-grounded-code-qa", "created_at": datetime.now(UTC).isoformat(),
        "model": model or getattr(backend.config, "model", "unknown"), "backend": backend.name, "mode": mode,
        "budget_tokens": budget_tokens, "repeats": repeats,
        "rubric_version": RUBRIC_VERSION,
        "accounting_scope": "observed_backend_calls; transport retries disabled; HTTP parameter negotiation excluded; no ledger charges added",
        "stopped_early": total < len(cases) * repeats,
        "summary": {
            "passed": passed, "strict_passed": sum(bool(r.get("citation_strict")) and bool(r["passed"]) for r in results),
            "total": total, "pass_rate": passed / total,
            "flaky_cases": sorted(c for c, v in per_case.items() if 0 < sum(v) < len(v)),
            "average_latency_ms": round(mean(r["latency_ms"] for r in results if "latency_ms" in r), 2) if any("latency_ms" in r for r in results) else None,
            **measured, "cost_usd": measured["reported_cost_usd"],
            "cost_per_success_usd": measured["reported_cost_usd"] / passed if passed and measured["cost_complete"] else None},
        "results": results}
    report["attempt_ledger"] = str(attempt_path.resolve())
    report["output"] = str(target.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return report
