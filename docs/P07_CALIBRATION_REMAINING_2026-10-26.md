# P07: remaining calibration audit, 26 October 2026

This is a source/evidence audit, not a new calibration run. The complete v2
batch is workflow `40309674235`, source
`71c97802544472e105035c1561fb3d9259a4e582`. Its preserved reports are
`validation/p07-budget20/calibration-v2/summary.json` and the 30 task JSONs;
the corresponding artifact ZIPs contain `result/{base,gold,wrong}.log`.
Exactly **10/30 are accepted; 20 remain OPEN**. Later evaluator fixes cannot
retroactively change these recorded outcomes. No model calls were made by this
audit, no tests were run, and no acceptance rules were relaxed.

The 10 accepted tasks are Sphinx 7738/8595; Pylint 5859/6506/7228/7993;
pytest 6116; SymPy 16988/20212/21614. The earlier 6/30 wording in
PROJECT_CONTEXT is a historical snapshot and does not describe this v2 batch.

## All 20 remaining tasks

| Task | Evidence in the v2 reports/logs | Required next experiment; acceptance remains OPEN |
| --- | --- | --- |
| requests-3362 | Gold has network/fixture errors; external HTTPBin is unavailable. Gold log also contains `10.255.255.1:80` producing immediate `ENETUNREACH`/ConnectionError. | Real HTTP+HTTPS HTTPBin with a verified CA in a derived image, then all three rows. Separately inspect the connect-timeout test: an unroutable-IP timeout may require a reviewed isolated networking profile, since `network=none` changes its behavior. Fixture health alone is insufficient. |
| requests-1963 | Gold still fails six requirement tests and many regressions; HTTPS `httpbin.org/delay/10` fails DNS. | Real local HTTPBin preserving digest auth, cookies, redirect history, delay and multipart semantics; rerun all rows without external egress. |
| requests-863 | Gold requirement is fixed but many HTTPBin-dependent regressions fail; logs show `httpbin.org:80`. | Same real fixture, version-compatible serving dependencies outside the target environment; retain unchanged protected assertions. |
| requests-2148 | Gold requirement/regressions fail around HTTPBin HTTP/HTTPS, including delay tests. | Same fixture; audit exact failing protected tests after first full isolated run rather than assuming HTTPBin repairs every failure. |
| requests-2317 | Gold still fails requirement tests and regressions; DNS/network errors surround HTTPBin requests. | Same fixture, then distinguish genuine method/text behavior from network effects by complete base/gold/wrong results. |
| requests-2674 | Gold requirement/regressions fail; `/status/500` and HTTPBin DNS failures, plus a separate ClosedPoolError assertion. | Same fixture; separately validate historical exception-message expectations. Do not normalize target exception messages to pass. |
| sphinx-8506 | Gold fixes the required cmd-option case but `test_productionlist` has 3 warning lines instead of 2. Extra warning: `sphinx.addnodes` node `meta` already registered. Installed Docutils 0.21.2, Pygments 2.18.0, historical Sphinx 3.4. | Prospective derived dependency profile, beginning with Docutils 0.16; record complete lock and rerun all rows. It is a hypothesis, not a proven fix. |
| sphinx-8713 | Gold fixes class-reference requirement but `test_token_type_invalid` sees 2 warnings instead of 1; same extra registered-meta warning. Historical Sphinx 4.0, Docutils 0.21.2. Wrong currently also does not demonstrate the requirement failure. | Test historical Docutils profile first, then reassess the negative control independently; a gold pass alone does not accept this task. |
| sphinx-8474 | Gold has many HTML snapshot failures and `test_html_warnings`; Docutils 0.21.2 / Pygments 2.18.0 against historical Sphinx 3.4. | Start with Docutils 0.16 and Pygments 2.7.4 in a locked derived image; preserve HTML expectations. Change one dependency at a time if diagnosis needs attribution. |
| sphinx-8721 | Gold fixes epub requirement but `test_viewcode` HTML differs: Pygments emits an additional whitespace span before a docstring. Pygments 2.18.0, historical Sphinx 3.5. | Probe Pygments 2.7.4, independently of Docutils where possible; compare original and derived full logs. No HTML whitespace rewriting. |
| pylint-7080 | Gold fixes ignore-path requirement but two `test_generate_toml_config*` regressions fail: TOMLKit `Can't add a table to a dotted key`. Installed tomlkit 0.13.2, astroid 2.11.7. | Probe tomlkit 0.11.4 in a locked derived image. Keep generated-config assertions; freeze all dependencies and rerun all rows. |
| pylint-7114 | Gold fixes nested-module requirement but wildcard-import init/non-init tests still fail. Installed astroid 2.12.13. Wrong adds numerous unrelated failures. | Inspect fixture resolution and exact target dependency constraints, then prospectively probe astroid 2.12.2 if permitted. Version drift is a hypothesis, not yet a proven cause. Narrow wrong mutation separately after environment acceptance. |
| sympy-13971 | Gold has nine native ERRORs; Python 3.9 with SymPy 1.1.2 exposes collections deprecation-as-error and AST `ValueError: Name node can't be used with 'False' constant`. | A historical Python runtime, e.g. a separately locked Python 3.6 image after checking target support. Updating/warning-filtering the target code is not an environment fix. All three rows must be rerun. |
| sympy-18057 | v2 adds protected `test_non_string_equality`; base/wrong fail it. Gold has no failing parsed tests but all rows are infrastructure_error because the native footer contaminated the final parsed test. | Fresh calibration on evaluator b44 or later, which strips native aggregate status footers. Require complete test coverage and gold success; do not relabel the old report. |
| sympy-17139 | Gold succeeds. Base has TypeError from protected test body into changed production code; wrong also creates unrelated failures (`test_TR5`, `test_TR6`, `test_fu`, etc.). | Fresh calibration of source-bound native requirement-exception receipt implemented in b44. A new narrowly targeted wrong control is also required; inversion of the general real-exponent guard is too broad. |
| pytest-7168 | Gold succeeds; wrong fails the three protected saferepr cases without regressions. Base runner crashes while formatting the failing object: INTERNALERROR RuntimeError, protected `test_saferepr.py:166` → changed `_io/saferepr.py:_format_repr_exception` accesses `obj.__class__`. Footer misleadingly says 10 passed; exit receipt is 0, parsed tests empty. | Define a separate, independently reviewed, source/test-bound runner-crash negative receipt, or a genuinely independent runner preserving the protected semantics. This is not an ordinary assertion failure, a dependency failure, or a completed native exception receipt. Keep current fail-closed verdict until then. |
| pytest-5227 | Base fails required logging tests, gold passes, current wrong also passes all tests. Its mutation merely removes logger name from DEFAULT_LOG_FORMAT. | New prospective controls version with a requirement-relevant logging/newline defect. Preserve old wrong-pass evidence; require new wrong to fail a protected requirement and preserve regressions. |
| pytest-7432 | Base/gold behave correctly. Wrong `if False and (` disables general skip handling and breaks many unrelated skip-reporting cases. | Narrow negative mutation to the xfail-with-skip interaction under test; retain the ordinary skip branch. Fresh versioned base/gold/wrong run. |
| pytest-11148 | Base/gold behave correctly. Wrong changes `suppress(KeyError)` to `suppress(TypeError)`, breaking many general importlib tests beyond the requirement. | Narrow the cache-recursion/remembered-import defect rather than globally suppressing the wrong exception. Fresh prospective control and full regression check. |
| pytest-11143 | Base fails protected non-string-docstring case, gold passes. Wrong treats bytes as docstrings; requirement passes while unrelated `test_place_initial_imports` / `test_dont_rewrite` fail. | New control must mishandle only the non-string Constant being fixed while preserving valid string docstrings. Current wrong is not a valid negative example. |

