"""Bounded public SymPy development pilot; proxy credentials are environment-only.

Baseline performs one direct completion. Atomic uses durable TaskRunner planning,
source-scoped plan approval and execution. Independent oracle grading is separate.
No account discovery, quota lookup, credential refresh, provider switching or
automatic retries are implemented. The authorized host owns quota admission.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path

from evaluation_stage4 import checked_git, digest, load_manifest, patch_paths

from atomic_flow.agents.execution import TaskCancelled
from atomic_flow.backends.base import ChatResult
from atomic_flow.backends.cancellation import cancellation_scope, current_check
from atomic_flow.backends.http import TransportError
from atomic_flow.backends.openai_compatible import OpenAICompatibleBackend
from atomic_flow.config import AtomicConfig, BackendConfig
from atomic_flow.llm.request_budget import admit_request

TASK_ID = "sympy__sympy-21614"
SOURCE = "sympy/core/function.py"
MODEL = "google/gemini-3.1-flash-lite-preview"


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def valid_amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class PilotLimit(TransportError):
    pass


class PilotBackend:
    """Physical-send meter with shared state across TaskBudgetBackend copies."""

    def __init__(self, transport, output, limits, deadline):
        self.transport = transport
        self.name, self.config = transport.name, transport.config
        self.output, self.limits, self.deadline = Path(output), limits, deadline
        self.calls = []

    def checkpoint(self):
        write_json(self.output / "attempts.json", self.calls)

    def chat(self, messages, *, json_mode=False, model=None):
        if model is not None and model != self.config.model:
            raise PilotLimit("pilot model override refused", outcome="not_sent")
        if len(self.calls) >= self.limits["max_calls"]:
            raise PilotLimit("pilot physical-call cap reached", outcome="not_sent")
        if any(row["cost_usd"] is None for row in self.calls):
            raise PilotLimit("previous pilot charge is unknown; no follow-up", outcome="not_sent")
        known = sum(row["cost_usd"] for row in self.calls)
        if known >= self.limits["max_reported_cost_usd"]:
            raise PilotLimit("pilot reported-cost stop reached", outcome="not_sent")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PilotLimit("pilot deadline before submission", outcome="not_sent")
        used = sum(row["estimated_input_tokens"] for row in self.calls)
        config = self.config.model_copy(update={"timeout_seconds": min(self.config.timeout_seconds, remaining),
                                               "max_retries": 0, "max_output_tokens": self.limits["max_output_tokens"]})
        admission = admit_request(messages, config)
        if used + admission.estimated_input_tokens > self.limits["max_input_tokens"]:
            raise PilotLimit("pilot aggregate input estimate cap reached", outcome="not_sent")
        row = {"attempt": len(self.calls) + 1, "status": "in_flight", "cost_usd": None,
               "tokens_in": None, "tokens_out": None, "estimated_input_tokens": admission.estimated_input_tokens,
               "request_sha256": digest(messages), "request_outcome": "unknown"}
        self.calls.append(row)
        self.checkpoint()  # Persist intent before sending, including crash uncertainty.
        selected = copy.copy(self.transport)
        selected.config = config
        selected.route_attempt_id = "pilot_" + str(row["attempt"])  # Disable hidden optional-parameter retries.
        selected.route_deadline = self.deadline
        inherited_check = current_check()

        def check():
            if inherited_check:
                inherited_check()
            if time.monotonic() >= self.deadline:
                raise TaskCancelled("pilot deadline reached")

        response = None
        error = None
        started = time.monotonic()
        try:
            with cancellation_scope(check):
                # Kaggle proxy rejects response_format for some models. Prompts
                # request JSON, but this transport does not negotiate or retry.
                response = selected.chat(messages, json_mode=False)
        except BaseException as exc:
            error = exc
            if isinstance(getattr(exc, "usage_result", None), ChatResult):
                response = exc.usage_result
            else:
                outcome = getattr(exc, "request_outcome", "unknown")
                row.update(status="error", error_type=type(exc).__name__, request_outcome=outcome)
                if outcome in {"not_sent", "refused"}:
                    row.update(cost_usd=0.0, tokens_in=0, tokens_out=0)
        finally:
            if response is not None:
                cost = response.cost_usd if valid_amount(response.cost_usd) else None
                row.update(status="response", request_outcome="completed", cost_usd=cost,
                           tokens_in=response.tokens_in if type(response.tokens_in) is int and response.tokens_in >= 0 else None,
                           tokens_out=response.tokens_out if type(response.tokens_out) is int and response.tokens_out >= 0 else None,
                           truncated=response.truncated, cost_source=response.cost_source,
                           response_sha256=digest(response.content.encode()))
                if error:
                    row["error_type"] = type(error).__name__
            row["wall_seconds"] = time.monotonic() - started
            self.checkpoint()  # Usage survives output parsing, model shape and cancellation errors.
        if error:
            raise error
        if response.cost_usd is not None and known + response.cost_usd > self.limits["max_reported_cost_usd"]:
            exc = PilotLimit("reported pilot charge exceeded stop threshold", outcome="completed")
            exc.usage_result = response
            raise exc
        return response


def source_excerpt(source):
    """Frozen issue-relevant context; no gold/test oracle content is read."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("class Derivative("))
    finish = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("class ")), len(lines))
    excerpt = "\n".join(lines[start:min(finish, start + 550)])
    if len(excerpt.encode()) > 32_000:
        raise ValueError("review source excerpt; fixed context exceeds pilot byte cap")
    return excerpt


