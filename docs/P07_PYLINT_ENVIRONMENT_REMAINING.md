# P07 Pylint7080/7114: historical environment audit

Both tasks remain **OPEN**. This read-only audit uses complete v2 workflow
`40309674235` at `71c97802544472e105035c1561fb3d9259a4e582`, retained task JSONs
and original `result/{base,gold,wrong}.log` files in
`validation/p07-budget20/calibration-v2/`. It performs no installation, target
import, setup/test execution, CI dispatch or model call and changes no controls.

| Task | Exact base | Verified original ZIP SHA256 |
| --- | --- | --- |
| Pylint7080 | `3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0` | `9769bf62a4adf2b6148f289543140336edabdca489abcf83728d9e909bbbba28` (`12645051168.zip`) |
| Pylint7114 | `397c1703e8ae6349d33f7b99f45b2ccaf581e666` | `e9bc988b33d8c0d004b57185d06d520d41103db15b8db6ae2271643c25e04062` (`12644232772.zip`) |

Both environments report Python3.9.20 and import Pylint from `/testbed/pylint`.
Their immutable official image digests are respectively:

- `swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7080@sha256:3389f0d1319ca611d04a209fc30990c131e0bae9acfeb2163938c75ad23479fe`
- `swebench/sweb.eval.x86_64.pylint-dev_1776_pylint-7114@sha256:6bf0ee60d9e1bb2772f3287f54df0875884ec8aa1e24e6fad14beec46aad70b0`

## Separate gold environment failures from negative-control failures

For7080, base and wrong fail the required recursive-ignore-path test; gold passes
it. All three also fail `test_generate_toml_config` and
`test_generate_toml_config_disable_symbolic_names`. The gold traceback reaches
`tomlkit.container._handle_dotted_key` through Pylint's config generation and
raises TOMLKitError when adding a table under a dotted key. Target freeze records
**tomlkit0.13.2, astroid2.11.7**. Gold is therefore not an accepted complete PASS;
the existing control cannot be accepted before these regressions are repaired.
There is presently no additional7080 wrong-only failure in the observed set.

For7114, base and wrong fail the required identically-named-nested-module test;
gold passes it. Both gold and base additionally fail the two wildcard-import
tests: the expected fixture module `empty` yields an unexpected `import-error`.
The exact base repository contains its zero-byte `tests/regrtest_data/empty.py`
and both wildcard fixtures; this does not establish that their resolution
context is correct in the image. Target freeze records **astroid2.12.13**.

7114 wrong has **five additional failures beyond the two shared gold failures
and its intended requirement failure**:

- `TestImportsChecker::test_relative_beyond_top_level_three`
- `test_analyze_explicit_script`
- `test_custom_should_analyze_file`
- `test_filename_with__init__`
- `test_import_sibling_module_from_namespace`

Those failures require a new prospective narrow control even if a dependency
overlay repairs the wildcard fixture resolution. Do not attribute all eight
wrong failures to Astroid, suppress the gold failures, or declare the task
calibrated because its single requirement has the expected three-row pattern.

## Exact historical declarations, not guessed release-series pins

