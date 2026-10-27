# Requests3362: first full-suite legacy attempt is not accepted

Audit date: 27 October 2026. No model call, target execution or rerun was made
in this audit. The original attempt remains infrastructure_error and uncalibrated.
This report distinguishes observed target outcomes from infrastructure admission.

## Exact retained evidence

- Workflow:40453010247; runtime `5d57a0f3b4762c40be6c3441e63dfb524fe5e511`.
- Artifact:12711726622, retained under
  `validation/p07-budget20/requests3362-calibration-v1/`.
- Archive SHA256:
  `7a65e55c455da39e3e1da0d56b4b258aeb7ec9d26915b05a4802fdf5f4d4da50`.
- The archive digest, ZIP member CRC/size and all three stdout digests were
  independently checked. Native full-suite command remained pytest -rA.
- Every row collected349 tests, completed its native session with exit1 and
  confirmed container cleanup. All rows were rejected by the host at
  `target runner identity or complete collection mismatch`.

| Row | Native summary | F2P direct call | Stdout SHA256 |
| --- | --- | --- | --- |
| base | 10 failed,336 passed,1 skipped,1 xfailed,1 xpassed;81.56s | FAILED | ee3d9ede15916a17ce887786d115c75028692fdafb0239ffba63adaa8e41f214 |
| gold | 9 failed,337 passed,1 skipped,1 xfailed,1 xpassed;81.54s | PASSED | 9b8517379313969079d512dab9538f2db418789fb1578c98b61d663f59547b79 |
| wrong-v6 | 10 failed,336 passed,1 skipped,1 xfailed,1 xpassed;81.53s | FAILED | 56bf87a4a2e73d9ec90698af28b4fa10762af6e9bed11538973a7e00985393ee |

The direct F2P observations concern only
`tests/test_requests.py::TestRequests::test_response_decode_unicode`.
They show that v6 discriminates this requirement in the actual runtime. They
cannot establish accepted calibration while the gold full suite fails.

## Host node-ID mismatch

Pytest2.8.7 reports class methods with an extra instance-collector segment:

    tests/test_requests.py::TestRequests::()::test_response_decode_unicode

The frozen manifest uses the same identity without ::()::. The retained raw
collection has349 unique IDs. Replacing only that exact collector segment yields
349 unique canonical IDs and includes all76 frozen requirements in every row.
This is a bijective mapping observed on this attempt, not missing test execution.

A prospective adapter can retain the complete raw collection/reports, emit an
explicit raw-to-canonical map and apply this exact structural normalization to
both observations and declared IDs. It must reject duplicate raw IDs, canonical
collisions, malformed segments and absent requirements. It must not strip arbitrary
parentheses, rewrite parameter IDs, merge observations or retroactively accept
this failed attempt. Missing target requirements must still fail admission.

## Nine real gold failures remain after ID normalization

### Eight SOCKS environment-proxy cases lack a dependency

All eight variants of
`tests/test_lowlevel.py::test_use_proxy_from_environment[...]` fail. The raw
traceback reaches requests/adapters.py:43, whose fallback SOCKSProxyManager raises:

    InvalidSchema: Missing dependencies for SOCKS support.

The test uses a real local fake proxy and expects ConnectionError from its closed
connection. It never reaches that behavior without SOCKS support. The admitted
13-wheel set lacks PySocks. A new prospective dependency overlay must pin a
Python3.6-compatible publisher wheel, hash its primary metadata and bytes, verify
its dependency closure and installed inventory, and rerun startup/health before
another full triple. Do not fake the proxy outcome, substitute exception types,
modify target Requests or suppress these unlisted tests.

### Cookie expiry fails because HTTPBin returns500

`tests/test_requests.py::TestRequests::()::test_cookie_removed_on_expire` fails
its final assertion that cookie foo was removed. Captured HTTPBin stderr shows
that /response-headers raised before delivering the Set-Cookie expiry header:

    httpbin/core.py:314: response = jsonify(headers.lists())
    Flask0.12.5/json.py -> json encoder
    TypeError: Object of type 'generator' is not JSON serializable

Werkzeug0.11.4 supplies a generator from headers.lists() under this Python3
combination. HTTPBin0.4.1 passes it to Flask's JSON encoder, which cannot serialize
it. The actual HTTP response is500; the subsequent cookie assertion is therefore
secondary to a fixture compatibility failure. The earlier /get and TLS health
checks did not exercise this endpoint.

Fix the prospective fixture environment with reviewed compatible publisher
packages or a separately disclosed minimal fixture-source overlay preserving
real endpoint and header behavior. Bind its source diff/hashes and repeat actual
HTTP/HTTPS /response-headers and cookie-expiry health. Do not rewrite target tests,
emulate responses, patch the target cookie jar or count the500 as a model failure.

## Expected failures require explicit recording

The raw report callback records a failed call for
`test_response_iter_lines_reentrant`, but pytest's native summary classifies it
as XPASS. It is not a tenth ordinary gold failure. The current callback does not
retain report.wasxfail, losing the reason for this distinction. Exact pytest2.8
skipping.py deliberately represents a successful xfail-marked call as outcome
failed plus wasxfail, then reports native category xpassed. The skipped call for
auth_is_stripped_on_redirect_off_host is the likely XFAIL; the utils IO-stream
setup skip is distinct. Old raw callbacks alone cannot prove these categories.

A prospective recorder must retain wasxfail and preserve XFAIL/XPASS separately
from ordinary FAILED/PASSED observations, with exact raw report evidence and native
summary corroboration. It must not treat an ordinary assertion failure as XFAIL,
or allow a required test's skip/XFAIL to satisfy a PASSED obligation. All declared
requirements still need the complete required setup/call/teardown outcomes.

## Next concrete gate

Preserve this archive and classification. Correct the structural node identity
mapping and recorder evidence without changing acceptance rules. Admit the SOCKS
dependency and actual /response-headers fixture compatibility separately. Then
run a fresh complete base/gold/wrong triple in isolated workers. Gold must pass
its frozen requirements and have no unexpected ordinary suite failures; base and
wrong must demonstrate the F2P negative with regressions preserved. Keep full
collection, expected-failure distinctions, native exits, all logs/source hashes,
network topology and cleanup evidence. Inventory remains24/30; P07 remains OPEN.
