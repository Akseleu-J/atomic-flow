"""Run a paired, read-only QA pilot using process-local Kaggle Model Proxy credentials."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from atomic_flow.backends.router import make_backend
from atomic_flow.config import AtomicConfig
from atomic_flow.qa_evaluation import run_qa_comparison

DEFAULT_CASES = ["invoice-subtotal", "refund-call-chain", "checkout-composition", "unanswerable-payout",
                 "russian-discount", "sourcing-pipeline", "ru-shipping", "multi-checkout-flow"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="google/gemini-3.1-flash-lite-preview")
    parser.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=64)
    parser.add_argument("--max-reported-cost", type=float, default=0.5)
    parser.add_argument("--yes", action="store_true", help="Acknowledge consumption of Kaggle inference quota")
    args = parser.parse_args()
    if not args.yes:
        parser.error("Pass --yes to acknowledge Kaggle quota consumption")
    if args.model.split("/", 1)[0] not in {"qwen", "google", "deepseek-ai", "ibm"}:
        parser.error("This pilot only permits the selected non-OpenAI/non-Anthropic Kaggle models")
    config = AtomicConfig(max_attempts=1)
    config.artifacts.save = False
    config.backends["kaggle"].max_retries = 0
    config.backends["kaggle"].max_output_tokens = 2048
    # Kaggle quota is free to the user; provider-reported costs still take precedence.
    config.backends["kaggle"].price_in_per_mtok = 0
    config.backends["kaggle"].price_out_per_mtok = 0
    backend = make_backend(config, "kaggle", args.model)
    report = run_qa_comparison(backend, config, case_ids=args.cases, output=args.output,
                               max_calls=args.max_calls, max_reported_cost=args.max_reported_cost)
    print(json.dumps({"summary": report["summary"], "stopped_early": report.get("stopped_early", False)}))
    return 1 if report.get("stopped_early") else 0


if __name__ == "__main__":
    raise SystemExit(main())
