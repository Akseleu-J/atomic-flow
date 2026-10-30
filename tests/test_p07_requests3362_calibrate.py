"""Strict Requests3362 oracle contracts; no target code executes on this host."""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_requests3362_calibrate as adapter  # noqa: E402


def task_control():
    task = next(item for item in json.loads((ROOT / "benchmarks/stage4/corpus-p07-v2.json").read_bytes())["tasks"]
                if item["instance_id"] == adapter.TASK)
    control = json.loads((ROOT / "benchmarks/stage4/p07-controls-v6.json").read_bytes())["tasks"][adapter.TASK]
    return task, control


def test_exact_task_and_prospective_control_keep_all76_obligations():
    task, control = task_control()
    wrong = adapter.admit_task(task, control)
    assert len(task["FAIL_TO_PASS"]) == 1 and len(task["PASS_TO_PASS"]) == 75
    assert adapter.oracle.sha(wrong.encode()) == control["wrong_sha256"]


@pytest.mark.parametrize("field,value", [("base_commit", "0" * 40), ("instance_id", "psf__requests-2674"),
    ("version", "2.9"), ("patch", ""), ("test_patch", ""), ("PASS_TO_PASS", []), ("FAIL_TO_PASS", [])])
def test_other_task_or_narrowed_obligation_refused(field, value):
    task, control = task_control()
    with pytest.raises(ValueError):
        adapter.admit_task({**task, field: value}, control)


def test_old_ascii_negative_refused():
    task, control = task_control()
    with pytest.raises(ValueError, match="v6 negative"):
        adapter.admit_task(task, {**control, "new": '+        encoding = "ascii"'})


def receipt(task, failed=False):
    ids = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    statuses = {test: "PASSED" for test in ids}
    if failed:
        statuses[ids[0]] = "FAILED"
    result = {"version_info": [3, 6, 15], "prefix": adapter.transplant.PREFIX,
        "executable": adapter.transplant.PREFIX + "/bin/python3.6", "pytest_version": "2.8.7",
        "pytest_module": adapter.health.PREFIX + "pytest.py", "requests_module": "/testbed/requests/__init__.py",
        "argv": [*adapter.health.PLUGIN_ARGS, "-rA", "-v"], "finished": True, "exit": int(failed),
        "collection": ids, "plugins": [["fixture", "pytest_httpbin.plugin"], ["mock", "pytest_mock"]],
        "reports": [{"id": test, "when": when, "outcome": statuses[test].lower() if when == "call" else "passed",
                     "has_wasxfail": False, "wasxfail": None, "native_category": statuses[test].lower() if when == "call" else ""}
                    for test in ids for when in ("setup", "call", "teardown")]}
    return result, statuses


def transcript(result, exit_code):
    lines = []
    for test in result["collection"]:
        phases = [report for report in result["reports"] if report["id"] == test]
        call = next((report for report in phases if report["when"] == "call"), None)
        status = "SKIPPED" if call is None else call["outcome"].upper()
        if call and call.get("has_wasxfail"):
            status = "xfail" if call["native_category"] == "xfailed" else "XPASS"
        lines.append(test + " " + status)
    return adapter.START + "\n" + "\n".join(lines) + "\n" + adapter.RESULT + json.dumps(result) + "\nP07_TEST_EXIT=" + str(exit_code) + "\n" + adapter.END


def stub_external(statuses, collection):
    canonical = [adapter.canonical_node_id(node) for node in collection]
    aliases = adapter.sealed_terminal_mapping(canonical)
    tokens = {node: "tests/p07_identity.py::case_" + format(index, "06d") for index, node in enumerate(sorted(canonical))}
    values = {tokens[aliases.get(adapter.canonical_node_id(node), node)]: status
              for node, status in statuses.items() if aliases.get(adapter.canonical_node_id(node), node) in tokens}
    return lambda *args: (values, {})


def test_complete_native_gold_and_real_requirement_negative(monkeypatch):
    task, _ = task_control()
    for failed in (False, True):
        result, statuses = receipt(task, failed)
        monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
        row = adapter.classify_target(task, transcript(result, int(failed)), int(failed), None, None)
        assert row["verified_success"] is not failed
        assert row["regressions_passed"] is True
        assert len(row["tests"]) == 76


