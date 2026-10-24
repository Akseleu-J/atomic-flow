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

### Live public pilot on one task (24 October)

A bounded live pilot ran on `sympy__sympy-21614` with the fixed model
`google/gemini-3.1-flash-lite-preview` through the Kaggle model proxy of the
owner's account (sequential requests only; every run's reported cost matched
the observed Kaggle quota delta exactly; total spend this day was about
US$0.036 of the US$10 daily quota). Both arms ran on the same task with
per-run caps (calls, output tokens, wall seconds, reported cost stop);
absolute per-arm limits are recorded inside each `report.json` and were NOT
identical across retries — the atomic arm needed larger wall/input-estimate
bounds after measured infrastructure bottlenecks, so this remains an exposed
development pilot, not a matched comparison.

Findings, in order of discovery:

1. **Parser strictness bug (fixed).** The baseline arm's first two runs
   returned a valid patch wrapped in a ```json fence, which `parse_patch`
   rejected (it accepted only ```diff or bare JSON). The tool now accepts any
   single fenced block; regression tests cover json/diff/plain fences and
   scope/shape refusals. Raw model output that exposed this is preserved at
   `stage4-live-pilot-debug-parser/raw-response.txt`.
2. **CRLF clone corruption on Windows (fixed).** The host Git for Windows
   system config sets `core.autocrlf=true`; every checkout written by
   `checked_git` (pilot clones and grading clones) was byte-different from
   the committed blobs (LF→CRLF). In the atomic arm this reached the changeset
   workspace (default copy backend copies bytes faithfully, so the CRLF came
   from the source clone) and broke patch-context matching. `checked_git` now
   passes `-c core.autocrlf=false`; a byte-identity regression
   (`test_checked_git_checkouts_are_byte_identical_to_blobs`) fails on an
   autocrlf=true host without the fix. The framework's own Git isolation
   (`GIT_CONFIG_NOSYSTEM`, null global config) was already correct.
3. **Deep output paths exceed Windows MAX_PATH.** The atomic arm's changeset
   workspace under `validation/import-20261024/...` exceeded 260 characters;
   runs 2026-10-24 now use a short output root (`C:/Users/<user>/.cache/...`)
   and only compact evidence (reports, attempts, task state, journals, run
   JSONs — no workspace copies) is committed under `validation/`.
4. **Measured infrastructure bounds.** One full SymPy tree build takes ~179 s
   / 44,379 nodes on this laptop; the TaskRunner resume path builds the tree
   twice (workspace at resume, project at publication), and tree builds are
   not interruptible by the pilot's cooperative deadline — actual wall time
   exceeded the deadline while model sends stayed bounded.
5. **The small model does not complete the atomic tool protocol within 6
   calls.** In the final run (artifacts preserved: `stage4-live-pilot-atomic-attempt6/`)
   the planner produced an approved single-step plan, but the answerer read
   the first 6 KiB page of `Derivative`, ignored `next_offset` pagination on
   the next read, attempted `edit_node`/`patch_file` with incomplete reads and
   a placeholder `old_text` (`...`), which the framework correctly refused
   (`SourcePreconditionFailed`), and the loop exhausted the call cap before a
   valid edit. No unverified write was applied — the refusal path worked as
   designed.

Results on this one development task:

| Arm | Runs | Calls | Cost | Candidate | Independent grade |
|---|---|---|---|---|---|
| baseline (one direct completion, full source excerpt in prompt) | 3 (2 refused by the fence bug, 1 finished) | 1 | $0.0016 | plausible unified diff | **requirement_failed** — `kind` implemented via kind division raises `TypeError` on the matrix requirement; grader caught it exactly as the wrong-patch calibration predicted |
| atomic (TaskRunner plan→review→approve→tool loop) | 6 (deadline, MAX_PATH, estimator caps ×2, protocol exhaustion) | 1–6 per run | $0.0108 final run | none — no accepted edit | not gradeable (no patch); all charges accounted, quota matched to the cent |

This is a negative pilot result for model quality on this task with this
small fixed model — and a positive validation of the harness: strict patch
admission, pagination-before-edit enforcement, cost accounting and quota
attribution all behaved as specified. It does not measure the framework's
value on easier tasks, with larger models, or across the other 29 corpus
entries. Evidence: `validation/import-20261024/stage4-live-pilot-*` (each run
directory contains `report.json`, `attempts.json`, `kaggle-quota.json` and,
for the final atomic run, the preserved tool-loop artifacts).
