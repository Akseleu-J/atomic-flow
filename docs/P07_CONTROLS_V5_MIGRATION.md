# P07 controls v5: narrow Pylint7114 negative control

This is a prospective, **ungraded and OPEN** oracle-side control. It changes only
Pylint7114. All29 other controls, the corpus, gold/test patches and requirement/
regression IDs remain unchanged. Existing v1–v4 files and prior results are
preserved; no previous failure is retroactively accepted.

## Evidence motivating the replacement

Workflow `40371778294`, runtime
`eb12cd7aaa7ab52b028d66b0c43cf80cccd07645`, used the explicitly admitted7114
Astroid2.12.2 image with `--pytest-launcher python-c`. Artifact12673550474's
verified ZIP SHA256 is
`f84534dae61922aae3abe6417fe936f276e53b4bc5ba63748948359674ade999`.
Its original archive and task report are retained in
`validation/p07-budget20/pylint-native-probe/`.

The new launcher probe establishes base requirement FAIL with regressions intact
and complete gold PASS. The old wrong control still fails six tests: the intended
nested-module requirement plus five unrelated import/file-handling regressions.
Therefore the environment probe alone does not calibrate the task. This control
replacement addresses the separately demonstrated wrong-patch scope problem.

## Exact source change

The old control changed gold's `os.path.isfile(something)` to
`os.path.isdir(something)`. That incorrectly excluded ordinary file inputs from
the concrete-file branch, explaining why file/import cases beyond the requirement
regressed.

V5 instead changes only that function call from `isfile` to **`exists`**. It
retains the remainder of gold, including the explicit-package condition and its
separate ImportError-only cleanup. This plausibly incomplete fix preserves generic
existing-file handling while still treating an existing namespace directory as a
concrete module path—the original defect. It uses no protected test name, path,
fixture, literal reproducer value or cache injection.

For a stable filesystem, the resulting branch has the same existence behavior
as the old base branch: if a directory's `__init__.py` exists, that directory
already exists too. Thus the second existence test does not exclude namespace
directories. The base's fresh single-requirement failure with intact regressions
supports this candidate, but does **not** prove the modified wrong patch's grade:
the independent exception-handler change remains, and a new run is mandatory.

## Byte-level provenance

| Item | Git-LF SHA256 |
| --- | --- |
| Predecessor `p07-controls-v4.json` | `e301135f5d6d866cfe553206b279235f9ad58a1509855993573ba0725e869038` |
| Unchanged `corpus-p07-v2.json` | `ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd` |
| New `p07-controls-v5.json` | `f4c8fba47a90f4cbc653d732b001969b0bb7e9a01f8ccb5c109ce1217d0698d1` |

The7114 gold source patch SHA256 remains
`1089cc6d5d8cda7a9b882bf08403e799a317acfa0a82638665df736ea6c3a6c4`.
The mutation matches exactly one added source line and leaves hunk counts,
removed/context lines and production path scope unchanged.

## Required fresh validation

Run a new7114 base/gold/wrong triple using controls-v5, unchanged corpus-v2, the
independently admitted exact dependency profile and explicit python-c launcher.
Retain original outputs and immutable image/source/control/runner identities.
Acceptance still requires base requirement failure, complete gold success and
wrong requirement failure with every regression intact. Any additional failure
keeps7114 OPEN and requires another prospective review; do not shorten the suite,
relabel regressions or overwrite old output.

`tests/test_p07_controls_v5.py` checks predecessor/corpus provenance, the exact
one-task migration, source-patch boundaries and the distinction between the broad
directory-only mutation and the restored ordinary-file behavior. Those tests are
not an oracle grade. No local target tests, remote CI or paid calls were run by
the author of this migration.