@pytest.mark.parametrize("mutation", ["missing", "skip", "setup", "unfinished", "duplicate", "plugin", "summary"])
def test_missing_skipped_setup_error_or_disagreeing_native_receipt_refused(monkeypatch, mutation):
    task, _ = task_control()
    result, statuses = receipt(task)
    if mutation == "missing":
        result["collection"].pop()
    elif mutation == "skip":
        result["reports"][1]["outcome"] = "skipped"
    elif mutation == "setup":
        result["reports"][0]["outcome"] = "failed"
    elif mutation == "unfinished":
        result["finished"] = False
    elif mutation == "duplicate":
        result["collection"].append(result["collection"][0])
    elif mutation == "plugin":
        result["plugins"].append(["duplicate", "pytest_httpbin.plugin"])
    else:
        statuses[result["collection"][0]] = "FAILED"
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
    with pytest.raises(ValueError):
        adapter.classify_target(task, transcript(result, 0), 0, None, None)


@pytest.mark.parametrize("text,exit_code", [("", 0), (adapter.START + adapter.END, 0),
    (adapter.START + adapter.START + adapter.END, 0), (adapter.END + adapter.START, 0), ("native crash", 3)])
def test_incomplete_or_crashed_output_is_never_a_negative(text, exit_code):
    task, _ = task_control()
    with pytest.raises(ValueError):
        adapter.classify_target(task, text, exit_code, None, None)


def test_bootstrap_is_syntax_valid_and_preserves_native_assertions_and_source_import_boundary():
    source = adapter.bootstrap_source({"pytest.py": "a" * 64})
    ast.parse(source)
    assert source.index("import pytest") < source.index("sys.path.insert(0,'/testbed')") < source.index("import requests")
    assert "--assert=plain" not in source and "--noconftest" not in source
    assert "['-rA','-v']" in source and "entries=list(pkg_resources.iter_entry_points('pytest11'))" in source
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in source and "os.environ.pop(key,None)" in source
    assert "pytest.main(argv,plugins=[recorder])" in source


def policy():
    return {"Image": "sha256:" + "a" * 64, "Mounts": [], "HostConfig": {"Binds": None, "Privileged": False,
        "NetworkMode": "none", "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges"],
        "Memory": 6 * 1024**3, "NanoCpus": 2_000_000_000, "PidsLimit": 256}}


@pytest.mark.parametrize("key,value", [("NetworkMode", "host"), ("CapAdd", ["NET_ADMIN"]), ("Privileged", True),
    ("Binds", ["/:/host"]), ("Memory", 8 * 1024**3), ("PidsLimit", 512)])
def test_target_worker_cannot_widen_isolation(key, value):
    config = policy()
    adapter.inspect_policy(config, config["Image"])
    config["HostConfig"][key] = value
    with pytest.raises(ValueError):
        adapter.inspect_policy(config, config["Image"])


def health_fixture(tmp_path, monkeypatch):
    cert_dir = tmp_path / "cert"
    health_dir = tmp_path / "health"
    cert_dir.mkdir()
    health_dir.mkdir()
    (cert_dir / "wrongca.pem").write_text("public CA")
    admitted = {"image_id": "sha256:" + "a" * 64, "installed_files": {}, "after": {"head": "source", "diff": "", "status": ""}}
    observed = {"health": "verified"}
    stdout, stderr = b"health proof", b""
    report = {"status": "synthetic_legacy_health_qualified", "cleanup_confirmed": True, "cleanup_exit": 0,
        "certificate_receipt_sha256": adapter.oracle.sha(b"certificate receipt"), "image_id": admitted["image_id"],
        "process_exit": 0, "stdout_sha256": adapter.oracle.sha(stdout), "stderr_sha256": adapter.oracle.sha(stderr),
        "bootstrap_sha256": adapter.oracle.sha(b"host health bootstrap"), "observed": observed,
        "before": admitted["after"], "after": admitted["after"], "target_setup_or_tests_executed": False, "model_calls": 0}
    monkeypatch.setattr(adapter.certificate, "admit_certificate_receipt", lambda directory: (b"certificate receipt", admitted))
    monkeypatch.setattr(adapter.health, "runtime_httpbin_version", lambda admitted: "0.5.0")
    monkeypatch.setattr(adapter.health, "bootstrap_source", lambda *args, httpbin_version: "host health bootstrap" if httpbin_version == "0.5.0" else "wrong bootstrap")
    monkeypatch.setattr(adapter.health, "validate_health", lambda text, httpbin_version: observed if httpbin_version == "0.5.0" else {})
    (health_dir / "health.stdout").write_bytes(stdout)
    (health_dir / "health.stderr").write_bytes(stderr)
    return cert_dir, health_dir, report


