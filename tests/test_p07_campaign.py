"""Campaign admission, shared billing quarantine and generated Task are offline-tested."""
import ast
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
campaign = importlib.import_module("p07_campaign")
prepare = importlib.import_module("prepare_p07_kaggle")


def config():
    return dict(model=campaign.MODEL, count=3, total_budget_usd=4.8, arm_budget_usd=.8,
                max_calls=30, max_input_tokens=600000, max_output_tokens=4096, deadline_seconds=900,
                price_in_per_mtok=2, price_out_per_mtok=10)


@pytest.mark.parametrize("change", [{"model": "anthropic/claude-sonnet-5-5@default"},
    {"total_budget_usd": float("nan")}, {"arm_budget_usd": -1}, {"max_calls": 31},
    {"max_calls": True}, {"max_input_tokens": 100.5}, {"count": 2}, {"authorized_owner": "unapproved-account"}])
def test_campaign_rejects_model_substitution_and_invalid_caps(change):
    with pytest.raises(ValueError):
        campaign.validate_config({**config(), **change})


def test_source_snapshot_never_executes_target_or_reads_test_blobs(tmp_path, monkeypatch):
    mirror, work = tmp_path / "mirror", tmp_path / "work"
    mirror.mkdir()
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        if "ls-tree" in args:
            return b"100644 blob a\tsrc/a.py\0" + b"100644 blob b\ttests/test_a.py\0" + b"120000 blob c\tsrc/link.py\0"
        assert args[-1] == "a" * 40 + ":src/a.py"
        return b"raise RuntimeError('this source must never execute')\n"

    monkeypatch.setattr(campaign.subprocess, "check_output", git)
    task = dict(repo="owner/repo", base_commit="a" * 40, source_path="src/a.py")
    result = campaign.source_snapshot(task, mirror, work)
    assert set(result) == {"src/a.py"}
    assert len(calls) == 2 and not (work / "tests").exists()


@pytest.mark.parametrize("cost_source,blocked", [("provider", False), ("configured_estimate", True)])
def test_shared_ledger_requires_real_provider_cost_across_arms(tmp_path, cost_source, blocked):
    from atomic_flow.backends.base import ChatResult
    from atomic_flow.config import BackendConfig

    class Transport:
        name = "scripted"
        config = BackendConfig(type="kaggle", model=campaign.MODEL, temperature=None,
                               price_in_per_mtok=2, price_out_per_mtok=10, max_retries=0, max_output_tokens=32)
        calls = []

        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return ChatResult('{' + '"done":true}', tokens_in=2, tokens_out=2, cost_usd=.001, cost_source=cost_source)

    settings = config()
    settings["max_output_tokens"] = 32
    limits = dict(max_calls=3, max_input_tokens=10000, max_output_tokens=32, max_reported_cost_usd=.8)
    ledger = tmp_path / "ledger.json"
    campaign.save(ledger, {"attempts": []})
    first, second = tmp_path / "baseline", tmp_path / "atomic"
    first.mkdir()
    second.mkdir()
    backend = campaign.CampaignBackend(Transport(), first, limits, time.monotonic() + 10, ledger, settings)
    backend.chat([{"role": "user", "content": "public fixture"}])
    other = campaign.CampaignBackend(Transport(), second, limits, time.monotonic() + 10, ledger, settings)
    if blocked:
        with pytest.raises(campaign.PilotLimit, match="unresolved"):
            other.chat([{"role": "user", "content": "next arm"}])
        assert not other.calls
    else:
        other.chat([{"role": "user", "content": "next arm"}])
        assert len(other.calls) == 1


def test_unknown_send_intent_blocks_new_arm_without_transport_call(tmp_path):
    ledger = tmp_path / "ledger.json"
    campaign.save(ledger, {"attempts": [{"status": "in_flight", "cost_usd": None}]})
    backend = campaign.CampaignBackend(SimpleNamespace(name="scripted", config=SimpleNamespace()), tmp_path, {}, time.monotonic()+10, ledger, config())
    with pytest.raises(campaign.PilotLimit, match="unresolved"):
        backend.chat([])
    assert not backend.calls


def test_empty_selection_still_creates_all_30_separate_roots_and_archive(tmp_path, monkeypatch):
    tasks = [{"instance_id": "case-" + str(i)} for i in range(30)]
    monkeypatch.setattr(campaign, "select", lambda tasks, count: [])
    result = campaign.campaign({"config": config(), "tasks": tasks}, tmp_path / "campaign")
    assert result["p07"] == "OPEN" and len(result["rows"]) == 30
    assert result["confirmed_physical_calls"] == 0
    assert all((tmp_path / "campaign/tasks" / t["instance_id"] / "atomic").is_dir() for t in tasks)
    assert (tmp_path / "campaign-results.zip").is_file()


