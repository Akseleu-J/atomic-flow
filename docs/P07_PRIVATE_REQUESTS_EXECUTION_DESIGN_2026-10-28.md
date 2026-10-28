# Private Requests execution design — 28 October 2026

Design only. This reconnaissance read source/profile/report bytes; it did not
run targets, setup, tests, Docker, Git, provider APIs or paid calls. No shared
runtime implementation changed. Historical calibration remains distinct from
new TaskRunner/executor acceptance.

## Six accepted task families and concrete source

The following retained reports were checked for `status=completed` and
`calibrated=true` in this pass. The earlier calibration-v2 copies are not the
accepted legacy environment evidence for this integration.

| Task | Exact private Python | Runtime/service profile | Retained accepted report under validation/p07-budget20 |
| --- | --- | --- | --- |
| Requests3362 | 3.6.15 | `benchmarks/stage4/p07-requests-legacy-profile-v2.json` plus own CN-only certificate overlay | `requests3362-calibration-v4/calibration.json` |
| Requests1963 | 3.6.15 | `benchmarks/stage4/p07-requests-function-service-profile-v3.json` | `function-calibration-v2/psf__requests-1963.json` |
| Requests2148 | 3.6.15 | same v3 service profile, own exact task/base record | `function-calibration-v2/psf__requests-2148.json` |
| Requests2317 | 3.6.15 | same v3 service profile, own exact task/base record | `function-calibration-v2/psf__requests-2317.json` |
| Requests2674 | 2.7.18 | `benchmarks/stage4/p07-requests2674-python27-service-profile-v2.json` | `requests2674-calibration-v3/oracle/calibration.json` |
| Requests863 | 2.7.13 | `benchmarks/stage4/p07-requests863-python27-service-profile-v7.json` | `requests863-calibration-v8/oracle/calibration.json` |

Service profile SHA256 constants in current helpers are respectively
`2be37770e20e5379d6e1cbed7b67808308a7903fefb3ca70726c70b9280ecfde`
(three Python3.6 function suites),
`61ed4c2119f1b93719297b54277f61293410b769ef5c090db7010177aa56a289`
(2674), and
`93b6a88a99cf04c62984c2404af94bf647c139f4f9f294576ea7b3505126f31c`
(863). These constants refer to the helpers' LF-normalized profile bytes.
An execution contract must clearly distinguish exact retained bytes from
normalization used by historical provisioners. Prospective status fields in
profile JSON do not override the accepted reports or attest a newly built image.

Concrete helper ownership:

| Family | Provisioning | Native runner / health / fixture source |
| --- | --- | --- |
| 3362 | `tools/p07_requests3362_legacy_image.py`, `tools/p07_requests3362_cert_image.py` | `tools/p07_requests3362_calibrate.py`, `tools/p07_requests3362_health.py` |
| 1963/2148/2317 | `tools/p07_requests_function_image.py` | `tools/p07_requests_function_calibrate.py`, `tools/p07_requests_function_health.py`, shared native template from 3362 |
| 2674 | `tools/p07_requests2674_python27_image.py`, `tools/p07_python27_support.py` | `tools/p07_requests2674_python27_calibrate.py`, `tools/p07_requests2674_python27_health.py` |
| 863 | `tools/p07_requests863_python27_image.py`, `tools/p07_requests863_python27_support.py` | `tools/p07_requests863_python27_calibrate.py`, `tools/p07_requests863_python27_health.py`, `tools/p07_requests863_service.py`, `tools/p07_requests863_wire_contracts.py` |

The exact current helper-file SHA256 bytes were read, without importing them:

| Helper | SHA256 |
| --- | --- |
| p07_requests3362_calibrate.py | `862ed1fc8caed51fbcf71c0760bfdf0aa9ed3b765ddd1f29b6de44586e44a9ca` |
| p07_requests3362_health.py | `a19f7354d9eb346509524b107c888addac6c74afebd227db5b8f3a2699acfe63` |
| p07_requests_function_calibrate.py | `9947e8f714b456484c00f9c6800bbed9072c7fa5add648ba93206f4dc7a6d9aa` |
| p07_requests_function_health.py | `44d98da47675938a9e26cc0ea6969406bc596c5f1eec7cdaaab34610e95f97fa` |
| p07_requests2674_python27_calibrate.py | `45b584d1cbeceecc68f704a9926b9dcfb9b7f3e2ce050a304113a61b76555511` |
| p07_requests2674_python27_health.py | `dffa7c77ee892a6af232156d09b9f07d4a9b917785817910a8971037e299740f` |
| p07_requests863_python27_calibrate.py | `d78ff10c6fc919fb7a6e6b3a52213163cdc6a19be935f3bc71308ff382af7690` |
| p07_requests863_python27_health.py | `c434086832feb05938e7478cdd74ee7579641e4bac9da012c679693cbdf06137` |
| p07_requests863_service.py | `5bd82a6e8e98a704bf5b4be9c7a0dc7b1e7b0265af223461f0910bcc91cb7dc7` |
| p07_requests863_wire_contracts.py | `885ea5c6a1f86d845fef39890782a3a06c994a6207e0fc116d0dd6d1484b59be` |
| p07_timeout_topology_probe.py | `cd588bc733a84df04e218c1816bf0cd77e0fcc48d04d0ee09045e8d6012b5f98` |

Generated bootstrap bytes also need their own hash; a helper-file hash alone
does not bind its concrete certificate, wheel inventory or task-specific input.
These source hashes are reconnaissance observations, not new accepted execution.

## Runtime, pytest and plugin constraints

Python3.6 uses `/opt/p07-runtime/python36`, private prefix
`/opt/p07-python36`, observed executable
`/opt/p07-python36/bin/python3.6`, and the fixed donor loader with `-I -B`.
Python2 uses `/opt/p07-runtime/python27`, prefix `/opt/p07-python27`, executable
`/opt/p07-python27/bin/python2.7`, and the fixed donor loader with
`-E -s -S -B`. Both Python2 support helpers rebuild exactly four private sys.path
entries and erase positional sys.argv for `-c`; embed validated canonical input
in the fixed template. Do not pass `-I` or Python3-only bootstrap syntax to 2.7.

All six native templates require installed private pytest **2.8.7** and the
exact pytest11 entrypoint map
`httpbin -> pytest_httpbin.plugin`, `pytest_mock -> pytest_mock`. Their admitted
native argv is:

`-p no:httpbin -p pytest_httpbin.plugin -p pytest_mock -rA -v`

The underlying corpus command is `pytest -rA`; `-v` exposes full reporting.
Do not reuse modern installed-pytest arguments `--no-header` or fail-fast `-x`:
the former is not the accepted historical profile and the latter prevents
complete obligation execution. A new fixed adapter may append the complete
sealed file inventory to the historical flags only after proving its collection
equals the approved obligation. It may not select only FAIL_TO_PASS tests.

Historical helpers clear PYTEST_ADDOPTS/PYTEST_PLUGINS/plugin-autoload input only
after verifying the installed file and exact entrypoint inventory. Pytest2.8.7
must not be assumed to honor modern PYTEST_DISABLE_PLUGIN_AUTOLOAD behavior.
Verify the actual registered plugin list against builtins plus the two reviewed
plugins and the host recorder, including plugin module paths. The 863 adapter
preloads those plugins before inserting `/testbed`, to prevent its historical
target module layout from shadowing plugin dependencies. Preserve that ordering.

Use complete raw node collection and per-phase reports, not a modern hook count
alone. The old collector includes an instance segment `Class::()::test`; the
existing canonicalizer removes only that exact reviewed four-segment shape,
refuses collisions and retains raw-to-canonical mapping. Non-ASCII terminal
spellings have a separate collision-checked mapping; do not decode arbitrary
escape sequences or equate unrecognized labels with PASS. Preserve explicit
xfail/xpass/skip categories and require the agreed regression inventory.

