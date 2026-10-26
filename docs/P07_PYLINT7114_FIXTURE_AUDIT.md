# P07 Pylint7114: wildcard fixture import-context audit

The Astroid2.12.2 overlay is genuinely installed and admitted, but Pylint7114
remains **OPEN**. This read-only audit changes no source, protected tests,
dependency pins, evaluator, launcher or acceptance rule and runs no target code.

## Exact evidence and corrected archive identity

Use `validation/p07-budget20/pylint-profile-v1/12672350766.zip` for7114, not
12672131304 (which contains7080). Both artifact names and embedded
`calibration/calibration.json` identify their respective tasks. The7114 ZIP
SHA256 is `a636d04d1ba2f250cf0284993f19a8a83e816f7d57165002e09a287cb7245d22`.
Its tested runtime is `2c938a0b4e8db8636c8f9f26fc226e6fb3b46ba3`; target base is
`397c1703e8ae6349d33f7b99f45b2ccaf581e666`.

The derived image is
`sha256:25942e3ab2a53f892254cca181309385e3750b97b9a2589aed38413f4b41830c`.
The profile Git-LF SHA256 is
`722003e23b71faa029f8c6f3521e3f91599a07813164937bd2dcae955c745dca`.
The source-bound receipt confirms unchanged source and packages except Astroid,
with the historical2.12.2 pin and satisfied active runtime dependencies.

Gold's actual runner is Python3.9.20, pytest7.4.4, pluggy1.5.0, importing Pylint
from `/testbed/pylint`. Its two failures are:

- `TestImportsChecker::test_wildcard_import_init`: expected no diagnostic;
  observed an `import-error` for `empty`.
- `TestImportsChecker::test_wildcard_import_non_init`: expected the wildcard
  diagnostic; observed that diagnostic plus the same `import-error` for `empty`.

The required `test_identically_named_nested_module` passes on gold and fails on
base/wrong as intended. Wrong nevertheless adds six failing tests beyond gold's
two: its intended requirement and **five other regressions**. Environment repair
alone cannot accept its current negative control.

Original gold log SHA256:
`69d1788523c50844ba4906fe03b052a7698960a2759ec2f67345544704f374ca`.
Actual eval.sh SHA256:
`ce30f337fc1a6787a03a6147be37f7a9e5a6eecebcc0437fc1b054352bbf5d8d`.

## What source inspection establishes

The exact base contains a tracked, empty `tests/regrtest_data/empty.py` and two
wildcard fixture files referring to that absolute module name. Missing fixture
content is therefore not established by the error. The isolated container's
actual fixture path/hash still needs to be recorded before ruling out image or
checkout damage conclusively.

The protected tests construct REGR_DATA with a trailing separator and load
`init_wildcard` or `wildcard` through `astroid.MANAGER.ast_from_module_name` with
that explicit context. They then give the resulting import node to the checker.
The protected test patch does not alter this context or these two tests; it
adds an unrelated xfail marker and the nested-module requirement.

Astroid2.12.2's relevant execution chain is:

1. `AstroidManager.ast_from_module_name` temporarily changes cwd to the explicit
   context's directory and restores cwd when the build returns.
2. During construction, `AstroidBuilder` eagerly resolves wildcard imports to
   populate locals. An import-resolution failure here is caught rather than
   removing the later checker diagnostic.
3. `Module.import_module` resolves the absolute name `empty` through the manager
   **without forwarding the outer REGR_DATA context**.
4. Without an explicit context, the module finder searches Python's `sys.path`.
   Merely knowing the fixture file exists is insufficient. A dynamic empty path
   entry can follow the temporary cwd; an absolute `/testbed` entry cannot.
   A previously cached module or cached import error can also affect this path.

Thus the immediate failure is **absolute fixture import resolution after the
outer explicit context has been lost**. Restoring Astroid's historical version
did not repair it, and another speculative downgrade is not the next justified
step. The exact missing runtime path/cache state is not captured in the current
artifact, so the launcher explanation below is a source-derived hypothesis,
not a verified runtime fix.

## Runner policy and plugin comparison