| Task/source | Relevant declaration | Source SHA256 |
| --- | --- | --- |
| [7080 setup.cfg](https://raw.githubusercontent.com/pylint-dev/pylint/3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0/setup.cfg) | Astroid lower bound2.11.6, upper bound2.12.0-dev0; tomlkit lower bound0.10.1; Python>=3.7.2 | `096b4bd698be938a3fc8939c7bc0500015da752e89db04ca47e5d374b7a8a8d3` |
| [7080 minimum test requirements](https://raw.githubusercontent.com/pylint-dev/pylint/3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0/requirements_test_min.txt) | Exact Astroid2.11.6 test pin | `0deb7f9ae3bc578e8f6ff8d3fad61930b806c98fe864e92c9fa7511470f84ccc` |
| [7114 pyproject.toml](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/pyproject.toml) | Astroid lower bound2.12.2, upper bound2.14.0-dev0; tomlkit lower bound0.10.1; Python>=3.7.2 | `a2c84fc0ffd68c619ce923496a0d6f9b4cafdb291be0f6a58a4ca90681d4a2d5` |
| [7114 minimum test requirements](https://raw.githubusercontent.com/pylint-dev/pylint/397c1703e8ae6349d33f7b99f45b2ccaf581e666/requirements_test_min.txt) | Exact Astroid2.12.2 test pin | `aa14ac83c4331b43563e44aa7199aa57bdbeb5c4fe58fdd762d339eedaa897bd` |

Thus **Astroid2.12.2 for7114 is supported by an exact historical test pin**;
it is stronger evidence than an arbitrary downgrade within the allowed range.
For7080, installed Astroid2.11.7 satisfies setup's range but differs from its
minimum-test pin. Do not apply7114's Astroid2.12.2 to7080: it exceeds7080's
declared upper bound.

Neither base exactly pins tomlkit0.11.4. Its use must be labelled an experiment.
The [0.11.4 container implementation](https://raw.githubusercontent.com/python-poetry/tomlkit/0.11.4/tomlkit/container.py)
and [0.10.1 implementation](https://raw.githubusercontent.com/python-poetry/tomlkit/0.10.1/tomlkit/container.py)
both lack the immediate table/dotted-key rejection visible in the0.13.2
traceback. Their source hashes are respectively
`0f8be7ab644be1d14c54d871dd143dc19a1699b34fb3ac16fc3cfa84d05e6fd9`
and `73f1cb15c38998f559eddbddfd9910df1012084e988b37b3acccfcec4c23b73d`.
This makes either a plausible targeted probe, not proof that a whole gold suite
passes. A minimal first7080 experiment is **tomlkit0.10.1 only**, matching the
declared minimum while retaining installed Astroid2.11.7 to isolate the TOML
change. A distinct later run may restore the exact Astroid2.11.6 test pin.

## Prospective wheel identities verified without executing package code

For each wheel below, the bytes were downloaded into memory and their SHA256
matched the primary PyPI version metadata. No wheel was installed or imported.

| Primary metadata | Universal wheel | SHA256 |
| --- | --- | --- |
| [tomlkit0.10.1](https://pypi.org/pypi/tomlkit/0.10.1/json) | `tomlkit-0.10.1-py3-none-any.whl` | `3eba517439dcb2f84cf39f4f85fd2c3398309823a3c75ac3e73003638daf7915` |
| [tomlkit0.11.4](https://pypi.org/pypi/tomlkit/0.11.4/json) | `tomlkit-0.11.4-py3-none-any.whl` | `25d4e2e446c453be6360c67ddfb88838cfc42026322770ba13d1fbd403a93a5c` |
| [Astroid2.12.2](https://pypi.org/pypi/astroid/2.12.2/json) | `astroid-2.12.2-py3-none-any.whl` | `2a704303bfaacf4b4e23cf07929a22b7f6d1af635c25a8cc2d6dd58ed5863970` |
| [Astroid2.11.6](https://pypi.org/pypi/astroid/2.11.6/json) | `astroid-2.11.6-py3-none-any.whl` | `ba33a82a9a9c06a5ceed98180c5aab16e29c285b828d94696bf32d6015ea82a9` |

The two tomlkit candidates declare Python>=3.6,<4 and no runtime dependencies.
Astroid2.12.2 admits Python>=3.7.2 and requires lazy-object-proxy>=1.4,
wrapt>=1.11,<2 and typing-extensions>=3.10 on Python<3.10; typed-ast is required
only below Python3.8. Existing freezes show lazy-object-proxy1.10.0, wrapt1.16.0
and typing_extensions4.12.2. These declared bounds support a narrow Astroid-only
probe on Python3.9, but do not establish that wildcard resolution will recover.
Astroid2.11.6 also declares setuptools>=20; record and verify that package before
claiming complete compatibility for an optional minimum-test profile.

## Next experiments and acceptance boundary

1. Create distinct reviewed task-specific overlays:7080 tomlkit0.10.1 first;
   7114 Astroid2.12.2 first. Keep all other target dependencies unchanged and
   retain complete before/after inventories and immutable source/image identity.
2. Execute each original base/gold/wrong triple in fresh accepted containers,
   with the exact protected test patch and original acceptance rules.
3. If7114 still cannot resolve `empty`, inspect the actual tracked fixture and
   Astroid's context-file handling inside the isolated worker. Do not add a
   global PYTHONPATH override: that previously changed Pylint subprocess import
   semantics and is not evidence-backed here.
4. Repair7114's negative control prospectively, preserving the old broad failure
   output. Accept only when gold has no observed failures and wrong fails the
   requirement with all regressions intact.7080's current control remains
   unaccepted until its complete fresh triple proves those conditions too.

The first dependency-image helper was scoped to Sphinx. The separate prospective
Pylint profile now records7080's explicit minimum pair (Astroid2.11.6 and
tomlkit0.10.1) and7114's Astroid2.12.2-only overlay. This differs from the more
causally isolated tomlkit-only first7080 experiment described above. The narrow
admission extension requires both installed and prospective Astroid runtime
requirements to be satisfied before/after building, with no dependency download
inside containers; each fresh grade repeats that check. Its implementation and
unit tests cannot establish calibration before an actual isolated run.