## Fixture differences that must remain explicit

3362 uses pytest-httpbin fixture-owned services and certificates installed at
`/opt/p07-python36/lib/python3.6/site-packages/pytest_httpbin/certs/`
(`cacert.pem`, `cert.pem`, `key.pem`). Its disclosed server leaf has CN
`127.0.0.1` and **no SAN**. Current accepted workflow provisions the v2 dependency
profile (HTTPBin0.5.0); old retained health-v1 material contains HTTPBin0.4.1.
The health helper supports both versions by reading the admitted runtime profile;
never choose its default0.4.1 as the current version. No target Requests CA
append exists in the 3362 calibration helper. Keep this family separate from
the function suites instead of silently imposing their CA overlay.

The three Python3.6 function suites use real HTTPBin0.5.0 via wsgiref HTTP and
TLS servers on loopback ports80/443. Certificates live in
`/opt/p07-function-fixture/{cacert.pem,cert.pem,key.pem}` and have the
reviewed DNS SAN. 1963 maps `httpbin.org`; 2148/2317 also map
`www.google.co.uk`. Real redirects retain their hostnames and local transport
resolution; no external request is admitted. 2674 uses the same fixture paths,
both host mappings, Python2.7.18 and TLSv1.2-compatible server code.

863 is a separate Python2.7.13/Wheezy SSLv3 donor profile, not interchangeable
with 2674. Its v7 certificates cover `httpbin.org` and `github.com`. Its fixed
loopback mappings are `httpbin.org`, `github.com`, `kennethreitz.com`,
`www.google.com`, `blip.fm`, `t.co`, `tinyurl.com`, `feedproxy.google.com`,
`www.dealipedia.com`, and `www.marketwire.com`. The transport is Gunicorn19.9.0
with real gthread workers, futures backport, real HTTP/TLS, SSLv3 capability,
digest authentication and explicit historical redirect/header/chunked/Unicode
wire cases. `p07_requests863_service.py` and `p07_requests863_wire_contracts.py`
are essential fixture source. A two-thread wsgiref substitute loses accepted
wire semantics. Child fixture processes start through the private launcher in
`/tmp`, without `/testbed` in sys.path; shutdown checks owned process groups,
with forced-kill/group-state uncertainty rejected.

Fixture identities bind URLs, certificates and negative checks: CA-verified
HTTPS, hostname validation, unrelated-CA rejection, real auth, redirects,
closed port1, and task-specific SSLv3/wire behavior. Fixed low ports require the
reviewed namespace sysctl `net.ipv4.ip_unprivileged_port_start=0`, fixed
`--add-host` loopback mappings, no root/capabilities or external Docker network.

Timeout cases require more than network none: the existing host-only topology
helper pins the owned container PID/start-time/netns inode, holds a namespace
descriptor, adds `10.255.255.1/32 dev lo src127.0.0.1`, and drops only TCP80 to
that destination in OUTPUT. It proves real socket timeout, dropped-packet count,
no external route and unchanged host namespace. No NET_ADMIN capability enters
the target. The current single docker-run boundary cannot install that namespace
before target tests start. Introduce a reviewed create/start/health/test lifecycle
or a trusted waiting entrypoint with explicit ready/release handshake; never
expose arbitrary nsenter/iptables arguments to CheckSpec/model tools. This
host namespace helper is a necessary executor change, not data in a receipt.

## Minimal immutable contracts

Add three separate frozen host-owned objects in new modules; none accepts
shell text, generic env/mount dictionaries, gold patches or hidden test input.
Their strict canonical JSON/digests include schema and policy version. Existing
CheckSpec serialization remains unchanged when the optional bindings are absent.

**PrivatePytestIdentity**: runtime digest, exact Python version, pytest version
`2.8.7`, private site-packages prefix, exact installed dependency inventory
digest, generated bootstrap hash, exact two entrypoint pairs, fixed plugin argv
profile enum, target module relative path `requests/__init__.py`, full approved
raw/canonical collection inventory digest and expected-failure policy digest.
Validate task/runtime/fixture cohort equality. Do not freeze editable production
implementation bytes as runner anchors; freeze tests, configuration, plugins
and host bootstrap separately. Reuse the target-grammar compile-only precheck.

