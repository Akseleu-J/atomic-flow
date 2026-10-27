# P07 Requests: prospective wheel-only legacy profile

`benchmarks/stage4/p07-requests-legacy-profile.json` records a concrete13-wheel
package closure for **CPython3.6/cp36m/Linux x86_64**, six exact Requests task
bases and official image digests, historical requirement-source hashes and
task-specific test needs. This is **prospective and runtime-blocked**, not an
accepted environment, historical-exact reproduction or completed calibration.
No package was installed or imported; only primary metadata and wheel bytes
were read in memory. No target tests, local subprocesses, CI or model calls ran.

## Concrete package closure

| Package | Prospective pin | Wheel type |
| --- | --- | --- |
| httpbin | 0.4.1 | universal py2.py3 |
| Flask | 0.12.5 | universal py2.py3 |
| Werkzeug | 0.11.4 | universal py2.py3 |
| Jinja2 | 2.8 | universal py2.py3 |
| MarkupSafe | 1.1.1 | **cp36-cp36m-manylinux1_x86_64** |
| decorator | 4.0.9 | universal py2.py3 |
| itsdangerous | 1.1.0 | universal py2.py3 |
| six | 1.10.0 | universal py2.py3 |
| click | 6.7 | universal py2.py3 |
| pytest | 2.8.7 | universal py2.py3 |
| py | 1.4.31 | universal py2.py3 |
| pytest-mock | 0.11.0 | universal py2.py3 |
| pytest-httpbin | 0.2.0 | universal py2.py3 |

All13 downloaded wheel SHA256 values matched their primary PyPI version metadata;
the total was1,146,812bytes. The profile retains exact URLs, hashes and actual
wheel METADATA Requires-Dist fields, including dependencies omitted from some old
PyPI JSON info fields. The selected pins satisfy every active declared runtime
edge for Python3.6/Linux with no extras. Inactive edges are Jinja's optional Babel
i18n extra, pytest's ancient-Python argparse and Windows colorama requirements,
and pytest-mock's Python2-only external mock dependency. Python3.6 supplies
unittest.mock.

This closure covers these selected wheels' runtime metadata. It does **not**
include a Python interpreter, immutable image identity, pip/setuptools bootstrap,
all historical documentation/coverage tools or proof that old code actually
runs on Python3.6. Provision and inspect those separately. The current official
task images report Python3.9.20 and cannot be labelled Python3.6 by changing the
profile. MarkupSafe's binary ABI/platform admission also requires a real matching
runtime; a universal-wheel claim would be false for that package.

## Why this is not the exact historical3362 requirements lock

The exact3362 requirements pin Flask0.10.1, MarkupSafe0.23 and itsdangerous0.24.
Their primary PyPI version metadata currently exposes **no wheels**. Exact
wheel-only reproduction therefore stops at those packages. Their source archive
identities are recorded, but no source distribution was downloaded or built.

The candidate intentionally substitutes:

- Flask0.12.5: satisfies httpbin0.4.1's unbounded Flask requirement and admits
  Werkzeug>=0.7,<1, Jinja2>=2.4 and itsdangerous>=0.21. Its additional click
  dependency is included explicitly.
- MarkupSafe1.1.1: supplies an exactCPython3.6 Linux binary wheel and meets the
  unbounded MarkupSafe requirements of Jinja2 and HTTPBin.
- itsdangerous1.1.0: has a wheel admitting Python3.6 and satisfies Flask's bound.

These are dependency-compatible candidates by metadata, **not proof of endpoint,
template, serialization or test-runner compatibility**. The preserved HTTPBin0.4.1
application imports historical Flask/Werkzeug APIs still present in the selected
release series; only an actual isolated import and health probe can accept that
combination. Do not silently mix HTTPBin0.4.1 with the existing modern Flask2.x
fixture environment or weaken that independently accepted fixture's lock.

