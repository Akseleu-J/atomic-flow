# pytest6116 public skip and xfail review — 2026-10-29

Scope: independent source and metadata review of the 125 individual nonpassing reports in `validation/p07-budget20/pytest6116-py38-node-reports-v1/nonpassing-observations.json`. The underlying full diagnostic [CI 40893566658](https://github.com/Akseleu-J/atomic-flow/actions/runs/40893566658) on `4fe8c09675020b1b7300b5c19f14ce12e4ed3c0e` observed 2617 unique completed public nodes, 2492 passes, 115 skips and 10 xfails. Archive SHA256 is `af20ec3755c405f96b35991111e5d6c810fdc7163deae217c14eb5c632c02a6e`. This review is not an allowlist and does not grant ordinary admission.

The 115 ordinary skips were reconciled by their exact `nodeid`, test phase and reported `longrepr` with the source-line locations. The frozen public base is `e670ff76cbad80108bde9bab616b66771b8653cf`; 26 relevant public source files, including each reason location and each xfail test file, were fetched from that base and checked as Git blobs and sizes against `benchmarks/stage4/p07-pytest6116-public-tree-v1.json`. Observed reasons alone do not establish that a future candidate should be allowed to skip or that its environment is equivalent.

## Ordinary skips: observed causes

| Count | Reported cause | Review and implications |
|---:|---|---|
| 40 | Missing `pexpect` | `src/_pytest/pytester.py:1249` calls `pytest.importorskip("pexpect", "3.0")` in the spawn helper. These cover debugger and interactive-process behavior; retry with an independently pinned and qualified `pexpect`/`ptyprocess` closure and PTY capability. This review does not choose versions or claim they work. |
| 19 | Missing `nose` | `testing/test_nose.py:5` calls `pytest.importorskip("nose")` from module setup. These are nose compatibility tests; absence is not proof that functionality passed. |
| 18 | Missing xdist or its plugin | 11 import `xdist.boxed` at `testing/test_runner.py:424`; six use `pytest.importorskip("xdist")`; one checks the loaded `xdist` plugin. These are distinct gates; installing an arbitrary current xdist does not resolve them all. |
| 13 | Missing `numpy` | Twelve reported in `testing/python/approx.py` and one in `testing/python/fixtures.py`; they cover numeric array and fixture behavior. A compatible binary wheel and its dependencies must be qualified for Linux/Python 3.8 before execution. |
| 8 | Missing `twisted.trial.unittest` | `testing/test_unittest.py:416` imports it in class setup. Twisted requires its own Python 3.8-compatible dependency closure and subprocess/runtime checks. |
| 5 | Missing `mock` | Four nodes are in `testing/python/integration.py`, one in `testing/test_doctest.py`. `setup.py` lists `mock` in the `testing` extra; a missing testing extra is not evidence that these tests are satisfied. |
| 4 | Missing `requests`, `decorator`, `jinja2`, `argcomplete` | One each. The argcomplete reason originates from a failed shell-completion probe at `testing/test_parseopt.py:320`; installation alone must not be assumed to establish shell integration. |
| 2 | Missing `lsof` | `testing/test_capture.py:889-895` attempts the external executable and skips on several errors. Provide and qualify the executable and permissions; the current skips do not prove descriptor behavior. |
| 3 | Platform conditions | One Windows console case (`testing/test_capture.py:1312-1314`), one Windows-only user case (`testing/test_tmpdir.py:212`) and one case-normalization case (`testing/test_conftest.py:281-283`). The last predicate checks `os.path.normcase`, not a direct runtime probe of filesystem case sensitivity. A policy must bind the exact node, phase, source predicate and Linux environment, without generalizing to other skips. |
| 3 | Explicit source skip | The three `change_default` parameters of `testing/test_warnings.py:500-503` are marked skipped with a source-declared reason. Any policy must bind all exact IDs and the immutable test source; preserve a future unexpected outcome as a distinct result. |

The missing-dependency rows total 107; missing-tool, platform and source-disabled rows total eight. These categories explain the retained run and are not a decision that any skip is permitted. The source `tox.ini` names optional `py37-{pexpect,xdist,twisted,numpy,...}` environments and `pytest-xdist>=1.13`; this is a useful source declaration, not a reproducible modern closure or a claim that every optional combination was historically run together.

## The xdist compatibility trap

The 11 `xdist.boxed` nodes are especially easy to misclassify. Publisher archives from PyPI were downloaded and checked against the publisher's SHA256:

- [`pytest-xdist 1.18.2` source archive](https://files.pythonhosted.org/packages/e9/31/bac9fbf85fc76590cf53a6df3067fc384257d2cb193fac47c337fc1af5b0/pytest-xdist-1.18.2.tar.gz), SHA256 `10468377901b80255cf192c4603a94ffe8b1f071f5c912868da5f5cb91170dae`, contains `xdist/boxed.py`; the PyPI release lists a source archive rather than a publisher wheel. Its compatibility with this Python 3.8/pytest base is untested, and any wheel built from it needs an isolated reproducible build and independently verified provenance.
- [`pytest-xdist 1.28.0` wheel](https://files.pythonhosted.org/packages/9f/ea/227e5ec64bd5deb31011fb2a2557d62d5c06c4793a8bfc4f47d398714e1c/pytest_xdist-1.28.0-py2.py3-none-any.whl), SHA256 `b0bb4b0293ee8657b9eb3ff334a3b6aac4db74fd4a86b81e1982c879237a47eb`, has no `xdist/boxed.py`; its metadata requires `execnet`, `pytest-forked` and `six` as well as pytest. The separately examined `pytest-forked 1.0.2` wheel contains `pytest_forked/__init__.py` with `forked_run_report`, but does not itself ship `xdist/boxed.py`. No automatic import compatibility is asserted.
- [`pytest-xdist 2.0.0` wheel](https://files.pythonhosted.org/packages/8b/c0/14d9ac24ae22a2dd2958c2d52892d3905ea70b0db3bac3c002a0c536fe17/pytest_xdist-2.0.0-py2.py3-none-any.whl), SHA256 `e5d9973083ffd809f1b895e4677f4d0c5b815f31953bdb4a468f74ce9877af31`, declares `pytest>=6.0.0`, conflicting with the pinned 5.2.2 distribution used by the current 3.8 candidate. Its presence of other dependencies and plugin entry points must be audited too.

Additional checks of publisher archives found `xdist/boxed.py` in source releases 1.15.0, 1.17.1 and 1.18.2, and did not find it in 1.19.0 or 1.19.1 source releases or the examined 1.20.1, 1.22.5, 1.23.2, 1.24.1, 1.25.0, 1.26.1, 1.27.0, 1.28.0, 1.29.0, 1.30.0, 1.31.0, 1.34.0 and 2.0.0 wheels. This bounded survey is not proof about every release or runtime import hook. Adding xdist exposes pytest plugin entry points and can change the whole run, not just the 18 previously skipped nodes; qualify it in a separate fixed profile and repeat the complete native inventory.

## Ten xfails

All ten were cross-checked against public source. Nine have source `pytest.mark.xfail` declarations, including a parameter-level mark in `testing/test_doctest.py:964`; `testing/test_pytester.py:24` instead calls `pytest.xfail()` during the test. The source `tox.ini:137` has `xfail_strict=true`. A future report must retain exact IDs and phase, the observed xfail reason and source binding, distinguish XFAIL from XPASS, and apply the pinned source's strict semantics. Do not turn xfails into generic successful assertions or assume that every later candidate has the same xfails.

## Gates before ordinary admission

1. Keep the full source-bound public collection, 2617-node evidence and individual reports; independently check each unexpected or missing node, phase, failure, skip and xfail against a frozen typed policy.
2. Qualify missing dependencies and system capabilities in isolated, separately versioned images. Verify Python and OS compatibility, wheel/source provenance, complete active dependency closure, installed bytes and plugin discovery; preserve effects of plugin autoload and environment variables as part of the profile. Do not fetch or build untrusted target code on the host.
3. After dependency changes, rerun the complete public inventory with no unapproved skip or truncation. Explicitly justify truly platform-inapplicable and source-disabled nodes with exact predicates and preserve their actual status; do not suppress warnings or exclude tests merely to obtain a pass.
4. Demonstrate a genuine controlled failure, repair and fresh full pass under the admitted envelope, then integrate and verify TaskRunner, publication and graph synchronization. The observed scripts, green diagnostic or a completed runner do not constitute an independently graded model solution.