def test_preflight_uses_same_snapshot_and_public_graph_without_model_or_child(tmp_path, monkeypatch):
    settings = {**config(), "preflight_only": True, "total_budget_usd": 0, "arm_budget_usd": 0}
    tasks = [{"instance_id": task_id, "source_path": "src/a.py"} for task_id in campaign.PILOT]
    tasks.extend({"instance_id": "not-selected-" + str(i)} for i in range(27))
    seen = []

    def snapshot(task, mirror, destination):
        seen.append(task["instance_id"])
        path = destination / "src/a.py"
        path.parent.mkdir()
        path.write_bytes(b"def value():\n    return 1\n")
        return {"src/a.py": campaign.sha(path.read_bytes())}

    def forbid(*args, **kwargs):
        raise AssertionError("preflight must not create a paid arm child")

    monkeypatch.setattr(campaign, "source_snapshot", snapshot)
    monkeypatch.setattr(campaign.subprocess, "run", forbid)
    output = tmp_path / "preflight"
    result = campaign.campaign({"config": settings, "tasks": tasks}, output)
    assert seen == list(campaign.PILOT)
    assert result["model_calls"] == 0 and result["confirmed_physical_calls"] == 0
    assert result["known_provider_cost_usd"] == 0
    assert len(result["rows"]) == 30
    assert all(row["status"] == "source_preflight_ready" for row in result["rows"][:3])
    assert all((output / "tasks" / task_id / "graph-preflight.json").exists() for task_id in campaign.PILOT)


def test_builder_preflight_needs_no_calibration_or_paid_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "catalog_model", lambda: {"version": {"slug": "claude-sonnet-5-default"}})
    args = SimpleNamespace(slug="offline-zero-call-preflight", owner="umirbayulgaisha", count=3, budget=None, arm_budget=.8, preflight=True,
        calibrations=tmp_path / "no-calibrations", output=tmp_path / "bundle")
    prepare.prepare(args)
    packet = json.loads((args.output / "packet.json").read_text())
    manifest = json.loads((args.output / "launch-manifest.json").read_text())
    assert packet["config"]["preflight_only"] is True
    assert packet["config"]["total_budget_usd"] == packet["config"]["arm_budget_usd"] == 0
    assert manifest["preflight_only"] is True and manifest["inference_started"] is False
    assert manifest["authorized_owner"] == packet["config"]["authorized_owner"] == "umirbayulgaisha"
    assert manifest["task_source_sha256"] == campaign.sha((args.output / "campaign_task.py").read_bytes())


def test_builder_embeds_current_runtime_but_not_gold_or_hidden_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "catalog_model", lambda: {"displayName": "Claude Sonnet 5", "version": {
        "slug": "claude-sonnet-5-default", "modelProxySlug": campaign.MODEL, "allowModelProxy": True}})
    args = SimpleNamespace(slug="offline-p07-pilot-three", count=3, budget=4.8, arm_budget=.8,
        calibrations=prepare.ROOT / "validation/p07-20261026/calibration-round7", output=tmp_path / "bundle")
    prepare.prepare(args)
    source = (args.output / "campaign_task.py").read_text()
    tree = ast.parse(source)
    # Check the generated nested driver, not only the outer notebook syntax.
    drivers = [node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text" and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("from p07_campaign")]
    assert len(drivers) == 1
    compile(drivers[0], "generated_driver.py", "exec")
    packet = json.loads((args.output / "packet.json").read_text())
    assert len(packet["tasks"]) == 30
    assert all(not ({"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"} & task.keys()) for task in packet["tasks"])
    assert json.loads((args.output / "launch-manifest.json").read_text())["inference_started"] is False
    manifest = json.loads((args.output / "launch-manifest.json").read_text())
    assert manifest["task_source_sha256"] == campaign.sha((args.output / "campaign_task.py").read_bytes())
    assert b"\r\n" not in (args.output / "campaign_task.py").read_bytes()


def test_campaign_baseline_performs_host_read_and_hash_bound_edit(tmp_path):
    from atomic_flow.agents.source import digest
    from atomic_flow.backends.base import ChatResult

    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    out.mkdir()
    raw = b"def value():\n    return 1\n"
    (work / "a.py").write_bytes(raw)
    actions = iter([{"tool": "read_file", "args": {"path": "a.py"}},
        {"tool": "patch_file", "args": {"path": "a.py", "expected_hash": digest(raw),
            "old_text": "return 1", "new_text": "return 2"}}, {"done": True}])

    class Scripted:
        calls = []
        limits = {"max_calls": 3}

        def chat(self, messages):
            self.calls.append(1)
            return ChatResult(json.dumps(next(actions)))

    task = {"source_path": "a.py", "problem_statement": "Return two"}
    assert campaign.baseline(task, work, Scripted(), out)["model_claimed_done"]
    assert b"return 2" in (work / "a.py").read_bytes()
    transcript = json.loads((out / "tools.json").read_text())
    assert transcript[0]["result"]["read_complete"]
    assert transcript[1]["result"]["status"] == "applied"


def test_builder_refuses_full30_with_failed_calibrations(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "catalog_model", lambda: {"version": {"slug": "claude-sonnet-5-default"}})
    args = SimpleNamespace(slug="offline-p07-all30-refused", count=30, budget=48, arm_budget=.8,
        calibrations=prepare.ROOT / "validation/p07-20261026/calibration-round7", output=tmp_path / "bundle")
    with pytest.raises(ValueError, match="calibration"):
        prepare.prepare(args)
    assert not args.output.exists()


def test_builder_refuses_underfunded_paired_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "catalog_model", lambda: {"version": {"slug": "claude-sonnet-5-default"}})
    args = SimpleNamespace(slug="offline-p07-small-budget", count=3, budget=1, arm_budget=.8,
        calibrations=prepare.ROOT / "validation/p07-20261026/calibration-round7", output=tmp_path / "bundle")
    with pytest.raises(ValueError, match="budget"):
        prepare.prepare(args)
    assert not args.output.exists()


