# P07 Requests3362: remaining failures after real HTTPBin admission

This is a read-only audit of workflow `40324021765`, runtime
`74c13402af13fa424ebfd61fcd1503f4771b3d42`, artifact12649834300. It makes no new
calibration or quality claim. The original ZIP is retained at
`validation/p07-budget20/httpbin-oracle/12649834300.zip`; the extracted report is
`validation/p07-budget20/httpbin-oracle/requests3362.json`. All three fresh grading
containers reported successful real HTTP and CA-verified HTTPS health before
target execution. Requests3362 remains **OPEN, not calibrated**.

The exact target base is `36453b95b13079296776d11b09cab2567ea3e703`.
Gold executed185 items under Python3.9.20, pytest7.4.4 and pluggy1.0.0:
**178 passed, 4 failed, 1 error, 1 xfailed, 1 xpassed**. These counts describe
execution only; successful fixture health and many passing tests cannot replace
complete gold success. Base, gold and wrong each remain `infrastructure_error`.

## Five gold blockers and their distinct causes

| Gold test | Actual evidence | Required next experiment |
| --- | --- | --- |
| `TestRequests::test_session_close_proxy_clear` | Setup error: fixture `mocker` is unavailable. Target freeze contains no pytest-mock. | Build a separately reviewed target test-runner environment with an exact, compatible pytest-mock wheel and its dependencies. The HTTPBin service venv deliberately does not populate target plugins. Do not implement a substitute fixture merely to satisfy the test. |
| `TestRequests::test_conflicting_post_params` | Historical test uses the string-expression form of `pytest.raises`; pytest7.4.4 raises TypeError because that argument must be callable. | Restore a historical compatible pytest/Python/plugin combination inside a pinned derived benchmark image. This is a test-runner API mismatch, not evidence that gold violates its requirement. Preserve the protected test unchanged. |
| `TestRequests::test_https_warnings` | Expected `SubjectAltNameWarning`, observed no warning. The new fixture certificate explicitly has a SAN; the audited test expects the historical missing-SAN warning. | Define a distinct audited historical certificate profile with CA verification still enabled and a hostname-matching CN, then verify its actual warning behavior on the historical target SSL stack. Keep the existing SAN fixture as the independently accepted health profile. Never fabricate a warning, disable certificate verification, or alter the protected assertion. |
| `TestTimeout::test_connect_timeout` | Connecting to `10.255.255.1:80` immediately raises ENETUNREACH and ultimately ConnectionError under `network=none`, rather than the expected connection timeout. | Design and separately review a hermetic network topology that can actually exercise a dropped connection while proving no external egress. This is not repaired by local HTTPBin. Keep the current isolation policy until an explicit replacement is accepted. |
| `TestTimeout::test_total_timeout_connect` | Same unroutable-IP ENETUNREACH path; the total-timeout connect phase cannot exercise its intended behavior. | The same separately reviewed networking profile is necessary; no exception/status rewriting or sleep-based fake transport. |

The TLS mismatch is a fixture semantics gap revealed by executing the full gold
suite, not a TLS verification failure: HTTPS health passed using the explicit
fixture CA. The `httpbin_ca_bundle` adapter currently supplies its CA through the
environment and returns None; the target consequently passes `verify=None`.
The target request completed without a TLS exception. The service's health proof
does not independently establish the target client's exact CA-selection path;
that path, the intended historical fixture API and warning behavior must be
reviewed together before admission.

## Independent negative-control gap

The sole declared FAIL_TO_PASS requirement is
`tests/test_requests.py::TestRequests::test_response_decode_unicode`; the corpus
also declares75 PASS_TO_PASS tests. Its observations are:

| Row | Sole requirement outcome |
| --- | --- |
| base | FAILED |
| gold | PASSED |
| wrong | PASSED |

The current wrong mutation replaces detected fallback encoding with fixed ASCII.
It does **not** fail the protected requirement in this actual fixture/runtime.
Therefore restoring the environment alone cannot make the current triple a valid
calibration. Prepare a new prospective, source-only control in a new controls
version, with a plausible requirement-relevant decoding defect, then rerun all
three rows. Preserve the present wrong-PASS evidence and existing control bytes.
No model-visible workspace may receive this oracle-side information.

The off-host redirect test is XPASS in this gold run. The real loopback host
mapping preserved the historical hostname transition; it did not repair or hide
the five blockers above. The pre-existing non-strict XFAIL and XPASS observations
must remain in the raw result, not be relabelled as ordinary passing tests.

## Exact historical dependency evidence

Read the target's
[requirements.txt at the pinned base](https://raw.githubusercontent.com/psf/requests/36453b95b13079296776d11b09cab2567ea3e703/requirements.txt),
[setup.py](https://raw.githubusercontent.com/psf/requests/36453b95b13079296776d11b09cab2567ea3e703/setup.py)
and [tests/conftest.py](https://raw.githubusercontent.com/psf/requests/36453b95b13079296776d11b09cab2567ea3e703/tests/conftest.py)
as data only. No target import, setup script, installation or tests were executed
by this audit.

| Source | SHA256 of fetched exact bytes |
| --- | --- |
| requirements.txt | `0365012e4cebaafab10f3e0c35336e6346bb4b679286b62ad8ea0d741b522e25` |
| setup.py | `f9bcd0ac4883ca0a4a5ade530ad5ce97a0b82e77b475c4b1e9a7a2cb52d24037` |
| tests/conftest.py | `490e783ae9673a20159da3d49f907b9ab01a63e06f28b33401f7f70be499dc9b` |

The development requirements pin pytest2.8.7, pytest-mock0.11.0,
pytest-httpbin0.2.0, httpbin0.4.1 and Werkzeug0.11.4. Setup instead specifies a
lower pytest bound and a different pytest-httpbin pin. These are historical
declarations, **not** proof that their complete transitive environment runs on
the current Python3.9 image. Do not blindly install the old requirements on the
host or replace the already locked real-service venv. A prospective historical
runner image needs its own Python/dependency compatibility evidence and hashes.

## Acceptance boundary

Keep the protected source/test patches, requirement and regression IDs unchanged.
Require a fresh immutable derived image, source identity, full target dependency
inventory, independently reviewed fixture/network receipts, all original logs and
cleanup evidence. Only a new base FAIL / gold complete PASS / wrong requirement
FAIL triple with preserved regressions can accept this task. No target suite is
to be shortened to its one requirement merely to remove the exposed environment
failures. This audit changes no evaluator, fixture, acceptance rule or budget.
