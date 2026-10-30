import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import p07_calibration_binding as binding  # noqa:E402
import p07_campaign as campaign  # noqa:E402
import prepare_p07_kaggle as prepare  # noqa:E402


def fixture():
    tasks = json.loads((ROOT / "benchmarks/stage4/corpus-p07-v3.json").read_bytes())["tasks"]
    task = next(task for task in tasks if task["instance_id"] == "psf__requests-2674")
    report = json.loads((ROOT / "validation/p07-budget20/requests2674-calibration-v3/oracle/calibration.json").read_bytes())
    config = dict(model=campaign.MODEL, count=3, total_budget_usd=1.32, arm_budget_usd=.22,
                  max_calls=20, max_input_tokens=60000, max_output_tokens=1024, deadline_seconds=900,
                  price_in_per_mtok=2, price_out_per_mtok=10)
    runtime = hashlib.sha256((ROOT / "tools/p07_campaign.py").read_bytes()).hexdigest()
    helper = hashlib.sha256((ROOT / "tools/p07_calibration_binding.py").read_bytes()).hexdigest()
    options = dict(corpus_sha256="a" * 64, config=config, runtime_sha256=runtime, binding_helper_sha256=helper)
    public = binding.public_task({**task, "source_path": "requests/adapters.py"})
    return task, report, public, options


def admitted():
    task, report, public, options = fixture()
    receipt = binding.create_host_binding(task, report, source_path=public["source_path"], **options)
    return public, receipt, options


