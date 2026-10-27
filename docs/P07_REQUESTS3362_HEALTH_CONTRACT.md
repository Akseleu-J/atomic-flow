# Requests3362: next fixed startup and HTTPS health contract

Read-only inspection on27October2026. Exact primary PyPI wheel bytes were fetched
and SHA256-checked in memory, then ZIP/source read without importing, installing
or executing package code. This is a prospective health contract, not plugin
startup evidence, certificate acceptance or benchmark calibration.

## Exact inspected inputs

| Input | SHA256 |
| --- | --- |
| pytest_httpbin-0.2.0-py2.py3-none-any.whl | 18da0fc28cc3325c582c0b6965ad1dda969564e04d4191b0fcd8c77e8356b682 |
| pytest-2.8.7-py2.py3-none-any.whl | 33612ed6de42f05640fe058aafdf08899eabcb210da74aeb0b788de9695c8bb5 |
| pytest_mock-0.11.0-py2.py3-none-any.whl | 5d109071ac1c6a5a883aece92c507b4562efce86054ab39c83a9f3554688289d |
| pytest_httpbin/plugin.py | 5d5405c85db038ecea16051b264b80dda02816dae848a24f77a18be2da45d78e |
| pytest_httpbin/serve.py | ce3e6a3c23abfce51df36d3903e93bf0bbb4f78b6342d5b189b13fdebd8dfa53 |
| pytest_httpbin/certs.py | e4b2845d493d06ac8f40cbd28d9da617cb2965c3ace4c4e13d7e51c99039d1f0 |
| _pytest/config.py | 08b510af255c045de5be2ebcff62abe4bbca9b2f9c8ca3c569f8c05829867409 |
| _pytest/vendored_packages/pluggy.py | e4188afc95b60fbbaebc3ae7def347011e75347542d02760ef1f2fda5df54863 |

Exact publisher URLs are frozen in p07-requests-legacy-profile.json. No target
source or oracle details should enter a model-visible workspace.

## Original certificate paths and fixture semantics

Under /opt/p07-python36/lib/python3.6/site-packages/pytest_httpbin/:

- certs/cacert.pem is the CA bundle returned by certs.where().
- certs/cert.pem is the server leaf certificate.
- certs/key.pem is the public benchmark fixture's corresponding server key.

serve.CERT_DIR derives those paths from its own installed __file__. SecureWSGIServer
calls ssl.wrap_socket with that key/certificate and server_side=True. The original
Server binds127.0.0.1 and port0, exposes .url, starts its actual WSGI HTTPBin app in
a thread, and stops through the session fixture's registered finalizer. It is a
real HTTP application, not a response emulator.

The original httpbin_ca_bundle fixture is function-scoped. It sets
REQUESTS_CA_BUNDLE=certs.where() through monkeypatch and has **no return statement**.
Thus the historical test passes verify=None while the target Session resolves the
CA through its environment. Returning a path instead would change this fixture's
API. verify=None here must not be described as disabled verification: successful
acceptance requires the effective CA bundle and real certificate verification.
Do not change target Requests, the fixture source, assertions or test selection.

The original CN127.0.0.1 leaf has no SAN and expired23June2025; its SHA256 is
d969eccf31a3b73cfeb164fb84f595d1ef2b9849d710b30168af710a68325f7a.
The original CA remains valid through18June2045, SHA256
237f79628140a4d988f1403eff8c54f1ee3de238e066a8a6aa2e76b8cec7caae.
The expired leaf cannot be reused for accepted HTTPS today.

## Important pytest2.8 startup limitation

PYTEST_DISABLE_PLUGIN_AUTOLOAD is absent from exact _pytest/config.py. Its _preparse
always calls load_setuptools_entrypoints('pytest11'). Setting the modern variable
does not disable old-version autoload and cannot establish a safety claim.
The two intended installed entrypoints are:

