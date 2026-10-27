# P07 pytest7168: native runner crash remains an open calibration

Audit date: 27 October 2026. No model calls or new remote executions were made
for this audit. The existing calibration is **not accepted**. Its complete
base/gold/wrong evidence is preserved; no result classifier was changed.

## Exact evidence

- Instance: `pytest-dev__pytest-7168`.
- Calibration runtime: `71c97802544472e105035c1561fb3d9259a4e582`.
- [GitHub workflow 40309674235](https://github.com/Akseleu-J/atomic-flow/actions/runs/40309674235).
- Artifact: `12644241476`, retained at
  `validation/p07-budget20/calibration-v2/12644241476.zip` with its metadata.
- Independently recomputed archive SHA256:
  `9a82f0445c833f5e3f373a91cee025a0336b6010d02c75ffb138bd3f3e757f72`.
  This equals the GitHub metadata digest. ZIP entries were decoded with CRC32
  and size checks using the local archive reader.
- Target base: `4787fd64a4ca0dba5528b5651bddd254102fe9f3`.
- Image:
  `swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-7168@sha256:9eef4ab057f209925aaf40a4f644f82c2aa3b525e02e341d800a9794225fa68e`.
- Harness: SWE-bench 3.0.15. Native command:
  `pytest -rA testing/io/test_saferepr.py`.
- Actual runner imports in all rows resolve to `/testbed/src/pytest/__init__.py`
  and `/testbed/src/_pytest/__init__.py`, Python 3.9.20.

The frozen `benchmarks/stage4/corpus-p07-v2.json` places **all eleven** test
IDs in FAIL_TO_PASS and has an empty PASS_TO_PASS list. In particular, the
original ten tests are not ten separately declared P2P obligations. The
protected test patch digest is
`ae6288ce2b11af1e23717c3234930c64a88181491124fa236253fdd7634ccc18`.

## Observed outcomes

| Row | Native P07_TEST_EXIT | Complete parsed test outcomes | Existing classification |
| --- | ---: | --- | --- |
| base | 3 | none; `tests={}` | infrastructure_error |
| gold | 0 | eleven PASSED | verified_success |
| wrong | 1 | eight PASSED, three FAILED | requirement_failed |

All three wrapper process exits are zero because the evaluation shell captures
the native exit marker and then restores the protected file. That wrapper exit
cannot replace `P07_TEST_EXIT`. All rows report `cleanup_exit=0`.

The base log collects eleven items and ends with a footer saying ten passed,
followed by `P07_TEST_EXIT=3`. It never emits a complete final outer pytest
short-summary inventory. Treating the footer as a complete eleven-test
observation would invent an outcome for the missing test.

The base traceback identifies the target bug itself: while formatting a failure,
`/testbed/src/_pytest/_io/saferepr.py:23` reads
`obj.__class__.__name__`. The protected
`/testbed/testing/io/test_saferepr.py:166` implementation of
`SomeClass.__getattribute__` raises `RuntimeError`. The same broken safe-repr
path is reached while pytest constructs the failure report, and the native
runner exits with INTERNALERROR rather than a normal assertion report. This
is evidence of a target-triggered runner failure, not an observed ordinary
FAILED test status.

The gold patch uses `type(obj).__name__` and completes all eleven tests. The
wrong control emits an ordinary assertion failure for `test_exceptions`,
`test_baseexception` and `test_broken_getattribute`; the other eight pass.
Its `regressions_passed=true` field is vacuous for the empty P2P list and does
not establish a separate regression suite.

Raw log SHA256 values were independently recomputed and match each archived
row receipt:

| Log | SHA256 |
| --- | --- |
| base | `385f73f98336f066cf3bdcc834169112d95c5e01612c1462541ffc976cb9f87c` |
| gold | `3dbbf8fc30d89e30ae64e8fc751948143cd228e33e902b1c44157cfdacf50aed` |
| wrong | `8c1d18058cd35aa5e8d62af0a9fe646cd507d6413c1e222fd6debd6c488506e2` |

## Required next implementation

A prospective protocol can isolate each of the eleven frozen node IDs into a
fresh native runner child, after separately sealing the complete collection
inventory and target/import identities. It must retain protected test bytes,
the original image/base, resource ceilings, logs, child exits and cleanup
receipts. The ten normally completing children would provide individual
observations; the broken-getattribute child needs an explicit, narrowly
specified target-runner-crash negative receipt.

Such a receipt must distinguish this exact protected test-body/reporting crash
from failed collection, missing children, import errors, timeout, truncation,
wrong source, unknown cleanup or arbitrary internal errors. None of those may
be converted to an ordinary assertion failure or a successful candidate. Gold
must still complete every required test successfully. A candidate with any
runner error remains non-successful. A new complete base/gold/wrong run is
required before admitting this prospective protocol.

This protocol is **not implemented or calibrated by this audit**. Existing
manifest labels, original logs and classifications remain unchanged.
`pytest-dev__pytest-7168` stays OPEN and contributes no additional accepted
calibration to the current inventory.

## Prospective reporting-only profile

A separate `p07_pytest7168_calibrate.py` now preserves the complete file and all11 IDs and changes only `pytest -rA` to `pytest -rA --tb=short`. The pinned source shows that short traceback formatting avoids repr_args(), the recursive reporting path. Seven runner/config/protected-file hashes are checked inside the image before tests. Strict classification remains unchanged: INTERNALERROR cannot be accepted. Local contract tests:23PASS,RuffPASS. A fresh actual base/gold/wrong run remains required; this is no acceptance claim.

## Actual reporting-only calibration accepted,27 October2026

Run40448428457 on9c84294f1d3080ee2c7836e4c1d45880648ef0cb completed successfully. Artifact12709446550 SHA256 bfc31475dcdbc0ca66f8fbf0b6080e0a0710004ccb9824fcac46ef03a2b54248 checked independently, along with all3rawlogs. All11 required IDs observed in every row; base fails broken_getattribute with nativeexit1, gold11PASS exit0, wrong3FAIL8PASS exit1. Cleanupexit0 for all. P2P remains empty; no extra regression suite claimed. No classifier change or target/test mutation. Historical failed attempt above remains retained. Inventory24/30; sixRequests OPEN, fullP07 OPEN.