def test_full_private_calibration_is_validated_before_only_digests_are_returned():
    task, report, public, options = fixture()
    receipt = binding.create_host_binding(task, report, source_path=public["source_path"], **options)
    assert set(receipt) == binding.BINDING_FIELDS
    assert receipt["required_count"] == 155
    assert receipt["scope"] == binding.SCOPE
    assert not ({"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "tests", "rows", "calibration"} & set(receipt))
    assert receipt["full_task_sha256"] == binding.canonical_sha(task)
    assert binding.validate_host_binding(public, receipt, expected_digest=binding.canonical_sha(receipt), **options)
    broken = copy.deepcopy(report)
    del broken["rows"][1]["tests"]["test_requests.py::test_urllib3_pool_connection_closed"]
    with pytest.raises(ValueError, match="calibration"):
        binding.create_host_binding(task, broken, source_path=public["source_path"], **options)


@pytest.mark.parametrize("changed", ["problem", "base", "corpus", "config", "runtime", "helper", "receipt", "protected"])
def test_redacted_binding_rejects_changed_public_or_prepared_identity(changed):
    public, receipt, options = admitted()
    expected = binding.canonical_sha(receipt)
    if changed == "problem":
        public["problem_statement"] += " changed"
    elif changed == "base":
        public["base_commit"] = "0" * 40
    elif changed == "corpus":
        options["corpus_sha256"] = "b" * 64
    elif changed == "config":
        options["config"] = {**options["config"], "max_calls": 19}
    elif changed == "runtime":
        options["runtime_sha256"] = "b" * 64
    elif changed == "helper":
        options["binding_helper_sha256"] = "b" * 64
    elif changed == "receipt":
        receipt["result"] = "full_p07"
    else:
        public["calibration"] = {"tests": "must stay private"}
    with pytest.raises(ValueError):
        binding.validate_host_binding(public, receipt, expected_digest=expected, **options)


def test_initial_source_binding_detects_actual_file_change_before_backend_creation(tmp_path, monkeypatch):
    import atomic_flow.backends.openai_compatible as backend

    public, receipt, options = admitted()
    workspace = tmp_path / "workspace"
    source = workspace / public["source_path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"def original():\n    return 1\n")
    files = binding.source_manifest(workspace)
    source_options = {key: value for key, value in options.items() if key != "corpus_sha256"}
    initial = binding.create_source_binding(public, receipt, files, **source_options)
    assert binding.validate_source_binding(public, receipt, initial, expected_digest=binding.canonical_sha(initial),
        files=files, workspace=workspace, **source_options)
    source.write_bytes(b"def changed():\n    return 2\n")
    calls = []
    monkeypatch.setattr(backend, "OpenAICompatibleBackend", lambda *args, **kwargs: calls.append(True))
    payload = {"task": public, "config": options["config"], "workspace": str(workspace), "out": str(tmp_path / "out"),
        "host_binding": receipt, "host_binding_sha256": binding.canonical_sha(receipt),
        "calibration_corpus_sha256": options["corpus_sha256"], "runtime_sha256": options["runtime_sha256"],
        "binding_helper_sha256": options["binding_helper_sha256"], "source_manifest": files,
        "source_binding": initial, "source_binding_sha256": binding.canonical_sha(initial)}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="initial source"):
        campaign.arm_main(path)
    assert not calls


def test_preflight_cannot_enter_arm_even_with_otherwise_valid_config(tmp_path, monkeypatch):
    import atomic_flow.backends.openai_compatible as backend

    calls = []
    monkeypatch.setattr(backend, "OpenAICompatibleBackend", lambda *args, **kwargs: calls.append(True))
    path = tmp_path / "input.json"
    _, _, public, options = fixture()
    path.write_text(json.dumps({"task": public, "config": {**options["config"], "preflight_only": True,
                                                          "total_budget_usd": 0, "arm_budget_usd": 0}}))
    with pytest.raises(ValueError, match="zero-call preflight"):
        campaign.arm_main(path)
    assert not calls


def test_builder_redacts_calibrations_and_embeds_the_exact_binding_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "catalog_model", lambda: {"version": {"slug": "claude-sonnet-5-default"}})
    inventory = json.loads((ROOT / "validation/p07-budget20/calibration-inventory.json").read_bytes())
    calibration_dir = tmp_path / "host-private-calibrations"
    calibration_dir.mkdir()
    for task_id in campaign.PILOT:
        record = next(record for record in inventory["rows"] if record["instance_id"] == task_id)
        (calibration_dir / (task_id + ".json")).write_bytes((ROOT / record["accepted_evidence"]).read_bytes())
    args = SimpleNamespace(slug="offline-two-phase-redacted", owner="akseley", count=3, budget=4.8, arm_budget=.8,
                           preflight=False, calibrations=calibration_dir, output=tmp_path / "bundle")
    prepare.prepare(args)
    packet = json.loads((args.output / "packet.json").read_bytes())
    for task in packet["tasks"]:
        assert not ({"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "calibration", "rows", "tests"} & set(task))
        if task["instance_id"] in campaign.PILOT:
            assert set(task["calibration_binding"]) == binding.BINDING_FIELDS
    assert len(packet["host_admissions"]) == 3
    assert packet["binding_helper_sha256"] == hashlib.sha256((ROOT / "tools/p07_calibration_binding.py").read_bytes()).hexdigest()
    assert "BINDING_HELPER = base64.b64decode" in (args.output / "campaign_task.py").read_text()
    assert not list(args.output.rglob("*calibration.json"))


@pytest.mark.parametrize('field', ['schema_version', 'validator_version'])
@pytest.mark.parametrize('value', [True, 1.0, '1'])
def test_binding_versions_require_exact_integer_types_even_with_matching_hash(field, value):
    public, receipt, options = admitted()
    receipt[field] = value
    with pytest.raises(ValueError, match='binding'):
        binding.validate_host_binding(public, receipt, expected_digest=binding.canonical_sha(receipt), **options)


@pytest.mark.parametrize('files', [[], None, True, {}, {'../a.py': 'a' * 64}, {'/a.py': 'a' * 64},
    {'a\\b.py': 'a' * 64}, {'a:stream.py': 'a' * 64}, {'a//b.py': 'a' * 64}, {'a\nb.py': 'a' * 64},
    {'a.txt': 'a' * 64}, {'a.py': True}, {'a.py': 'wrong'}, {False: 'a' * 64}])
def test_source_binding_rejects_malformed_mapping_or_unconfined_paths(files):
    public, receipt, options = admitted()
    with pytest.raises(ValueError):
        binding.create_source_binding(public, receipt, files,
            **{key: value for key, value in options.items() if key != 'corpus_sha256'})


def test_manifest_traversal_counts_directories_before_building_a_file_list(tmp_path, monkeypatch):
    for name in ('a', 'b', 'c', 'd'):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(binding, 'MAX_ENTRIES', 3)
    with pytest.raises(ValueError, match='traversal entry bound'):
        binding.source_manifest(tmp_path)


def test_manifest_refuses_directory_depth_before_descending(tmp_path, monkeypatch):
    (tmp_path / 'one/two/three').mkdir(parents=True)
    monkeypatch.setattr(binding, 'MAX_DEPTH', 2)
    with pytest.raises(ValueError, match='directory depth bound'):
        binding.source_manifest(tmp_path)


def test_manifest_refuses_links_and_junctions_before_reading(tmp_path, monkeypatch):
    (tmp_path / 'linked').mkdir()
    monkeypatch.setattr(binding, '_linked', lambda path: path.name == 'linked')
    with pytest.raises(ValueError, match='links are not admitted'):
        binding.source_manifest(tmp_path)


def test_manifest_refuses_linked_ancestor(tmp_path, monkeypatch):
    root = tmp_path / 'ordinary'
    root.mkdir()
    monkeypatch.setattr(binding, '_linked', lambda path: path == tmp_path)
    with pytest.raises(ValueError, match='linked ancestors'):
        binding.source_manifest(root)


@pytest.mark.parametrize('limit', ['files', 'per_file', 'total'])
def test_manifest_enforces_file_and_byte_bounds(tmp_path, monkeypatch, limit):
    for name in ('a.py', 'b.py', 'c.py'):
        (tmp_path / name).write_bytes(b'123')
    if limit == 'files':
        monkeypatch.setattr(binding, 'MAX_FILES', 2)
    elif limit == 'per_file':
        monkeypatch.setattr(binding, 'MAX_FILE_BYTES', 2)
    else:
        monkeypatch.setattr(binding, 'MAX_TOTAL_BYTES', 4)
    with pytest.raises(ValueError, match='bounds'):
        binding.source_manifest(tmp_path)


def test_streaming_hash_caps_reads_when_file_grows_after_stat(tmp_path, monkeypatch):
    path = tmp_path / 'a.py'
    path.write_bytes(b'123')
    monkeypatch.setattr(binding, 'MAX_FILE_BYTES', 8)
    requested = []

    def growth(fd, amount):
        requested.append(amount)
        return b'x' * amount

    monkeypatch.setattr(binding.os, 'read', growth)
    with pytest.raises(ValueError, match='byte bounds'):
        binding._file_digest(path, path.stat(), 20)
    assert requested == [9]


def test_source_receipt_cannot_use_boolean_version_with_recomputed_index(tmp_path):
    public, receipt, options = admitted()
    path = tmp_path / public['source_path']
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original')
    files = binding.source_manifest(tmp_path)
    source_options = {key: value for key, value in options.items() if key != 'corpus_sha256'}
    initial = binding.create_source_binding(public, receipt, files, **source_options)
    initial['schema_version'] = True
    with pytest.raises(ValueError, match='source snapshot/binding'):
        binding.validate_source_binding(public, receipt, initial, expected_digest=binding.canonical_sha(initial),
            files=files, workspace=tmp_path, **source_options)
