"""Read-only P00 source/contracts/claims/dependency and evidence inventory.

No model, fixture execution, dependency install, baseline mutation or imports of
project runtime. Evidence status is copied as historical data, never upgraded.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def collect(root=ROOT):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    files = sorted([*root.glob("src/**/*.py"), *root.glob("tests/test_*.py")])
    source, tests, imports = {}, {}, {}
    for path in files:
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        source[rel] = sha(raw.replace(b"\r\n", b"\n"))
        module = ast.parse(raw, filename=rel)
        if rel.startswith("tests/"):
            tests[rel] = sorted(n.name for n in ast.walk(module) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and n.name.startswith("test_"))
        else:
            imports[rel] = sorted({n.module or "" for n in ast.walk(module) if isinstance(n, ast.ImportFrom)} |
                                  {a.name for n in ast.walk(module) if isinstance(n, ast.Import) for a in n.names})
    historical = {}
    for name in ("validation/p00-baseline-20261024/card.json", "validation/marathon-20261025/baseline-card.json",
                 "validation/p00-baseline-20261024/acceptance-registry.json",
                 "validation/p00-baseline-20261024/coverage-map.json"):
        raw = (root / name).read_bytes()
        parsed = json.loads(raw)
        historical[name] = {"sha256": sha(raw), "bytes": len(raw),
                            "status": "retained_historical_not_rerun",
                            "source": parsed.get("source", parsed.get("head", parsed.get("baseline_head"))),
                            "model_calls": parsed.get("model_calls", 0)}
    installed = sorted({d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                        if d.metadata.get("Name")}.items())
    evidence = {"schema": "p00-contract-evidence/1", "phase": "P00", "model_calls": 0,
                "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "environment": {"python": sys.version, "platform": platform.platform(),
                                "dependencies": dict(installed)},
                "source_normalized_sha256": source, "pyproject_sha256": sha((root / "pyproject.toml").read_bytes()),
                "frozen_run_contract": {"provider": "none (offline P00)", "model": "none", "execution_mode": "static/source-only",
                    "dataset_identity": "existing pinned benchmark manifests; exposed SymPy remains development-only",
                    "config_identity": "normalized source config.py hash plus pyproject digest; no process credentials captured",
                    "limits": {"new_model_calls": 0, "new_paid_usd": 0}},
                "declared_dependencies": project["project"]["dependencies"],
                "optional_dependencies": project["project"]["optional-dependencies"],
                "static_module_imports": imports, "coverage_test_inventory": tests,
                "w_coverage_map": {
                    "W1_source_integrity": ["tests/test_p01_contract.py", "tests/test_source_freshness.py", "tests/test_changeset.py"],
                    "W2_recovery": ["tests/test_host_reconciliation.py", "tests/test_unresolved_effects.py"],
                    "W3_routing": ["tests/test_routed_accounting.py", "tests/test_routing.py"],
                    "W4_execution": ["tests/test_execution.py", "tests/test_remote_execution.py"],
                    "W5_release": ["tools/release_acceptance.py", ".github/workflows/integrations-release.yml"],
                    "W6_composition": ["tests/test_composition_acceptance.py", ".github/workflows/integrations-release.yml"],
                    "W7_scale": ["tools/p00_baseline.py", "tools/resource_scale_smoke.py"],
                    "W8_quality": ["tests/test_evaluation_integrity.py", "tools/evaluation_live_pilot.py"],
                    "W9_acceptance": ["docs/PRODUCTION_PLAN.md", "docs/PRODUCTION_ROADMAP.md"]},
                "historical_evidence": historical,
                "legacy_claim_corrections": [{
                    "artifact": "validation/p00-baseline-20261024/acceptance-registry.json",
                    "historical_label": "ORACLE_PASS for live edit pilot",
                    "current_interpretation": "grader calibration only; baseline patch failed and atomic had no accepted edit",
                    "agent_task_quality": "not_assessed; no accepted atomic patch",
                    "historical_bytes": "retained unchanged"}],
                "acceptance_record_contract": {"required": ["phase", "source_sha", "dirty_state", "config_digest", "environment_identity",
                    "commands", "exit_codes", "artifact_hashes", "failures", "skips", "unknown_charges", "decision", "reviewer"],
                    "dimensions": ["local", "CI", "live", "consumer"], "no_inferred_pass": True},
                "design_contracts": ["docs/P00_CORE_CONTRACTS_2026-10-25.md", "docs/P01_GRAPH_COMPATIBILITY.md"],
                "comparison": {"dataset": "30 pinned development tasks; replace exposed SymPy holdout before confirmatory run",
                               "arms": "capable plain agent and atomic; same frozen model/provider/caps alternating order",
                               "hypothesis": "quality margin 5 percentage points and >=20% lower full cost per verified success",
                               "paid_budget": "none authorized by this document; each live run needs explicit run budget",
                               "quality_status": "not_assessed", "historical_unknown_cost": "retained; not zero"},
                "limitations": ["Static inventory maps tests, not proof of requirement coverage",
                                "Installed distributions are inventory, not a release lock/SBOM attestation",
                                "P00 baseline indicators are historical host observations, not performance acceptance"]}
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fresh output required; historical evidence must never be overwritten")
    evidence = collect()
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    evidence["source_head"] = result.stdout.strip()
    status = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True)
    evidence["dirty_files"] = status.stdout.splitlines()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"source_files": len(evidence["source_normalized_sha256"]),
                      "test_files": len(evidence["coverage_test_inventory"]), "model_calls": 0,
                      "historical_bytes_unchanged": True}))


if __name__ == "__main__":
    main()
