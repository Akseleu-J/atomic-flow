"""Reproducible, local evaluation against a small grounded-code QA suite."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from atomic_flow.backends.router import make_backend
from atomic_flow.config import AtomicConfig, user_dir
from atomic_flow.llm.orchestrator import Orchestrator
from atomic_flow.tree.builder import build_tree, save_tree
from atomic_flow.tree.node import TreeNode


def benchmark_root() -> Path:
    return Path(__file__).resolve().parent / "benchmark_data"


def benchmark_cases() -> list[dict[str, Any]]:
    path = benchmark_root() / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_benchmark(config: AtomicConfig, *, model: str | None = None, mode: str = "medium",
                  budget_tokens: int = 5000, limit: int | None = None,
                  output: str | Path | None = None, backend_name: str = "auto") -> dict[str, Any]:
    root = benchmark_root()
    fixture_source = root / "fixtures"
    cases = benchmark_cases()[:limit]
    if not cases:
        raise ValueError("The benchmark case list is empty.")
    backend = make_backend(config, backend_name, model)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="atomic-flow-benchmark-") as temp:
        project = Path(temp) / "fixture"
        shutil.copytree(fixture_source, project)
        tree = build_tree(project)
        save_tree(project, tree)
        tree_nodes = [TreeNode.model_validate(item) for item in tree["nodes"]]
        target_ids = {(node.path, node.symbol): node.node_id for node in tree_nodes}
        for case in cases:
            target_node = target_ids.get((case["path"], case["symbol"]))
            if not target_node:
                raise ValueError(f"Benchmark case {case['id']} points to missing node {case['path']}::{case['symbol']}.")
            try:
                answer = Orchestrator(config, project, backend).answer(case["query"], mode=mode,
                                                                       budget_tokens=budget_tokens)
                lowered = answer.text.casefold()
                checks = {term: term.casefold() in lowered for term in case["must_mention"]}
                valid = {node.node_id for node in tree_nodes}
                cited = {citation.node_id for citation in answer.citations}
                result = {
                    "case_id": case["id"], "mode": mode,
                    "text": answer.text,
                    "expected_citation": target_node,
                    "citation_present": target_node in cited,
                    "citations_valid": cited.issubset(valid),
                    "required_terms": checks,
                    "passed": target_node in cited and cited.issubset(valid) and all(checks.values()),
                    "verdict": answer.verdict,
                    "confidence": answer.confidence,
                    "tokens_in": answer.tokens_in,
                    "tokens_out": answer.tokens_out,
                    "cost_usd": answer.cost_usd,
                    "latency_ms": answer.latency_ms,
                    "request_id": answer.ledger_id,
                }
            except Exception as exc:
                result = {"case_id": case["id"], "mode": mode, "passed": False,
                          "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)
    total = len(results)
    pass_count = sum(bool(item["passed"]) for item in results)
    known_costs = [item["cost_usd"] for item in results if item.get("cost_usd") is not None]
    report = {
        "suite": "atomic-flow-grounded-code-qa",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model or getattr(backend.config, "model", "unknown"),
        "backend": backend.name,
        "mode": mode,
        "budget_tokens": budget_tokens,
        "summary": {
            "passed": pass_count, "total": total, "pass_rate": pass_count / total,
            "average_latency_ms": round(mean([item["latency_ms"] for item in results if "latency_ms" in item]), 2) if any("latency_ms" in item for item in results) else None,
            "tokens_in": sum(item.get("tokens_in", 0) for item in results),
            "tokens_out": sum(item.get("tokens_out", 0) for item in results),
            "cost_usd": round(sum(known_costs), 8) if len(known_costs) == len(results) else None,
        },
        "results": results,
    }
    target = Path(output).expanduser() if output else user_dir() / "benchmarks" / "runs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report["output"] = str(target.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(target)
    return report
