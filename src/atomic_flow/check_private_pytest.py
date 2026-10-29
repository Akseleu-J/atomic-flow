"""Candidate private Requests/pytest bootstrap; no fixture lifecycle admission.

This pure builder never runs target code. Historical Requests checks still need
separate qualified HTTP service, CA overlay, image and source admission. A target
interpreter's hooks are observations, not independent attestation or an oracle.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from atomic_flow.check_fixture import PrivatePytestIdentity
from atomic_flow.check_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from atomic_flow.agents.execution import CommandResult
    from atomic_flow.check_spec import CheckSpec

MARKER = "ATOMIC_CHECK_SPEC_RECEIPT:"

_BOOTSTRAP = r'''
from __future__ import print_function
import hashlib,json,os,sys
payload=json.loads(PAYLOAD_JSON)
runtime=payload['runtime']
identity=payload['identity']
root=payload['root']
paths=payload['paths']
site=identity['site_packages']
if (os.path.realpath(os.getcwd())!=root or os.path.realpath(root)!=root
        or sys.executable!=runtime['interpreter'] or sys.prefix!=runtime['private_prefix']
        or list(sys.version_info[:3])!=runtime['python_version'] or not sys.flags.dont_write_bytecode):
    raise SystemExit(125)
if sys.version_info[0]==2:
    expected=[runtime['private_prefix']+'/lib/python27.zip',runtime['private_prefix']+'/lib/python2.7',
              runtime['private_prefix']+'/lib/python2.7/lib-dynload',site.rstrip('/')]
    if not (sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.no_site) or sys.path!=expected:
        raise SystemExit(125)
else:
    if (not sys.flags.isolated or os.environ.get('PYTHONHOME')!=runtime['private_prefix']
            or any(value and not value.startswith(runtime['private_prefix']+'/') for value in sys.path)):
        raise SystemExit(125)
def checked_file(absolute,expected,maximum):
    if os.path.realpath(absolute)!=absolute or not os.path.isfile(absolute):
        raise SystemExit(125)
    with open(absolute,'rb') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum or hashlib.sha256(raw).hexdigest()!=expected:
        raise SystemExit(125)
    return len(raw)
def protected():
    checked_file(runtime['launcher'],runtime['launcher_sha256'],65536)
    total=0
    for relative,digest in identity['installed_files'].items():
        total+=checked_file(site+relative,digest,4*1024*1024)
        if total>128*1024*1024:raise SystemExit(125)
protected()
entries=0
for directory,dirs,files in os.walk(root,followlinks=False):
    for name in dirs+files:
        entries+=1
        if entries>100000 or os.path.islink(os.path.join(directory,name)) or name.endswith(('.pyc','.pyo')):
            raise SystemExit(125)
import pytest,_pytest,pkg_resources
pytest_module=os.path.realpath(pytest.__file__)
internal_module=os.path.realpath(_pytest.__file__)
if (pytest_module!=site+'pytest.py' or internal_module!=site+'_pytest/__init__.py'
        or pytest.__version__!=identity['pytest_version']):
    raise SystemExit(125)
entrypoints=sorted((entry.name,entry.module_name) for entry in pkg_resources.iter_entry_points('pytest11'))
if entrypoints!=sorted(identity['entrypoints'].items()):
    raise SystemExit(125)
for name in ('PYTEST_ADDOPTS','PYTEST_PLUGINS','PYTEST_DISABLE_PLUGIN_AUTOLOAD'):
    os.environ.pop(name,None)
# Import the two reviewed plugin modules before exposing edited target source.
# Their dependency entry points and source bytes were checked above.
for name in sorted(identity['entrypoints'].values()):
    # Python 2's C import API rejects unicode names produced by json.loads.
    plugin=__import__(str(name),fromlist=['*'])
    path=os.path.realpath(plugin.__file__)
    if not path.startswith(site) or os.path.relpath(path,site).replace(os.sep,'/') not in identity['installed_files']:
        raise SystemExit(125)
sys.path.insert(0,root)
import requests
requests_module=os.path.realpath(requests.__file__)
if requests_module!=os.path.join(root,'requests','__init__.py'):
    raise SystemExit(125)
class Evidence:
    def __init__(self):
        self.collected=0;self.files=set();self.completed=set();self.passed=0
        self.skipped=0;self.failed=0;self.call_failed=0;self.setup_failed=0;self.teardown_failed=0
        self.collection_errors=0;self.collection_complete=False;self.finished=False;self.xpassed=0
    def pytest_collection_finish(self,session):
        self.collection_complete=True
        self.collected=len(session.items)
        for item in session.items:
            filename=str(getattr(item,'path',None) or item.fspath)
            self.files.add(os.path.relpath(os.path.realpath(filename),root).replace(os.sep,'/'))
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors+=1
        if report.skipped:self.skipped+=1
    def pytest_runtest_logreport(self,report):
        if report.when=='teardown':self.completed.add(report.nodeid)
        if report.skipped:self.skipped+=1
        if report.when=='call' and report.passed:
            self.passed+=1
            if hasattr(report,'wasxfail'):self.xpassed+=1
        if report.failed:
            self.failed+=1
            if report.when=='call':self.call_failed+=1
            elif report.when=='setup':self.setup_failed+=1
            elif report.when=='teardown':self.teardown_failed+=1
    def pytest_sessionfinish(self,session,exitstatus):self.finished=True
evidence=Evidence()
# All admitted plugin arguments and test paths are ASCII host-owned strings.
# pytest 2.8 on Python 2 requires native str instead of JSON unicode.
arguments=[str(value) for value in identity['plugin_args']+paths]
code=int(pytest.main(arguments,plugins=[evidence]))
protected()
receipt={'nonce':payload['nonce'],'root':root,'runner':'pytest-private','interpreter':sys.executable,
         'interpreter_resolved':os.path.realpath(sys.executable),'python_version':list(sys.version_info[:3]),
         'pytest_module':pytest_module,'_pytest_module':internal_module,'pytest_version':pytest.__version__,
         'requests_module':requests_module,'runtime_digest':payload['runtime_digest'],
         'check_spec_digest':payload['check_spec_digest'],'source_revision':payload['source_revision'],
         'private_pytest_identity_digest':payload['identity_digest'],'launcher_sha256':runtime['launcher_sha256'],
         'installed_inventory_sha256':payload['installed_inventory_sha256'],'argv':arguments,
         'collected':evidence.collected,'completed':len(evidence.completed),'files':sorted(evidence.files),
         'collection_complete':evidence.collection_complete,'collection_errors':evidence.collection_errors,
         'finished':evidence.finished,'passed':evidence.passed,'failed':evidence.failed,'skipped':evidence.skipped,
         'call_failed':evidence.call_failed,'setup_failed':evidence.setup_failed,'teardown_failed':evidence.teardown_failed,
         'xpassed':evidence.xpassed,'exit_code':code,
         'fixture_lifecycle':'requires_separate_host_admission'}
print('\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True))
sys.stdout.flush()
complete=(evidence.finished and evidence.collection_complete and evidence.collected>0
          and len(evidence.completed)==evidence.collected and evidence.files==set(paths)
          and evidence.passed+evidence.call_failed==evidence.collected
          and not evidence.collection_errors and not evidence.skipped and not evidence.xpassed
          and not evidence.setup_failed and not evidence.teardown_failed)
raise SystemExit(code if code else 0 if complete and not evidence.failed else 5)
'''


def _binding(spec: CheckSpec, identity: PrivatePytestIdentity, nonce: str) -> dict:
    from atomic_flow.agents.step_evidence import manifest_digest
    from atomic_flow.check_spec import CheckSpec
    if (type(spec) is not CheckSpec or type(identity) is not PrivatePytestIdentity
            or type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None):
        raise ValueError("immutable private pytest spec, identity and fresh nonce required")
    runtime = spec.runtime_identity
    if type(runtime) is not RuntimeIdentity:
        raise ValueError("private pytest requires an eligible runtime identity")
    runtime.require_execution_eligible()
    if (spec.runner != "pytest" or runtime.runner != "pytest" or spec.task_id != identity.task_id
            or runtime.digest != identity.runtime_digest or runtime.python_version != identity.python_version
            or (spec.private_pytest_identity is not None and spec.private_pytest_identity != identity)
            or spec.image != runtime.image_id or spec.interpreter != runtime.interpreter):
        raise ValueError("private pytest task/runtime/version binding differs")
    if not {node.split("::", 1)[0] for node, _ in identity.optional_test_outcomes} <= set(spec.test_paths):
        raise ValueError("declared optional outcomes require their complete ordinary test files")
    files = dict(identity.installed_files)
    if (not {"pytest_httpbin/__init__.py", "pytest_httpbin/plugin.py"} <= set(files)
            or not ({"pytest_mock.py", "pytest_mock/__init__.py"} & set(files))):
        raise ValueError("explicit plugin source inventory is incomplete")
    return {"runtime": runtime.as_dict(), "runtime_digest": runtime.digest,
            "identity": identity.as_dict(), "identity_digest": identity.digest,
            "installed_inventory_sha256": manifest_digest(files), "check_spec_digest": spec.digest,
            "source_revision": spec.source_revision, "root": spec.target_root,
            "paths": list(spec.test_paths), "nonce": nonce}


def private_pytest_args(spec: CheckSpec, identity: PrivatePytestIdentity, nonce: str) -> list[str]:
    """Pure candidate builder; HTTP fixture/CA lifecycle remains unqualified."""
    payload = _binding(spec, identity, nonce)
    source = _BOOTSTRAP
    if identity.optional_test_outcomes:
        from atomic_flow.check_private_outcomes import outcome_source
        source = outcome_source(source)
    return ["-c", source.replace("PAYLOAD_JSON", repr(json.dumps(payload, sort_keys=True, ensure_ascii=True)))]


def private_pytest_receipt(result: CommandResult, spec: CheckSpec, identity: PrivatePytestIdentity, nonce: str) -> dict | None:
    """Validate completed ordinary observations; never grants fixture admission."""
    from atomic_flow.agents.execution import CommandResult
    payload = _binding(spec, identity, nonce)
    if (type(result) is not CommandResult or type(result.returncode) is not int or result.returncode not in {0, 1}
            or result.timed_out or result.stdout_truncated or result.stderr_truncated
            or type(result.stdout) is not str or type(result.stderr) is not str):
        return None
    lines = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        return None
    try:
        value = json.loads(lines[0])
    except (TypeError, ValueError):
        return None
    site = identity.site_packages
    expected = {"nonce": nonce, "root": spec.target_root, "runner": "pytest-private", "interpreter": spec.interpreter,
                "python_version": list(identity.python_version), "pytest_version": identity.pytest_version,
                "pytest_module": site + "pytest.py", "_pytest_module": site + "_pytest/__init__.py",
                "requests_module": spec.target_root + "/requests/__init__.py", "runtime_digest": payload["runtime_digest"],
                "check_spec_digest": spec.digest, "source_revision": spec.source_revision,
                "private_pytest_identity_digest": identity.digest, "launcher_sha256": spec.runtime_identity.launcher_sha256,
                "installed_inventory_sha256": payload["installed_inventory_sha256"],
                "argv": [*identity.plugin_args, *spec.test_paths], "files": sorted(spec.test_paths),
                "collection_complete": True, "finished": True, "fixture_lifecycle": "requires_separate_host_admission"}
    if type(value) is not dict or any(type(value.get(key)) is not type(item) or value[key] != item for key, item in expected.items()):
        return None
    counters = ("collected", "completed", "collection_errors", "passed", "failed", "skipped", "call_failed", "setup_failed", "teardown_failed", "xpassed", "exit_code")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in counters):
        return None
    optional = len(identity.optional_test_outcomes)
    if optional:
        from atomic_flow.check_private_outcomes import optional_receipt_valid
        if not optional_receipt_valid(value, identity.optional_test_outcomes, variant=identity.variant):
            return None
    if (value["collected"] <= 0 or value["completed"] != value["collected"]
            or any(value[key] for key in ("collection_errors", "skipped", "setup_failed", "teardown_failed", "xpassed"))
            or value["passed"] + value["call_failed"] + optional != value["collected"]
            or value["failed"] != value["call_failed"] or value["exit_code"] != result.returncode
            or bool(value["call_failed"]) != (result.returncode == 1)):
        return None
    return value
