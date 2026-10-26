"""Build a private, self-contained Kaggle Task; never uploads or starts inference."""
import argparse
import base64
import hashlib
import inspect
import json
import re
import urllib.request
import zipfile
from pathlib import Path

from evaluation_p07_source_pilot import admit_calibration
from evaluation_stage4 import load_manifest, patch_paths, protected_test_path
from p07_campaign import AUTHORIZED_OWNERS, MODEL, select, validate_config

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = "b44bb67bffb0a451d3434027316b94443ebfc314"
CATALOG = "https://api.kaggle.com/v1/benchmarks.BenchmarksApiService/ListBenchmarkModels"


def export_campaign(scratch, output, packet, needles, wrapper):
    """Export audit records, never target workspaces, including interrupted runs.

    Rebuild the ZIP from individually scrubbed records rather than copying an
    archive created before the wrapper knew whether the driver had finished.
    The original driver summary remains a separate record if status is amended.
    """
    excluded = {"workspace", "source", "mirrors", ".git", "venv", "__pycache__"}
    suffixes = {".json", ".jsonl", ".txt", ".log", ".patch", ".xml", ".yaml", ".yml"}
    output.mkdir(parents=True, exist_ok=False)
    records, originals = {}, {}
    omitted, redactions = 0, 0
    secrets = set(filter(None, needles))
    for needle in list(secrets):
        try:
            secrets.add(json.dumps(needle.decode("utf-8"), ensure_ascii=False)[1:-1].encode())
        except UnicodeError:
            pass

    def scrub(raw):
        nonlocal redactions
        for needle in sorted(secrets, key=len, reverse=True):
            redactions += raw.count(needle)
            raw = raw.replace(needle, b"[REDACTED]")
        return raw

    if scratch.exists():
        for path in sorted(scratch.rglob("*")):
            relative = path.relative_to(scratch)
            if not path.is_file():
                continue
            if path.is_symlink() or excluded.intersection(relative.parts) or path.suffix not in suffixes:
                omitted += 1
                continue
            original = path.read_bytes()
            raw = scrub(original)
            name = relative.as_posix()
            records[name] = raw
            originals[name] = hashlib.sha256(original).hexdigest()
    if "summary.json" in records:
        records["driver-summary.json"] = records["summary.json"]
        originals["driver-summary.json"] = originals.get("summary.json")
        try:
            summary = json.loads(records["summary.json"])
            if not isinstance(summary, dict):
                raise ValueError("driver summary must be an object")
        except (ValueError, UnicodeError):
            summary = {"p07": "OPEN", "rows": [], "oracle_status": "not_run",
                       "cost_complete": False, "attempt_outcome": "unknown_do_not_repeat",
                       "driver_summary_status": "malformed_preserved_as_record"}
    else:
        summary = {"p07": "OPEN", "rows": [], "oracle_status": "not_run",
                   "cost_complete": False, "attempt_outcome": "unknown_do_not_repeat"}
    summary["wrapper_receipt"] = wrapper
    if wrapper.get("status") != "campaign_returned":
        summary["original_cost_complete"] = summary.get("cost_complete")
        summary["cost_complete"] = False
        summary["attempt_outcome"] = "unknown_do_not_repeat"
    summary["artifact_scope"] = "audit records only; target workspaces/source/mirrors excluded"
    records["summary.json"] = scrub(json.dumps(summary, ensure_ascii=False, indent=2).encode())
    if "packet.json" not in records:
        records["packet.json"] = scrub(json.dumps(packet, ensure_ascii=False, indent=2).encode())
    for task in packet["tasks"]:
        task_id = task["instance_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            raise ValueError("unsafe export task identity")
        root = output / "tasks" / task_id
        for arm in ("baseline", "atomic"):
            (root / arm).mkdir(parents=True)
        prefix = "tasks/" + task_id + "/"
        name = prefix + "status.json"
        if name not in records:
            records[name] = json.dumps({"instance_id": task_id, "status": "not_observed_before_export",
                                       "oracle_status": "not_assessed"}).encode()
        for leaf in ("status.json", "source-manifest.json", "graph-preflight.json"):
            if prefix + leaf in records:
                (root / leaf).write_bytes(records[prefix + leaf])
    manifest = [{"path": name, "original_sha256": originals.get(name),
                 "export_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
                for name, raw in sorted(records.items())]
    receipt = {"schema_version": 1, "wrapper": wrapper, "record_count": len(records),
               "record_count_scope": "excluding_this_receipt",
               "redactions": redactions, "excluded_files": omitted,
               "exclusion_policy": sorted(excluded), "allowed_record_suffixes": sorted(suffixes),
               "full_target_snapshots_exported": False, "records": manifest, "p07": "OPEN"}
    records["export-receipt.json"] = scrub(json.dumps(receipt, ensure_ascii=False, indent=2).encode())
    for name in ("summary.json", "packet.json", "export-receipt.json", "framework-dependencies.txt"):
        if name in records:
            (output / name).write_bytes(records[name])
    archive = output / "results.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in sorted(records.items()):
            bundle.writestr(name, raw)
    (output / "archive-sha256.json").write_text(json.dumps({"file": "results.zip",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "bytes": archive.stat().st_size}), encoding="utf-8")
    return summary


def catalog_model():
    page, seen = {}, set()
    for _ in range(30):
        request = urllib.request.Request(CATALOG, json.dumps(page).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        for model in data.get("benchmarkModels", []):
            version = model.get("version", {})
            if version.get("modelProxySlug") == MODEL and version.get("allowModelProxy") is True:
                return model
        token = data.get("nextPageToken")
        if not token or token in seen:
            break
        seen.add(token)
        page = {"pageToken": token}
    raise ValueError("exact Sonnet 5 unavailable in Kaggle catalog; no model substitution")


def prepare(args):
    if not re.fullmatch(r"[a-z0-9-]{5,100}", args.slug):
        raise ValueError("safe, new private task slug required")
    if args.output.exists():
        raise ValueError("fresh output required; never replace an earlier experiment")
    preflight = getattr(args, "preflight", False)
    owner = getattr(args, "owner", "akseley")
    if owner not in AUTHORIZED_OWNERS:
        raise ValueError("exact explicitly authorized owner required")
    if args.budget is None and not preflight:
        raise ValueError("paid preparation requires an explicit --budget")
    config = dict(model=MODEL, authorized_owner=owner, count=args.count, total_budget_usd=0 if preflight else args.budget,
                  arm_budget_usd=0 if preflight else args.arm_budget, preflight_only=preflight,
                  max_calls=30, max_input_tokens=600000,
                  max_output_tokens=4096, deadline_seconds=900, price_in_per_mtok=2, price_out_per_mtok=10,
                  price_note="Direct Anthropic reference 2026-10-26; not a Kaggle billing guarantee. Provider cost required.")
    validate_config(config)
    catalog = catalog_model()
    tasks = []
    for task in load_manifest(ROOT / "benchmarks/stage4/corpus.json")["tasks"]:
        paths = patch_paths(task["patch"])
        if len(paths) != 1 or any(protected_test_path(p) for p in paths):
            raise ValueError("campaign currently requires one declared production source per task")
        receipt = args.calibrations / (task["instance_id"] + ".json")
        calibration = json.loads(receipt.read_text()) if receipt.exists() else {}
        if "report" in calibration:
            calibration = calibration["report"]
        # No gold patch, wrong patch, hidden tests, or oracle source in generator input.
        tasks.append({**{k: task[k] for k in ("instance_id", "repo", "base_commit", "problem_statement")},
                      "source_path": next(iter(paths)), "calibration": calibration})
    chosen = select(tasks, args.count)
    if not preflight:
        for task in chosen:
            admit_calibration(task["calibration"], task)
    if not preflight and len(chosen) * 2 * args.arm_budget > args.budget + 1e-9:
        raise ValueError("budget does not cover all selected paired arm caps; choose the 3-task pilot or revise explicit caps")
    runtime = (ROOT / "tools/p07_campaign.py").read_bytes()
    packet = {"framework": FRAMEWORK, "model_catalog_record": catalog, "config": config, "tasks": tasks,
              "runtime_sha256": hashlib.sha256(runtime).hexdigest(), "p07": "OPEN",
              "study": "exposed source-scoped development campaign; not blinded/full autonomous acceptance",
              "arm_order": "baseline/atomic on even selected index, atomic/baseline on odd selected index"}
    encoded_packet = base64.b64encode(json.dumps(packet).encode()).decode()
    encoded_runtime = base64.b64encode(runtime).decode()
    source = '''"""Private source-only P07 campaign. Creation warmup sends zero model prompts."""
import base64, hashlib, json, os, re, signal, subprocess, sys, tempfile, uuid, zipfile
from pathlib import Path
import kaggle_benchmarks as kbench
PACKET = json.loads(base64.b64decode("__PACKET__"))
RUNTIME = base64.b64decode("__RUNTIME__")
__EXPORT_HELPER__
@kbench.task(name="__SLUG__", description="Source-only Sonnet 5 diagnostic; independent container oracle runs separately; P07 OPEN.")
def run_campaign(llm) -> dict:
    actor = getattr(llm, "model", None)
    if not isinstance(actor, str):
        actor = getattr(llm, "name", "unknown")
    version = PACKET["model_catalog_record"]["version"]
    if not PACKET["config"]["preflight_only"] and actor not in {PACKET["config"]["model"], version["slug"], "anthropic/" + version["slug"]}:
        return {"status":"warmup_actor_refused", "actor":actor, "provider_calls":0, "p07":"OPEN"}
    if not PACKET["config"]["preflight_only"] and not all(os.environ.get(k) for k in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY")):
        return {"status":"proxy_environment_missing", "provider_calls":0, "p07":"OPEN"}
    if hashlib.sha256(RUNTIME).hexdigest() != PACKET["runtime_sha256"]:
        raise ValueError("runtime payload hash mismatch")
    output = Path("/kaggle/working") / ("p07-" + uuid.uuid4().hex[:12])
    needles = [os.environ.get(k, "").encode() for k in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY", "KAGGLE_API_TOKEN")]
    safe_env = {k:v for k,v in os.environ.items() if k in {"PATH","HOME","LANG","LC_ALL","TMPDIR"}}
    safe_env.update(PIP_CONFIG_FILE="/dev/null", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    with tempfile.TemporaryDirectory(prefix="p07-campaign-", dir="/tmp") as directory:
        repo = Path(directory) / "framework"
        scratch = Path(directory) / "campaign"
        driver_log = Path(directory) / "campaign-driver.txt"
        state = {"status":"framework_preparation_failed", "driver_started":False, "exit":None, "p07":"OPEN"}
        execution_packet = dict(PACKET)
        process = None
        def command(args):
            result = subprocess.run([str(a) for a in args], env=safe_env, capture_output=True, timeout=300)
            if result.returncode:
                raise RuntimeError("trusted framework preparation failed")
            return result.stdout.decode().strip()
        try:
            command(["git","init",repo])
            command(["git","-C",repo,"fetch","--depth","1","https://github.com/Akseleu-J/atomic-flow.git",PACKET["framework"]])
            command(["git","-C",repo,"checkout","--detach","FETCH_HEAD"])
            if command(["git","-C",repo,"rev-parse","HEAD"]) != PACKET["framework"]:
                raise ValueError("framework identity mismatch")
            execution_packet["framework_tree"] = command(["git","-C",repo,"rev-parse","HEAD^{tree}"])
            venv = Path(directory) / "venv"
            command([sys.executable,"-m","venv",venv])
            python = venv / "bin/python"
            command([python,"-m","pip","install",repo])
            execution_packet["framework_dependencies"] = command([python,"-m","pip","freeze"])
            (repo / "tools/p07_campaign.py").write_bytes(RUNTIME)
            packet_path = Path(directory) / "packet.json"
            packet_path.write_text(json.dumps(execution_packet))
            driver = repo / "tools/campaign_driver.py"
            driver.write_text("from p07_campaign import campaign,load\\nfrom pathlib import Path\\nimport sys\\ncampaign(load(sys.argv[1]),Path(sys.argv[2]))\\n")
            environment = dict(safe_env)
            if not PACKET["config"]["preflight_only"]:
                environment.update({k:os.environ[k] for k in ("MODEL_PROXY_URL","MODEL_PROXY_API_KEY")})
            with driver_log.open("wb") as log:
                process = subprocess.Popen([str(python),str(driver),str(packet_path),str(scratch)], cwd=repo, env=environment,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                state["driver_started"] = True
                deadline = PACKET["config"]["count"] * (2 * (PACKET["config"]["deadline_seconds"] + 30) + 300) + 600
                try:
                    process.wait(timeout=deadline)
                except subprocess.TimeoutExpired:
                    state["status"] = "campaign_timeout_unknown_do_not_repeat"
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                else:
                    state["status"] = "campaign_returned" if process.returncode == 0 and (scratch / "summary.json").exists() else "campaign_failed_without_complete_result"
                state["exit"] = process.returncode
        except BaseException as exc:
            state.update(status="campaign_exception_unknown_do_not_repeat", error_type=type(exc).__name__)
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        finally:
            scratch.mkdir(parents=True, exist_ok=True)
            if driver_log.exists():
                (scratch / "campaign-driver.txt").write_bytes(driver_log.read_bytes())
            if execution_packet.get("framework_dependencies"):
                (scratch / "framework-dependencies.txt").write_text(execution_packet["framework_dependencies"])
            summary = export_campaign(scratch, output, execution_packet, needles, state)
    return {"status":state["status"], "exit":state["exit"], "p07":"OPEN", "summary":summary,
            "provider_calls":summary.get("model_calls"), "preflight_only":PACKET["config"]["preflight_only"],
            "archive":str(output / "results.zip")}
run_campaign.run(kbench.llm)
'''.replace("__PACKET__", encoded_packet).replace("__RUNTIME__", encoded_runtime).replace("__SLUG__", args.slug).replace("__EXPORT_HELPER__", inspect.getsource(export_campaign))
    compile(source, "campaign_task.py", "exec")
    args.output.mkdir(parents=True)
    # Exact bytes on Windows too: text-mode newline translation otherwise makes
    # the launcher's source digest disagree with the newly prepared Task file.
    (args.output / "campaign_task.py").write_bytes(source.encode("utf-8"))
    (args.output / "packet.json").write_bytes(json.dumps(packet, indent=2).encode("utf-8"))
    manifest = {"task_slug": args.slug, "framework": FRAMEWORK, "task_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "authorized_owner": owner, "authorization_scope": "fixed owner and fixed campaign portion; combined user cap $20; no quota fallback or automatic account rotation",
                "selected": [t["instance_id"] for t in chosen], "total_budget_usd": config["total_budget_usd"],
                "arm_budget_usd": config["arm_budget_usd"], "preflight_only": preflight, "p07": "OPEN", "inference_started": False,
                "schedule_model_slug": catalog["version"]["slug"], "proxy_model_slug": MODEL}
    (args.output / "launch-manifest.json").write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=(3, 30), default=3)
    parser.add_argument("--budget", type=float, help="NEW campaign allowance, within remaining owner authorization; omitted for --preflight")
    parser.add_argument("--preflight", action="store_true", help="Source snapshot and public graph inspection only; zero model calls even in creation warmup")
    parser.add_argument("--arm-budget", type=float, default=.8)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--owner", choices=AUTHORIZED_OWNERS, default="akseley", help="Pin one explicitly authorized owner to this bundle; never automatic fallback")
    parser.add_argument("--calibrations", type=Path, default=ROOT / "validation/p07-20261026/calibration-round7")
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args())