def test_schedule_disconnect_keeps_unknown_intent_and_never_replays(tmp_path, monkeypatch):
    import io

    launch = importlib.import_module("launch_p07_kaggle")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = b"# prepared fixture"
    (bundle / "campaign_task.py").write_bytes(source)
    campaign.save(bundle / "launch-manifest.json", {"task_source_sha256": campaign.sha(source),
        "task_slug": "offline-fixture", "schedule_model_slug": "claude-sonnet-5-default",
        "authorized_owner": "akseley", "proxy_model_slug": campaign.MODEL, "total_budget_usd": 4.8})
    receipt = tmp_path / "intent.json"
    calls = []

    def request(method, body, *args):
        calls.append(method)
        if method == "IntrospectToken":
            return io.BytesIO(b'{"active":true,"username":"akseley"}')
        if method == "ListBenchmarkTaskRuns":
            return io.BytesIO(b'{"runs":[]}')
        if method == "GetModelProxyQuotas":
            return io.BytesIO(b'{"quotaBalances":[{"totalQuotaAllowed":10,"quotaUsed":1}]}')
        raise TimeoutError("unknown scheduling outcome")

    monkeypatch.setattr(launch, "request", request)
    monkeypatch.setattr(sys, "argv", ["launch", "schedule", "--bundle", str(bundle), "--receipt", str(receipt), "--yes"])
    with pytest.raises(TimeoutError):
        launch.main()
    assert json.loads(receipt.read_text())["status"] == "schedule_intent_outcome_unknown"
    assert calls.count("BatchScheduleBenchmarkTaskRuns") == 1
    monkeypatch.setattr(sys, "argv", ["launch", "schedule", "--bundle", str(bundle),
                                    "--receipt", str(tmp_path / "different-receipt.json"), "--yes"])
    with pytest.raises(ValueError, match="already has a scheduling intent"):
        launch.main()
    assert calls.count("BatchScheduleBenchmarkTaskRuns") == 1
    monkeypatch.setattr(sys, "argv", ["launch", "schedule", "--bundle", str(bundle), "--receipt", str(receipt), "--yes"])
    with pytest.raises(ValueError, match="fresh receipt"):
        launch.main()
    assert calls.count("BatchScheduleBenchmarkTaskRuns") == 1


def test_launcher_refuses_wrong_account_before_scheduling(tmp_path, monkeypatch):
    import io

    launch = importlib.import_module("launch_p07_kaggle")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    raw = b"# prepared fixture"
    (bundle / "campaign_task.py").write_bytes(raw)
    campaign.save(bundle / "launch-manifest.json", {"task_source_sha256": campaign.sha(raw),
        "task_slug": "offline-owner-three", "schedule_model_slug": "claude-sonnet-5-default",
        "authorized_owner": "umirbayulgaisha", "proxy_model_slug": campaign.MODEL, "total_budget_usd": 4.8})
    calls = []

    def request(method, body, *args):
        calls.append(method)
        assert method == "IntrospectToken"
        return io.BytesIO(b'{"active":true,"username":"akseley"}')

    monkeypatch.setattr(launch, "request", request)
    monkeypatch.setattr(sys, "argv", ["launch", "schedule", "--bundle", str(bundle),
        "--receipt", str(tmp_path / "receipt.json"), "--yes"])
    with pytest.raises(ValueError, match="credential identity"):
        launch.main()
    assert calls == ["IntrospectToken"]
    assert not (bundle / "schedule-v1.json").exists()