| Entry name | Module |
| --- | --- |
| httpbin | pytest_httpbin.plugin |
| pytest_mock | pytest_mock |

Explicit '-p pytest_httpbin.plugin' alone registers that module under its full
module name. The old loader later attempts alias httpbin and Pluggy rejects the
same module registered twice. A fixed startup candidate using unmodified pytest
source is:

    -p no:httpbin -p pytest_httpbin.plugin -p pytest_mock

consider_preparse processes these arguments before entrypoint discovery. The first
argument blocks entrypoint alias httpbin (and name pytest_httpbin), while the full
module name pytest_httpbin.plugin remains explicitly loadable. pytest_mock is
explicitly registered under the same name as its entrypoint, so discovery skips
it as already registered. The exact old Pluggy loader skips blocked or registered
entrypoint names before ep.load(). This is source-reviewed behavior requiring an
actual startup probe, not measured execution yet.

Before startup, enumerate entrypoint metadata without ep.load() and require exactly
these two names/modules/distributions from the admitted private prefix. Refuse
unexpected entries. Clear PYTEST_ADDOPTS and PYTEST_PLUGINS in the host-controlled
bootstrap/environment. Bind installed source hashes and record actual registered
plugins afterwards; reject unapproved external plugins. Describe this as a frozen
entrypoint allowlist, not global autoload support. Target conftest imports remain
untrusted code and belong only in the isolated target-test phase.

## Fresh disclosed public test certificate overlay

Generate a new benchmark-only CA and signed leaf through trusted host/build tooling
with a fixed reviewed configuration. The leaf must contain CN127.0.0.1, **no SAN**,
a currently valid bounded lifetime, server-appropriate key usage and a key matching
certs/key.pem. The CA must have CA basic constraints and keyCertSign. Record exact
certificate/key file SHA256, public subjects/issuers, serials, validity, extension
inventory, signature/chain verification and matching public keys. These are
purpose-made public test fixtures, never user credentials. The CA signing key is
not needed in the installed runtime.

Verify original wheel certificate hashes before replacing only the three disclosed
certs/*.pem files in a fresh child layer. Freeze the parent runtime receipt/image
and original wheel inventory; retain the three new expected file hashes as an
explicit overlay. Do not relabel changed certificate bytes as unchanged publisher
wheel contents or weaken the prior runtime inventory checker.

A SAN-bearing leaf would remove the historical SubjectAltNameWarning. The accepted
modern3362 run already showed that mismatch: gold.log in artifact12649834300 expects
exactly that warning with modern SSL, but got no warning. The new overlay preserves
this missing-SAN condition; it must never synthesize a warning or change system time.

## Small next artifact before target execution

A dedicated network-none Linux worker should admit the exact Requests3362 private
runtime parent, certificate overlay, actual interpreter/stdlib/ABI and source bytes,
then run a fixed health program from outside /testbed with isolated Python flags.
It may import the admitted HTTPBin/plugin packages, but no target Requests code,
setup.py, target conftest or target tests at this phase.

Prove real HTTPBin HTTP and CA-verified HTTPS to127.0.0.1 on ephemeral ports, response
status/content and server shutdown. Preserve certificate verification and hostname
checking; if the selected legacy interpreter rejects CN-only IP identity, retain
that failure and inspect compatibility rather than turning either check off.
An unrelated CA must fail verification. The actual plugin startup probe must prove
both intended plugins register once, the expected fixtures exist, old pytest has
the required string-raises/warns APIs, and all imports resolve to admitted private
wheel paths. A synthetic fixture exercise can prove original environment-based
CA fixture behavior; it is not a target requirement result.

Capture cleanup, exact commands, plugin inventory, source/profile/certificate hashes
and all diagnostics. The separately qualified timeout namespace profile remains a
separate receipt until integrated into the same fresh worker. Only after this
health admission should full protected base/gold/wrong target tests run. The
Requests3362 wrong control also still needs review; health success cannot replace
that independent negative-control gate.