**FixtureIdentity**: task ID, exact cohort enum (3362 CN-only;
function36; function2718; wire2713), profile digest, runtime/image digest,
service/health/stop/topology generated-helper digests, installed-file inventory,
certificate path/hash tuples and validity/SAN policy, exact fixed host map,
fixed URL/port pairs, plugin inventory binding, wrong-CA public fixture hash,
and timeout topology policy enum. Cohort enums select fixed host implementation;
they do not carry caller-defined Docker flags. The fixture identity establishes
what must run; each invocation requires fresh lifecycle evidence binding its
nonce, immutable container ID, health results and shutdown/absence proofs.

**ExecutionOverlay**: a host request/receipt for the sole reviewed operation
`append_fixture_ca` on `requests/cacert.pem`, binding task ID, candidate-source
revision, execution-source revision, fixture/runtime/check-spec digests,
original file hash, exact fixture CA hash, resulting file hash, complete
before/after manifest digests, unchanged-file proof and operation version.
It must verify bytes are exactly `original + b'\n' + fixture_ca`, matching the
accepted function/2674/863 helpers. No arbitrary path, replacement text, file
mount or second overlay is admitted. 3362 has no such overlay unless separately
qualified evidence explicitly requires one.

## Candidate source and execution source remain distinct

CheckObligation currently binds one template source revision to execution,
and source-before/source-after/completed-check validation assumes it is also
the candidate revision. Do not set source-before to the candidate when the
container actually reads overlaid bytes, and do not replace the publication
manifest with the overlaid manifest to make equality assertions pass.

After host admission of candidate revision C, copy the **complete admitted
candidate** to a private disposable execution snapshot. Remove only copied
bytecode with an explicit removal receipt as the private SymPy smoke does.
Verify all original admitted candidate bytes match C. Before sealing execution,
append only the exact fixture CA if this cohort requires it, producing E.
Require unchanged path set and exact equality of every other file. Seal a
separate execution CheckSpec bound to E and the typed overlay digest; protect
the overlaid CA bytes/configuration/test inventory in that execution snapshot.
Mount the whole E readonly. There is no hidden CA-only mount over C.

Retain original candidate-bound obligation O and its approved scope/deadline.
A new host execution receipt must bind O.digest, C, execution CheckSpec.digest,
E, overlay digest, runtime/fixture identities and the actual counted receipt.
The ordinary completion validator checks E before/after, reconstructs E from
current C plus the sole authorized overlay, and verifies C remains unchanged.
Only then does check_observation associate this execution with O/C. The generic
source-equality predicate is not globally weakened to accept arbitrary C/E pairs.
Persist both revisions through tool failures, replan, resume, final gate and
publication. A changed candidate always requires a fresh execution snapshot and
fresh overlay/lifecycle nonce. Publish only C. Never copy the CA append back.

If scope approval includes a model change to requests/cacert.pem, reject this
overlay branch pending explicit host review; do not silently append a CA to an
unknown certificate change. Fixture preparation never edits candidate source,
runs setup, installs target code, or executes historical tests on the host.

## Acceptance sequence and concrete next assignments

1. Pure contracts: new `check_private_pytest.py`, `check_fixture.py`, and
   `check_execution_overlay.py`, each with a new pure test module. Implement
   strict shapes, canonicalization, task/cohort restrictions, overlay manifest
   equality and collection mappings before touching shared executor state.
2. Root-owned shared integration: CheckSpec/CheckObligation optional typed
   bindings; new candidate-to-execution receipt validator; existing completion,
   failure/replan/resume routes; legacy canonical-byte regression tests.