The actual script unsets PYTHONPATH, disables pytest plugin autoload and invokes
the target environment's console `pytest` with both complete selected test files.
The pinned setup.cfg uses ordinary pytest collection settings and no fixture
pythonpath option. Neither tests/conftest.py, tests/checkers/conftest.py nor
CheckerTestCase.setup_method adds REGR_DATA to the import path. The checker
conftest defines a path constant; defining that constant does not configure
Astroid's module search. There is no observed missing pytest fixture or plugin
error in these failures.

Console-script Python startup normally gives an absolute script-directory path,
and pytest prepends collection directories; that does not imply a dynamic empty
entry or REGR_DATA is present. A `python -c` launcher naturally has an empty path
entry, whereas `python -m pytest` normally starts with an absolute cwd path.
That distinction matters because Astroid temporarily changes cwd during eager
wildcard inference. A complete suite could additionally prime the module cache,
but no such priming is proved by these two-file results.

## Narrow next diagnostic, before another acceptance run

Inside fresh accepted network-none containers, retain the exact dependency/source
identity and record, before target collection:

- cwd, complete sys.path, Python/pytest/Astroid identities and console entrypoint;
- tracked fixture presence and SHA256 for empty.py and both importing fixtures;
- module-finder resolution with explicit REGR_DATA versus default search;
- relevant manager module-cache and file-cache entries before/after building the
  fixture AST, including any cached import failure for `empty`.

Keep the console run as the unchanged control. A separately labelled diagnostic
may invoke the same target pytest through Python's native `-c`/pytest.main path,
with the same arguments, plugin policy and test files, to test the empty-entry
hypothesis. It must not inject a fabricated module, modify an assertion, preload
`empty` into Astroid's cache, add a global PYTHONPATH, or suppress import-error.
Changing the launcher is not yet established as an accepted environment repair by
the evidence in this document. If the diagnostic supports it, separately review
the runner contract and rerun all base/gold/wrong cases, including subprocess
import regressions. Any unresolved regression keeps7114 OPEN.

The prospective implementation exposes explicit `--pytest-launcher python-c`
only for7114 with its independently admitted Astroid2.12.2 image. Default console
script generation remains byte-identical; only its one native pytest command's
launcher prefix changes, with every original argument retained. Reports retain
the selected launcher and actual eval script SHA256. This is not a rewrite of
the earlier profile or recorded results and still needs fresh isolated grading.

## Primary source anchors

All Pylint links below refer to the exact base; all Astroid links refer to
v2.12.2. They were read as text, without import or execution.

| Source | SHA256 |
| --- | --- |
| [Pylint unittest_imports.py](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/tests/checkers/unittest_imports.py) | `bbdee41a102adfcd43d0244eae1b49e454e4a69359e1d58b817b0be37b584011` |
| [Pylint checker conftest](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/tests/checkers/conftest.py) | `8ed0175d9b4cb1e9a6bdadf5d3a07b19827534bf015f939a9df32637a89eb0c1` |
| [Pylint tests conftest](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/tests/conftest.py) | `dd828617e86d50ba296c7cbcba751785d24ad167b6ace234d4bda3d3f9f1e093` |
| [CheckerTestCase](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/pylint/testutils/checker_test_case.py) | `ad5edb627358079eb359ac2ec8155db8c63306d479fb8df47f904962fa2207e3` |
| [empty fixture](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/tests/regrtest_data/empty.py) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [Astroid manager](https://raw.githubusercontent.com/pylint-dev/astroid/v2.12.2/astroid/manager.py) | `1b9d3b582587751e650a10d3f5fbe256d5be0fa0ee63dc6a65b2a6404deebd6b` |
| [Astroid module import](https://raw.githubusercontent.com/pylint-dev/astroid/v2.12.2/astroid/nodes/scoped_nodes/scoped_nodes.py) | `29ec9319b7dc2c8e43689082e38a3b627b3612a20bb26d208aaa2589cb091952` |
| [Astroid builder](https://raw.githubusercontent.com/pylint-dev/astroid/v2.12.2/astroid/builder.py) | `0a8e726d4af79511bde352c9bf5985acbc4341f03be8c76ab61f2b56a946cefb` |
| [Astroid finder](https://raw.githubusercontent.com/pylint-dev/astroid/v2.12.2/astroid/interpreter/_import/spec.py) | `860c5587985bc23bc971e06b67f3872ccf6fe17c70719e2f491b450c67023c15` |
