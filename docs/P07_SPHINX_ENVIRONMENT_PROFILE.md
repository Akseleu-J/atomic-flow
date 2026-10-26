# P07 prospective Sphinx environment profile

Status: **OPEN / prospective / ungraded**, 26 October 2026. This package neither
executes target code nor changes any source/test/control patch. It describes a
two-package overlay for four independently isolated historical benchmark images.
It is not a complete dependency lock and does not establish calibrated results.

Machine-readable data: `benchmarks/stage4/p07-sphinx-environment-profile.json`.
The profile binds each task's exact base commit, original image digest, runner
identity, observed gold target `pip freeze`, and SHA256 of that freeze. Each
historical setup/tox response is independently hashed; relevant line numbers
are retained. The primary wheel bytes were downloaded into memory, hashed and
compared with PyPI's published SHA256 without installing or importing them.

## Why these pins are a supported first experiment

All four target images currently contain Docutils 0.21.2 and Pygments 2.18.0.
Sphinx 8506 and 8713 gold logs include an additional registered-`meta` warning
which breaks exact warning-count assertions. Sphinx 8474 gold has HTML/warning
regressions. Sphinx 8721 gold HTML differs by an additional whitespace span in
highlighted Python code. These are observations; a dependency cause remains a
hypothesis until clean comparisons establish it.

**Docutils 0.16** has direct historical CI support: each exact base `tox.ini`
contains the `du16` environment with `docutils==0.16`:
[8474 tox](https://raw.githubusercontent.com/sphinx-doc/sphinx/3ea1ec84cc610f7a9f4f6b354e264565254923ff/tox.ini),
[8506 tox](https://raw.githubusercontent.com/sphinx-doc/sphinx/e4bd3bd3ddd42c6642ff779a4f7381f219655c2c/tox.ini),
[8713 tox](https://raw.githubusercontent.com/sphinx-doc/sphinx/3ed7590ed411bd93b26098faab4f23619cdb2267/tox.ini),
[8721 tox](https://raw.githubusercontent.com/sphinx-doc/sphinx/82ef497a8c88f0f6e50d84520e7276bfbf65025d/tox.ini).
It also satisfies the exact base setup lower bounds: >=0.12 for
8474/8506/8721, >=0.14 for 8713. None of these declarations requires the modern
0.21 release. The profile retains those source lines and their SHA256 hashes.

**Pygments 2.7.4** satisfies each exact base setup declaration `Pygments>=2.0`.
It is an explicitly chosen release-series probe for the observed highlighting
change, **not an exact pin recovered from these tox files**. Historical source
permits it but does not prove that these base commits were tested against that
exact release. A future experiment must not present the choice as an official
Sphinx lock. See the exact
[8721 setup](https://raw.githubusercontent.com/sphinx-doc/sphinx/82ef497a8c88f0f6e50d84520e7276bfbf65025d/setup.py);
all four exact setup sources are bound in the JSON profile.

The two published wheels are universal Python wheels with package metadata
compatible with the images' CPython 3.9. Their metadata declares no runtime
Requires-Dist dependencies. This says nothing about complete-environment
compatibility, which still needs installation checks and isolated grading.
Primary metadata:
[Docutils 0.16](https://pypi.org/pypi/docutils/0.16/json),
[Pygments 2.7.4](https://pypi.org/pypi/Pygments/2.7.4/json).

| Wheel | Verified SHA256 |
| --- | --- |
| docutils-0.16-py2.py3-none-any.whl | `0c5b78adfbf7762415433f5515cd5c9e762339e23369dbe8000d84a4bf4ab3af` |
| Pygments-2.7.4-py3-none-any.whl | `bc9591213a8f0e0ca1a5e68a479b4887fdc3e75d0774e5c71c31920c427de435` |

## How to grade without contaminating the comparison

1. Verify the original image digest and exact base commit against the profile.
   Obtain the two exact wheels, check their published hashes, and create an
   offline installation layer on that immutable image. Never execute target
   setup hooks to prepare this overlay.
2. Install only the pinned overlay packages in the target Python environment,
   using local wheels with hash enforcement and no dependency resolution.
   Leave all other packages unchanged for the initial experiment. Record the
   derived immutable image identity, complete post-overlay freeze and package
   consistency diagnostics. If installed consumers conflict with the selected
   versions, stop and revise the profile prospectively; do not ignore conflicts.
3. Reuse exactly the accepted isolation settings and unchanged oracle/test
   patches. Run separate clean base/gold/wrong containers. Preserve original
   and derived logs and compare complete outcomes, including extra regressions.
4. If attribution is uncertain, probe Docutils alone, Pygments alone, then both
   as distinct attempts. This profile does not silently select the best outcome.
5. Accept only a complete base FAIL / gold PASS / wrong FAIL triple with all
   regressions preserved. Sphinx 8713 may still need a valid negative control
   after the environment is fixed; environment compatibility alone cannot close
   that task. Preserve every previous failed result.

These old packages are restricted benchmark-runtime dependencies, not proposed
production dependencies for atomic-flow. No Docker build, pip install, pytest,
target test or paid inference was performed while producing this profile.