def test_qualified_health_bound_to_same_cert_image_bootstrap_and_raw_evidence(tmp_path, monkeypatch):
    cert_dir, health_dir, report = health_fixture(tmp_path, monkeypatch)
    (health_dir / "health.json").write_text(json.dumps(report))
    admitted, receipts = adapter.admit_health(cert_dir, health_dir)
    assert admitted["image_id"] == report["image_id"]
    assert receipts["certificate_receipt_sha256"] == report["certificate_receipt_sha256"]


@pytest.mark.parametrize("field,value", [("status", "health_unavailable"), ("cleanup_confirmed", False),
    ("image_id", "other"), ("certificate_receipt_sha256", "0" * 64), ("stdout_sha256", "0" * 64),
    ("bootstrap_sha256", "0" * 64), ("after", {"head": "changed"}), ("model_calls", 1)])
def test_unknown_health_or_changed_evidence_refused(tmp_path, monkeypatch, field, value):
    cert_dir, health_dir, report = health_fixture(tmp_path, monkeypatch)
    (health_dir / "health.json").write_text(json.dumps({**report, field: value}))
    with pytest.raises(ValueError):
        adapter.admit_health(cert_dir, health_dir)


def test_existing_output_or_nonroot_never_starts_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calibrate", "--certificate-receipt", "cert", "--health-receipt", "health", "--output", str(tmp_path)])
    monkeypatch.setattr(adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(adapter.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="fresh output"):
        adapter.main()


def test_same_cardinality_substituted_regression_ids_refused():
    task, control = task_control()
    task["PASS_TO_PASS"] = [*task["PASS_TO_PASS"][:-1], "different::test"]
    with pytest.raises(ValueError):
        adapter.admit_task(task, control)


@pytest.mark.parametrize("raw,expected", [
    ("tests/a.py::TestA::()::test_b", "tests/a.py::TestA::test_b"),
    ("tests/a.py::test_b[param::()::value]", "tests/a.py::test_b[param::()::value]"),
    ("tests/a.py::TestA::()::test_b[x]", "tests/a.py::TestA::test_b[x]")])
def test_exact_collector_normalization_preserves_parameter_bytes(raw, expected):
    assert adapter.canonical_node_id(raw) == expected


@pytest.mark.parametrize("ids", [
    ["a.py::TestA::()::test_b", "a.py::TestA::test_b"],
    ["a.py::TestA::()::test_b"] * 2,
    ["a.py::()::test_b"], ["a.py::::test_b"]])
def test_ambiguous_or_malformed_node_mapping_refused(ids):
    with pytest.raises(ValueError):
        adapter.canonicalize_runner({"collection": ids, "reports": []})


def test_complete_historical_raw_collection_and_reports_are_retained(monkeypatch):
    task, _ = task_control()
    result, statuses = receipt(task)
    mapping = {test: test.replace("::TestRequests::", "::TestRequests::()::") for test in result["collection"]}
    result["collection"] = [mapping[test] for test in result["collection"]]
    result["reports"] = [{**report, "id": mapping[report["id"]]} for report in result["reports"]]
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
    row = adapter.classify_target(task, transcript(result, 0), 0, None, None)
    assert row["runner_receipt"] == result
    assert len(row["raw_to_canonical_node_ids"]) == 76
    assert row["canonical_runner_receipt"]["collection"] == list(mapping)


@pytest.mark.parametrize("category,outcome,expected", [("xfailed", "skipped", "XFAILED"),
                                                       ("xpassed", "failed", "XPASSED")])
def test_old_pytest_expected_outcomes_remain_distinct_outside_requirements(monkeypatch, category, outcome, expected):
    task, _ = task_control()
    result, statuses = receipt(task)
    extra = "tests/extra.py::test_expected"
    result["collection"].append(extra)
    result["reports"].extend([
        {"id": extra, "when": "setup", "outcome": "passed", "has_wasxfail": False},
        {"id": extra, "when": "call", "outcome": outcome, "has_wasxfail": True,
         "wasxfail": "original native mark", "native_category": category},
        {"id": extra, "when": "teardown", "outcome": "passed", "has_wasxfail": False}])
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
    row = adapter.classify_target(task, transcript(result, 0), 0, None, None)
    assert row["verified_success"] is True
    assert row["tests"][extra] == expected
    assert row["expected_failure_outcomes"] == {extra: expected}


def test_required_xpass_does_not_satisfy_a_required_pass(monkeypatch):
    task, _ = task_control()
    result, statuses = receipt(task)
    call = result["reports"][1]
    call.update(outcome="failed", has_wasxfail=True, wasxfail="native mark", native_category="xpassed")
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
    with pytest.raises(ValueError):
        adapter.classify_target(task, transcript(result, 0), 0, None, None)


def test_verbose_output_requires_every_node_and_rejects_collisions():
    with pytest.raises(ValueError, match="full native verbose"):
        adapter.verbose_observations("a.py::test_a PASSED\n", ["a.py::test_a", "a.py::test_b"])
    with pytest.raises(ValueError, match="duplicate canonical"):
        adapter.verbose_observations("a.py::TestA::()::test_a PASSED\na.py::TestA::test_a PASSED\n", ["a.py::TestA::test_a"])


@pytest.mark.parametrize("node,expected", [
    ("a.py::test_x[ø]", r"a.py::test_x[\xf8]"),
    ("a.py::test_x[Ж]", r"a.py::test_x[\u0416]"),
    ("a.py::test_x[😀]", r"a.py::test_x[\U0001f600]"),
    (r"a.py::test_x[literal\xf8]", r"a.py::test_x[literal\xf8]")])
def test_only_nonascii_codepoints_gain_derived_terminal_aliases(node, expected):
    assert adapter.terminal_escape_nonascii(node) == expected
    mapping = adapter.sealed_terminal_mapping([node])
    assert mapping[node] == mapping[expected] == node


def test_exact_captured_unicode_verbose_line_maps_to_sealed_raw_node():
    raw = "tests/test_requests.py::TestRequests::()::test_unicode_get[ø-params4]"
    canonical = adapter.canonical_node_id(raw)
    printed = r"tests/test_requests.py::TestRequests::test_unicode_get[\xf8-params4]"
    assert adapter.verbose_observations(printed + " PASSED\n", [canonical]) == {canonical: "PASSED"}


@pytest.mark.parametrize("nodes", [
    ["a.py::test_x[ø]", r"a.py::test_x[\xf8]"],
    [r"a.py::test_x[\xf8]", "a.py::test_x[ø]"],
    ["a.py::test_x[Ж]", r"a.py::test_x[\u0416]"],
    ["a.py::test_x[😀]", r"a.py::test_x[\U0001f600]"]])
def test_literal_escape_and_unicode_spelling_collisions_are_refused(nodes):
    with pytest.raises(ValueError, match="terminal spelling collision"):
        adapter.sealed_terminal_mapping(nodes)


def test_unsealed_escape_is_not_decoded_into_a_different_node():
    with pytest.raises(ValueError, match="outside collection"):
        adapter.verbose_observations(r"a.py::test_x[\u00f8] PASSED" + "\n", ["a.py::test_x[ø]"])


def test_independent_parser_uses_the_same_sealed_terminal_alias_map(monkeypatch):
    task, _ = task_control()
    result, statuses = receipt(task)
    raw = "tests/test_requests.py::TestRequests::()::test_unicode_get[ø-params4]"
    canonical = adapter.canonical_node_id(raw)
    printed = r"tests/test_requests.py::TestRequests::test_unicode_get[\xf8-params4]"
    assert canonical not in result["collection"]
    result["collection"].append(raw)
    result["reports"].extend({"id": raw, "when": phase, "outcome": "passed", "has_wasxfail": False}
                             for phase in ("setup", "call", "teardown"))
    parser_results = {**statuses, printed: "PASSED"}
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(parser_results, result["collection"]))
    text = transcript(result, 0).replace(raw + " PASSED", printed + " PASSED", 1)
    row = adapter.classify_target(task, text, 0, None, None)
    assert row["tests"][canonical] == "PASSED"
    assert row["printed_to_canonical_node_ids"][printed] == canonical
    assert row["raw_to_canonical_node_ids"][raw] == canonical
    assert row["runner_receipt"]["collection"][-1] == raw



