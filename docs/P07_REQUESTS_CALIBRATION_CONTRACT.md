# Requests calibration: exact remaining contracts

Audit date: 27 October 2026. This is host-only oracle context. No target code,
setup, test or paid inference was executed in this audit. No manifest was changed.
The inventory remains 24/30 until fresh complete triples are independently accepted.

## Six distinct target identities

The current corpus is `benchmarks/stage4/corpus-p07-v2.json`. Existing v2 reports
record the native command `pytest -rA` for all six tasks: the complete suite,
not a model-selected requirement subset.

| Instance suffix | Base commit | F2P | P2P | Required total | HTTPBin style | Timeout topology |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 863 | a0df2cbb10419037d11d04352b3175405ab52941 | 4 | 60 | 64 | HTTPBIN_URL function | Not declared |
| 1963 | 110048f9837f8441ea536804115e80b69f400277 | 7 | 112 | 119 | HTTPBIN_URL function | Not declared |
| 2317 | 091991be0da19de9108dbe5e3752917fea3d7fdc | 8 | 133 | 141 | HTTPBIN_URL function | 10.255.255.1:80 |
| 2674 | 0be38a0c37c59c4b66ce908731da15b401655113 | 12 | 142 | 154 | HTTPBIN_URL function | 10.255.255.1:80 |
| 3362 | 36453b95b13079296776d11b09cab2567ea3e703 | 1 | 75 | 76 | pytest-httpbin object.url | 10.255.255.1:80 |
| 2148 | fe693c492242ae532211e0c173324f09ca8cf227 | 10 | 118 | 128 | HTTPBIN_URL function | Not declared |

Each task's immutable official image digest and historical source hashes are
frozen in `p07-requests-legacy-profile.json`. The qualified Requests3362 runtime
only admits its own base/image. It cannot establish source ancestry, dependency
compatibility, startup or calibration for the other five official images.
A future shared builder must accept only these six frozen task records, produce
one image per original official base, retain that base's layers and repeat all
ABI, package inventory, source-preservation and cleanup evidence independently.
A common donor or package lock does not replace those proofs.

## Verified blockers in the current contracts

### The old3362 ASCII negative is non-discriminating

The protected F2P `test_response_decode_unicode` adds a Response whose raw bytes
are `b'the content'`, explicit encoding is None, and only asserts that yielded
chunks are str. The fixed ASCII fallback in v5 meets this ASCII-only obligation.
The retained report `validation/p07-budget20/httpbin-oracle/requests3362.json`
confirms base FAILED, gold PASSED, wrong-ASCII PASSED for that exact requirement,
while the overall triple failed infrastructure acceptance. This is not evidence
of a model defect or successful calibration.

Prospective `p07-controls-v6.json` changes only3362: it replaces the added fallback
encoding line with `yield from iterator; return`, preserving raw-byte passthrough
when explicit encoding is absent. Explicit-encoding decoding remains unchanged.
It is a CPython3.6-compatible prospective negative, not a Python2 compatibility
claim. The wrong patch SHA256 is
`9d0a89d0040b05a3ca5ddda9b252553b397612b99c35b8979f5c1f00aaac4946`.
All other29 control records and v5 remain unchanged. A fresh full triple must
still establish actual requirement failure and regression preservation; source
inspection and AST parsing do not establish a calibration result.

### Two newly added protected tests are absent from manifest labels

The complete protected patches introduce:

- 863: `tests/test_requests.py::RequestsTestSuite::test_allow_list_of_hooks_to_register_hook`.
- 2674: `test_requests.py::test_urllib3_pool_connection_closed`.

Neither ID is in its current F2P or P2P list. Existing v2 logs observe both tests
failing in base and passing/failing among broader service failures in other rows.
The current classifier treats every FAILED observation outside F2P as a regression
failure, even if it belongs to an added protected requirement. Therefore a valid
full-suite run can expose a manifest mismatch and remain unaccepted.

