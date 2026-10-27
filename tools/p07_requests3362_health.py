"""Fixed synthetic legacy plugin/HTTP/TLS health; no target imports or grading."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

import p07_legacy_transplant as transplant
import p07_requests3362_legacy_image as runtime

PREFIX = "/opt/p07-python36/lib/python3.6/site-packages/"
TEST_IDS = ("test_legacy_apis", "test_real_http", "test_ca_verified_https")
PLUGIN_ARGS = ("-p", "no:httpbin", "-p", "pytest_httpbin.plugin", "-p", "pytest_mock")
ENTRYPOINTS = {"httpbin": "pytest_httpbin.plugin", "pytest_mock": "pytest_mock"}
RESULT_PREFIX = "P07_HEALTH_RESULT="
LIMIT = 2 * 1024**2
TEST_SOURCE = r'''
import json,os,ssl,urllib.error,urllib.request,warnings
import pytest
from pytest_httpbin import certs

def request(server,context=None):
    handlers=[urllib.request.ProxyHandler({})]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    with urllib.request.build_opener(*handlers).open(server.url+'/get?health=p07',timeout=3) as response:
        if response.status!=200:
            raise RuntimeError('real HTTPBin status mismatch')
        result=json.loads(response.read(65536).decode())
    if result.get('args')!={'health':'p07'}:
        raise RuntimeError('real HTTPBin payload mismatch')

def test_legacy_apis():
    pytest.raises(ValueError,"int('p07-not-an-integer')")
    with pytest.warns(UserWarning):
        warnings.warn('p07 synthetic warning',UserWarning)

def test_real_http(httpbin):
    if httpbin.host!='127.0.0.1' or httpbin.protocol!='http':
        raise RuntimeError('HTTP fixture is not real loopback')
    request(httpbin)

def test_ca_verified_https(httpbin_secure,httpbin_ca_bundle):
    if httpbin_ca_bundle is not None or os.environ.get('REQUESTS_CA_BUNDLE')!=certs.where():
        raise RuntimeError('original CA fixture semantics changed')
    if httpbin_secure.host!='127.0.0.1' or httpbin_secure.protocol!='https':
        raise RuntimeError('HTTPS fixture is not real loopback')
    context=ssl.create_default_context(cafile=certs.where())
    if not context.check_hostname or context.verify_mode!=ssl.CERT_REQUIRED:
        raise RuntimeError('TLS verification disabled')
    request(httpbin_secure,context)
    wrong=ssl.create_default_context(cafile='/tmp/p07-health/wrongca.pem')
    try:
        request(httpbin_secure,wrong)
    except urllib.error.URLError as error:
        if not isinstance(error.reason,(ssl.SSLError,ssl.CertificateError)):
            raise
    else:
        raise RuntimeError('unrelated CA unexpectedly accepted')
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def runtime_httpbin_version(admitted):
    parent = admitted["parent_runtime_report"]
    _, profile = runtime.receipt_profile(parent)
    return next(item["version"] for item in profile["wheels"] if item["name"].lower() == "httpbin")


def bootstrap_source(installed_files, wrong_ca, *, httpbin_version="0.4.1"):
    if (not isinstance(installed_files, dict) or not 1 <= len(installed_files) <= 5000
            or any(not isinstance(k, str) or k.startswith("/") or ".." in k.split("/")
                   or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in installed_files.items())
            or not isinstance(wrong_ca, str) or "BEGIN CERTIFICATE" not in wrong_ca
            or httpbin_version not in {"0.4.1", "0.5.0"}):
        raise ValueError("complete fixed installed inventory and unrelated public CA required")
    data = {"files": installed_files, "wrong_ca": wrong_ca, "test_source": TEST_SOURCE,
            "plugin_args": list(PLUGIN_ARGS), "entrypoints": ENTRYPOINTS, "httpbin_version": httpbin_version}
    return r'''import hashlib,json,os,pathlib,sys
data=json.loads(DATA)
prefix='/opt/p07-python36/lib/python3.6/site-packages/'
if list(sys.version_info[:3])!=[3,6,15] or sys.prefix!='/opt/p07-python36' or not sys.flags.isolated:
    raise RuntimeError('private interpreter identity mismatch')
if os.getcwd()!='/tmp' or any(str(p).startswith('/testbed') for p in sys.path):
    raise RuntimeError('target import path exposed')
for relative,expected in data['files'].items():
    path=pathlib.Path(prefix)/relative
    if path.is_symlink() or not str(path.resolve()).startswith(prefix) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise RuntimeError('installed dependency or certificate bytes mismatch')
for name in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD'):
    os.environ.pop(name,None)
import pkg_resources
entries=list(pkg_resources.iter_entry_points('pytest11'))
entrypoints={ep.name:ep.module_name for ep in entries}
if len(entries)!=2 or entrypoints!=data['entrypoints']:
    raise RuntimeError('unreviewed pytest entrypoint inventory')
for ep in entries:
    if str(pathlib.Path(ep.dist.location).resolve())!=prefix.rstrip('/'):
        raise RuntimeError('entrypoint outside admitted private prefix')
import pytest,httpbin,pytest_httpbin.plugin,pytest_mock
imports={name:sys.modules[name].__file__ for name in ('pytest','httpbin','pytest_httpbin.plugin','pytest_mock')}
if pytest.__version__!='2.8.7' or pkg_resources.get_distribution('httpbin').version!=data['httpbin_version'] or any(not str(pathlib.Path(path).resolve()).startswith(prefix) for path in imports.values()):
    raise RuntimeError('private package import identity mismatch')
directory=pathlib.Path('/tmp/p07-health')
directory.mkdir()
(directory/'pytest.ini').write_text('[pytest]\n')
(directory/'test_health.py').write_text(data['test_source'])
(directory/'wrongca.pem').write_text(data['wrong_ca'])
class Recorder:
    def __init__(self):
        self.reports=[]
        self.collection=[]
        self.plugins=[]
        self.servers=[]
        self.finished=False
        self.exit=None
    def pytest_configure(self,config):
        for name,plugin in config.pluginmanager.list_name_plugin():
            if plugin is None:
                continue
            module=getattr(plugin,'__name__',plugin.__class__.__module__)
            if plugin is self:
                module='host_health_recorder'
            elif not (module.startswith('_pytest.') or module in ('pytest_httpbin.plugin','pytest_mock')):
                raise RuntimeError('unapproved registered plugin: '+module)
            self.plugins.append([name,module])
    def pytest_collection_finish(self,session):
        self.collection=[item.nodeid for item in session.items]
    def pytest_runtest_logreport(self,report):
        self.reports.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome})
    def pytest_runtest_teardown(self,item,nextitem):
        for name in ('httpbin','httpbin_secure'):
            server=item.funcargs.get(name)
            if server is not None and server not in self.servers:
                self.servers.append(server)
    def pytest_sessionfinish(self,session,exitstatus):
        self.finished=True
        self.exit=exitstatus
recorder=Recorder()
argv=data['plugin_args']+['--noconftest','--assert=plain','-c',str(directory/'pytest.ini'),
    '--confcutdir',str(directory),'--basetemp',str(directory/'base'),'-v',str(directory/'test_health.py')]
exit_code=pytest.main(argv,plugins=[recorder])
for server in recorder.servers:
    # Server.join is a URL helper; threading.Thread.join must be called directly.
    import threading
    threading.Thread.join(server,3)
    server._server.server_close()
stopped=len(recorder.servers)==2 and all(not server.is_alive() for server in recorder.servers)
target_modules=[name for name,module in list(sys.modules.items()) if str(getattr(module,'__file__','')).startswith('/testbed/')]
result={'exit_code':exit_code,'session_exit':recorder.exit,'finished':recorder.finished,
    'collection':recorder.collection,'reports':recorder.reports,'plugins':recorder.plugins,
    'entrypoints':entrypoints,'imports':imports,'servers_stopped':stopped,
    'target_imports':target_modules,'requests_imported':'requests' in sys.modules,
    'version_info':list(sys.version_info[:3]),'prefix':sys.prefix,'executable':sys.executable,
    'assertion_mode':'plain_synthetic_only','fixture_tests_sha256':hashlib.sha256(data['test_source'].encode()).hexdigest(),
    'httpbin_version':data['httpbin_version']}
print('P07_HEALTH_RESULT='+json.dumps(result,sort_keys=True))
if exit_code or not stopped or target_modules or 'requests' in sys.modules:
    raise RuntimeError('synthetic health did not complete safely')
'''.replace("DATA", repr(json.dumps(data, sort_keys=True)))


def validate_health(text, *, httpbin_version="0.4.1"):
    if httpbin_version not in {"0.4.1", "0.5.0"}:
        raise ValueError("unreviewed HTTPBin health version")
    lines = [line[len(RESULT_PREFIX):] for line in text.splitlines() if line.startswith(RESULT_PREFIX)]
    if len(lines) != 1:
        raise ValueError("one complete health receipt required")
    result = json.loads(lines[0])
    if (result.get("exit_code") != 0 or result.get("session_exit") != 0 or result.get("finished") is not True
            or result.get("servers_stopped") is not True or result.get("target_imports") != []
            or result.get("requests_imported") is not False or result.get("entrypoints") != ENTRYPOINTS
            or result.get("version_info") != [3, 6, 15] or result.get("prefix") != transplant.PREFIX
            or result.get("executable") != transplant.PREFIX + "/bin/python3.6"
            or result.get("assertion_mode") != "plain_synthetic_only"
            or result.get("fixture_tests_sha256") != sha(TEST_SOURCE.encode())
            or result.get("httpbin_version", "0.4.1") != httpbin_version):
        raise ValueError("health completion, identity or isolation mismatch")
    collection = result.get("collection", [])
    if len(collection) != 3 or {name.split("::")[-1] for name in collection} != set(TEST_IDS):
        raise ValueError("complete synthetic test inventory required")
    reports = result.get("reports", [])
    if (len(reports) != 9 or any(item.get("outcome") != "passed" for item in reports)
            or {(item.get("nodeid"), item.get("when")) for item in reports}
            != {(node, phase) for node in collection for phase in ("setup", "call", "teardown")}):
        raise ValueError("complete unskipped synthetic reports required")
    plugins = result.get("plugins", [])
    for module in ENTRYPOINTS.values():
        if sum(pair[1] == module for pair in plugins) != 1:
            raise ValueError("registered plugins must occur exactly once")
    if any(pair[1] != "host_health_recorder" and not pair[1].startswith("_pytest.")
           and pair[1] not in ENTRYPOINTS.values() for pair in plugins):
        raise ValueError("unexpected registered plugin")
    if set(result.get("imports", {})) != {"pytest", "httpbin", "pytest_httpbin.plugin", "pytest_mock"} or any(
            not path.startswith(PREFIX) for path in result["imports"].values()):
        raise ValueError("private imports must resolve to verified wheels")
    return result


def inspect_policy(config, image_id):
    host = config["HostConfig"]
    if (config.get("Image") != image_id or config.get("Mounts") or host.get("Binds") or host.get("Privileged")
            or host.get("NetworkMode") != "none" or not host.get("ReadonlyRootfs")
            or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or "no-new-privileges" not in host.get("SecurityOpt", [])
            or host.get("Memory") != 1024**3 or host.get("NanoCpus") != 2_000_000_000
            or host.get("PidsLimit") != 128 or config["Config"].get("User") != "65534:65534"):
        raise ValueError("health worker isolation mismatch")


def admit_image(image, parent, admitted):
    layers = parent.get("RootFS", {}).get("Layers", [])
    actual_layers = image.get("RootFS", {}).get("Layers", [])
    labels = image.get("Config", {}).get("Labels", {})
    if (parent.get("Id") != admitted["parent_image_id"]
            or parent.get("RootFS") != admitted["parent_runtime_report"]["image_inspect"]["RootFS"]
            or image.get("Id") != admitted["image_id"] or image.get("Os") != "linux"
            or image.get("Architecture") != "amd64" or not layers
            or actual_layers[:len(layers)] != layers or len(actual_layers) <= len(layers)
            or image.get("RootFS") != admitted["image_inspect"]["RootFS"]
            or labels != admitted["image_inspect"]["Config"].get("Labels")
            or labels.get("atomic.p07.requests.cert.parent") != parent["Id"]
            or labels.get("atomic.p07.requests.cert.overlay") != sha(json.dumps(admitted["overlay_files"], sort_keys=True).encode())):
        raise ValueError("actual certificate image or parent ancestry differs from admission")


def qualify(certificate_receipt, output):
    if platform.system() != "Linux" or output.exists():
        raise ValueError("fresh output on approved Linux Docker host required")
    import p07_requests3362_cert_image as certificate

    raw, admitted = certificate.admit_certificate_receipt(certificate_receipt)
    output.mkdir(parents=True)
    report = {"status": "health_unavailable", "model_calls": 0, "calibrated": False,
              "verified_success": False, "target_setup_or_tests_executed": False,
              "certificate_receipt_sha256": sha(raw), "image_id": admitted["image_id"]}
    name = "p07-requests-health-" + uuid.uuid4().hex[:20]

    def execute(args, timeout=30, *, input_bytes=None):
        clean = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}}
        return subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, check=False, env=clean)

    def checked(args, timeout=30):
        process = execute(args, timeout)
        if process.returncode:
            report["command_error"] = runtime.command_failure(args, process)
            raise RuntimeError("fixed health worker command failed")
        return process.stdout.decode().strip()

    def source():
        git = ["docker", "exec", name, "git", "-c", "safe.directory=/testbed", "-C", "/testbed"]
        return {"head": checked([*git, "rev-parse", "HEAD"]), "diff": checked([*git, "diff", "--binary", "HEAD"]),
                "status": checked([*git, "status", "--porcelain", "--untracked-files=all"])}

    try:
        image = json.loads(checked(["docker", "image", "inspect", admitted["image_id"]]))[0]
        parent = json.loads(checked(["docker", "image", "inspect", admitted["parent_image_id"]]))[0]
        admit_image(image, parent, admitted)
        checked(["docker", "create", "--name", name, "--network", "none", "--read-only", "--user", "65534:65534",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "2",
                 "--pids-limit", "128", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,mode=1777",
                 "--workdir", "/tmp", "--entrypoint", "/bin/sleep", image["Id"], "infinity"])
        inspect_policy(json.loads(checked(["docker", "inspect", name]))[0], image["Id"])
        checked(["docker", "start", name])
        report["before"] = source()
        if report["before"] != admitted["after"]:
            raise ValueError("health target source differs from admitted source")
        httpbin_version = runtime_httpbin_version(admitted)
        code = bootstrap_source(admitted["installed_files"], (certificate_receipt / "wrongca.pem").read_text(),
                                httpbin_version=httpbin_version)
        (output / "health.py").write_bytes(code.encode())
        process = execute(["docker", "exec", "-i", name, transplant.LAUNCHER, "-c", runtime.STDIN_BOOTSTRAP],
                          120, input_bytes=code.encode())
        (output / "health.stdout").write_bytes(process.stdout)
        (output / "health.stderr").write_bytes(process.stderr)
        report.update(process_exit=process.returncode, stdout_sha256=sha(process.stdout), stderr_sha256=sha(process.stderr),
                      bootstrap_sha256=sha(code.encode()), synthetic_assertion_mode="plain")
        if process.returncode or len(process.stdout) + len(process.stderr) > LIMIT:
            raise ValueError("health process failed or exceeded audit output bound")
        report["observed"] = validate_health(process.stdout.decode(errors="replace"), httpbin_version=httpbin_version)
        report["httpbin_version"] = httpbin_version
        report["after"] = source()
        if report["before"] != report["after"]:
            raise ValueError("synthetic health changed target source")
        report["status"] = "synthetic_legacy_health_qualified"
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:500])
    finally:
        try:
            process = execute(["docker", "rm", "-f", name])
            absent = execute(["docker", "container", "ls", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"])
            report.update(cleanup_exit=process.returncode, cleanup_confirmed=absent.returncode == 0 and not absent.stdout.strip())
        except (OSError, subprocess.SubprocessError) as exc:
            report.update(cleanup_confirmed=False, cleanup_error_type=type(exc).__name__)
        if not report["cleanup_confirmed"]:
            report["status"] = "cleanup_uncertain"
        (output / "health.json").write_bytes((json.dumps(report, sort_keys=True, indent=2) + "\n").encode())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    result = qualify(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "synthetic_legacy_health_qualified" else 1)
