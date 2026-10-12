"""Reproducible local evaluation: free retrieval metrics + end-to-end grounded-QA runs."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
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
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree
from atomic_flow.tree.node import TreeNode

ABSTAIN_RE = re.compile(r"(?i)(not (shown|present|defined|found|visible|included)|no (information|evidence|code)|"
                        r"cannot (determine|find|tell)|can't (determine|find|tell)|does not (show|contain|define)|"
                        r"doesn't (show|contain|define)|нет информации|не (найден|показан|определ))")


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
    min_required = case.get("min_citations", len(target_ids))
    citation_hit = sum(1 for target in target_ids if target in cited) >= min_required
    abstained = None
    if case.get("must_abstain"):
        abstained = answer.confidence == "low" or bool(answer.missing_context) or bool(ABSTAIN_RE.search(text))
    passed = (citation_hit and all(checks.values()) and all(forbidden.values())
              and (abstained is not False) and not answer.unknown_identifiers)
    return {"citation_hit": citation_hit, "required_patterns": checks, "forbidden_patterns": forbidden,
            "abstained": abstained, "grounded": not answer.unknown_identifiers, "passed": passed}


def run_benchmark(config: AtomicConfig, *, model: str | None = None, mode: str = "medium", budget_tokens: int = 5000,
                  limit: int | None = None, output: str | Path | None = None, backend_name: str = "auto",
                  repeats: int = 1) -> dict[str, Any]:
    cases = benchmark_cases()[:limit]
    if not cases:
        raise ValueError("The benchmark case list is empty.")
    backend = make_backend(config, backend_name, model)
    results: list[dict[str, Any]] = []
    with fixture_project() as (project, nodes):
        known = {n.node_id for n in nodes}
        for case in cases:
            targets = case.get("expected_nodes", [])
            missing = [t for t in targets if t not in known]
            if missing:
                raise ValueError(f"Benchmark case {case['id']} points to missing node(s): {missing}")
            for repeat in range(repeats):
                try:
                    answer = Orchestrator(config, project, backend).answer(case["query"], mode=mode, budget_tokens=budget_tokens)
                    result = {"case_id": case["id"], "repeat": repeat, "mode": mode, "text": answer.text,
                              "expected_nodes": targets, "cited": [c.node_id for c in answer.citations],
                              "verdict": answer.verdict, "confidence": answer.confidence, "grounding": answer.grounding,
                              "tokens_in": answer.tokens_in, "tokens_out": answer.tokens_out, "cost_usd": answer.cost_usd,
                              "latency_ms": answer.latency_ms, "request_id": answer.ledger_id,
                              **_evaluate(case, answer, targets)}
                except (ConfigurationError, BudgetExceeded):
                    raise                                     # an expired key fails EVERY case: stop, don't burn 9 error rows
                except Exception as exc:  # noqa: BLE001 - one flaky case must not hide the rest
                    result = {"case_id": case["id"], "repeat": repeat, "mode": mode, "passed": False,
                              "error": f"{type(exc).__name__}: {exc}"}
                results.append(result)
    total = len(results)
    passed = sum(bool(r["passed"]) for r in results)
    costs = [r["cost_usd"] for r in results if r.get("cost_usd") is not None]
    per_case: dict[str, list[bool]] = {}
    for r in results:
        per_case.setdefault(r["case_id"], []).append(bool(r["passed"]))
    report = {
        "suite": "atomic-flow-grounded-code-qa", "created_at": datetime.now(UTC).isoformat(),
        "model": model or getattr(backend.config, "model", "unknown"), "backend": backend.name, "mode": mode,
        "budget_tokens": budget_tokens, "repeats": repeats,
        "summary": {
            "passed": passed, "total": total, "pass_rate": passed / total,
            "flaky_cases": sorted(c for c, v in per_case.items() if 0 < sum(v) < len(v)),
            "average_latency_ms": round(mean(r["latency_ms"] for r in results if "latency_ms" in r), 2) if any("latency_ms" in r for r in results) else None,
            "tokens_in": sum(r.get("tokens_in", 0) for r in results), "tokens_out": sum(r.get("tokens_out", 0) for r in results),
            "cost_usd": round(sum(costs), 8) if len(costs) == len([r for r in results if "error" not in r]) and costs else None},
        "results": results}
    target = Path(output).expanduser() if output else user_dir() / "benchmarks" / "runs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report["output"] = str(target.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return report