def parse_patch(content):
    text = content.strip()
    if text.startswith("```diff") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("{"):
        obj = json.loads(text)
        if not isinstance(obj, dict) or set(obj) != {"patch"} or not isinstance(obj["patch"], str):
            raise ValueError("expected only patch JSON field")
        text = obj["patch"]
    if patch_paths(text) != {SOURCE}:
        raise ValueError("pilot candidate exceeds approved source path")
    return text.rstrip() + "\n"


def baseline(task, source, backend):
    messages = [{"role": "system", "content": "Solve the issue by changing the permitted source file. Return ONLY JSON with one string field 'patch' containing a unified diff. Do not change tests. Do not run commands."},
                {"role": "user", "content": "Issue:\n" + task["problem_statement"] + "\nPermitted source: " + SOURCE
                 + "\nBase commit: " + task["base_commit"] + "\nSource excerpt:\n" + source_excerpt(source)}]
    result = backend.chat(messages)
    return parse_patch(result.content), {"generation_kind": "one_direct_completion", "task_status": "finished"}


def atomic(task, work, source, backend, args):
    from atomic_flow.agents.changeset import ChangeSet
    from atomic_flow.agents.control import request_cancel
    from atomic_flow.agents.task_loop import TaskCapabilities, TaskRunner
    from atomic_flow.agents.tools import ROLE_TOOLS

    config = AtomicConfig(max_attempts=1, auto_mode=False)
    config.artifacts.save = False
    config.budget.daily_limit_usd = 0  # Pilot has its own sidecar and per-task ledger; no host-global spend mutation.
    config.task.max_model_calls = args.max_calls
    config.task.max_replans = 0
    config.task.default_max_output_tokens = args.max_output_tokens
    priced = backend.config.price_in_per_mtok is not None and backend.config.price_out_per_mtok is not None
    config.task.max_budget_usd = args.max_reported_cost if priced else 0
    config.task.fail_on_budget_exceeded = priced
    config.task.final_check = "syntax"
    config.execution.mode = "local"
    config.context_budget_tokens = 6000
    tools = {role: [name for name in names if name in {"read_node", "search_tree", "find_callers", "search_text", "read_file", "find_files", "read_result", "patch_file", "edit_node"}]
             for role, names in ROLE_TOOLS.items()}

    def capabilities(context):
        return TaskCapabilities(role_tools=tools)

    def approve(tool, *unused):
        return tool in {"patch_file", "edit_node", "read_file", "read_node", "search_tree", "search_text", "find_files", "find_callers", "read_result"}

    runner = TaskRunner(config, work, backend, approval_callback=approve, capability_factory=capabilities)
    query = task["problem_statement"] + "\nHost scope: only " + SOURCE + ". Tests are independently run AFTER generation. " \
            "Use verify_with=py_compile for the pilot syntax check. No test execution, shell, dependency installation or test edits are permitted."
    prepared = runner.prepare(query, candidates=[SOURCE], n_files=1)
    if not prepared.plan_ready:
        return "", {"task_status": prepared.status, "task_id": prepared.id, "generation_kind": "TaskRunner"}
    if any(step.verify_with != "py_compile" for step in prepared.steps):
        return "", {"task_status": "plan_policy_refused", "task_id": prepared.id, "generation_kind": "TaskRunner"}
    review = runner.review(prepared, allowed_paths=[SOURCE])
    prepared = runner.approve(prepared, review["digest"], allowed_paths=[SOURCE])
    timer = threading.Timer(max(0.001, backend.deadline - time.monotonic()), lambda: request_cancel(prepared.id))
    timer.daemon = True
    timer.start()
    try:
        finished = runner.resume(prepared)
    finally:
        timer.cancel()
    candidate = work
    if finished.status != "done" and finished.changeset_id:
        candidate = ChangeSet.open(work, finished.changeset_id).workspace
    after = (candidate / SOURCE).read_text(encoding="utf-8")
    patch = "".join(difflib.unified_diff(source.splitlines(keepends=True), after.splitlines(keepends=True),
                                         fromfile="a/" + SOURCE, tofile="b/" + SOURCE))
    return patch, {"task_status": finished.status, "task_id": finished.id, "generation_kind": "TaskRunner",
                   "model_planning": True, "reviewed_paths": [SOURCE], "completion_report": finished.completion_report}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--arm", choices=("baseline", "atomic"), required=True)
    result.add_argument("--mirror", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--model", default=MODEL)
    result.add_argument("--max-calls", type=int, default=4)
    result.add_argument("--max-output-tokens", type=int, default=2048)
    result.add_argument("--max-input-tokens", type=int, default=60_000)
    result.add_argument("--max-wall-seconds", type=float, default=120)
    result.add_argument("--max-reported-cost", type=float, default=.05)
    result.add_argument("--price-in", type=float)
    result.add_argument("--price-out", type=float)
    result.add_argument("--yes", action="store_true")
    return result


def main():
    args = parser().parse_args()
    if not args.yes or args.model != MODEL or not 1 <= args.max_calls <= 4 or not 16 <= args.max_output_tokens <= 2048:
        raise ValueError("explicit authorized fixed-model pilot and bounded caps required")
    if not 0 < args.max_wall_seconds <= 120 or not 1 <= args.max_input_tokens <= 60_000 or not 0 < args.max_reported_cost <= .05:
        raise ValueError("pilot caps exceed reviewed bounds")
    if (args.price_in is None) != (args.price_out is None) or any(v is not None and not valid_amount(v) for v in (args.price_in, args.price_out)):
        raise ValueError("both explicit nonnegative prices or neither required")
    endpoint, key = os.getenv("MODEL_PROXY_URL", ""), os.getenv("MODEL_PROXY_API_KEY", "")
    if not endpoint or not key:
        raise ValueError("host must provide process-only model proxy credentials")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("fresh output directory required; pilot is never automatically resumed")
    root = Path(__file__).resolve().parents[1]
    task = next(t for t in load_manifest(root / "benchmarks/stage4/corpus.json")["tasks"] if t["instance_id"] == TASK_ID)
    limits = {"max_calls": args.max_calls, "max_output_tokens": args.max_output_tokens,
              "max_input_tokens": args.max_input_tokens, "max_wall_seconds": args.max_wall_seconds,
              "max_reported_cost_usd": args.max_reported_cost}
    report = {"version": 1, "instance_id": TASK_ID, "arm": args.arm, "base_commit": task["base_commit"],
              "model": args.model, "provider": "host_model_proxy", "limits": limits, "status": "not_run",
              "calls": 0, "tokens_in": None, "tokens_out": None, "cost_usd": None, "cost_complete": False,
              "human_interventions": 0, "model_quality_measured": False,
              "experiment": "exposed_public_development_pilot_not_formal_matched_comparison",
              "money_admission": "declared_estimate_not_billing_guarantee" if args.price_in is not None else "disabled_unknown_tariff_reported_cost_stop_only",
              "quota_admission": "authorized_host_responsibility", "wall_enforcement": "cooperative_deadline_and_bounded_transport_not_os_hard_kill"}
    write_json(output / "report.json", report)
    started = time.monotonic()
    config = BackendConfig(type="kaggle", name="host_model_proxy", model=args.model,
                           base_url=endpoint.rstrip("/") if endpoint.rstrip("/").endswith("/openapi") else endpoint.rstrip("/") + "/openapi",
                           api_key_env="MODEL_PROXY_API_KEY", max_retries=0, timeout_seconds=args.max_wall_seconds,
                           max_output_tokens=args.max_output_tokens, price_in_per_mtok=args.price_in, price_out_per_mtok=args.price_out)
    backend = PilotBackend(OpenAICompatibleBackend(config, backend_name="host_model_proxy"), output, limits, started + args.max_wall_seconds)
    previous_home = os.environ.get("ATOMIC_FLOW_HOME")
    os.environ["ATOMIC_FLOW_HOME"] = str(output / "atomic-state")
    try:
        with tempfile.TemporaryDirectory(prefix="stage4-generation-") as temp:
            work = Path(temp) / "repo"
            checked_git(["clone", "--no-checkout", "--no-hardlinks", str(Path(args.mirror).resolve()), str(work)])
            checked_git(["checkout", "--detach", task["base_commit"]], work)
            if checked_git(["rev-parse", "HEAD"], work) != task["base_commit"]:
                raise ValueError("generation source identity mismatch")
            source = (work / SOURCE).read_text(encoding="utf-8")
            patch, detail = baseline(task, source, backend) if args.arm == "baseline" else atomic(task, work, source, backend, args)
            report.update(detail)
            if patch:
                if patch_paths(patch) != {SOURCE}:
                    raise ValueError("candidate scope mismatch")
                (output / "candidate.patch").write_bytes(patch.encode())
                report["patch_sha256"] = digest(patch.encode())
            report["status"] = "finished" if patch and detail["task_status"] in {"finished", "done"} else "failed"
    except BaseException as exc:
        outcome = getattr(exc, "request_outcome", "unknown")
        report.update(status="failed", error_type=type(exc).__name__, request_outcome=outcome)
        if time.monotonic() >= backend.deadline:
            report["status"] = "timeout"
        elif backend.calls and backend.calls[-1].get("request_outcome") == "unknown":
            report["status"] = "unknown_provider_outcome"
        # Never log provider exception text, URLs, headers or environment values.
    finally:
        if previous_home is None:
            os.environ.pop("ATOMIC_FLOW_HOME", None)
        else:
            os.environ["ATOMIC_FLOW_HOME"] = previous_home
        rows = backend.calls
        complete = bool(rows) and all(row["cost_usd"] is not None for row in rows)
        report.update(calls=len(rows), wall_seconds=time.monotonic() - started, cost_complete=complete,
                      cost_usd=sum(row["cost_usd"] for row in rows) if complete else None,
                      known_cost_lower_bound_usd=sum(row["cost_usd"] or 0 for row in rows))
        for token in ("tokens_in", "tokens_out"):
            report[token] = sum(row[token] for row in rows) if rows and all(row[token] is not None for row in rows) else None
        write_json(output / "report.json", report)
    print(json.dumps({k: report[k] for k in ("instance_id", "arm", "status", "calls", "cost_usd", "cost_complete")}))
    return 0 if report["status"] == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
