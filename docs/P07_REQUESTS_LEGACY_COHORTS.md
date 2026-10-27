# Requests legacy cohorts after the3362 worker

Read-only audit,27 October2026. Exact frozen-base GitHub source was fetched and
read without importing, executing setup/tests or changing manifests. No Docker
or model calls were made. These are prospective cohort profiles; only the
separate3362 dependency/health receipts can establish their own measured scope.

## One runtime per original official image

The five original images below must each be rebuilt independently. Reusing a
common Python donor or wheel lock is possible, but their source/image identities,
ancestry, ABI, installed inventory, service health and cleanup must be measured
per base. Never retag the3362 source image as another task's environment.

| Instance | Exact base | Original immutable image |
| --- | --- | --- |
|863|a0df2cbb10419037d11d04352b3175405ab52941|swebench/sweb.eval.x86_64.psf_1776_requests-863@sha256:4dcddfdcbc2bcba96327de327f411c671e695366f4e7d09781f64c1758053b18|
|1963|110048f9837f8441ea536804115e80b69f400277|swebench/sweb.eval.x86_64.psf_1776_requests-1963@sha256:5ca751c160affa98d160831608fffe3e2c4fb61c4fb2dad2b147ef2a885088e4|
|2148|fe693c492242ae532211e0c173324f09ca8cf227|swebench/sweb.eval.x86_64.psf_1776_requests-2148@sha256:ada704cd7cd3d1325dc7d148d3dd473fb2a0ba75a8e703aec7ae485cabc6106a|
|2317|091991be0da19de9108dbe5e3752917fea3d7fdc|swebench/sweb.eval.x86_64.psf_1776_requests-2317@sha256:c6b5c8846507b5e48e307decca28dbdfa4f5ef17070c1087aacde765e8c4b178|
|2674|0be38a0c37c59c4b66ce908731da15b401655113|swebench/sweb.eval.x86_64.psf_1776_requests-2674@sha256:0b80f823b65adecd8d8e26c139d636260de360e4676db18030925650dc22cca8|

## Minimum prospective CPython3.6 cohort

863,1963,2148 and2317 have no additional interpreter blocker identified by this
bounded source inspection. The existing private Python3.6.15 donor plus the
same13 wheel lock is a concrete candidate, independently applied to each own
base. This does not establish historical exactness or test success. In particular:

- 863 declares nose and rudolf2. Its inspected test file is a unittest.TestCase
 suite and uses ordinary assertEqual/assertRaises/assertTrue/assertFalse APIs;
 pytest can collect such classes. No nose/rudolf import appears in that file.
- 1963 and2148 declare py1.4.12,pytest2.3.4,pytest-cov1.6,invoke0.2.0 and wheel.
- 2317 and2674 declare the same old py/pytest/pytest-cov plus wheel, without invoke.
- The current13-wheel candidate uses pytest2.8.7 and py1.4.31. It preserves the
 string-code pytest.raises API used in the suites, but remains a disclosed
 prospective substitution. Coverage/invoke are not imported by the inspected
 main test files; do not install unneeded plugins solely because a development
 requirements file listed them.
- At these bases, setup.cfg for1963/2148/2317/2674 contains only universal-wheel
 configuration. No root conftest.py,tests/conftest.py,pytest.ini or tox.ini was
 found at the inspected exact raw paths. This bounded path check is not a full
 Git tree inventory; a real worker must still seal complete collection/config.
- The Requests compatibility modules import bundled chardet/urllib3 and optional
 simplejson with a stdlib-json fallback. No extra live external dependency was
 identified in those module import lines.

Do not mutate or omit tests, run target setup on the host, or claim the shared
candidate supplies historical nose/coverage behavior. A fixed bootstrap should
load the admitted private pytest, freeze the two installed pytest11 entrypoints,
apply the reviewed explicit plugin policy, then expose the admitted target root.
The native obligation remains the complete suite, not a selected test file.

## Old function-based HTTPBin URLs need a distinct service profile

All five main suites read HTTPBIN_URL, falling back to http://httpbin.org/;
there is no pytest-httpbin object fixture used by those functions. A real
host-owned server process must be started independently of pytest fixture
requests. Import the admitted HTTPBin WSGI application; do not emulate responses.

For1963,2148,2317 and2674, test_mixed_case_scheme_acceptable swaps http/https while
keeping the same URL netloc. Two different ephemeral ports do not satisfy that
contract. Use a separately reviewed prospective service profile:

- HTTPBIN_URL=http://httpbin.org/ with no explicit port.
- Docker host mapping httpbin.org:127.0.0.1.
- Real HTTP on127.0.0.1:80 and TLS on127.0.0.1:443 in the same isolated namespace.
- The reviewed sysctl net.ipv4.ip_unprivileged_port_start=0 allows these ports
 without giving target code network capabilities. Retain network=none and the
 existing cap-drop/no-new-privileges/no-host-mount contracts.
