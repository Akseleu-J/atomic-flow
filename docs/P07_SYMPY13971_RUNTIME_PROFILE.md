# P07 SymPy13971: prospective private Python3.6 runtime profile

The new `benchmarks/stage4/p07-sympy13971-runtime-profile.json` binds the exact
SymPy task/base, official image, qualified donor, transplant layout and verified
mpmath wheel. It remains **ungraded and OPEN**. No target import/setup, package
installation, subprocess, tests, CI dispatch or paid model call ran in preparing
this profile. Oracle requirement names are retained only in the profile's
explicitly non-model-visible oracle section; no protected test content is copied.

## Exact target and observed original problem

- Task: SymPy13971, version1.1.
- Target base: `84c125972ad535b2dfb245f8d311d347b45e5b8a`.
- Official image:
  `swebench/sweb.eval.x86_64.sympy_1776_sympy-13971@sha256:cc0369f168735cf7b9add74f5d3a50cf3c51a03549b798fa853181633ed2921a`.

The earlier official-image run used Python3.9.20, not the prospective3.6 donor.
Its source-bound calibration records are in
`validation/p07-budget20/calibration-v2/sympy__sympy-13971.json`, original artifact
12645106603. The original ZIP SHA256 is
`285914782b6ad16e4a068310e8597e0a69610878271debc8c245834677c4f3d1`.
The prior historical-AST/collections errors cannot be relabelled as requirement
failures; they motivate an independently tested runtime experiment.

## Qualified donor and transplant, pending dependency/native-runner acceptance

Donor workflow40376444491 on runtime8516b05 reports compatible CPython3.6.15,
including SSL, ctypes, SQLite, bz2/lzma, historical AST Name(False) and
collections.Mapping probes, with confirmed cleanup. Its exact donor is
`library/python@sha256:d59ee182c4629dd33b240b9bac1a6ff44276e79e66117eba17aed4016845d3b2`,
imageID
`sha256:dc468cf655a42fccd7f2bb1549e9d43d69c1e0a7dbff5457fb45de19e124ed6d`.
The retained report is `validation/p07-budget20/legacy-donor-probe/result.json`.

This proves the original donor's limited qualification. It does not prove a
transplanted interpreter, mpmath import, SymPy import or any target test passes.
The transplant tool copies the runtime to `/opt/p07-python36` and donor shared
libraries to `/opt/p07-donor`, preserving official task-image ancestry and target
source. Its fixed `/opt/p07-runtime/python36` launcher uses the donor dynamic
loader, an explicit private library path and Python `-I -B`. Transplant startup,
stdlib paths, mapped libraries, source identity and cleanup require their own
actual receipt before task admission.

That limited transplant receipt is now available: workflow40377315871 on
runtime1146f07 reports `transplant_stdlib_qualified`, unchanged target source,
private-prefix stdlib/sys.path and privately mapped ELF libraries, with confirmed
cleanup. The verified report is
`validation/p07-budget20/legacy-transplant-real/result.json`; the profile records
its exact SHA256 and derived imageID. This advances the transplant gate only:
mpmath/SymPy imports and the native target suite still have not been demonstrated
by this qualification.

## Concrete mpmath candidate

The [exact base setup.py](https://raw.githubusercontent.com/sympy/sympy/84c125972ad535b2dfb245f8d311d347b45e5b8a/setup.py)
declares **mpmath>=0.19**, not an exact0.19 requirement. Source SHA256 is
`061482f8755b921d4a4e6c638d06cee7103d5085aa916b1bd3122697d351ca8a`;
the minimum and install requirement are on lines38 and387.

Prospective mpmath1.2.1 satisfies that bound. Its universal wheel is
`mpmath-1.2.1-py3-none-any.whl`,532,568bytes, SHA256
`604bc21bd22d2322a177c73bdb573994ef76e62edd595d17e00aff24b0667e5c`.
The downloaded bytes matched
[primary PyPI metadata](https://pypi.org/pypi/mpmath/1.2.1/json). Exact filename,
URL, metadata-response hash and actual wheel-METADATA hash are in the profile.

Actual wheel metadata says Name=mpmath and Version=1.2.1 and explicitly classifies
Python3.6. It has **no Requires-Python header**; do not invent a version bound.
Every Requires-Dist is gated by an unselected develop/tests extra, so this
no-extra runtime candidate has no active package dependencies. No pytest or
development tools are needed merely because their optional metadata appears.
Wheel tags/classifiers and satisfying SymPy's lower bound do not prove runtime
compatibility; verify the installed private-prefix package identity inside the
accepted isolated worker before running SymPy.

Install only the hash-verified wheel offline in the private runtime. Do not run
old SymPy setup or install target source on the host; do not read the old task
image's Python3.9 site-packages as a substitute for the private dependency.

## Preserve the actual native command and fix startup explicitly

The official specification command is:

```text
PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose
```

The retained actual evaluation script adds the ordinary selected file
`sympy/printing/tests/test_latex.py`. Its script SHA256 is
`7a7219c8e7074439f1b637c50a13ce9e1e3d8a96c5ce86420591d88db87a933d`.
The oracle obligation remains one FAIL_TO_PASS plus102 PASS_TO_PASS IDs, recorded
unchanged in the non-model-visible profile.

Directly invoking the current private launcher with bin/test is **not yet an
executable accepted path**:

1. The [exact bin/test source](https://raw.githubusercontent.com/sympy/sympy/84c125972ad535b2dfb245f8d311d347b45e5b8a/bin/test)
   imports get_sympy from its own bin directory before calling path_hack to add
   the repository root. Python `-I` removes the script directory from discovery,
   so this bootstrap cannot be assumed to work. Exact source SHA256:
   `973e3cf784388b8e7821e4962da8eeb4b615447cd652af920452b322bd63588c`.
2. `-I` ignores PYTHONWARNINGS. Merely copying the official shell assignment
   would fail to establish its actual UserWarning/SyntaxWarning filter policy.

A future adapter must implement and record a separately reviewed host bootstrap
with exact source/runner identity and the same native flags, file selection,
warning behavior and native test semantics. Do not silently remove isolation
flags, label this pytest, alter protected assertions, suppress new errors or
claim the candidate argv in the profile has already run. The profile deliberately
marks that candidate non-executable pending this admission work.

## Required fresh evidence

Accept transplant/private-library/source identity first; then offline mpmath
identity; then native-runner bootstrap/import and warning policy. Only then run
fresh independent base/gold/wrong containers with complete ordinary oracle
selection, source/test/control/runtime/image hashes and confirmed cleanup.
Gold must have complete required success and no unexpected observed regressions.
The qualified donor and this package profile do not close SymPy13971 or P07.

## Actual qualification,27 October2026

Workflow40447626567 on87ae817d8731581c20213871969c0b3b152452b0 completed all3native rows. Base/wrong fail the single F2P; gold passes all103required tests; all102P2P pass in every row.114total observations per row, cleanup confirmed, rawlogs/archive digests independently checked. Retained at `validation/p07-budget20/sympy13971-legacy/`. Inventory23/30,P07OPEN. Native direct child -RV exits127 (private libpython not on default loader path); original runner takes its same-process fallback without modified tests, flags or sys.executable. No subprocess hash-randomization qualification is claimed. No model inference performed.
