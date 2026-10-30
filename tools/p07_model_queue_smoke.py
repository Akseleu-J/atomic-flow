"""Real GitHub data-queue roundtrip; scripted broker, zero provider inference."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from atomic_flow.backends.github_queue import MODEL, GitHubContentsQueueTransport, GitHubQueueBackend, digest
from atomic_flow.config import BackendConfig

PROFILE = {"schema_version": 1, "kind": "scripted_model_queue_smoke", "physical_provider_calls": 0, "model": MODEL}
PROMPT = "Return the fixed public scripted transport marker. No provider inference is permitted."
ANSWER = "p07_scripted_queue_roundtrip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue-branch", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("fresh output required")
    args.output.mkdir(parents=True)
    sha = os.environ["GITHUB_SHA"]
    identity = {"campaign_id": "p07_zero_provider_queue_smoke", "owner": "akseley", "arm": "scripted",
                "task_id": "public_transport_smoke", "framework_sha": sha, "workflow_sha": sha,
                "profile_sha256": digest(PROFILE)}
    report = {"status": "not_qualified", "tested_commit": sha, "provider_calls": 0,
              "model_generation": False, "scope": "real GitHub queue with scripted host broker only",
              "identity": identity}
    try:
        transport = GitHubContentsQueueTransport(os.environ["GITHUB_REPOSITORY"], args.queue_branch,
                                                os.environ["GITHUB_TOKEN"])
        config = BackendConfig(model=MODEL, api_key_env="", temperature=None, max_input_tokens=1000,
                               max_output_tokens=128, max_retries=0, timeout_seconds=300)
        backend = GitHubQueueBackend(config, transport, identity, args.output / "attempts", wait_seconds=240)
        result = backend.chat([{"role": "user", "content": PROMPT}])
        report.update(result=asdict(result), backend_name=backend.name)
        if result.content != ANSWER or result.cost_usd != 0 or result.cost_source != "scripted_zero_provider_calls":
            raise ValueError("scripted queue marker or zero-inference scope mismatch")
        report["status"] = "scripted_real_github_queue_qualified"
    except Exception as exc:
        report.update(status="not_qualified", error_type=type(exc).__name__)
    finally:
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "tested_commit", "provider_calls", "model_generation")}))
    return 0 if report["status"] == "scripted_real_github_queue_qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
