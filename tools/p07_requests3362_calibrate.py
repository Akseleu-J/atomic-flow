"""Fixed3362 complete target oracle on independently qualified legacy services."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

import evaluation_p07_isolated as oracle
import p07_legacy_transplant as transplant
import p07_requests3362_cert_image as certificate
import p07_requests3362_health as health
import p07_requests3362_legacy_image as runtime
import p07_timeout_topology_probe as topology
from evaluation_stage4 import load_manifest, patch_paths, protected_test_path

ROOT = Path(__file__).resolve().parents[1]
TASK = runtime.TASK
LIMIT = 16 * 1024**2
START = ">>>>> Start Test Output"
END = ">>>>> End Test Output"
RESULT = "P07_TARGET_RESULT="


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def admit_health(cert_dir, health_dir):
    raw_cert, admitted = certificate.admit_certificate_receipt(cert_dir)
    raw = (health_dir / "health.json").read_bytes()
    report = json.loads(raw)
    stdout = (health_dir / "health.stdout").read_bytes()
    stderr = (health_dir / "health.stderr").read_bytes()
    httpbin_version = health.runtime_httpbin_version(admitted)
    bootstrap = health.bootstrap_source(admitted["installed_files"], (cert_dir / "wrongca.pem").read_text(),
                                        httpbin_version=httpbin_version)
    if (report.get("status") != "synthetic_legacy_health_qualified" or report.get("cleanup_confirmed") is not True
            or report.get("cleanup_exit") != 0 or report.get("certificate_receipt_sha256") != oracle.sha(raw_cert)
            or report.get("image_id") != admitted["image_id"] or report.get("process_exit") != 0
            or report.get("stdout_sha256") != oracle.sha(stdout) or report.get("stderr_sha256") != oracle.sha(stderr)
            or report.get("bootstrap_sha256") != oracle.sha(bootstrap.encode())
            or report.get("observed") != health.validate_health(stdout.decode(), httpbin_version=httpbin_version)
            or report.get("before") != admitted["after"] or report.get("before") != report.get("after")
            or report.get("target_setup_or_tests_executed") is not False or report.get("model_calls") != 0):
        raise ValueError("fresh qualified same-image health receipt required")
    return admitted, {"certificate_receipt_sha256": oracle.sha(raw_cert), "health_receipt_sha256": oracle.sha(raw)}


def admit_task(task, control):
    if (task.get("instance_id") != TASK or task.get("repo") != "psf/requests" or task.get("base_commit") != runtime.COMMIT
            or task.get("version") != "2.10" or task.get("FAIL_TO_PASS") != ["tests/test_requests.py::TestRequests::test_response_decode_unicode"]
            or oracle.sha(json.dumps(task.get("PASS_TO_PASS", []), separators=(",", ":")).encode()) != "f92485be254095c67dfe0b4792d9dcfa8ecac6e3771d518c8fd4fd64bb002425"
            or oracle.sha(task.get("patch", "").encode()) != "23e64f2da841d5d49874221e915a084e9bb1def2fcd6d6a24fd8e7dce71fba8d"
            or oracle.sha(task.get("test_patch", "").encode()) != "cbb63761e0d50033c347fb0847126632ba4c8848db8bd7f598e21d3998d5c260"):
        raise ValueError("frozen3362 task identity or obligations differ")
    old, new = control.get("old"), control.get("new")
    if old != "+        encoding = r.apparent_encoding" or new != "+        yield from iterator; return" or task["patch"].count(old) != 1:
        raise ValueError("reviewed v6 negative required")
    wrong = task["patch"].replace(old, new)
    if oracle.sha(wrong.encode()) != "9d0a89d0040b05a3ca5ddda9b252553b397612b99c35b8979f5c1f00aaac4946":
        raise ValueError("wrong patch differs")
    return wrong


def bootstrap_source(files):
    # This program executes only inside the independently admitted target worker.
    data = {"files": files, "plugins": list(health.PLUGIN_ARGS), "entrypoints": health.ENTRYPOINTS}
    return r'''import hashlib,json,os,pathlib,sys
payload=json.loads(DATA)
prefix='/opt/p07-python36/lib/python3.6/site-packages/'
if list(sys.version_info[:3])!=[3,6,15] or sys.prefix!='/opt/p07-python36' or not sys.flags.isolated or os.getcwd()!='/testbed':
    raise RuntimeError('private target interpreter identity mismatch')
for relative,expected in payload['files'].items():
    path=pathlib.Path(prefix)/relative
    if path.is_symlink() or not str(path.resolve()).startswith(prefix) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('installed wheel or certificate overlay changed')
for key in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD'):
    os.environ.pop(key,None)
import pkg_resources
entries=list(pkg_resources.iter_entry_points('pytest11'))
if len(entries)!=2 or {ep.name:ep.module_name for ep in entries}!=payload['entrypoints']:
    raise RuntimeError('pytest entrypoint allowlist mismatch')
if any(str(pathlib.Path(ep.dist.location).resolve())!=prefix.rstrip('/') for ep in entries):
    raise RuntimeError('entrypoint outside private prefix')
import pytest
if pytest.__version__!='2.8.7' or not str(pathlib.Path(pytest.__file__).resolve()).startswith(prefix):
    raise RuntimeError('pytest import identity mismatch')
sys.path.insert(0,'/testbed')
import requests
if str(pathlib.Path(requests.__file__).resolve())!='/testbed/requests/__init__.py':
    raise RuntimeError('target Requests source import mismatch')
class Recorder:
    def __init__(self):
        self.collected=[]; self.reports=[]; self.finished=False; self.exit=None; self.plugins=[]
    def pytest_configure(self,config):
        self.config=config
        self.plugins=[[name,getattr(plugin,'__name__',plugin.__class__.__module__)] for name,plugin in config.pluginmanager.list_name_plugin() if plugin is not None]
    def pytest_collection_finish(self,session):
        self.collected=[item.nodeid for item in session.items]
    def pytest_runtest_logreport(self,report):
        self.reports.append({'id':report.nodeid,'when':report.when,'outcome':report.outcome,
            'has_wasxfail':hasattr(report,'wasxfail'),'wasxfail':getattr(report,'wasxfail',None),
            'native_category':self.config.hook.pytest_report_teststatus(report=report)[0]})
    def pytest_sessionfinish(self,session,exitstatus):
        self.finished=True; self.exit=exitstatus
recorder=Recorder()
argv=payload['plugins']+['-rA','-v']
print('>>>>> Start Test Output',flush=True)
exit_code=pytest.main(argv,plugins=[recorder])
print('P07_TARGET_RESULT='+json.dumps({'version_info':list(sys.version_info[:3]),'prefix':sys.prefix,'executable':sys.executable,
    'pytest_module':pytest.__file__,'pytest_version':pytest.__version__,'requests_module':requests.__file__,
    'argv':argv,'plugins':recorder.plugins,'collection':recorder.collected,'reports':recorder.reports,'finished':recorder.finished,'exit':recorder.exit},sort_keys=True),flush=True)
print('P07_TEST_EXIT='+str(exit_code),flush=True)
print('>>>>> End Test Output',flush=True)
raise SystemExit(exit_code)
'''.replace("DATA", repr(json.dumps(data, sort_keys=True)))


def canonical_node_id(raw):
    if not isinstance(raw, str) or not raw or "\n" in raw or "\r" in raw:
        raise ValueError("bounded plain raw node identity required")
    base, separator, parameters = raw.partition("[")
    parts = base.split("::")
    if any(not part for part in parts):
        raise ValueError("empty node identity segment")
    if "()" in parts:
        if len(parts) != 4 or parts[2] != "()" or parts.count("()") != 1:
            raise ValueError("unreviewed historical instance collector shape")
        parts.pop(2)
    return "::".join(parts) + separator + parameters


def canonicalize_runner(raw):
    observed = {**raw}
    collection = raw.get("collection")
    if not isinstance(collection, list) or not collection or len(set(collection)) != len(collection):
        raise ValueError("unique complete raw collection required")
    mapping = {node: canonical_node_id(node) for node in collection}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("raw collection canonicalization collision")
    observed["collection"] = list(mapping.values())
    observed["reports"] = []
    for report in raw.get("reports", []):
        if report.get("id") not in mapping:
            raise ValueError("report outside sealed raw collection")
        observed["reports"].append({**report, "id": mapping[report["id"]]})
    return observed, mapping


def terminal_escape_nonascii(node):
    """Derive legacy ASCII-terminal spellings without decoding literal escapes."""
    output = []
    for character in node:
        point = ord(character)
        if point < 128:
            output.append(character)
        elif point <= 0xff:
            output.append("\\x" + format(point, "02x"))
        elif point <= 0xffff:
            output.append("\\u" + format(point, "04x"))
        else:
            output.append("\\U" + format(point, "08x"))
    return "".join(output)


def sealed_terminal_mapping(collection):
    mapping = {}
    for node in collection:
        if canonical_node_id(node) != node:
            raise ValueError("canonical sealed collection required for terminal mapping")
        for printed in {node, terminal_escape_nonascii(node)}:
            if printed in mapping and mapping[printed] != node:
                raise ValueError("sealed collection terminal spelling collision")
            mapping[printed] = node
    return mapping


def verbose_observations(body, collection, printed_mapping=None):
    results = {}
    printed_mapping = sealed_terminal_mapping(collection) if printed_mapping is None else printed_mapping
    pattern = r"^(\S+::.*?)\s+(PASSED|FAILED|ERROR|SKIPPED|xfail|XPASS)(?:\s+\[\s*\d+%\])?\s*$"
    statuses = {"xfail": "XFAILED", "XPASS": "XPASSED"}
    for match in re.finditer(pattern, oracle.ANSI.sub("", body), re.MULTILINE):
        node = printed_mapping.get(canonical_node_id(match[1]))
        if node not in collection or node in results:
            raise ValueError("verbose result outside collection or duplicate canonical node")
        results[node] = statuses.get(match[2], match[2])
    if set(results) != set(collection):
        raise ValueError("full native verbose observation inventory required")
    return results


def opaque_parser_transport(body, collection, printed_mapping):
    """Transport full exact native identities through the pinned whitespace parser."""
    tests = verbose_observations(body, collection, printed_mapping)
    node_to_opaque = {node: "tests/p07_identity.py::case_" + format(index, "06d")
                      for index, node in enumerate(sorted(collection))}
    opaque_to_node = {opaque: node for node, opaque in node_to_opaque.items()}
    lines = []
    pattern = r"^(\S+::.*?)\s+(PASSED|FAILED|ERROR|SKIPPED|xfail|XPASS)(?:\s+\[\s*\d+%\])?\s*$"
    for match in re.finditer(pattern, oracle.ANSI.sub("", body), re.MULTILINE):
        node = printed_mapping[canonical_node_id(match[1])]
        lines.append(node_to_opaque[node] + " " + match[2])
    if len(lines) != len(collection) or len(tests) != len(collection):
        raise ValueError("opaque transport omitted or duplicated a native observation")
    return "\n".join(lines) + "\n", opaque_to_node


def classify_target(task, text, exit_code, parser, spec, *, runtime_profile="python36"):
    if runtime_profile == "python27" and task.get("instance_id") == "psf__requests-2674":
        version, prefix, executable = [2, 7, 18], "/opt/p07-python27", "/opt/p07-python27/bin/python2.7"
    elif runtime_profile == "python36":
        version, prefix, executable = [3, 6, 15], transplant.PREFIX, transplant.PREFIX + "/bin/python3.6"
    else:
        raise ValueError("unreviewed task/runtime classification profile")
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        raise ValueError("complete unique target test markers required")
    body = text.split(START, 1)[1].split(END, 1)[0]
    receipts = re.findall(r"^" + RESULT + r"(.*)$", body, re.MULTILINE)
    if len(receipts) != 1 or re.findall(r"^P07_TEST_EXIT=(\d+)$", body, re.MULTILINE) != [str(exit_code)] or exit_code not in {0, 1}:
        raise ValueError("one complete native target receipt required")
    raw_observed = json.loads(receipts[0])
    observed, node_mapping = canonicalize_runner(raw_observed)
    required = task["FAIL_TO_PASS"] + task["PASS_TO_PASS"]
    if (observed.get("version_info") != version or observed.get("prefix") != prefix
            or observed.get("executable") != executable
            or observed.get("pytest_version") != "2.8.7"
            or not observed.get("pytest_module", "").startswith(prefix + ("/lib/python2.7/site-packages/" if runtime_profile == "python27" else "/lib/python3.6/site-packages/"))
            or observed.get("requests_module") != "/testbed/requests/__init__.py"
            or observed.get("argv") != [*health.PLUGIN_ARGS, "-rA", "-v"]
            or observed.get("finished") is not True or observed.get("exit") != exit_code
            or not observed.get("collection") or len(set(observed["collection"])) != len(observed["collection"])
            or not set(required) <= set(observed["collection"])):
        raise ValueError("target runner identity or complete collection mismatch")
    for module in ("pytest_httpbin.plugin", "pytest_mock"):
        if sum(item[1] == module for item in observed.get("plugins", [])) != 1:
            raise ValueError("historical fixture plugin not registered exactly once")
    for test in observed["collection"]:
        phases = [item for item in observed.get("reports", []) if item.get("id") == test]
        if not phases or not {"setup", "teardown"} <= {item.get("when") for item in phases}:
            raise ValueError("ordinary collection lacks complete setup and teardown")
    printed_mapping = sealed_terminal_mapping(observed["collection"])
    tests = verbose_observations(body, observed["collection"], printed_mapping)
    transported_body, opaque_mapping = opaque_parser_transport(body, observed["collection"], printed_mapping)
    parsed, diagnostics = oracle.parse_observations(task, transported_body, parser, spec)
    diagnostics["identity_transport"] = "bijective sealed full verbose node identities; statuses unchanged"
    diagnostics["original_body_sha256"] = oracle.sha(body.encode())
    diagnostics["transported_body_sha256"] = oracle.sha(transported_body.encode())
    diagnostics["opaque_to_canonical_node_ids"] = opaque_mapping
    parsed_canonical = {}
    for raw_id, status in parsed.items():
        node = opaque_mapping.get(raw_id)
        if node is None:
            raise ValueError("independent parser result outside sealed opaque identities")
        if node in parsed_canonical:
            raise ValueError("native summary node canonicalization collision")
        parsed_canonical[node] = status
        if status in {"PASSED", "FAILED", "ERROR", "SKIPPED"} and tests.get(node) != status:
            raise ValueError("independent parser disagrees with exact native verbose observation")
    expected_outcomes = {}
    for test in observed["collection"]:
        phases = [item for item in observed["reports"] if item["id"] == test]
        calls = [item for item in phases if item["when"] == "call"]
        for item in phases:
            if item.get("has_wasxfail"):
                category = item.get("native_category")
                if (not isinstance(item.get("wasxfail"), str) or category not in {"xfailed", "xpassed"}
                        or item.get("outcome") != {"xfailed": "skipped", "xpassed": "failed"}[category]):
                    raise ValueError("historical expected-failure semantics mismatch")
                expected_outcomes[test] = "XFAILED" if category == "xfailed" else "XPASSED"
        if test in expected_outcomes:
            if tests.get(test) not in {expected_outcomes[test], "XFAIL" if expected_outcomes[test] == "XFAILED" else "XPASS"}:
                raise ValueError("native verbose expected-failure observation disagrees")
            tests[test] = expected_outcomes[test]
        elif len(calls) == 1 and calls[0].get("outcome") in {"passed", "failed"}:
            if tests.get(test) != calls[0]["outcome"].upper():
                raise ValueError("ordinary native verbose output disagrees with direct report")
    for test in required:
        phases = [item for item in observed.get("reports", []) if item.get("id") == test]
        if (len(phases) != 3 or {item.get("when") for item in phases} != {"setup", "call", "teardown"}
                or any(item.get("has_wasxfail") for item in phases)
                or any(item.get("outcome") != "passed" for item in phases if item["when"] != "call")
                or sum(item.get("when") == "call" and item.get("outcome") in {"passed", "failed"} for item in phases) != 1):
            raise ValueError("required target setup/call/teardown incomplete or skipped")
        call = next(item["outcome"] for item in phases if item["when"] == "call").upper()
        if tests.get(test) != {"PASSED": "PASSED", "FAILED": "FAILED"}[call] or parsed_canonical.get(test) != call:
            raise ValueError("native summary disagrees with direct phase observation")
    return {**oracle.classify(task, tests, completed=True, exit_code=exit_code), "tests": tests,
            "parser_diagnostics": diagnostics, "runner_receipt": raw_observed,
            "canonical_runner_receipt": observed, "raw_to_canonical_node_ids": node_mapping,
            "expected_failure_outcomes": expected_outcomes, "printed_to_canonical_node_ids": printed_mapping}


def execute(args, timeout=60, *, input_bytes=None):
    clean = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
    return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=clean)


def checked(args, timeout=60):
    process = execute(args, timeout)
    if process.returncode:
        raise RuntimeError("fixed oracle worker command failed: " + args[1])
    return process.stdout.decode().strip()


def inspect_policy(config, image_id):
    host = config["HostConfig"]
    if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
            or host.get("NetworkMode") != "none" or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or "no-new-privileges" not in host.get("SecurityOpt", [])
            or host.get("Memory") != 6 * 1024**3 or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 256):
        raise ValueError("oracle isolation policy mismatch")


def grade(task, spec, parser, admitted, patch, label, output):
    name = "p07-requests3362-" + uuid.uuid4().hex[:20]
    row = {"label": label, "status": "infrastructure_error", "verified_success": False, "container_name": name,
           "patch_sha256": oracle.sha(patch.encode())}
    fd = None
    try:
        if patch and (patch_paths(patch) & patch_paths(task["test_patch"]) or any(protected_test_path(p) for p in patch_paths(patch))):
            raise ValueError("production patch crosses protected boundary")
        checked(["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--memory", "6g", "--cpus", "2", "--pids-limit", "256", "--entrypoint", "/bin/sleep", admitted["image_id"], "infinity"])
        checked(["docker", "start", name])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        inspect_policy(config, admitted["image_id"])
        pid = config["State"]["Pid"]
        identity = topology.process_identity(pid)
        host_inode = Path("/proc/self/ns/net").stat().st_ino
        if identity["netns_inode"] == host_inode:
            raise ValueError("target shares host network namespace")
        fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        namespace = f"/proc/{os.getpid()}/fd/{fd}"
        if os.fstat(fd).st_ino != identity["netns_inode"] or topology.process_identity(pid) != identity:
            raise ValueError("owned namespace changed before fixed setup")
        for command in topology.setup_commands(namespace):
            if topology.process_identity(pid) != identity:
                raise ValueError("target PID changed before setup")
            checked(command)
        row["routes"] = json.loads(checked(topology.namespace_command(namespace, "ip", "-j", "route", "show", "table", "all")))
        if any(route.get("dst") == "default" for route in row["routes"]):
            raise ValueError("default route in target namespace")
        if not any(route.get("dst") == "10.255.255.1" and route.get("dev") == "lo" for route in row["routes"]):
            raise ValueError("fixed timeout loopback route absent")
        external = execute(topology.namespace_command(namespace, "ip", "route", "get", "1.1.1.1"))
        if external.returncode == 0:
            raise ValueError("target has external route")
        checked(topology.namespace_command(namespace, "iptables", "-w", "5", "-C", "OUTPUT", *topology.RULE))
        probe = execute(["docker", "exec", "-w", "/tmp", name, transplant.LAUNCHER, "-c", topology.PROBE], 15)
        row["topology_probe"] = json.loads(probe.stdout) if probe.returncode == 0 else {}
        if probe.returncode or row["topology_probe"].get("blackhole") != "socket.timeout":
            raise ValueError("same-worker real timeout topology unqualified")
        launcher = checked(["docker", "exec", name, "cat", transplant.LAUNCHER])
        if launcher + "\n" != transplant.LAUNCHER_SOURCE:
            raise ValueError("private launcher source differs")
        abi = execute(["docker", "exec", "-w", "/tmp", name, transplant.LAUNCHER, "-c", transplant.PROBE], 45)
        if abi.returncode or not transplant.validate_probe(json.loads(abi.stdout)):
            raise ValueError("fresh target worker private ABI differs")
        row["abi_stdout_sha256"] = oracle.sha(abi.stdout)
        git = ["docker", "exec", name, "git", "-C", "/testbed"]
        original = {"head": checked([*git, "rev-parse", "HEAD"]), "diff": checked([*git, "diff", "--binary", "HEAD"]),
                    "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}
        if original != admitted["after"]:
            raise ValueError("fresh target image source differs from admission")
        checked([*git, "reset", "--hard", runtime.COMMIT])
        checked([*git, "clean", "-fd"])
        if checked([*git, "rev-parse", "HEAD"]) != runtime.COMMIT:
            raise ValueError("target base mismatch")
        for kind, content in (("test", task["test_patch"]), (label, patch)):
            if not content:
                continue
            file = output / (label + "-" + kind + ".patch")
            file.write_bytes(content.encode())
            inside = "/tmp/p07-" + kind + ".patch"
            checked(["docker", "cp", str(file), name + ":" + inside])
            checked([*git, "apply", "--check", inside])
            checked([*git, "apply", inside])
        before = checked([*git, "diff", "--binary", "HEAD"])
        bootstrap = bootstrap_source(admitted["installed_files"])
        process = execute(["docker", "exec", "-i", "-w", "/testbed", name, transplant.LAUNCHER, "-c", runtime.STDIN_BOOTSTRAP],
                          300, input_bytes=bootstrap.encode())
        (output / (label + ".stdout")).write_bytes(process.stdout)
        (output / (label + ".stderr")).write_bytes(process.stderr)
        row.update(process_exit=process.returncode, stdout_sha256=oracle.sha(process.stdout), stderr_sha256=oracle.sha(process.stderr),
                   bootstrap_sha256=oracle.sha(bootstrap.encode()))
        if len(process.stdout) + len(process.stderr) > LIMIT or before != checked([*git, "diff", "--binary", "HEAD"]):
            raise ValueError("audit output bound exceeded or tracked source changed")
        inventory = runtime.run_inventory(name, runtime.inventory_source(admitted["installed_files"]), execute)
        if inventory.returncode or json.loads(inventory.stdout).get("files") != admitted["installed_files"]:
            raise ValueError("tests changed admitted dependency or certificate bytes")
        row["post_inventory_stdout_sha256"] = oracle.sha(inventory.stdout)
        row.update(classify_target(task, process.stdout.decode(errors="replace"), process.returncode, parser, spec))
        counters = checked(topology.namespace_command(namespace, "iptables", "-w", "5", "-L", "OUTPUT", "-v", "-n", "-x"))
        row["dropped_tcp_packets"] = topology.dropped_tcp_packets(counters)
        row["firewall_counters"] = counters
        if row["dropped_tcp_packets"] < 1 or topology.process_identity(pid) != identity or Path("/proc/self/ns/net").stat().st_ino != host_inode:
            raise ValueError("target namespace or DROP counters unconfirmed")
        if execute(topology.namespace_command(namespace, "ip", "route", "get", "1.1.1.1")).returncode == 0:
            raise ValueError("external route appeared after target tests")
        checked(topology.namespace_command(namespace, "iptables", "-w", "5", "-C", "OUTPUT", *topology.RULE))
        inspect_policy(json.loads(checked(["docker", "inspect", name]))[0], admitted["image_id"])
        row.update(namespace_identity=identity, host_netns_inode=host_inode, external_route_exit=external.returncode,
                   base_verified=runtime.COMMIT, source_preserved=True)
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError, subprocess.SubprocessError) as exc:
        row.update(status="infrastructure_error", verified_success=False, error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        try:
            removal = execute(["docker", "rm", "-f", name], 30)
            absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], 15)
            row.update(cleanup_exit=removal.returncode, cleanup_confirmed=absent.returncode == 0 and not absent.stdout.strip())
        except (OSError, subprocess.SubprocessError) as exc:
            row.update(cleanup_confirmed=False, cleanup_error_type=type(exc).__name__)
        if fd is not None:
            os.close(fd)
        if not row["cleanup_confirmed"]:
            row.update(status="infrastructure_error", verified_success=False, cleanup_uncertain=True)
        save(output / (label + ".json"), row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-receipt", type=Path, required=True)
    parser.add_argument("--health-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or os.geteuid() != 0 or args.output.exists():
        raise ValueError("fresh output and approved root-owned isolated Linux worker required")
    args.output.mkdir(parents=True)
    report = {"instance_id": TASK, "status": "infrastructure_error", "calibrated": False, "model_calls": 0,
              "provider_credentials": "none", "candidate_scope": "not_supported", "harness": oracle.HARNESS_VERSION}
    try:
        if importlib.metadata.version("swebench") != oracle.HARNESS_VERSION:
            raise ValueError("unreviewed independent evaluator")
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.test_spec import make_test_spec
        manifest = ROOT / "benchmarks/stage4/corpus-p07-v2.json"
        controls = ROOT / "benchmarks/stage4/p07-controls-v6.json"
        if oracle.sha(manifest.read_bytes().replace(b"\r\n", b"\n")) != "ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd":
            raise ValueError("frozen corpus bytes differ")
        task = next(item for item in load_manifest(manifest)["tasks"] if item["instance_id"] == TASK)
        wrong = admit_task(task, json.loads(controls.read_bytes())["tasks"][TASK])
        admitted, receipts = admit_health(args.certificate_receipt, args.health_receipt)
        image = json.loads(checked(["docker", "image", "inspect", admitted["image_id"]]))[0]
        parent = json.loads(checked(["docker", "image", "inspect", admitted["parent_image_id"]]))[0]
        health.admit_image(image, parent, admitted)
        if MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]]["test_cmd"] != "pytest -rA":
            raise ValueError("official native full-suite command changed")
        spec = make_test_spec(task, namespace="swebench")
        report.update(**receipts, image_id=admitted["image_id"], base_commit=runtime.COMMIT,
                      manifest_sha256=oracle.sha(manifest.read_bytes()), controls_sha256=oracle.sha(controls.read_bytes()),
                      required_tests=len(task["FAIL_TO_PASS"]) + len(task["PASS_TO_PASS"]),
                      native_command="pytest -rA", reporting_only_flags=["-v"], argv=[*health.PLUGIN_ARGS, "-rA", "-v"])
        (args.output / "bootstrap.py").write_bytes(bootstrap_source(admitted["installed_files"]).encode())
        rows = [grade(task, spec, MAP_REPO_TO_PARSER[task["repo"]], admitted, patch, label, args.output)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(rows=rows, status="completed", calibrated=oracle.calibration_decision(rows))
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    save(args.output / "calibration.json", report)
    print(json.dumps({key: report[key] for key in ("instance_id", "status", "calibrated", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