Do not suppress these tests, filter collection, relabel old outcomes or relax the
classifier. First preserve a fresh complete triple in the admitted environment.
If it proves a protected test is a legitimate additional base/gold distinction,
review a prospective corpus version that adds its F2P identity while retaining
every prior F2P/P2P obligation, source patch, old manifest and attempt. Calibrate
that version afresh. No manifest change is justified merely to make a run green.

## Additional2674 interpreter risk: source-backed, not yet measured

The protected2674 test catches ConnectionError and reads `e.message`. Exact
production `requests/exceptions.py` at the frozen base has ConnectionError
inheriting RequestException(IOError), with no message property or assignment.
The primary source inspected read-only is:

https://raw.githubusercontent.com/psf/requests/0be38a0c37c59c4b66ce908731da15b401655113/requests/exceptions.py

Its SHA256 is
`cd984789e5e0475b5ea9bbeea3ff4eaf00cc1e7aef46dba55bded57f3ba642fe`.
A CPython3.6 gold run may therefore fail on the Python2-era exception API even
after fixing services and timeout behavior. This inference must be checked by
actual isolated execution. Do not shim the target exception, amend the hidden
assertion or represent the shared3.6 candidate as historically exact. If confirmed,
a separately admitted native Python2-compatible runtime/profile may be needed;
its interpreter, wheels, plugins, TLS and complete triple all require new evidence.

## Minimal3362 integration after certificate and plugin health

1. Freeze the current Requests3362 runtime receipt, exact base/donor/layers,
   private launcher and ABI,13 publisher wheels, installed-file inventory and
   the separately disclosed three-file certificate overlay. Verify actual image
   ancestry and hashes before target imports. Require completed plugin/HTTP/CA-HTTPS
   health and cleanup; a synthetic health receipt remains distinct from calibration.
2. Start a fresh target oracle container for each base/gold/wrong row under the
   existing independently admitted oracle isolation/resource profile. Do not
   reuse a health process or reinterpret a read-only health receipt as permission
   to widen that container. No host mounts, external network or target capabilities.
3. For the timeout tests, reuse the fixed namespace primitives from
   `p07_timeout_topology_probe.py`, not its `qualify()` entry point (that creates
   a separate synthetic container). Bind the actual owned target container's PID,
   process start ticks, distinct netns inode and pinned namespace FD. Trusted host
   setup alone adds the exact loopback route and TCP80 DROP. Recheck route, rule,
   real timeout, DROP count, external-route absence and namespace identity in that
   same worker. Give no NET_ADMIN capability to target code.
4. Use the qualified private Python launcher, not the private console-script
   shebang that bypasses the donor ELF loader. Bootstrap installed pytest first,
   then expose only the admitted /testbed source root. Record interpreter,
   pytest/plugin module paths, target Requests source identity and exact argv.
   Preserve the native full-suite `-rA` command and full collection.
5. Old pytest2.8 ignores modern PYTEST_DISABLE_PLUGIN_AUTOLOAD. Freeze exactly the
   admitted httpbin and pytest_mock entrypoint metadata; refuse extras. Clear
   PYTEST_ADDOPTS/PYTEST_PLUGINS and apply the reviewed explicit plugin arguments
   `-p no:httpbin -p pytest_httpbin.plugin -p pytest_mock`. Prove actual registration
   once per allowed plugin rather than claiming unsupported global autoload control.
6. Apply only frozen protected test bytes and the correct row's production patch.
   Keep all oracle details outside model contexts and agent workspaces. Bind
   source/test/runner hashes before execution and verify source preservation after.
   The model must not choose a command, test subset, image, fixture or environment.
7. Parse all required IDs and retain all ordinary observations, native exit,
   stdout/stderr digests, collection/completion evidence and cleanup. Missing IDs,
   skips, import/setup errors, timeout, truncation, changed source or uncertain
   cleanup remain non-successful. Require base negative, gold all-pass and wrong
   negative with preserved regressions. No model inference is needed for this triple.

The other five targets need their own images and task-specific endpoint/startup
proofs. Function-based HTTPBIN_URL tests include literal hostnames and ports;
changing only an environment URL may not cover them. Preserve previously reviewed
host mapping semantics and real HTTPBin behavior. Never fabricate responses or
weaken TLS verification to make a target pass.
