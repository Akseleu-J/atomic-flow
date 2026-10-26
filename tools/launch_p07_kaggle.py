"""Single-send scheduling/status/download. Token is read only from process environment."""
import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from p07_campaign import save

SERVICE = "https://api.kaggle.com/v1/benchmarks.BenchmarkTasksApiService/"


def request(method, body, service=SERVICE):
    token = os.environ.get("KAGGLE_API_TOKEN")
    if not token:
        raise ValueError("provide KAGGLE_API_TOKEN in this process, never in source/CLI arguments")
    req = urllib.request.Request(service + method, json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        return urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Kaggle HTTP " + str(exc.code) + "; reconcile status before scheduling again") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "schedule", "download"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--owner", default="akseley")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.owner != "akseley" or args.version < 1:
        raise ValueError("this campaign is authorized only for the configured owner 2")
    if args.receipt.exists():
        raise ValueError("fresh receipt required; never replay an earlier intent")
    manifest = json.loads((args.bundle / "launch-manifest.json").read_text())
    source = (args.bundle / "campaign_task.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["task_source_sha256"]:
        raise ValueError("task source changed after preparation")
    slug = {"ownerSlug": args.owner, "taskSlug": manifest["task_slug"], "versionNumber": args.version}
    with request("ListBenchmarkTaskRuns", {"taskSlug": slug, "pageSize": 100}) as response:
        state = json.load(response)
    if args.action == "status":
        save(args.receipt, {"p07": "OPEN", "state": state})
        print(json.dumps(state, indent=2))
        return
    matching = [r for r in state.get("runs", []) if r.get("modelVersionSlug") == manifest["schedule_model_slug"]]
    if args.action == "schedule":
        if not args.yes or matching or state.get("nextPageToken"):
            raise ValueError("explicit --yes and fully reconciled absence of a prior model run required")
        with request("GetModelProxyQuotas", {}, "https://api.kaggle.com/v1/models.ModelProxyApiService/") as response:
            quota = json.load(response)
        if not quota.get("quotaBalances"):
            raise ValueError("quota observation unavailable")
        for balance in quota["quotaBalances"]:
            if balance["totalQuotaAllowed"] - balance["quotaUsed"] < manifest["total_budget_usd"]:
                raise ValueError("remaining Kaggle quota below declared campaign budget")
        body = {"taskSlugs": [slug], "modelVersionSlugs": [manifest["schedule_model_slug"]]}
        save(args.receipt, {"status": "schedule_intent_outcome_unknown", "body": body, "manifest": manifest,
                            "state_before": state, "quota_before": quota})
        # One physical scheduling request. Never CLI with_retry, never automatic retry.
        with request("BatchScheduleBenchmarkTaskRuns", body) as response:
            result = json.load(response)
        save(args.receipt, {"status": "schedule_response", "body": body, "response": result, "manifest": manifest,
                            "quota_before": quota})
        print(json.dumps(result, indent=2))
    else:
        run = next((r for r in matching if r.get("id") == args.run_id), None)
        if not run or run.get("state") not in {"BENCHMARK_TASK_RUN_STATE_COMPLETED", "BENCHMARK_TASK_RUN_STATE_ERRORED"}:
            raise ValueError("select a terminal run of the exact requested model/version")
        path = args.receipt.with_suffix(".zip")
        if path.exists():
            raise ValueError("archive already exists")
        hasher, total = hashlib.sha256(), 0
        with request("DownloadBenchmarkTaskRunOutput", {"runId": args.run_id, "includeSource": False}) as response, path.open("xb") as target:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 4 * 1024**3:
                    raise ValueError("download exceeds 4 GiB bound; partial archive is not accepted")
                hasher.update(chunk)
                target.write(chunk)
        save(args.receipt, {"p07": "OPEN", "run": run, "archive": str(path), "bytes": total,
                            "sha256": hasher.hexdigest(), "oracle_status": "not_assessed"})


if __name__ == "__main__":
    main()
