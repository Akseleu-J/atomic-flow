# Production CI repair — 5 November 2026

Work stays on `experimental`, with owner author/committer identity and process-only
owner authentication. No new branch or model/provider call is needed.

## Exact-source native checkpoint

Push run **42027392401** tested `dc89e2af562a66c21c3d2c5ef7db640f0124b282`.
Both retained archives were downloaded, compared with their GitHub SHA256, and
all **115 recorded source hashes per platform** matched exact Git-object bytes.
Linux passed **346** Rust tests, Windows **343**; each retained **1 ignored real
Docker test**, **36 SDK tests**, **8 UI protocol tests**, and the native installed
HTTP/SDK lifecycle smoke with owned server cleanup. No generation, independent
behavioral grading, browser visual QA or full production acceptance follows.
Audits and raw archives: `validation/production-20261105/ci-dc89e2a/`.

Linux archive SHA256: `141a035a6cfd858a7e06cb77d00f125bd18753eec4730881abec9a9937e4f164`.
Windows archive SHA256: `d23bd7f7df850e14663331e4171c4f4f6da97f681bbdfb4ccae549b0a91d9b60`.

## Separate failures that native success does not cover

The general PR workflows expose further failures. Their run `head_sha` is the
branch head, while Actions checks out a synthetic PR merge. Logs/artifact names
record merge `380422d9c5b64f87c81fe1b13f7cc3d60c9dcff4`; it is not labelled as an
exact-head run. Ordinary Python and installed wheel jobs succeeded, while:

- Rust run 42027402330 lacked Linux `libwayland-dev`/`pkg-config` for the native
  folder picker. Its Linux tests did not execute successfully.
- Surfaces run 42027402306 could not resolve its obsolete Node action pin. Its
  Docker test also lacked the explicitly required pre-provisioned immutable
  image. The test failed, but the shell's `tee` pipeline hid that failure from
  the step; the next step failed because `rg` was absent. No real Docker success
  is admitted from this run.
- General run 42027402429 passed real Python container boundaries but failed
  historical grader calibration: a pinned historical commit disappeared from a
  fresh checkout after the owner's identity rewrite.

## Repairs and evidence boundaries

Rust CI installs the native picker development dependencies, retains locked
serialized tests, uses `pipefail`, and retains strict workspace Clippy. Mechanical
lint repairs preserve conditions, errors, layouts and iteration order. Two local
API-arity exceptions retain the existing explicit stream/host-check signatures;
there is no crate-wide suppression or weakening of verification conditions.
Test-only async HOME isolation uses an async mutex across the same critical
section. Focused verification is recorded below.

Surfaces CI uses the established Node action pin and exact `npm ci` lock. The CI
host provisions an Alpine fixture, records its full inspect/RepoDigests and
immutable local image ID, then passes that ID to the native driver. The driver
never pulls an image itself. A receipt marker is emitted from the real ignored
test only after its existing assertions. A strict verifier requires one actually
executed passing test, Docker posture, settled observations, live target witness,
immutable image/container identities, cleanup, and a successful independent host
absence observation. Failed Docker commands cannot mean absence. `pipefail`
propagates the actual test exit. Nine offline verifier tests are protocol tests,
not a substitute for real Docker execution. The input tag can move between runs;
the resolved image identity is retained per run. This is not reproducible release
image/signing acceptance. Firecracker availability is observed without claiming
an executed microVM check.

Historical `benchmarks/pilot/tasks.json` and `checks.py` remain byte-identical.
The active versioned calibration manifest retains old labels and pins each
rewritten export commit to its exact pre-rewrite Git tree. Every tree is checked
before any export. No arbitrary fallback revision or changed grading is allowed.
The report retains this provenance. Local trusted calibration is separately
labelled; Docker calibration still needs the repaired remote run. These remain
six known historical changes, not a holdout or twelve independent tasks.

## Validation status

- Native checkpoint above: accepted in that engineering scope only.
- Docker evidence verifier: 9 offline negative/positive tests passed.
- Calibration provenance and existing evaluation: 19 focused tests passed;
  trusted local calibration retained six base FAIL and six gold PASS, model calls 0.
- Final Windows offline workspace/all-targets Clippy with `-D warnings`: passed;
  `cargo fmt --check` and the fresh workspace suite passed **343 tests / 0 failed /
  1 ignored real-Docker test**. The mechanical/check history is retained under
  `validation/production-ci-20261105/`.
- All five push workflows succeeded on `aa575ddf91cbc34e2ad12f8b65d9d517faa5bec8`.
  Runs 42031833371 (Rust), 42031833362 (Surfaces), 42031833346 (Project and
  container), 42031833476 (interoperability), and 42031839997 (Studio).
- Downloaded four archives and verified their SHA256 against GitHub metadata.
  Both Studio archives and the native Docker archive each matched 124 recorded
  source files against Git objects. Studio passed 346 Linux and 343 Windows Rust
  tests, 36 SDK and 8 UI protocol tests per OS, plus HTTP smoke and server cleanup.
- The separate native Docker test executed once using the provisioned image ID.
  Its receipt and host observation confirmed the network-disabled, read-only
  boundary, live target and container removal. Firecracker was unassessed.
- Docker calibration recorded six base failures and six gold passes with zero
  model calls. Manifest and grader hashes match the tested commit. These cases
  measure historical grader calibration only.
- Archive metadata, receipts and audit are retained in
  `validation/production-ci-20261105/ci-aa575dd/`.

P00–P14/E00–E08 full chapter statuses remain open as recorded in the
[production registry](PRODUCTION_STATUS_2026-11-04.md).