def test_whitespace_parameter_ids_with_identical_prefix_keep_distinct_outcomes():
    first = "tests/test_requests.py::TestTimeout::test_invalid_timeout[timeout0-(connect, read)]"
    second = "tests/test_requests.py::TestTimeout::test_invalid_timeout[timeout0-(connect, write)]"
    collection = [first, second]
    body = first + " PASSED\n" + second + " FAILED\n"
    transformed, inverse = adapter.opaque_parser_transport(body, collection, adapter.sealed_terminal_mapping(collection))
    assert len(inverse) == 2 and len(set(inverse.values())) == 2
    # The independent parser's whitespace-based token extraction sees the whole
    # fixed opaque identity, preserving both statuses instead of truncating them.
    observed = {}
    for line in transformed.splitlines():
        identity, status = line.split()
        assert identity.startswith("tests/p07_identity.py::case_")
        observed[inverse[identity]] = status
    assert observed == {first: "PASSED", second: "FAILED"}
    assert adapter.oracle.sha(body.encode()) != adapter.oracle.sha(transformed.encode())


def test_opaque_transport_retains_exact_expected_failure_status_tokens():
    collection = ["a.py::test_x", "a.py::test_y"]
    transformed, inverse = adapter.opaque_parser_transport("a.py::test_x xfail\na.py::test_y XPASS\n",
                                                         collection, adapter.sealed_terminal_mapping(collection))
    assert [line.split()[1] for line in transformed.splitlines()] == ["xfail", "XPASS"]
    assert set(inverse.values()) == set(collection)


