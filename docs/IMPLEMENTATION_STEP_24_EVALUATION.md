# Package 24: provider usage and reproducible Stage 4 preparation

## Anthropic accounting

The Messages adapter normalizes usage before parsing content and raises
`ResponseShapeError` carrying the received `ChatResult` when content is unusable.
Boolean/string/negative/missing counters are not coerced to known usage. Cache
read and creation counters are included in total input tokens, but are priced
separately from uncached input. Cache tariffs are trusted host constructor data
(`AnthropicCachePrices`), not model/project instructions or a built-in tariff
catalog. No model multiplier is guessed. Missing read/write prices or TTL
breakdown leave cost unknown, as do server-tool charges and a model override
whose configured price belongs to another model. Actual invoices remain the
provider's responsibility; these are declared host estimates.

Primary contract checked on 2026-10-24:
[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
The documented token breakdown separates uncached input, cache reads and cache
creation. The page lists distinct cache TTL tariffs and model exceptions, which
is why this implementation requires explicit prices rather than universal
multipliers. Existing input/output prices must already account for applicable
model/context/service-tier pricing; this change does not discover those tariffs.

Validation uses mocked provider responses only; no provider credentials, live
requests or paid calls. Focused test evidence will be recorded separately.

Recorded local check: **59 passed** across the Anthropic normalization tests,
provider contracts, routed accounting and the missing-usage regression.
`validation/import-20261024/anthropic-usage-focused.txt`; scoped Ruff passed.

## Stage 4

`benchmarks/stage4/corpus.json` contains **30 real public issue/patch pairs from
five Python repositories**, six each from Requests, pytest, pylint, Sphinx and
SymPy. Full base commit SHAs, upstream PR URLs, original issue statements,
FAIL_TO_PASS/PASS_TO_PASS IDs and gold/test patch SHA-256 values are retained.
Gold patches are selected by smallest UTF-8 byte size with an instance-ID tie
break, before any success measurement. Requests/pytest are development repos;
pylint/Sphinx/SymPy are confirmation repos. This is an exploratory public corpus,
not a secret holdout or a statistically powered superiority test. SymPy has a
larger source tree; small patches do not imply small repositories.

Source: [official SWE-bench Lite](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite/tree/b0dde1093fe417d83b7184254edf8199c1f0dff5),
whose dataset revision was observed unchanged before and after export. The rows
API itself is not commit-addressed; manifest page/row hashes pin exported data,
and this distinction is explicit in provenance. Original patches/code remain
subject to their upstream licenses; this package does not assert new license
rights over third-party source. Dataset scripts and shell eval bodies are data
and are never executed by the importer.

Tools:

- `evaluation_build_corpus.py`: deterministic selection from bounded public row
  exports; no imports or execution of dataset code.
- `evaluation_stage4.py`: manifest integrity, source-only candidate admission,
  fresh local clone at the full SHA, independent test patch application, trusted
  evaluator command, required test/report admission, base FAIL/gold PASS/wrong
  FAIL calibration, paired attempt validation and all-task result joining.
- `evaluation_pytest.py`: installed pytest imported under `python -I` before
  project paths are inserted. Required outcomes and nonzero collection failures
  are explicit; zero collected tests never establish success.

Example preparation (offline after the corpus is present):

```powershell
.venv/Scripts/python.exe tools/evaluation_stage4.py benchmarks/stage4/corpus.json --validate-only
.venv/Scripts/python.exe tools/evaluation_stage4.py benchmarks/stage4/corpus.json --prepare --output .local-tools/stage4-prepared
```

The preparation command was executed. It creates thirty model-visible prompt
files containing only source identity, issue and identical limits, separate
host-only oracle target files, and a deterministic alternating/hash-based paired
schedule. It does not invoke either agent. Proposed caps per arm: 20 calls,
60,000 input tokens, 20,000 output tokens, 600 seconds and USD 0.05 declared cost.
These are proposed experiment caps, not authorization to spend or evidence that
an adapter already enforces them. A host runner must enforce the identical caps,
pin provider/model/runtime, preserve every failed/unknown physical attempt and
save actual counters; source-only permission differences must be reported.

For each case, a reviewed host config names an installed interpreter/evaluator,
immutable oracle target JSON and a distinct plausible wrong source patch:

```json
{
  "evaluator": ["ABSOLUTE_PYTHON", "-I", "ABSOLUTE_TOOLS/evaluation_pytest.py", "--workspace", "{workspace}", "--report", "{report}", "--tests", "ABSOLUTE_ORACLE_TARGETS.json"],
  "wrong_patch": "ABSOLUTE_REVIEWED_WRONG.patch",
  "timeout": 180
}
```

```powershell
.venv/Scripts/python.exe tools/evaluation_stage4.py benchmarks/stage4/corpus.json --task INSTANCE_ID --mirror ABSOLUTE_LOCAL_GIT_CACHE --host-config ABSOLUTE_HOST_CONFIG.json --output ABSOLUTE_REPORT_DIRECTORY
```

The command creates separate base/gold/wrong clones. It never downloads code,
installs packages or launches a model. The historical repositories require
reviewed per-version Python/dependency environments; the laptop's current Python
3.12 environment is not assumed compatible. The initial preparation did not
establish those environments or thirty real-case calibrations. Source caches and
wrong patches must be independently reviewed before those runs. Grading executes
repository code and therefore belongs on a trusted disposable worker with the
appropriate OS isolation; subprocess timeouts alone are not a sandbox or a
guaranteed descendant-process cleanup mechanism.

Paired JSON admission retains missing arms, failures, timeouts, quota failures,
unknown charges and human interventions. Cost per success includes failed
attempt costs; unknown/missing costs leave it null. Candidate success requires
matching source/patch identities, independent oracle evidence and calibration;
an agent's own success message is insufficient. `--paired-report INPUT.json
--output REPORT.json` joins saved attempts, grades and calibrations without
provider calls. Agent-generation adapters and the actual sixty arm runs remain
separate work; this package does not imply they have happened.

Local infrastructure validation: **7 tests passed** including a real temporary
Git base/gold/wrong round trip on a synthetic calculation fixture, followed by
**8 passed / 1 expensive round-trip test deselected** for the final prompt-export
and report checks. Logs: `validation/import-20261024/stage4-harness.txt` and
`stage4-harness-additional.txt`. These are harness tests, not results on the
thirty real issues, and not measured LLM quality. No live models, provider
credentials, paid calls or Docker runs were used.

### One real public issue calibrated

The first actual corpus calibration now passes for `sympy__sympy-21614`, exact
base `b4777fdcef467b7132c055f8ac2c9a5059e6a145`. The source bug is the missing
`Derivative.kind` propagation. Its original upstream test patch was applied to
fresh clones independently of each candidate. The complete affected test file
was run, with explicit host aliases for the dataset's seven short test IDs.

- Base: requirement failure, exit 1 (`UndefinedKind` instead of `NumberKind`).
- Gold: verified success, exit 0, including every original required ID.
- Plausible wrong patch: scalar-only kind propagation, requirement failure,
  exit 1 on the matrix requirement.

Evidence: `validation/import-20261024/stage4-public-pilot-v2/calibration.json`,
three raw logs/oracle JSON files, `wrong.patch` and `host-config.json`.
`tools/evaluation_public_pilot.py` reproduces this check from an already fetched
local source cache and installed interpreter. Environment: Windows Python
3.12.14, pytest 8.4.2, mpmath 1.2.1 and setuptools 75.8.2 (providing the
`distutils` compatibility module absent from Python 3.12). The first attempt
failed before test collection due to missing distutils and is retained at
`stage4-public-pilot/`; it is an environment failure, not a failed fix.
The oracle adapter now explicitly distinguishes collection/environment failures
from requirement failures. No repository source was changed to resolve the
environment incompatibility.

This public issue and gold are now exposed during grader development. Treat it
as a development pilot and exclude it from any blinded confirmation statistic,
despite the original repository-based confirmation split. Other 29 corpus cases
and their historical environments remain uncalibrated. The generic installed
pytest oracle also cannot establish correctness of pytest's own source: those
cases require a separately reviewed self-hosted runner so an installed pytest
does not mask the target source.

Candidate grading after generation is separate and uses the same host oracle:

```powershell
.venv/Scripts/python.exe tools/evaluation_stage4.py benchmarks/stage4/corpus.json --task sympy__sympy-21614 --mirror .local-tools/stage4-sympy --host-config validation/import-20261024/stage4-public-pilot-v2/host-config.json --candidate-patch ABSOLUTE_CANDIDATE.patch --label atomic --output ABSOLUTE_GRADE_DIRECTORY
```

The generator sees the prepared issue/source prompt and permitted source code;
it must not receive gold/test patches or host oracle files. Candidate grading
never calls providers. A successful base/gold/wrong calibration measures grader
sensitivity for this case, not model success or framework superiority.

Final small regression recheck after compatibility/report hardening and updated
CI fixtures: **47 passed / 1 expensive synthetic round-trip deselected** in
`validation/import-20261024/step24-fixture-recheck.txt`; scoped Ruff passed.