3. Root-owned Docker lifecycle: immutable-ID create/start namespace handshake,
   existing limits, fixed mappings/sysctl, fresh same-container health/tests/
   stop, cancellation cleanup. No target capabilities or network widening.
4. New synthetic smoke/workflow: private pytest2.8.7 without fixtures first;
   then 3362 CN-only fixture path; then one function36 CA overlay case; then
   2674; finally 863 real Gunicorn/SSLv3/wire case. Preserve failed artifacts.
   Real FAIL/edited PASS/required-SKIP refusal and target-grammar checks are
   necessary at each boundary, with source C/E and cleanup evidence retained.
5. Historical full ordinary TaskRunner journeys for all six, using complete
   inventories; only afterward proceed to paid matched pairs/holdout/ablations.

Useful independent new-module assignment: one agent owns pure ExecutionOverlay
and manifest proof; one owns FixtureIdentity/cohort selection; one owns
PrivatePytestIdentity/raw-to-canonical collection validation. Root integrates
shared contracts and Docker lifecycle serially after those interfaces agree.
These are proposed assignments, not authorization to start extra agents.


Pure implementation checkpoint: check_fixture.py binds all six exact cohorts, reviewed helper/profile/dependency/public certificate inventories and explicit candidate/execution CA-overlay revisions. check_private_pytest.py builds fixed Python2/3.6-compatible installed-pytest/plugin execution and strict complete raw receipts, but has no fixture lifecycle admission and is deliberately disconnected from ordinary execution dispatch. Combined fixture/bootstrap tests66PASS. Flat attempt executor is prepared separately; it is not yet a matched baseline driver. No historical Requests in-loop acceptance follows from these pure contracts.


CheckSpec now optionally seals exact PrivatePytestIdentity and FixtureIdentity; absent fields preserve canonical contract bytes. Private metadata selects historical plugin argv without -x/--no-header, but checker_args continues to refuse Requests fixture execution until its lifecycle is implemented. Source overlay candidate/execution separation remains explicit and unintegrated. Binding/legacy spec/bootstrap neighbors128PASS. A separate root-worker topology helper pins the owned container namespace FD and process birth, applies only the reviewed loopback TCP80 DROP route/rule, and verifies Docker isolation before/after. Its42 mocked tests PASS; it is not health/execution/target acceptance and does not change generic executor controls.


Requests3362 pure fixture lifecycle composition is prepared in check_requests3362.py: public CA/server byte/CN-noSAN proof, actual plugin-owned HTTP/TLS fixture capture, CA-env semantics and mandatory shutdown/join/close before receipt. It remains disconnected from checker_args: native fixture health, topology, immutable key provisioning and real Docker controller acceptance are still required. Pure Requests composition/privatepytest/fixture tests101PASS. Exact hash-bound empty installed initializer files are now admitted as legitimate bytes; arbitrary mutation still refuses. No source/runtime authority relaxed.


General CI on2436824 failed onLinux because topology unit fixtures monkeypatched process-global os.open/fstat/close; pytestFDcapture received fake stat withoutst_size. Windows local tests used a different capture path. Downloaded JUnit retained under flat-core-ci-v1 (41setup errors); mockedproductionnamespace code was not implicated. Fix uses module-local facades and a real tempfile/fstat regression; topology43PASS locally. Fresh generalCI is required. Separate Atomic/flat Docker journey40742941290 remains accepted at its ownscope.


The separate Requests3362 controller is implemented prospectively: rootLinux-only, fixed ownedCID container with read-only source/root and core bounds; PIDbirth/netns binding before/after fixed topology; only then private pytest fixture execution; bounded streams/cancellation, source equality, fixture shutdown and immutableCID absenceproof before returning. Generic check dispatch remains disabled until real Docker/runtime/health acceptance. Eleven mocked controller testsPASS withoutpytestcache; an initial Windows cache read ended withMemoryError, so that earlier run is not accepted as a completed suite. The fixed privatepytest builder clears reviewed pytest environment overrides and preloads pinned plugins before target sys.path. No new provider calls beyond the two recorded diagnostics.