## Dependency candidates are experiments, not accepted locks

The proposed exact package versions above exist in primary package metadata:
[Docutils 0.16](https://pypi.org/pypi/docutils/0.16/json),
[Pygments 2.7.4](https://pypi.org/pypi/Pygments/2.7.4/json),
[tomlkit 0.11.4](https://pypi.org/pypi/tomlkit/0.11.4/json),
[astroid 2.12.2](https://pypi.org/pypi/astroid/2.12.2/json).
Their existence does not prove compatibility with a target base commit.
Retrieve and inspect that commit's dependency declarations before constructing a
derived image. These historical dependencies belong only inside the isolated
benchmark runtime; they are not proposed production application dependencies.

For each accepted environment repair retain original image digest, base commit,
derived immutable image identity, complete wheel hashes/lock, Python identity,
source/test/control hashes and full original output. Do not alter a protected
test, filter an unexpected regression, fabricate an HTTP endpoint, or enable
unrestricted networking to make calibration green.

## Order and boundaries

1. Rerun the parser-only SymPy 18057 case on current published evaluator.
2. Run independent environment lanes: real Requests fixture, Sphinx dependency
   profile, Pylint dependency/fixture diagnosis, historical SymPy 13971 runtime.
3. Prepare versioned, source-hash-bound new controls for pytest
   5227/7432/11148/11143 and SymPy 17139; inspect Sphinx 8713 and Pylint 7114
   again after their environments work. Never overwrite v2 evidence.
4. Review the pytest 7168 runner-crash contract separately. It must never turn a
   runner crash into candidate success or mark an interrupted test suite complete.
5. Run all affected base/gold/wrong triples from clean containers; only then
   regenerate an aggregate acceptance table. No inference is required for this.

All 30 calibrations are a prerequisite to the full matched evaluation. Their
completion would still not close the full P07 agent-loop, holdout and ablation
gates. The source-only Sonnet campaign is a development diagnostic, not evidence
that the full Atomic public task loop beats a capable baseline.
