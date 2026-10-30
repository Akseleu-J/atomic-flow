"""Private source-only development pair. No target imports, setup or tests."""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import kaggle_benchmarks as kbench

FRAMEWORK = "013caf01cf148eaa5915d153ec0d1d85ea36c9c8"
MODEL = "google/gemini-3.1-flash-lite-preview"
BASE = "b4777fdcef467b7132c055f8ac2c9a5059e6a145"


@kbench.task(name="atomic-p07-sympy-source-pair-20261026-r3", description="Frozen exposed SymPy source-only development pair, iterative baseline and durable Atomic; independent oracle runs elsewhere.")
def p07_sympy_source_pair(llm) -> dict:
    actor = getattr(llm, "model", None)
    if not isinstance(actor, str):
        actor = getattr(llm, "name", "unknown")
    if actor not in {MODEL, "gemini-3.1-flash-lite-preview", "models/gemini-3.1-flash-lite-preview"}:
        # Kaggle push performs a default-actor warmup. It must never spend on
        # an unintended model; only the explicit frozen-model run is admitted.
        return {"status": "warmup_actor_refused", "actor": actor, "provider_calls": 0}
    if not os.getenv("MODEL_PROXY_URL") or not os.getenv("MODEL_PROXY_API_KEY"):
        return {"status": "proxy_environment_missing", "provider_calls": 0}
    safe_env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
    safe_env.update(PIP_CONFIG_FILE="/dev/null", PYTHONDONTWRITEBYTECODE="1")
    secrets = [os.environ[name] for name in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY")]
    result = {"status": "preparing", "actor": actor, "model": MODEL, "framework": FRAMEWORK,
              "scope": "exposed public single-source development pair; not full P07 comparison",
              "arm_order": ["baseline", "atomic"], "reports": [], "provider_calls": 0,
              "known_reported_cost_usd": 0, "planned_pair_stop_usd": .8, "target_tests_executed": False}
    out = Path("/kaggle/working") / ("p07-source-pair-" + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="atomic-p07-") as directory:
            temp = Path(directory)
            repo, mirror, venv = temp / "framework", temp / "mirror", temp / "venv"

            def run(args, cwd=None, timeout=120):
                process = subprocess.run([str(arg) for arg in args], cwd=cwd, env=safe_env,
                                         capture_output=True, timeout=timeout, check=False)
                if process.returncode:
                    raise RuntimeError("preparation failed: " + str(args[0]))
                return process.stdout.decode(errors="replace").strip()

            for target, origin, revision in ((repo, "https://github.com/Akseleu-J/atomic-flow.git", FRAMEWORK),
                                             (mirror, "https://github.com/sympy/sympy.git", BASE)):
                target.mkdir()
                run(["git", "init", str(target)])
                run(["git", "-C", str(target), "fetch", "--depth", "1", origin, revision])
                run(["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"])
                if run(["git", "-C", str(target), "rev-parse", "HEAD"]) != revision:
                    raise RuntimeError("source revision mismatch")
            run([sys.executable, "-m", "venv", str(venv)])
            python = venv / "bin/python"
            run([python, "-m", "pip", "install", str(repo)], timeout=300)
            (out / "dependencies.txt").write_text(run([python, "-m", "pip", "freeze"]))
            calibration = repo / "validation/p07-20261026/accepted-sympy-calibration.json"
            run_env = {**safe_env, **{name: os.environ[name] for name in ("MODEL_PROXY_URL", "MODEL_PROXY_API_KEY")}}
            for arm in result["arm_order"]:
                arm_out = out / arm
                args = [str(python), "tools/evaluation_p07_source_pilot.py", "--arm", arm,
                        "--mirror", str(mirror), "--calibration", str(calibration), "--output", str(arm_out), "--yes"]
                with (out / (arm + "-driver.txt")).open("wb") as log:
                    try:
                        completed = subprocess.run(args, cwd=repo, env=run_env, stdout=log, stderr=subprocess.STDOUT,
                                                   timeout=970, check=False)
                        code = completed.returncode
                    except subprocess.TimeoutExpired:
                        code = None
                report_file = arm_out / "report.json"
                attempts_file = arm_out / "attempts.json"
                report = json.loads(report_file.read_text()) if report_file.exists() else {"status": "report_missing", "calls": 0}
                attempts = json.loads(attempts_file.read_text()) if attempts_file.exists() else []
                report["driver_exit"] = code
                report["attempts"] = attempts
                patch = arm_out / "candidate.patch"
                if patch.exists():
                    report["candidate_patch"] = patch.read_text()
                result["reports"].append(report)
                result["provider_calls"] += len(attempts)
                result["known_reported_cost_usd"] += sum(a.get("cost_usd") or 0 for a in attempts)
                if code is None or any(a.get("cost_usd") is None or a.get("request_outcome") == "unknown" for a in attempts):
                    result["status"] = "unknown_attempt_quarantined"
                    break
                if result["known_reported_cost_usd"] >= .8:
                    result["status"] = "pair_cost_stop"
                    break
                if not attempts and report["status"] == "generation_failed":
                    result["status"] = "infrastructure_stop"
                    break
            else:
                result["status"] = "generation_pair_finished_oracle_pending"
    except BaseException as exc:
        result.update(status="infrastructure_error", error_type=type(exc).__name__)
    # Return auditable text artifacts without putting credentials into notebook
    # source, output, files, archive, exception text or the returned task result.
    # Kaggle also exports the original working files: scrub them in place,
    # including files omitted from the curated archive, before returning.
    redactions = 0
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        original = path.read_bytes()
        sanitized = original
        for secret in secrets:
            needle = secret.encode()
            redactions += sanitized.count(needle)
            sanitized = sanitized.replace(needle, b"[REDACTED_PROXY_VALUE]")
        if sanitized != original:
            path.write_bytes(sanitized)
    result["export_secret_redactions"] = redactions
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(out.rglob("*")):
            if (not path.is_file() or path.suffix not in {".json", ".jsonl", ".txt", ".patch"}
                    or "workspace" in path.relative_to(out).parts):
                continue
            data = path.read_text(errors="replace")
            for secret in secrets:
                data = data.replace(secret, "[REDACTED_PROXY_VALUE]")
            bundle.writestr(path.relative_to(out).as_posix(), data.encode())
    raw = archive.getvalue()
    result.update(evidence_zip_sha256=hashlib.sha256(raw).hexdigest(), evidence_zip_base64=base64.b64encode(raw).decode())
    encoded = json.dumps(result)
    for secret in secrets:
        encoded = encoded.replace(secret, "[REDACTED_PROXY_VALUE]")
    return json.loads(encoded)


p07_sympy_source_pair.run(kbench.llm)