- A separate fresh CA-verified DNS certificate profile for httpbin.org, including
 its DNS SAN. Do not reuse or overwrite3362's CN127.0.0.1/no-SAN profile: that
 profile exists for a different historical warning requirement.
- Set the original Requests CA environment to the admitted CA. Never disable
 hostname or chain verification, install a time shim, or fabricate warnings.

Literal HTTPS requests also use https://httpbin.org/status/301 and /delay/10;
2317 additionally uses /redirect/5. Changing only HTTPBIN_URL therefore cannot
redirect all traffic. The same fixed host mapping and standard ports cover
these literal requests while retaining their actual URL semantics.

2148,2317 and2674 follow HTTPBin redirect-to to http://www.google.co.uk in an
auth-header stripping test. A prospective reviewed mapping of exactly that
hostname to127.0.0.1 lets the same real HTTPBin application serve the redirected
request. Those tests inspect Authorization stripping, not Google page content.
This is source-backed feasibility, not measured endpoint or alias acceptance.
Do not map broad wildcard domains or provide Internet egress. Do not change the
redirect Location or replace it with a synthetic response.

863 has a literal http://httpbin.org/get in its custom URL-object test, so it
also needs the explicit httpbin.org mapping even if its environment URL were
changed. Requests to localhost:1 or httpbin.org:1 must continue to fail; do not
start a service on port1. Negative DNS names must remain unresolvable.
2317 and2674 also need the separately qualified exact10.255.255.1:80 timeout
route/DROP profile inside their actual fresh oracle worker namespace.

## 2674 is a separate Python2 candidate

Its protected test catches ConnectionError and reads e.message. The frozen
production ConnectionError inherits RequestException(IOError), with no message
property or assignment. Gold catches ClosedPoolError and converts it into that
ConnectionError, so the protected catch can reach the Python2-era attribute on
the gold path. This is a concrete source-backed Python3 risk; only actual
isolated execution can confirm the resulting outcome.

Do not shim e.message or alter the hidden test. A native CPython2.7.18 candidate
would need a separately pinned immutable Linux donor, private loader/stdlib
qualification and bootstrap compatible with Python2. The3.6 probe uses pathlib,
Python3 stdin APIs and3.6 identity checks; it cannot qualify Python2 by relabeling.

The current lock has twelve py2.py3 wheels and one CPython3.6-specific MarkupSafe
binary. A Python2 profile therefore cannot literally reuse all13 wheel bytes:
MarkupSafe1.1.1 needs a primary-source-verified cp27/cp27mu-compatible wheel for
the chosen donor ABI. Recheck actual METADATA, Python requirements, active closure,
private file inventory, pytest/plugin startup and TLS behavior for that profile.
Do not invent a donor digest or claim any such profile has already been accepted.

The missing protected IDs for863 and2674 remain a separate prospective corpus
review gate described in P07_REQUESTS_CALIBRATION_CONTRACT.md. Environment work
cannot repair those labels or justify silently discarding their observations.

## Exact inspected primary source bytes

URLs use https://raw.githubusercontent.com/psf/requests/<full-base>/<path>.
Each SHA below was recomputed from the complete raw response. Source files were
read only and never executed.

| Instance | Path | SHA256 |
| --- | --- | --- |
|863|requirements.txt|9c38f7a86db3ba935c7dd446c24e50f8a08b98dd73936f77ceac8ef4abadf69d|
|863|tests/test_requests.py|0a642f3bef5bbdad802aa5a56e541e8c5e253e15e4947c1e04663ef0523bd538|
|1963|requirements.txt|878c487bab1e59dc96e58d6268f03aeb70a644b1c549b11a087f7ae06e9ad57f|
|1963|test_requests.py|d2de326a548d9c5cbe86a9df3329678de75c2a532d3fd4213966071d8aaa440c|
|2317|requirements.txt|9c8e6109e5c02e74e4a4f81f5f5231a4760d1e23dd605790c961322f79362f0e|
|2317|test_requests.py|e0f10cee274f0a40816209cbb01aef2d146b39ad78f38502402e95c4c0b4287a|
|2674|requirements.txt|9c8e6109e5c02e74e4a4f81f5f5231a4760d1e23dd605790c961322f79362f0e|
|2674|test_requests.py|c43cd34148559634c7be942dc853887a1a2fe4ce2822d101a7c70dfc1570ee07|
|2148|requirements.txt|878c487bab1e59dc96e58d6268f03aeb70a644b1c549b11a087f7ae06e9ad57f|
|2148|test_requests.py|2d831a0da26974c36aea46d17063d880603e43cc2962c957e7c2f9fd5c7a21cd|
|2674|requests/exceptions.py|cd984789e5e0475b5ea9bbeea3ff4eaf00cc1e7aef46dba55bded57f3ba642fe|

The exact bases, complete production/test patches and prior manifest obligations
remain in the frozen corpus/profile. No new calibration is accepted by this audit.
