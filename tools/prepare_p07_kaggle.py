"""Build a private, self-contained Kaggle Task; never uploads or starts inference."""
import argparse
import base64
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from evaluation_p07_source_pilot import admit_calibration
from evaluation_stage4 import load_manifest, patch_paths, protected_test_path
from p07_campaign import MODEL, select, validate_config

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK = "9645972b5845237f43306655e862cf318cf3b7f3"
CATALOG = "https://api.kaggle.com/v1/benchmarks.BenchmarksApiService/ListBenchmarkModels"


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
    config = dict(model=MODEL, count=args.count, total_budget_usd=args.budget,
                  arm_budget_usd=args.arm_budget, max_calls=30, max_input_tokens=600000,
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
    for task in chosen:
        admit_calibration(task["calibration"], task)
    if len(chosen) * 2 * args.arm_budget > args.budget + 1e-9:
        raise ValueError("budget does not cover all selected paired arm caps; choose the 3-task pilot or revise explicit caps")
    runtime = (ROOT / "tools/p07_campaign.py").read_bytes()
    packet = {"framework": FRAMEWORK, "model_catalog_record": catalog, "config": config, "tasks": tasks,
              "runtime_sha256": hashlib.sha256(runtime).hexdigest(), "p07": "OPEN",
              "study": "exposed source-scoped development campaign; not blinded/full autonomous acceptance",
              "arm_order": "baseline/atomic on even selected index, atomic/baseline on odd selected index"}
    encoded_packet = base64.b64encode(json.dumps(packet).encode()).decode()
    encoded_runtime = base64.b64encode(runtime).decode()
    source = '''"""Private source-only P07 campaign. Creation warmup sends zero model prompts."""
import base64, hashlib, json, os, signal, subprocess, sys, tempfile, uuid
from pathlib import Path
import kaggle_benchmarks as kbench
PACKET = json.loads(base64.b64decode("__PACKET__"))
RUNTIME = base64.b64decode("__RUNTIME__")
@kbench.task(name="__SLUG__", description="Source-only Sonnet 5 diagnostic; independent container oracle runs separately; P07 OPEN.")
def run_campaign(llm) -> dict:
    actor = getattr(llm, "model", None)
    if not isinstance(actor, str):
        actor = getattr(llm, "name", "unknown")
    version = PACKET["model_catalog_record"]["version"]
    if actor not in {PACKET["config"]["model"], version["slug"], "anthropic/" + version["slug"]}:
        return {"status":"warmup_actor_refused", "actor":actor, "provider_calls":0, "p07":"OPEN"}
    if not all(os.environ.get(k) for k in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY")):
        return {"status":"proxy_environment_missing", "provider_calls":0, "p07":"OPEN"}
    if hashlib.sha256(RUNTIME).hexdigest() != PACKET["runtime_sha256"]:
        raise ValueError("runtime payload hash mismatch")
    output = Path("/kaggle/working") / ("p07-" + uuid.uuid4().hex[:12])
    safe_env = {k:v for k,v in os.environ.items() if k in {"PATH","HOME","LANG","LC_ALL","TMPDIR"}}
    with tempfile.TemporaryDirectory(prefix="p07-framework-") as directory:
        repo = Path(directory) / "framework"
        def command(args):
            result = subprocess.run([str(a) for a in args], env=safe_env, capture_output=True, timeout=300)
            if result.returncode:
                raise RuntimeError("trusted framework preparation failed")
            return result.stdout.decode().strip()
        command(["git","init",repo])
        command(["git","-C",repo,"fetch","--depth","1","https://github.com/Akseleu-J/atomic-flow.git",PACKET["framework"]])
        command(["git","-C",repo,"checkout","--detach","FETCH_HEAD"])
        if command(["git","-C",repo,"rev-parse","HEAD"]) != PACKET["framework"]:
            raise ValueError("framework identity mismatch")
        venv = Path(directory) / "venv"
        command([sys.executable,"-m","venv",venv])
        python = venv / "bin/python"
        command([python,"-m","pip","install",repo])
        (repo / "tools/p07_campaign.py").write_bytes(RUNTIME)
        packet_path = Path(directory) / "packet.json"
        packet_path.write_text(json.dumps(PACKET))
        driver = repo / "tools/campaign_driver.py"
        driver.write_text("from p07_campaign import campaign,load\\nfrom pathlib import Path\\nimport sys\\ncampaign(load(sys.argv[1]),Path(sys.argv[2]))\\n")
        environment = {**safe_env, **{k:os.environ[k] for k in ("MODEL_PROXY_URL","MODEL_PROXY_API_KEY")}}
        process = subprocess.Popen([str(python),str(driver),str(packet_path),str(output)], cwd=repo, env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = PACKET["config"]["count"] * (2 * (PACKET["config"]["deadline_seconds"] + 30) + 300) + 600
        try:
            process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return {"status":"campaign_timeout_unknown_do_not_repeat", "p07":"OPEN"}
        if not (output / "summary.json").exists():
            return {"status":"campaign_failed_without_summary", "exit":process.returncode,"p07":"OPEN","attempt_outcome":"unknown_do_not_repeat"}
        summary = json.loads((output / "summary.json").read_text())
        return {"status":"campaign_returned", "exit":process.returncode,"p07":"OPEN", "summary":summary,
                "archive":str(output.parent / (output.name + "-results.zip"))}
run_campaign.run(kbench.llm)
'''.replace("__PACKET__", encoded_packet).replace("__RUNTIME__", encoded_runtime).replace("__SLUG__", args.slug)
    compile(source, "campaign_task.py", "exec")
    args.output.mkdir(parents=True)
    (args.output / "campaign_task.py").write_text(source, encoding="utf-8")
    (args.output / "packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
    manifest = {"task_slug": args.slug, "framework": FRAMEWORK, "task_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "selected": [t["instance_id"] for t in chosen], "total_budget_usd": args.budget,
                "arm_budget_usd": args.arm_budget, "p07": "OPEN", "inference_started": False,
                "schedule_model_slug": catalog["version"]["slug"], "proxy_model_slug": MODEL}
    (args.output / "launch-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=(3, 30), default=3)
    parser.add_argument("--budget", type=float, required=True, help="NEW campaign allowance, within remaining owner authorization")
    parser.add_argument("--arm-budget", type=float, default=.8)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--calibrations", type=Path, default=ROOT / "validation/p07-20261026/calibration-round7")
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args())
