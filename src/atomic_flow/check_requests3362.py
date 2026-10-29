"""Pure Requests3362 plugin-owned HTTP/TLS lifecycle composition.

No process, target import or service starts on the host. The generated private
Python3.6 source records actual pytest_httpbin fixtures used by ordinary tests.
It verifies public certificate bytes and CN/no-SAN policy, and admits no CA
overlay. Health correctness remains the ordinary synthetic fixture test's job;
observing/stopping servers alone is not TLS health or independent oracle proof.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from atomic_flow.check_fixture import FixtureIdentity, PrivatePytestIdentity
from atomic_flow.check_private_pytest import private_pytest_args, private_pytest_receipt
from atomic_flow.check_runtime import RuntimeIdentity

if TYPE_CHECKING:
    from atomic_flow.agents.execution import CommandResult
    from atomic_flow.check_spec import CheckSpec

LIFECYCLE_POLICY = "requests3362-plugin-owned-lifecycle/1"
_SETUP = r'''
import ssl,threading
fixture=json.loads(FIXTURE_JSON)
fixture_servers=[]
fixture_ca_seen=False
fixture_cleanup_ok=False
certificate_hashes={}
for absolute,expected in fixture['certificate_files'].items():
    checked_file(absolute,expected,16384)
    with open(absolute,'rb') as stream:
        certificate_hashes[absolute]=hashlib.sha256(stream.read(16385)).hexdigest()
server_certificate=fixture['certificate_root']+'cert.pem'
decoded=ssl._ssl._test_decode_cert(server_certificate)
common_names=[value for group in decoded.get('subject',()) for key,value in group if key=='commonName']
if common_names!=['127.0.0.1'] or decoded.get('subjectAltName'):
    raise SystemExit(125)
def fixture_capture(item):
    global fixture_ca_seen
    for name in ('httpbin','httpbin_secure'):
        server=item.funcargs.get(name)
        if server is None:
            continue
        if not isinstance(server,threading.Thread) and callable(server):
            # Historical tests/conftest.py wraps the plugin-owned server in
            # prepare_url. Resolve the actual cached plugin fixture, never
            # treat the URL callable itself as a server/lifecycle receipt.
            wrapper=server
            definitions=item._request._fixturemanager.getfixturedefs(name,item.nodeid) or ()
            actual=[definition.cached_result[0] for definition in definitions
                    if getattr(definition,'cached_result',None) is not None
                    and isinstance(definition.cached_result[0],threading.Thread)]
            if len(actual)!=1:
                raise RuntimeError('actual wrapped plugin server is unavailable')
            server=actual[0]
            if wrapper()!=server.url.rstrip('/')+'/':
                raise RuntimeError('historical URL wrapper differs from plugin server')
        if (not isinstance(server,threading.Thread) or server.host!='127.0.0.1'
                or server.protocol!=('http' if name=='httpbin' else 'https')
                or type(server.port) is not int or not 1<=server.port<=65535
                or server.url!=server.protocol+'://127.0.0.1:'+str(server.port)
                or not server.is_alive() or not hasattr(server,'_server')):
            raise RuntimeError('actual plugin fixture identity differs')
        if not any(entry['server'] is server for entry in fixture_servers):
            if len(fixture_servers)>=256:
                raise RuntimeError('plugin fixture server count exceeded')
            fixture_servers.append({'server':server,'fixture_name':name,'host':server.host,
                                    'protocol':server.protocol,'port':server.port})
    if 'httpbin_ca_bundle' in item.funcargs:
        if (item.funcargs['httpbin_ca_bundle'] is not None
                or os.environ.get('REQUESTS_CA_BUNDLE')!=fixture['certificate_root']+'cacert.pem'):
            raise RuntimeError('actual plugin CA fixture semantics differ')
        fixture_ca_seen=True
def fixture_stop():
    global fixture_cleanup_ok
    stopped=True
    for entry in fixture_servers:
        server=entry['server']
        try:
            if server.is_alive():
                server._server.shutdown()
            threading.Thread.join(server,3)
            server._server.server_close()
            if server.is_alive():
                stopped=False
        except BaseException:
            stopped=False
    fixture_cleanup_ok=stopped
'''
_TEARDOWN = r'''    @pytest.mark.tryfirst
    def pytest_runtest_teardown(self,item,nextitem):
        fixture_capture(item)
'''
_MAIN = r'''try:
    code=int(pytest.main(arguments,plugins=[evidence]))
finally:
    fixture_stop()
if (not fixture_cleanup_ok or len(fixture_servers)<2
        or {entry['fixture_name'] for entry in fixture_servers}!={'httpbin','httpbin_secure'}
        or not fixture_ca_seen):
    raise SystemExit(125)'''
_OBSERVATION = r'''fixture_lifecycle={'policy':LIFECYCLE_POLICY,'fixture_digest':FIXTURE_DIGEST,
    'runtime_digest':payload['runtime_digest'],'check_spec_digest':payload['check_spec_digest'],
    'source_revision':payload['source_revision'],'certificate_files':certificate_hashes,
    'certificate_metadata_sha256':fixture['certificate_metadata_sha256'],
    'certificate_policy':{'common_name':'127.0.0.1','dns_sans':[],'ip_sans':[]},
    'servers':[{'fixture_name':entry['fixture_name'],'host':entry['host'],'protocol':entry['protocol'],
                'port':entry['port'],'stopped':not entry['server'].is_alive()} for entry in fixture_servers],
    'server_count':len(fixture_servers),'servers_stopped':fixture_cleanup_ok,
    'httpbin_ca_bundle_observed':fixture_ca_seen,'ca_overlay':False,
    'key_material_binding':'separate_provisioning_evidence_required',
    'health_assurance':'ordinary_declared_tests_only_no_fabricated_health'}
receipt['requests3362_fixture']=fixture_lifecycle
'''


def _identities(spec: CheckSpec):
    from atomic_flow.check_spec import CheckSpec

    if type(spec) is not CheckSpec:
        raise ValueError("immutable Requests3362 spec required")
    identity, fixture, runtime = spec.private_pytest_identity, spec.fixture_identity, spec.runtime_identity
    if (type(identity) is not PrivatePytestIdentity or type(fixture) is not FixtureIdentity
            or type(runtime) is not RuntimeIdentity or spec.task_id != "psf__requests-3362"
            or identity.task_id != spec.task_id or fixture.task_id != spec.task_id
            or identity.variant != "3362-cn" or fixture.variant != "3362-cn"
            or identity.python_version != (3, 6, 15) or runtime.python_version != (3, 6, 15)
            or identity.runtime_digest != runtime.digest or fixture.runtime_digest != runtime.digest
            or fixture.image_id != runtime.image_id or spec.image != runtime.image_id
            or fixture.host_mappings):
        raise ValueError("exact private Requests3362 CN fixture/runtime identity required")
    return identity, fixture


def _replace(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("reviewed private pytest composition anchor changed")
    return source.replace(anchor, replacement, 1)


def requests3362_args(spec: CheckSpec, nonce: str) -> list[str]:
    """Build fixed private-launcher args; actual service health is not inferred."""
    identity, fixture = _identities(spec)
    source = private_pytest_args(spec, identity, nonce)[1]
    data = {**fixture.as_dict(), "certificate_root": fixture.certificate_root}
    setup = _SETUP.replace("FIXTURE_JSON", repr(json.dumps(data, sort_keys=True, ensure_ascii=True)))
    source = _replace(source, "entries=0\n", setup + "\nentries=0\n")
    source = _replace(source, "    def pytest_sessionfinish(self,session,exitstatus):self.finished=True\n",
                      _TEARDOWN + "    def pytest_sessionfinish(self,session,exitstatus):self.finished=True\n")
    source = _replace(source, "code=int(pytest.main(arguments,plugins=[evidence]))", _MAIN)
    observation = _OBSERVATION.replace("LIFECYCLE_POLICY", repr(LIFECYCLE_POLICY)).replace("FIXTURE_DIGEST", repr(fixture.digest))
    source = _replace(source, "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True))",
                      observation + "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'+json.dumps(receipt,sort_keys=True))")
    return ["-c", source]


def requests3362_receipt(result: CommandResult, spec: CheckSpec, nonce: str) -> dict | None:
    """Validate complete native pytest observation plus actual stopped fixtures.

    This does not assert CA health tests existed or passed independently; the
    declared ordinary test inventory and separate provisioning/health admission
    remain host responsibilities.
    """
    identity, fixture = _identities(spec)
    requests3362_args(spec, nonce)
    value = private_pytest_receipt(result, spec, identity, nonce)
    if value is None:
        return None
    observed = value.get("requests3362_fixture")
    expected = {"policy": LIFECYCLE_POLICY, "fixture_digest": fixture.digest,
                "runtime_digest": spec.runtime_identity.digest, "check_spec_digest": spec.digest,
                "source_revision": spec.source_revision, "certificate_files": dict(fixture.certificate_files),
                "certificate_metadata_sha256": fixture.certificate_metadata_sha256,
                "certificate_policy": fixture.certificate_policy, "servers_stopped": True,
                "httpbin_ca_bundle_observed": True, "ca_overlay": False,
                "key_material_binding": "separate_provisioning_evidence_required",
                "health_assurance": "ordinary_declared_tests_only_no_fabricated_health"}
    if (type(observed) is not dict or set(observed) != set(expected) | {"servers", "server_count"}
            or any(type(observed.get(key)) is not type(item) or observed[key] != item for key, item in expected.items())):
        return None
    servers = observed["servers"]
    if (type(servers) is not list or type(observed["server_count"]) is not int
            or not 2 <= observed["server_count"] <= 256 or observed["server_count"] != len(servers)):
        return None
    kinds, ports = set(), set()
    for server in servers:
        if (type(server) is not dict or set(server) != {"fixture_name", "host", "protocol", "port", "stopped"}
                or type(server["fixture_name"]) is not str
                or server["fixture_name"] not in {"httpbin", "httpbin_secure"} or server["host"] != "127.0.0.1"
                or server["protocol"] != ("http" if server["fixture_name"] == "httpbin" else "https")
                or type(server["port"]) is not int or not 1 <= server["port"] <= 65535 or server["stopped"] is not True):
            return None
        kinds.add(server["fixture_name"])
        ports.add((server["protocol"], server["port"]))
    if kinds != {"httpbin", "httpbin_secure"} or len(ports) != len(servers):
        return None
    return value
