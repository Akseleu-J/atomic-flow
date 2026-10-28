"""Full public TaskRunner/Docker journey over GitHub JSON data queue; scripted host broker only."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from p07_batch_obligation_journey import journey

from atomic_flow.backends.github_queue import MODEL, GitHubContentsQueueTransport, GitHubQueueBackend, digest
from atomic_flow.config import BackendConfig

CAMPAIGN_ID = "p07_public_task_queue_smoke"
TASK_ID = "public_coupled_queue_smoke"
WIRE_CONFIG = {"model": MODEL, "max_input_tokens": 16384, "max_output_tokens": 4096,
               "temperature": None, "max_retries": 0}
PROFILE = {"schema_version": 1, "kind": "scripted_public_queued_task_journey",
           "campaign_id": CAMPAIGN_ID, "task_id": TASK_ID, "owner": "akseley", "arm": "scripted",
           "physical_provider_calls": 0, "model_generation": False, "wire_config": WIRE_CONFIG,
           "price_in_per_mtok": "0", "price_out_per_mtok": "0",
           "check_command": "check", "max_task_calls": 40, "answerer_steps": 7}
PROFILE_SHA256 = digest(PROFILE)
_ROLE = re.compile(r"<role>(\w+)</role>")


class ObservedQueueBackend(GitHubQueueBackend):
    """Share only observation-list state across the ordinary TaskBudget shallow copy."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._roles = []

    def chat(self, messages, **kwargs):
        user = messages[1]["content"] if len(messages) > 1 else ""
        found = _ROLE.findall(user)
        role = found[0] if len(found) == 1 else "host_planner"
        self._roles.append(role)
        return super().chat(messages, **kwargs)

    def roles(self):
        return list(self._roles)


def queued_journey(output: Path, queue_branch: str):
    """Inject the actual queue adapter into the existing full Docker TaskRunner path."""
    sha = os.environ["GITHUB_SHA"]
    identity = {"campaign_id": CAMPAIGN_ID, "owner": "akseley", "arm": "scripted", "task_id": TASK_ID,
                "framework_sha": sha, "workflow_sha": sha, "profile_sha256": PROFILE_SHA256}
    transport = GitHubContentsQueueTransport(os.environ["GITHUB_REPOSITORY"], queue_branch,
                                            os.environ["GITHUB_TOKEN"])
    config = BackendConfig(**WIRE_CONFIG, api_key_env="", timeout_seconds=300,
                           price_in_per_mtok=0, price_out_per_mtok=0)
    created = []

    def factory(*, originals, step, output):
        backend = ObservedQueueBackend(config, transport, identity, output / "queue-attempts",
                                       poll_seconds=1, wait_seconds=240)
        created.append(backend)
        return backend

    report = journey(output, "check", backend_factory=factory)
    report.update(tested_commit=sha, identity=identity, profile=PROFILE, profile_sha256=PROFILE_SHA256,
                  provider_calls=0, model_generation=False, model_transport="real_github_queue_scripted_windows_broker",
                  independent_requirement_success=False, hidden_oracle="not_run",
                  scope="Public two-file TaskRunner/Docker failure, builtin replan, repair and publication over data queue")
    if created:
        report["backend_name"] = created[0].name
    events = report.get("budget_events", [])
    roles = report.get("backend_roles", [])
    graph = report.get("graph_sync")
    qualified = (
        report.get("status") == "scripted_public_coupled_obligation_journey_qualified"
        and report.get("task_status") == "done" and report.get("replan_count") == 1
        and report.get("completion", {}).get("ready_to_publish") is True
        and report.get("completion", {}).get("check_completed") is True
        and report.get("completion", {}).get("check_passed") is True
        and report.get("completion", {}).get("check_obligation_digest") == report.get("obligation_digest")
        and isinstance(graph, dict) and graph.get("failed") is False
        and graph.get("coverage_complete") is True
        and roles == ["parser", "answerer", "answerer", "critic", "host_planner", "parser",
                      "answerer", "answerer", "answerer", "answerer", "answerer", "critic"]
        and bool(events) and len(events) == len(roles)
        and report.get("budget_spent_usd") == 0
        and all(event.get("status") == "completed" and event.get("cost_usd") == 0
                and event.get("cost_source") == "scripted_zero_provider_calls" for event in events)
    )
    report["status"] = "scripted_queued_task_journey_qualified" if qualified else "not_qualified"
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--queue-branch", required=True)
    args = parser.parse_args()
    report = queued_journey(args.output, args.queue_branch)
    print(json.dumps({key: report.get(key) for key in
                      ("status", "tested_commit", "task_status", "replan_count", "provider_calls", "model_generation")}))
    return 0 if report["status"] == "scripted_queued_task_journey_qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