@pytest.mark.parametrize("body", [
    "a.py::test_a PASSED\na.py::unknown FAILED\n",
    "a.py::test_a PASSED\na.py::test_a FAILED\n",
    "a.py::test_a UNKNOWN\n",
    "a.py::test_a PASSED\na.py::test_a PASSED\n"])
def test_opaque_transport_refuses_unknown_duplicate_or_missing_native_rows(body):
    with pytest.raises(ValueError):
        adapter.opaque_parser_transport(body, ["a.py::test_a"], {"a.py::test_a": "a.py::test_a"})


def test_external_parser_unknown_opaque_identity_is_not_decoded_or_accepted(monkeypatch):
    task, _ = task_control()
    result, _ = receipt(task)
    monkeypatch.setattr(adapter.oracle, "parse_observations",
                        lambda *args: ({"tests/p07_identity.py::case_999999": "PASSED"}, {}))
    with pytest.raises(ValueError, match="outside sealed opaque"):
        adapter.classify_target(task, transcript(result, 0), 0, None, None)


def test_opaque_transport_diagnostics_preserve_full_inverse_map_and_original_body(monkeypatch):
    task, _ = task_control()
    result, statuses = receipt(task)
    monkeypatch.setattr(adapter.oracle, "parse_observations", stub_external(statuses, result["collection"]))
    text = transcript(result, 0)
    row = adapter.classify_target(task, text, 0, None, None)
    diagnostics = row["parser_diagnostics"]
    body = text.split(adapter.START, 1)[1].split(adapter.END, 1)[0]
    assert diagnostics["original_body_sha256"] == adapter.oracle.sha(body.encode())
    assert len(diagnostics["opaque_to_canonical_node_ids"]) == 76
    assert set(diagnostics["opaque_to_canonical_node_ids"].values()) == set(result["collection"])
    assert "statuses unchanged" in diagnostics["identity_transport"]
