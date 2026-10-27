"""Four fixed Requests full-suite oracles on their own admitted legacy images.

Only host-owned base/gold/wrong controls are supported. Services are real
HTTPBin instances in the same network-disabled worker as each target suite.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import uuid
from pathlib import Path

import evaluation_p07_isolated as oracle
import p07_requests863_python27_health as service
import p07_requests863_python27_image as runtime
import p07_requests863_python27_support as legacy
import p07_requests863_python27_support as transplant
import p07_requests3362_calibrate as common
import p07_timeout_topology_probe as topology
from evaluation_stage4 import load_manifest, patch_paths, protected_test_path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 16 * 1024**2
PROFILE = ROOT / "benchmarks/stage4/p07-requests863-python27-service-profile-v2.json"
MANIFEST = ROOT / "benchmarks/stage4/corpus-p07-v3.json"
CONTROLS = ROOT / "benchmarks/stage4/p07-controls-v7.json"
MANIFEST_SHA256 = "1f9c11ac35ebf9082f38672f5ce92f64c0692f58949a7695c8196cfefb7de920"
PROFILE_SHA256 = runtime.PROFILE_SHA


PATH_COMPAT = "import os\nclass PathCompat(object):\n    def __init__(self,path):self.path=path\n    def __str__(self):return self.path\n    def __div__(self,other):return PathCompat(os.path.join(self.path,other))\n    def __truediv__(self,other):return self.__div__(other)\n    def is_symlink(self):return os.path.islink(self.path)\n    def resolve(self):return os.path.realpath(self.path)\n    def read_bytes(self):\n        with open(self.path,'rb') as stream:return stream.read()\n    def write_bytes(self,data):\n        with open(self.path,'wb') as stream:return stream.write(data)\n"
TIMEOUT_PROBE = "\nimport json,socket,threading,time,sys\nfrom contextlib import closing\nserver=socket.socket()\nserver.bind(('127.0.0.1',0))\nserver.listen(1)\ndef serve():\n    connection,_=server.accept()\n    with closing(connection):\n        connection.sendall(b'p07-real-loopback')\nthread=threading.Thread(target=serve)\nthread.daemon=True\nthread.start()\nwith closing(socket.create_connection(server.getsockname(),timeout=1)) as connection:\n    received=connection.recv(64)\nthread.join(2)\nserver.close()\nif received!=b'p07-real-loopback' or thread.is_alive():\n    raise RuntimeError('real loopback health failed')\nstarted=time.time()\ntry:\n    connection=socket.create_connection(('10.255.255.1',80),timeout=0.1)\nexcept socket.timeout:\n    elapsed=time.time()-started\nelse:\n    connection.close()\n    raise RuntimeError('blackhole unexpectedly connected')\nif not 0.08<=elapsed<=2:\n    raise RuntimeError('timeout timing outside bounded synthetic window')\nprint(json.dumps({'loopback':'real_socket_pass','blackhole':'socket.timeout',\n    'destination':['10.255.255.1',80],'requested_timeout':0.1,'elapsed':elapsed,\n    'ignore_environment':sys.flags.ignore_environment,'executable':sys.executable},sort_keys=True))\n"

def native_bootstrap_source(installed_files):
    payload = {"files": installed_files, "plugins": ["-p", "no:httpbin", "-p", "pytest_httpbin.plugin", "-p", "pytest_mock"], "entrypoints": {"httpbin": "pytest_httpbin.plugin", "pytest_mock": "pytest_mock"}}
    return "from __future__ import print_function\nimport hashlib,json,os,sys\nPATH_COMPAT\npayload=json.loads(DATA)\nprefix='/opt/p07-python27/lib/python2.7/site-packages/'\nif list(sys.version_info[:3])!=[2,7,13] or sys.prefix!='/opt/p07-python27' or not (sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.no_site) or os.getcwd()!='/testbed':\n    raise RuntimeError('private target interpreter identity mismatch')\nfor relative,expected in payload['files'].items():\n    path=PathCompat(prefix)/relative\n    if path.is_symlink() or not str(path.resolve()).startswith(prefix) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:\n        raise RuntimeError('installed wheel or certificate overlay changed')\nfor key in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD'):\n    os.environ.pop(key,None)\nimport pkg_resources\nentries=list(pkg_resources.iter_entry_points('pytest11'))\nif len(entries)!=2 or {ep.name:ep.module_name for ep in entries}!=payload['entrypoints']:\n    raise RuntimeError('pytest entrypoint allowlist mismatch')\nif any(str(PathCompat(ep.dist.location).resolve())!=prefix.rstrip('/') for ep in entries):\n    raise RuntimeError('entrypoint outside private prefix')\nimport pytest\nif pytest.__version__!='2.8.7' or not str(PathCompat(pytest.__file__).resolve()).startswith(prefix):\n    raise RuntimeError('pytest import identity mismatch')\nsys.path.insert(0,'/testbed')\nimport requests\nif str(PathCompat(requests.__file__).resolve())!='/testbed/requests/__init__.py':\n    raise RuntimeError('target Requests source import mismatch')\nclass Recorder:\n    def __init__(self):\n        self.collected=[]; self.reports=[]; self.finished=False; self.exit=None; self.plugins=[]\n    def pytest_configure(self,config):\n        self.config=config\n        self.plugins=[[name,getattr(plugin,'__name__',plugin.__class__.__module__)] for name,plugin in config.pluginmanager.list_name_plugin() if plugin is not None]\n    def pytest_collection_finish(self,session):\n        self.collected=[item.nodeid for item in session.items]\n    def pytest_runtest_logreport(self,report):\n        self.reports.append({'id':report.nodeid,'when':report.when,'outcome':report.outcome,\n            'has_wasxfail':hasattr(report,'wasxfail'),'wasxfail':getattr(report,'wasxfail',None),\n            'native_category':self.config.hook.pytest_report_teststatus(report=report)[0]})\n    def pytest_sessionfinish(self,session,exitstatus):\n        self.finished=True; self.exit=exitstatus\nrecorder=Recorder()\nargv=[str(value) for value in payload['plugins']]+['-rA','-v']\nprint('>>>>> Start Test Output')\nexit_code=pytest.main(argv,plugins=[recorder])\nprint('P07_TARGET_RESULT='+json.dumps({'version_info':list(sys.version_info[:3]),'prefix':sys.prefix,'executable':sys.executable,\n    'pytest_module':pytest.__file__,'pytest_version':pytest.__version__,'requests_module':requests.__file__,\n    'argv':argv,'plugins':recorder.plugins,'collection':recorder.collected,'reports':recorder.reports,'finished':recorder.finished,'exit':recorder.exit},sort_keys=True))\nprint('P07_TEST_EXIT='+str(exit_code))\nprint('>>>>> End Test Output')\nraise SystemExit(exit_code)\n".replace("DATA", repr(json.dumps(payload, sort_keys=True))).replace("PATH_COMPAT", PATH_COMPAT)


def save(path, value):
    path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def task_record(task_id):
    if task_id not in service.TASK_HOSTS:
        raise ValueError("one of four admitted function task identities required")
    raw = PROFILE.read_bytes().replace(b"\r\n", b"\n")
    if oracle.sha(raw) != PROFILE_SHA256:
        raise ValueError("function service profile differs from reviewed bytes")
    records = [row for row in json.loads(raw)["tasks"] if row["instance_id"] == task_id]
    if len(records) != 1:
        raise ValueError("one exact task profile required")
    return records[0]


def admit_task(task, control):
    record = task_record(task.get("instance_id"))
    required = record["oracle_obligations"]
    if (task.get("repo") != "psf/requests" or task.get("base_commit") != record["base_commit"]
            or task.get("FAIL_TO_PASS") != required["FAIL_TO_PASS"]
            or task.get("PASS_TO_PASS") != required["PASS_TO_PASS"]
            or oracle.sha(task.get("patch", "").encode()) != required["gold_patch_sha256"]
            or oracle.sha(task.get("test_patch", "").encode()) != required["protected_test_patch_sha256"]):
        raise ValueError("target source, patches or complete requirements differ")
    old, new = control.get("old"), control.get("new")
    if (control.get("gold_sha256") != required["gold_patch_sha256"]
            or not isinstance(old, str) or not old or not isinstance(new, str) or old == new
            or task["patch"].count(old) != 1 or not control.get("rationale")):
        raise ValueError("exact reviewed negative control required")
    wrong = task["patch"].replace(old, new)
    if control.get("wrong_sha256") is not None and oracle.sha(wrong.encode()) != control["wrong_sha256"]:
        raise ValueError("negative control digest differs")
    return wrong


def transport_arguments(task_id):
    task_record(task_id)
    return ["--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
            *[part for host, address in sorted(service.TASK_HOSTS[task_id].items())
              for part in ("--add-host", host + ":" + address)]]


def inspect_policy(config, image_id, task_id):
    common.inspect_policy(config, image_id)
    host = config["HostConfig"]
    if (host.get("Sysctls") != {"net.ipv4.ip_unprivileged_port_start": "0"}
            or set(host.get("ExtraHosts", [])) != {name + ":" + address for name, address in service.TASK_HOSTS[task_id].items()}):
        raise ValueError("fixed function transport policy differs")


def certificate_inventory_source(files):
    if set(files) != set(service.CERTIFICATE_PATHS):
        raise ValueError("only the three admitted function certificate files allowed")
    return '''import hashlib,json
PATH_COMPAT
expected=json.loads(EXPECTED)
for absolute,digest in expected.items():
    path=PathCompat(absolute)
    if path.is_symlink() or str(path.resolve())!=absolute or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise RuntimeError('function certificate identity changed')
print(json.dumps(expected,sort_keys=True))
'''.replace("EXPECTED", repr(json.dumps(files, sort_keys=True))).replace("PATH_COMPAT", PATH_COMPAT)


def server_source(certificate_files):
    return service.wsgi.server_source(certificate_files)


SERVER_STOP = service.wsgi.STOP


def bootstrap_source(task_id, installed_files, certificate_files):
    service.bound_inputs(task_id, installed_files, certificate_files)
    code = native_bootstrap_source(installed_files)
    start = "sys.path.insert(0,'/testbed')\n"
    finish = "exit_code=pytest.main(argv,plugins=[recorder])\n"
    if code.count(start) != 1 or code.count(finish) != 1:
        raise ValueError("shared native runner template changed")
    # This composes the trusted host template, without replacing any target
    # function, module, assertion, test selection or model-controlled command.
    shutdown = "".join("    " + line + "\n" for line in SERVER_STOP.splitlines())
    guarded = "try:\n    " + finish + "finally:\n" + shutdown
    return code.replace(start, server_source(certificate_files) + start, 1).replace(finish, guarded, 1)


def classify_target(task, text, exit_code, parser, spec):
    task_record(task["instance_id"])
    if text.splitlines().count("P07_FUNCTION_SERVERS_STOPPED=1") != 1:
        raise ValueError("same-worker function server shutdown unconfirmed")
    return common.classify_target(task, text, exit_code, parser, spec, runtime_profile="python2713")


def execute(args, timeout=60, *, input_bytes=None):
    clean = {key: value for key, value in os.environ.items()
             if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
    return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=clean)


def checked(args, timeout=60):
    process = execute(args, timeout)
    if process.returncode:
        raise RuntimeError("fixed function worker command failed: " + args[1])
    return process.stdout.decode().strip()


def qualify_services(name, task_id, admitted, output, label):
    code = service.bootstrap_source(task_id, admitted["installed_files"], admitted["certificate_files"], admitted["wrong_ca"])
    result = execute(["docker", "exec", "-i", "-w", "/tmp", name, transplant.LAUNCHER, "-c", legacy.STDIN_BOOTSTRAP],
                     60, input_bytes=code.encode())
    (output / (label + "-health.stdout")).write_bytes(result.stdout)
    (output / (label + "-health.stderr")).write_bytes(result.stderr)
    if result.returncode or len(result.stdout) + len(result.stderr) > LIMIT:
        raise ValueError("same-worker function service health failed")
    observed = service.validate_health(result.stdout.decode(), task_id, admitted["installed_files"], admitted["certificate_files"])
    return {"observed": observed, "stdout_sha256": oracle.sha(result.stdout), "stderr_sha256": oracle.sha(result.stderr),
            "bootstrap_sha256": oracle.sha(code.encode())}


def admit_image(image, base, admitted):
    recorded = admitted["image_inspect"]
    expected_base = admitted["base_image_inspect"]
    layers = base.get("RootFS", {}).get("Layers", [])
    actual_layers = image.get("RootFS", {}).get("Layers", [])
    if (image.get("Id") != admitted["image_id"] or image.get("Os") != "linux" or image.get("Architecture") != "amd64"
            or image.get("RootFS") != recorded.get("RootFS") or image.get("Config", {}).get("Labels") != recorded.get("Config", {}).get("Labels")
            or base.get("Id") != expected_base.get("Id") or base.get("RootFS") != expected_base.get("RootFS")
            or not layers or actual_layers[:len(layers)] != layers or len(actual_layers) <= len(layers)):
        raise ValueError("actual own-base image ancestry differs from admission")


def default_ca_overlay_source(original_sha256, fixture_sha256):
    import re

    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (original_sha256, fixture_sha256)):
        raise ValueError("exact original and fixture CA hashes required")
    data = {"original": original_sha256, "fixture": fixture_sha256}
    return r'''import hashlib,json
PATH_COMPAT
expected=json.loads(EXPECTED)
path=PathCompat('/testbed/requests/cacert.pem')
fixture=PathCompat('/opt/p07-function-fixture/cacert.pem')
if path.is_symlink() or fixture.is_symlink() or str(path.resolve())!='/testbed/requests/cacert.pem':
    raise RuntimeError('CA overlay path confinement failed')
original=path.read_bytes()
ca=fixture.read_bytes()
if hashlib.sha256(original).hexdigest()!=expected['original'] or hashlib.sha256(ca).hexdigest()!=expected['fixture']:
    raise RuntimeError('CA overlay original or fixture identity mismatch')
if b'-----BEGIN CERTIFICATE-----' not in ca:
    raise RuntimeError('fixture CA is not PEM')
combined=original+b'\n'+ca
path.write_bytes(combined)
if path.read_bytes()!=combined or not combined.startswith(original):
    raise RuntimeError('CA overlay bytes changed')
print(json.dumps({'path':'requests/cacert.pem','operation':'append_admitted_fixture_CA','original_sha256':expected['original'],'fixture_sha256':expected['fixture'],'result_sha256':hashlib.sha256(combined).hexdigest(),'original_bytes_preserved':True,'verification_disabled':False},sort_keys=True))
'''.replace("EXPECTED", repr(json.dumps(data, sort_keys=True))).replace("PATH_COMPAT", PATH_COMPAT)


def grade(task, spec, parser, admitted, patch, label, output):
    name = "p07-requests-function-" + uuid.uuid4().hex[:20]
    row = {"label": label, "status": "infrastructure_error", "verified_success": False, "container_name": name,
           "patch_sha256": oracle.sha(patch.encode())}
    fd = None
    try:
        if patch and (patch_paths(patch) & patch_paths(task["test_patch"]) or any(protected_test_path(p) for p in patch_paths(patch))):
            raise ValueError("production patch crosses protected boundary")
        checked(["docker", "create", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--memory", "6g", "--cpus", "2", "--pids-limit", "256", *transport_arguments(task["instance_id"]), "--entrypoint", "/bin/sleep", admitted["image_id"], "infinity"])
        checked(["docker", "start", name])
        config = json.loads(checked(["docker", "inspect", name]))[0]
        inspect_policy(config, admitted["image_id"], task["instance_id"])
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
        probe = execute(["docker", "exec", "-w", "/tmp", name, transplant.LAUNCHER, "-c", TIMEOUT_PROBE], 15)
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
        row["function_health"] = qualify_services(name, task["instance_id"], admitted, output, label)
        checked([*git, "reset", "--hard", task["base_commit"]])
        checked([*git, "clean", "-fd"])
        if checked([*git, "rev-parse", "HEAD"]) != task["base_commit"]:
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
        original_ca = execute([*git, "show", task["base_commit"] + ":requests/cacert.pem"])
        if original_ca.returncode:
            raise ValueError("original default CA bundle unavailable")
        overlay_code = default_ca_overlay_source(oracle.sha(original_ca.stdout), admitted["certificate_files"]["/opt/p07-function-fixture/cacert.pem"])
        overlay = execute(["docker", "exec", "-i", "-w", "/tmp", name, transplant.LAUNCHER, "-c", legacy.STDIN_BOOTSTRAP], 30,
                          input_bytes=overlay_code.encode())
        (output / (label + "-ca-overlay.stdout")).write_bytes(overlay.stdout)
        (output / (label + "-ca-overlay.stderr")).write_bytes(overlay.stderr)
        if overlay.returncode:
            raise ValueError("explicit default CA trust overlay failed")
        row["default_ca_overlay"] = json.loads(overlay.stdout)
        row["default_ca_overlay_stdout_sha256"] = oracle.sha(overlay.stdout)
        before = checked([*git, "diff", "--binary", "HEAD"])
        bootstrap = bootstrap_source(task["instance_id"], admitted["installed_files"], admitted["certificate_files"])
        process = execute(["docker", "exec", "-i", "-w", "/testbed", name, transplant.LAUNCHER, "-c", legacy.STDIN_BOOTSTRAP],
                          300, input_bytes=bootstrap.encode())
        (output / (label + ".stdout")).write_bytes(process.stdout)
        (output / (label + ".stderr")).write_bytes(process.stderr)
        row.update(process_exit=process.returncode, stdout_sha256=oracle.sha(process.stdout), stderr_sha256=oracle.sha(process.stderr),
                   bootstrap_sha256=oracle.sha(bootstrap.encode()))
        if len(process.stdout) + len(process.stderr) > LIMIT or before != checked([*git, "diff", "--binary", "HEAD"]):
            raise ValueError("audit output bound exceeded or tracked source changed")
        inventory = legacy.run_inventory(name, legacy.inventory_source(admitted["installed_files"]), execute)
        if inventory.returncode or json.loads(inventory.stdout).get("files") != admitted["installed_files"]:
            raise ValueError("tests changed admitted dependency or certificate bytes")
        row["post_inventory_stdout_sha256"] = oracle.sha(inventory.stdout)
        certificate_probe = execute(["docker", "exec", "-i", "-w", "/tmp", name, transplant.LAUNCHER, "-c", legacy.STDIN_BOOTSTRAP], 30,
                                    input_bytes=certificate_inventory_source(admitted["certificate_files"]).encode())
        if certificate_probe.returncode:
            raise ValueError("target tests changed function TLS certificate files")
        row["post_certificate_stdout_sha256"] = oracle.sha(certificate_probe.stdout)
        row.update(classify_target(task, process.stdout.decode(errors="replace"), process.returncode, parser, spec))
        counters = checked(topology.namespace_command(namespace, "iptables", "-w", "5", "-L", "OUTPUT", "-v", "-n", "-x"))
        row["dropped_tcp_packets"] = topology.dropped_tcp_packets(counters)
        row["firewall_counters"] = counters
        if row["dropped_tcp_packets"] < 1 or topology.process_identity(pid) != identity or Path("/proc/self/ns/net").stat().st_ino != host_inode:
            raise ValueError("target namespace or DROP counters unconfirmed")
        if execute(topology.namespace_command(namespace, "ip", "route", "get", "1.1.1.1")).returncode == 0:
            raise ValueError("external route appeared after target tests")
        checked(topology.namespace_command(namespace, "iptables", "-w", "5", "-C", "OUTPUT", *topology.RULE))
        inspect_policy(json.loads(checked(["docker", "inspect", name]))[0], admitted["image_id"], task["instance_id"])
        row.update(namespace_identity=identity, host_netns_inode=host_inode, external_route_exit=external.returncode,
                   base_verified=task["base_commit"], source_preserved=True)
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
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or os.geteuid() != 0 or args.output.exists():
        raise ValueError("fresh output on approved root-owned Linux Docker host required")
    args.output.mkdir(parents=True)
    report = {"status": "infrastructure_error", "calibrated": False, "model_calls": 0,
              "candidate_scope": "not_supported", "provider_credentials": "none", "harness": oracle.HARNESS_VERSION}
    try:
        if importlib.metadata.version("swebench") != oracle.HARNESS_VERSION:
            raise ValueError("unreviewed independent evaluator")
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.test_spec import make_test_spec

        raw, admitted = runtime.admit_receipt(args.runtime_receipt)
        task_id = admitted["task"]
        record = task_record(task_id)
        if admitted["base_commit"] != record["base_commit"] or admitted["base_image"] != record["official_base_image"]:
            raise ValueError("runtime belongs to another task")
        admitted = {**admitted, "wrong_ca": (args.runtime_receipt / "wrongca.pem").read_text()}
        if oracle.sha(MANIFEST.read_bytes().replace(b"\r\n", b"\n")) != MANIFEST_SHA256:
            raise ValueError("frozen corpus bytes differ")
        task = next(row for row in load_manifest(MANIFEST)["tasks"] if row["instance_id"] == task_id)
        control = json.loads(CONTROLS.read_bytes())["tasks"][task_id]
        wrong = admit_task(task, control)
        image = json.loads(checked(["docker", "image", "inspect", admitted["image_id"]]))[0]
        base = json.loads(checked(["docker", "image", "inspect", admitted["base_image"]]))[0]
        admit_image(image, base, admitted)
        if MAP_REPO_VERSION_TO_SPECS[task["repo"]][task["version"]]["test_cmd"] != "pytest -rA":
            raise ValueError("official full-suite native test command differs")
        spec = make_test_spec(task, namespace="swebench")
        report.update(instance_id=task_id, base_commit=task["base_commit"], image_id=admitted["image_id"],
                      runtime_receipt_sha256=oracle.sha(raw), service_profile_sha256=PROFILE_SHA256,
                      dependency_profile_sha256=admitted["dependency_profile_sha256"],
                      manifest_sha256=oracle.sha(MANIFEST.read_bytes()), controls_sha256=oracle.sha(CONTROLS.read_bytes()),
                      native_command="pytest -rA", reporting_only_flags=["-v"],
                      required_tests=len(task["FAIL_TO_PASS"]) + len(task["PASS_TO_PASS"]))
        (args.output / "bootstrap.py").write_bytes(bootstrap_source(task_id, admitted["installed_files"], admitted["certificate_files"]).encode())
        rows = [grade(task, spec, MAP_REPO_TO_PARSER[task["repo"]], admitted, patch, label, args.output)
                for label, patch in (("base", ""), ("gold", task["patch"]), ("wrong", wrong))]
        report.update(rows=rows, status="completed", calibrated=oracle.calibration_decision(rows))
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    save(args.output / "calibration.json", report)
    print(json.dumps({key: report[key] for key in ("status", "calibrated", "model_calls")}))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