Primary metadata examples:
[HTTPBin0.4.1](https://pypi.org/pypi/httpbin/0.4.1/json),
[Flask0.12.5](https://pypi.org/pypi/Flask/0.12.5/json),
[MarkupSafe1.1.1](https://pypi.org/pypi/MarkupSafe/1.1.1/json),
[pytest2.8.7](https://pypi.org/pypi/pytest/2.8.7/json),
[pytest-httpbin0.2.0](https://pypi.org/pypi/pytest-httpbin/0.2.0/json).
The JSON profile retains all13 primary metadata response hashes and exact wheel
digests, including the three historical versions without wheels.

## Critical TLS blocker in the actual legacy plugin wheel

The unmodified pytest-httpbin0.2.0 wheel starts a real HTTPBin WSGI application
through its own HTTP/SSL server, with ephemeral loopback ports. It also contains
the historical CA and server certificate:

| Bundled certificate | Actual identity | Validity |
| --- | --- | --- |
| CA | pytest-httpbin Certificate Authority | through18 June2045 |
| Leaf | CN127.0.0.1, **no SAN** | **expired23 June2025** |

The leaf SHA256 is
`d969eccf31a3b73cfeb164fb84f595d1ef2b9849d710b30168af710a68325f7a`;
the CA SHA256 is
`237f79628140a4d988f1403eff8c54f1ee3de238e066a8a6aa2e76b8cec7caae`.
Its unchanged leaf cannot provide accepted HTTPS in October2026. The CA's later
expiration does not repair its expired leaf.

The CN-only identity explains why the historical3362 warning test expects a
SubjectAltNameWarning and the current SAN-bearing fixture emits none. A legacy
experiment needs a **separately reviewed fresh public test certificate** preserving
the intended hostname and no-SAN warning semantics, explicit CA verification and
recorded byte hashes. Never disable verification, alter system time, fabricate a
warning or count an expired certificate failure as a model defect. A certificate
replacement is a disclosed fixture change, not an unchanged-plugin claim.

Plugin autoload should remain disabled. Exact host-owned loading of the historic
pytest_httpbin.plugin and pytest_mock modules requires its own admission and
health evidence; wheel presence alone does not prove fixture registration.

## Six exact tasks have different historical needs

| Task | Base prefix | Historical runner/fixture context |
| --- | --- | --- |
| Requests863 | a0df2cbb | nose/rudolf2 declarations; function-based HTTPBIN_URL, no pytest-httpbin fixtures |
| Requests1963 | 110048f9 | pytest2.3.4, py1.4.12 declarations; string raises and function-based HTTPBIN_URL |
| Requests2148 | fe693c49 | same historical runner pins; function-based HTTPBIN_URL |
| Requests2317 | 091991be | same old runner pins plus unroutable10.255.255.1 timeout tests |
| Requests2674 | 0be38a0c | same old runner pins plus unroutable timeout tests |
| Requests3362 | 36453b95 | pytest2.8.7, pytest-mock0.11.0, pytest-httpbin0.2.0; object.url fixture API, CN-only warning and unroutable timeout tests |

Full40-character bases, official immutable image digests, all relevant source
hashes and declared requirement/regression counts are in the profile. The common
candidate is chiefly grounded in3362's declared runner requirements; it is a
prospective experiment for the other five, not their exact historical environment.
Do not import these oracle-side requirement details into model-visible workspaces.

## Remaining admission gates

1. Pin and accept an actual Python3.6 Linux runtime, bootstrap identity and all
   selected wheel hashes before executing target source. Inspect and run the
   historical pytest/assertion rewriting and plugin startup inside isolation.
2. Prove unmodified real HTTPBin0.4.1 endpoint behavior over HTTP and CA-verified
   HTTPS with the disclosed certificate profile. Preserve ordinary target
   Requests source and protect tests; no host target setup/import execution.
3. Retain the existing no-external-egress policy. A separate hermetic topology
   must exercise the declared unroutable connection timeout; local HTTPBin and
   older packages do not fix ENETUNREACH under network=none.
4. Rerun each full base/gold/wrong suite independently. Requests3362's present
   wrong control passes its sole requirement with the modern fixture and needs
   a prospective control review; neither environment changes nor many passing
   tests remove that separate gate.

The profile identifies a complete small package candidate and concrete blockers.
It does not bypass source-distribution review, freeze missing interpreter
provenance, shorten tests or declare any Requests task accepted.

## Requests dependency runtime accepted, 27 October 2026

Run `40449494148` on `fe6c3a44d8a4a5ba42e3e34108fb736079335353`
completed successfully. Artifact `12709189504` SHA256
`09c81fbb99c88745e83826b91ed87b23f07bdac2be74ffc78e7490e91515bbe3`
was checked independently, as were the stdlib and installed-inventory stdout
digests. Private Python 3.6.15, all 13 wheels and 235 installed file hashes were
confirmed. Target source was unchanged and cleanup confirmed. Fixed host
inventory was streamed via stdin; worker root remained read-only. No target
imports, setup, plugins or tests ran. Status: `runtime_dependencies_qualified`,
`calibrated=false`. The earlier failed copy attempt is retained separately.
All CI workflows for this runtime completed successfully.

Inventory remains 24/30. Next: execute the certificate/plugin/HTTPBin health
contract in `P07_REQUESTS3362_HEALTH_CONTRACT.md`, then integrate the qualified
timeout topology and run all six complete Requests base/gold/wrong triples.
Model quality and the remaining full P07 gates are still OPEN; no paid calls
were added by these environment qualifications.
