"""One physical live call through the Rust kernel backend (protocol §4).

Sequential, single attempt, no retries (the Rust router has none). Quota is
read before and after through the authorized account; proxy credentials are
passed to the child process via environment only and never logged. The
report records every attempt outcome; unknown stays unknown.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR = Path(__file__).parent
REQUEST = json.loads((RUN_DIR / "request.json").read_text(encoding="utf-8-sig"))
OUT = RUN_DIR / "report.json"

report = {
    "run_id": REQUEST["run_id"],
    "kind": "LIVE_API_RESPONSE (transport smoke of the Rust OpenAI-compatible backend)",
    "credential_label": os.environ["ATOMIC_CREDENTIAL_LABEL"],
    "owner_authorized": True,
    "authorized_by": "owner granted Kaggle3_TOKEN for one bounded transport smoke (26.10.2026)",
    "utc_start": datetime.now(timezone.utc).isoformat(),
    "caps": REQUEST["caps"],
    "physical_attempts": [],
    "model_calls": 0,
}

exit_code = 0
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.models.types.model_proxy_api_service import ApiGetModelProxyQuotasRequest

        api = KaggleApi()
        api.authenticate()
        with api.build_kaggle_client() as client:
            before = client.models.model_proxy_api_client.get_model_proxy_quotas(
                ApiGetModelProxyQuotasRequest()
            )
        proxy = api._fetch_model_proxy_env(source="auth")

    def balances(quota):
        return [
            {"period": str(b.refill_period), "used": b.quota_used, "allowed": b.total_quota_allowed}
            for b in quota.quota_balances
        ]

    report["quota_before"] = balances(before)
    if any(b["allowed"] - b["used"] < REQUEST["caps"]["min_quota_reserve"] for b in report["quota_before"]):
        raise RuntimeError("insufficient quota reserve before the run; aborting without any call")

    child_env = {**os.environ, **{k: v for k, v in proxy.items() if v}}
    # Kaggle proxy contract (backends/kaggle.py): the chat path is
    # {endpoint}/openapi/chat/completions — the platform URL needs the
    # /openapi suffix appended exactly once.
    proxy_url = child_env.get("MODEL_PROXY_URL", "").rstrip("/")
    if not proxy_url.endswith("/openapi"):
        proxy_url += "/openapi"
    report["model_allowlist"] = child_env.get("LLMS_AVAILABLE", "")
    default_model = child_env.get("LLM_DEFAULT", "").strip()
    if default_model:
        report["model_choice"] = "account LLM_DEFAULT"
    elif REQUEST.get("model_override"):
        default_model = REQUEST["model_override"]
        report["model_choice"] = REQUEST.get("model_choice_note", "owner-pinned model override")
    else:
        # No default on this account: take the FIRST entry of the account's
        # own curated allowlist and record the decision explicitly.
        allowlisted = [item.strip() for item in report["model_allowlist"].split(",") if item.strip()]
        if not allowlisted:
            raise RuntimeError("proxy env has neither LLM_DEFAULT nor LLMS_AVAILABLE; refusing to guess")
        default_model = allowlisted[0]
        report["model_choice"] = "first entry of the account allowlist (no default configured)"
    report["model"] = default_model
    attempt = {
        "attempt": 1,
        "command": [
            REQUEST["af_exe"], "backend", "call",
            "--base-url", proxy_url,
            "--api-key-env", "MODEL_PROXY_API_KEY",
            "--model", report["model"],
            "--prompt", REQUEST["prompt"],
        ],
        "note": "MODEL_PROXY_API_KEY value is in child env only; never in argv or logs",
    }
    report["model_calls"] = 1
    completed = subprocess.run(
        attempt["command"],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=REQUEST["caps"]["timeout_seconds"],
        check=False,
    )
    attempt["exit_code"] = completed.returncode
    attempt["stdout_tail"] = completed.stdout[-2000:]
    attempt["stderr_tail"] = completed.stderr[-2000:]
    if completed.returncode == 0:
        attempt["outcome"] = "success"
        report["status"] = "LIVE_API_RESPONSE"
    elif "HOLD" in completed.stderr:
        attempt["outcome"] = "unknown"
        report["status"] = "UNKNOWN_HELD"
    else:
        attempt["outcome"] = "refused_or_failed"
        report["status"] = "FAILED"
    report["physical_attempts"].append(attempt)

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with api.build_kaggle_client() as client:
            after = client.models.model_proxy_api_client.get_model_proxy_quotas(
                ApiGetModelProxyQuotasRequest()
            )
    report["quota_after"] = balances(after)
except BaseException as exc:  # noqa: BLE001 - the report must capture every failure class
    report["error_type"] = type(exc).__name__
    report["error"] = str(exc)[:500]
    report["status"] = report.get("status", "ERROR")
    exit_code = 1

report["utc_end"] = datetime.now(timezone.utc).isoformat()
OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "status": report.get("status"),
    "model": report.get("model"),
    "attempts": len(report["physical_attempts"]),
    "model_calls": report["model_calls"],
    "report": str(OUT),
}))
raise SystemExit(exit_code)
