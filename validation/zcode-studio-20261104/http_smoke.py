"""Installed native binary HTTP acceptance; isolated state, no provider/network calls."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / ("rust/target/debug/af.exe" if os.name == "nt" else "rust/target/debug/af")
BASE = "http://127.0.0.1:18789"


def run():
    results = []
    with tempfile.TemporaryDirectory(prefix="af-studio-smoke-") as temp:
        directory = Path(temp)
        state, project = directory / "state", directory / "project"
        state.mkdir()
        project.mkdir()
        (project / "src").mkdir()
        raw = "/// Refund policy.\npub fn refund_days() -> u32 { 14 }\n".encode()
        (project / "src/refund.rs").write_bytes(raw)
        token = secrets.token_hex(24)
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "USERPROFILE", "TEMP", "TMP") if k in os.environ}
        env.update(ATOMIC_FLOW_HOME=str(state), STUDIO_TEST_TOKEN=token, AF_SMOKE_DUMMY_KEY="fixture")
        (state / "providers.json").write_text(json.dumps({"schema_version": 1, "providers": [{
            "id": "fixture", "label": "Host fixture", "base_url": BASE,
            "api_key_env": "AF_SMOKE_DUMMY_KEY", "models": ["fixture"], "reservation_usd": "0.5",
        }]}), encoding="utf-8")
        log = open(directory / "server.txt", "wb")
        process = subprocess.Popen([str(BINARY), "serve", "--bind", "127.0.0.1:18789", "--token-env", "STUDIO_TEST_TOKEN"],
            cwd=project, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def request(path, body=None, *, method=None, authorized=True, headers=None, expected=200):
            observed = {}
            if authorized:
                observed["Authorization"] = "Bearer " + token
            if body is not None:
                observed["Content-Type"] = "application/json"
            observed.update(headers or {})
            req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                method=method or ("POST" if body is not None else "GET"), headers=observed)
            try:
                response = urllib.request.urlopen(req, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                data = response.read(1024 * 1024 + 1)
                assert response.status == expected, (path, response.status, data[:200])
                assert len(data) <= 1024 * 1024
                return data, response.headers

        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("owned server exited before health")
                try:
                    request("/health")
                    break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(0.25)
            else:
                raise RuntimeError("owned server failed to become ready")
            request("/health", authorized=False, expected=401)
            request("/v1/capabilities", authorized=False, expected=401)
            capabilities = json.loads(request("/v1/capabilities")[0])
            assert capabilities["verification_schema_version"] == 1
            assert capabilities["requirement_checker"] == "unavailable"
            assert capabilities["verification_report_authorizes_execution"] is False
            html, headers = request("/dashboard", authorized=False)
            policy = headers["Content-Security-Policy"]
            assert "script-src 'nonce-" in policy and b"innerHTML" not in html and b"onclick=" not in html
            results.append("static UI nonce CSP and token-only APIs")
            request("/health", headers={"Origin": "http://attacker.example"}, expected=403)
            request("/health", headers={"Host": "attacker.example:18789"}, expected=403)
            request("/v1/chat/stream", {"prompt":"test", "backend":{"base_url":"https://attacker.example","api_key_env":"PATH","model":"x"}}, expected=403)
            results.append("hostile origin, DNS-rebinding Host and browser credential routing refused")
            catalogue = json.loads(request("/v1/providers")[0])
            assert catalogue["providers"][0]["id"] == "scripted"
            request("/v1/roots", {"root_id":"pilot", "path":str(project), "host_approved":True})
            graph = json.loads(request("/v1/graph/build", {"root_id":"pilot"})[0])
            assert graph["files"] >= 1
            source = json.loads(request("/v1/roots/pilot/file?path=src%2Frefund.rs")[0])
            assert source["sha256"] == hashlib.sha256(raw).hexdigest() and source["verification"] == "not_assessed"
            request("/v1/roots/pilot/file?path=..%2Foutside.rs", expected=400)
            results.append("registered Rust project graph and hash-bound source preview")
            session = json.loads(request("/v1/sessions", {"id":"pilot-session","root_id":"pilot"})[0])["session_id"]
            budget_probe = {"prompt": "refund policy", "session_id": session, "root_id": "pilot",
                "backend": {"provider_id": "scripted", "model": "scripted"}}
            history_path = f"/v1/sessions/{session}/history?after=0&limit=100"
            before_budget = json.loads(request(history_path)[0])
            request("/v1/chat/stream", {**budget_probe, "reserve_usd": "0.1"}, expected=409)
            request("/v1/chat/stream", {**budget_probe, "reserve_usd": "invalid"}, expected=400)
            assert json.loads(request(history_path)[0]) == before_budget
            stream, _ = request("/v1/chat/stream", {"prompt":"refund policy", "session_id":session, "root_id":"pilot",
                "backend":{"provider_id":"scripted","model":"scripted"}})
            events = [json.loads(line[5:].strip()) for line in stream.decode().splitlines() if line.startswith("data:")]
            final = next(e for e in events if e.get("type") == "final")
            assert final["content"] == "scripted-echo" and final["verification"] == "not_assessed"
            assert final["sources"][0]["sha256"] == source["sha256"]
            report = final["verification_report"]
            assert report["schema_version"] == 1 and report["requirements"]["checked"] == 0
            assert report["requirements"]["status"] == "not_assessed"
            assert report["evidence_coverage"]["delivered_complete"] == 0
            assert final["coverage_scope"] == "selected_request_excerpts"
            assert len(report["binding"]["inventory_sha256"]) == 64
            history = json.loads(request(f"/v1/sessions/{session}/history?after=0&limit=100")[0])
            assert history["events"][-1]["payload"]["content"] == "scripted-echo"
            assert history["effect_admission_clear"] and not history["unresolved_call_ids"]
            request(f"/v1/sessions/{session}/events", {"event_type":"model_response","payload":{},"call_id":"forged","outcome":"success"}, expected=403)
            results.append("actual native scripted chat, source context, durable history and forged terminal refusal")
            snapshot = json.loads(request(f"/v1/sessions/{session}/snapshot", method="POST")[0])
            saved = state / "studio-snapshots" / ("snapshot-" + snapshot["snapshot_id"] + ".json")
            assert snapshot["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
            assert snapshot["source_backup"] is False
            request("/v1/budget", {"cap_usd":"1.50","host_approved":True})
            budget = json.loads(request("/v1/budget")[0])
            assert budget["cap_usd"] == "1.5" and budget["enforced"] and budget["available_usd"] == "1.5"
            results.append("immutable journal snapshot exact SHA256 and durable budget projection")
            before_ceiling = json.loads(request(history_path)[0])
            request("/v1/chat/stream", {**budget_probe, "reserve_usd": "0.4",
                "backend": {"provider_id": "fixture", "model": "fixture"}}, expected=403)
            assert json.loads(request(history_path)[0]) == before_ceiling
            assert json.loads(request("/v1/budget")[0]) == budget
            denied, _ = request("/v1/chat/stream", {**budget_probe, "reserve_usd": "2"})
            refused = [json.loads(line[5:].strip()) for line in denied.decode().splitlines() if line.startswith("data:")]
            assert len(refused) == 1 and refused[0]["sent"] is False and refused[0]["unknown"] is False
            after_refusal = json.loads(request(history_path)[0])
            assert after_refusal["effect_admission_clear"] and not after_refusal["pending_call_ids"]
            assert after_refusal["events"][-1]["outcome"] == "not_sent"
            resumed, _ = request("/v1/chat/stream", budget_probe)
            assert any(json.loads(line[5:].strip())["type"] == "final" for line in resumed.decode().splitlines() if line.startswith("data:"))
            results.append("budget refusal records not_sent and permits a fresh request")
            # A separate consumer resolves only an installed dist/package copy.
            package = directory / "consumer/node_modules/@atomic-flow/af-client"
            package.mkdir(parents=True)
            sdk = ROOT / "ts/af-client"
            shutil.copytree(sdk / "dist", package / "dist")
            shutil.copyfile(sdk / "package.json", package / "package.json")
            consumer = directory / "consumer/consumer.mjs"
            shutil.copyfile(ROOT / "validation/production-20261104/sdk-native-consumer.mjs", consumer)
            node = os.environ.get("AF_NODE_BINARY") or shutil.which("node")
            if not node:
                raise RuntimeError("Node runtime required for installed SDK acceptance")
            consumer_env = dict(env, AF_CLIENT_BASE=BASE)
            output = subprocess.run([node, str(consumer)], cwd=consumer.parent, env=consumer_env,
                capture_output=True, text=True, timeout=30)
            assert output.returncode == 0, output.stderr[:2000]
            consumer_result = json.loads(output.stdout)
            assert consumer_result["ok"] and consumer_result["provider_calls"] == 0
            results.append("installed SDK copy outside checkout against actual native API")
        finally:
            process.terminate()
            process.wait(timeout=10)
            log.close()
            token = None
        report = {"status":"passed", "binary_sha256":hashlib.sha256(BINARY.read_bytes()).hexdigest(),
            "scenarios":results, "provider_calls":0, "transport":"native HTTP + scripted backend",
            "owned_server_cleanup":process.poll() is not None, "browser_visual_qa":"not_assessed"}
        Path(os.environ.get("AF_HTTP_SMOKE_RESULT", str(Path(__file__).parent / "http-smoke-result.json"))).write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
