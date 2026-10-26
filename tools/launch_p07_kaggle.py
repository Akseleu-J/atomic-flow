"""Single-send scheduling/status/download. Token is read only from process environment."""
import argparse
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from p07_campaign import AUTHORIZED_OWNERS, MODEL, save

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
    parser.add_argument("--owner", choices=AUTHORIZED_OWNERS, help="Must equal this bundle's pinned authorized owner")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.version < 1:
        raise ValueError("positive task version required")
    if args.receipt.exists():
        raise ValueError("fresh receipt required; never replay an earlier intent")
    manifest = json.loads((args.bundle / "launch-manifest.json").read_text())
    owner = manifest.get("authorized_owner")
    if owner not in AUTHORIZED_OWNERS or args.owner is not None and args.owner != owner:
        raise ValueError("bundle owner must be one exact authorized owner and cannot be switched at launch")
    if (manifest.get("proxy_model_slug") != MODEL
            or manifest.get("schedule_model_slug") != "claude-sonnet-5-default"
            or not re.fullmatch(r"[a-z0-9-]{5,100}", manifest.get("task_slug", ""))):
        raise ValueError("prepared manifest does not identify the exact Sonnet 5 campaign")
    budget = manifest.get("total_budget_usd")
    if (type(budget) not in (int, float) or not math.isfinite(budget) or budget < 0
            or budget == 0 and manifest.get("preflight_only") is not True):
        raise ValueError("finite positive campaign budget required")
    source = (args.bundle / "campaign_task.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != manifest["task_source_sha256"]:
        raise ValueError("task source changed after preparation")
    with request("IntrospectToken", {"token": os.environ.get("KAGGLE_API_TOKEN", "")},
                 "https://api.kaggle.com/v1/security.OAuthService/") as response:
        identity = json.load(response)
    if identity.get("active") is not True or identity.get("username") != owner:
        raise ValueError("credential identity does not match the independently authorized bundle owner")
    slug = {"ownerSlug": owner, "taskSlug": manifest["task_slug"], "versionNumber": args.version}
    with request("ListBenchmarkTaskRuns", {"taskSlug": slug, "pageSize": 100}) as response:
        state = json.load(response)
    if args.action == "status":
        save(args.receipt, {"p07": "OPEN", "state": state})
        print(json.dumps(state, indent=2))
        return
    matching = [r for r in state.get("runs", []) if manifest.get("preflight_only") is True
                or r.get("modelVersionSlug") == manifest["schedule_model_slug"]]
    if args.action == "schedule":
        if manifest.get("preflight_only") is True:
            raise ValueError("preflight runs in task creation warmup; do not schedule a paid-model actor")
        fence = args.bundle / ("schedule-v" + str(args.version) + ".json")
        if fence.exists():
            raise ValueError("this bundle/version already has a scheduling intent; reconcile its receipt and remote state, never change receipt to retry")
        if not args.yes or matching or state.get("nextPageToken"):
            raise ValueError("explicit --yes and fully reconciled absence of a prior model run required")
        with request("GetModelProxyQuotas", {}, "https://api.kaggle.com/v1/models.ModelProxyApiService/") as response:
            quota = json.load(response)
        if not quota.get("quotaBalances"):
            raise ValueError("quota observation unavailable")
        for balance in quota["quotaBalances"]:
            total, used = balance.get("totalQuotaAllowed"), balance.get("quotaUsed", 0)
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (total, used)):
                raise ValueError("invalid quota observation")
            if total - used < manifest["total_budget_usd"]:
                raise ValueError("remaining Kaggle quota below declared campaign budget")
        body = {"taskSlugs": [slug], "modelVersionSlugs": [manifest["schedule_model_slug"]]}
        intent = {"status": "schedule_intent_outcome_unknown", "body": body, "manifest": manifest,
                  "state_before": state, "quota_before": quota, "receipt": str(args.receipt)}
        # Bundle-level O_EXCL fence prevents a different receipt or concurrent
        # launcher from replaying while Kaggle's run listing is not yet updated.
        with fence.open("x", encoding="utf-8") as stream:
            json.dump(intent, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        save(args.receipt, intent)
        # One physical scheduling request. Never CLI with_retry, never automatic retry.
        with request("BatchScheduleBenchmarkTaskRuns", body) as response:
            result = json.load(response)
        receipt = {"status": "schedule_response", "body": body, "response": result, "manifest": manifest,
                   "quota_before": quota}
        save(args.receipt, receipt)
        save(fence, receipt)
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
