# P07 legacy runtime: verified inputs and an unproven execution path

Read-only audit on 26 October 2026. No Docker process, package installation,
target setup/import/test, paid call or CI workflow was started by this audit.
The proposed Python runtime is available as verified public registry bytes;
neither its compatibility inside the SWE-bench image nor any calibration is
claimed to work yet.

## Exact SymPy13971 requirements

Task base: `84c125972ad535b2dfb245f8d311d347b45e5b8a`, historical SymPy1.1.
The gold patch changes sequence LaTeX bracket escaping in
`sympy/printing/latex.py`; Python compatibility is a separate environment issue.

| Exact source, fetched as data | SHA256 | Relevant declaration |
| --- | --- | --- |
| [setup.py](https://raw.githubusercontent.com/sympy/sympy/84c125972ad535b2dfb245f8d311d347b45e5b8a/setup.py) | `061482f8755b921d4a4e6c638d06cee7103d5085aa916b1bd3122697d351ca8a` | line38 `mpmath_version = '0.19'`; line387 `mpmath>=0.19`; classifiers explicitly include Python3.4,3.5,3.6. |
| [.travis.yml](https://raw.githubusercontent.com/sympy/sympy/84c125972ad535b2dfb245f8d311d347b45e5b8a/.travis.yml) | `2c1abc6d938b55b7994c0571542726ef32573663496e1e8fb3a9f00bdbdb8bfb` | Python3.6 is an actual declared CI matrix entry. |
| [sympy_parser.py](https://raw.githubusercontent.com/sympy/sympy/84c125972ad535b2dfb245f8d311d347b45e5b8a/sympy/parsing/sympy_parser.py) | `d69a429d8bea390a8a26e8076dee9fe5d79de386a70f3f90a88ac19d72320d9e` | `EvaluateFalseTransformer` constructs `ast.Name(id='False', ctx=ast.Load())` at lines946,954,962. |

This supports Python3.6 as a historically declared experiment, not an arbitrary
version downgrade. The exact base's `tox.ini` URL returned404; no tox pin was
inferred from that missing file.

Primary [mpmath0.19 metadata](https://pypi.org/pypi/mpmath/0.19/json), response
SHA256 `4382ea9ba222dc81b3c0c17461a104c06486192879e68ebe00beef6b8d28c012`,
lists **no wheel**. Its two distributions are:

| Distribution | SHA256 |
| --- | --- |
| [mpmath-0.19.tar.gz](https://files.pythonhosted.org/packages/74/39/08b1b5c9848b498f922c7c92d31bb6edf00fe5a3bd87b04ebe1cc1d63948/mpmath-0.19.tar.gz) | `68ddf6426dcda445323467d89892d2cffbbd1ae0b31ac1241b1b671749d63222` |
| [mpmath-all-0.19.tar.gz](https://files.pythonhosted.org/packages/b0/30/b7b20f07babf64cd3897b226f2bd4c2af65a899f1796f7723bccb4c59fa0/mpmath-all-0.19.tar.gz) | `21a824db176799f42843e4510da4bc6db36ef92b5a43fff70fad7e29bffb9c86` |

Consequently the existing reviewed wheel-only overlay cannot install0.19
directly. A separate audited, network-disabled source-to-wheel build is required
if this version is selected; its setup executes only inside the isolated build
worker. Retain original archive hash, safe extraction inventory, pinned build
tools/runtime, build output/logs and produced wheel hash. Never execute setup on
the host, silently extract a package onto host sys.path, or claim a hand-built
wheel was a publisher-provided wheel. The declaration is a lower bound, not a
strict0.19 pin; selecting a later compatible release is a separate prospective
profile decision that also needs evidence and a fresh calibration.

## Public Python3.6.15 image availability verified

Public Docker Hub Registry v2 HTTP reads succeeded for
`library/python:3.6.15-slim-buster`. The anonymous pull token was used only in
memory and is not recorded here. No layer was pulled or executed.

| Registry object | HTTP / immutable digest |
| --- | --- |
| [Tag manifest endpoint](https://registry-1.docker.io/v2/library/python/manifests/3.6.15-slim-buster) | 200; multi-platform manifest-list SHA256 `e10aa83604948c6d8d9f72a9a20193d84bb2dbe550b725eb5208387117fde065` |
| Linux amd64 selected child | 200; `sha256:d59ee182c4629dd33b240b9bac1a6ff44276e79e66117eba17aed4016845d3b2` |
| Selected image config | 200; `sha256:dc468cf655a42fccd7f2bb1549e9d43d69c1e0a7dbff5457fb45de19e124ed6d` |

For each JSON object its downloaded byte SHA256 matched the advertised digest.
The selected config identifies Linux/amd64, `PYTHON_VERSION=3.6.15`,
`PYTHON_PIP_VERSION=21.2.4`, `PYTHON_SETUPTOOLS_VERSION=57.5.0`, and a Python
build installed under `/usr/local` using `--enable-shared`. The child manifest's
compressed layers are:

```text
sha256:72a69066d2febc34d8f3dbcb645f7b851a57e9681322ece7ad8007503b783c19 27153723
sha256:758aab0c7b735d74a7b2aecfeb1426f35c5932647aba1e1b54a25eaf5935ed81 2770102
sha256:a9f18a3c91aaa5f50b134b2990ae88104489e8def723c1bfecc0bb874a118d94 9689389
sha256:ec34755f4ebe48ddb2780e153be11675c425da2ad61bd66b8a8cb3b3d68339fd 233
sha256:46cf1a15d5b7c95c7fe2f25c70b0e0d140b96ff5724ceed277fcfe493d7350ee 2497678
```

These are registry identities, not proof of runtime imports, dependency closure,
source compatibility or a successful test suite. Pin the linux/amd64 child
digest in any later experiment; the mutable tag is not the accepted input.

## Why Python3.6 is plausible, with a remaining AST caveat

Exact [CPython3.6.15 collections source](https://raw.githubusercontent.com/python/cpython/v3.6.15/Lib/collections/__init__.py)
SHA256 `825e0bbe2610bbe0fcdeb44b9c5584b45b80bf0ae14898c7e08764baa532e1b8`
directly exports `_collections_abc` at lines20–24. The
[3.9.20 source](https://raw.githubusercontent.com/python/cpython/v3.9.20/Lib/collections/__init__.py)
SHA256 `0521f8084998b03f29317872a35531adea4708b32b0ebd4cfdf8e178a93dba76`
uses `__getattr__` and emits the deprecation warning at lines55–65. Thus moving
this old source to3.6 specifically avoids that newer compatibility-warning path
without suppressing warnings or editing tests.

The [3.9.20 AST validator](https://raw.githubusercontent.com/python/cpython/v3.9.20/Python/ast.c)
SHA256 `ba724bbc9ed3593976d75d32e45d3efa7bd781c9f08f90d1c687342fad166ed4`
explicitly rejects Name nodes for False/True/None. The
[3.6.15 AST validator](https://raw.githubusercontent.com/python/cpython/v3.6.15/Python/ast.c)
SHA256 `dcc82caad311d3caffb2c2a5017db19c8a826b891f4704af0e16efc14d6d8585`
does not apply that newer Name validation; its Name/NameConstant cases simply
return valid at lines342–346. However the
[3.6.15 compiler source](https://raw.githubusercontent.com/python/cpython/v3.6.15/Python/compile.c)
SHA256 `05b9ae23b184975dededd7c77d0cc1fb26ab9fa71019e518e34c8451e25f69da`
still contains assertions against those Name identifiers at lines3021–3023.
Source inspection therefore does **not** prove that every generated legacy AST
will compile successfully. A real bounded runtime smoke test and all required
base/gold/wrong tests remain necessary. Do not claim the AST failure is solved
from version selection alone.

## Concrete multi-stage runtime constraints

A prospective image may keep `FROM <official-SWE-task-image>@sha256:...` as its
final stage and use the pinned Python3.6 donor only as a source stage. This
retains the task's original source/history and the independently checked base
layer ancestry. It still adds a materially different runtime, so the existing
two-package overlay receipt cannot authorize it.

The donor's `/usr/local/bin/python3.6` needs its matching
`libpython3.6m.so.1.0`, stdlib `python3.6/`, extension modules, importable
site-packages and all ELF `DT_NEEDED` dependencies. Copying only the executable
or only a3.6 virtualenv is insufficient. Copying a virtualenv also preserves
absolute paths/shebangs that may reference the donor's missing `/usr/local`.
The official image's current conda Python3.9 packages and `cp39` native wheels
cannot be reused as Python3.6 packages. Pure Python universal wheels still need
their declared Python/dependency compatibility checked.

Choose an explicit private prefix, such as `/opt/p07-python36`, and prove the
relocated prefix with the actual interpreter before target execution. Its
loader must resolve the donor's libpython from that prefix, not accidentally
from conda or `/usr/local`. Record `sys.executable`, `sys.version`,
`sys.prefix`, `sys.path`, SOABI and module origins. Existing evaluator commands
hard-code `/opt/miniconda3/envs/testbed/bin/python` and/or conda activation in
several places: a new explicit interpreter/runtime receipt and runner admission
are needed. Replacing that path behind the existing receipt would conceal a
runtime change.

Inspect donor and final ELF dependencies inside the accepted build worker.
The Buster donor and Ubuntu-based SWE image do not establish ABI closure by
name alone. In particular OpenSSL, libffi, readline, sqlite, bz2, lzma, expat,
zlib and libpython sonames used by imported extension modules must exist and
resolve correctly. Copy any required donor libraries to the private prefix
with a recorded file hash/soname inventory and narrowly scoped loader path;
never overwrite the final image's libc/loader or globally replace conda's SSL
libraries. Executing `ldd` against untrusted arbitrary ELF on the host is not
part of this plan. All provisioning and runtime probes occur in isolated,
network-disabled workers after immutable inputs are admitted.

An image config alone does not enumerate this transitive ELF dependency set.
It is not possible to honestly provide a complete portable-library list from
the verified metadata reads performed here. Preserve that gap rather than
guessing that copying `/usr/local` will work.

## Requests is a separate environment and networking problem

`P07_REQUESTS3362_REMAINING.md` already records exact historical Requests base
requirements and actual gold failures; it is not rewritten by this audit.
Primary PyPI metadata confirms universal wheels exist for pytest2.8.7,
httpbin0.4.1, Werkzeug0.11.4, pytest-mock0.11.0 and pytest-httpbin0.2.0.
But the historically pinned Flask0.10.1 has **only an sdist**:
`Flask-0.10.1.tar.gz`, SHA256
`4c83829ff83d408b5e1d4995472265411d2c414112298f2eb4b359d9e4563373`.
Its [primary metadata](https://pypi.org/pypi/Flask/0.10.1/json) response SHA256
was `c06f9b00632247b535349ce1ec851bd173b736d371adad868c17c1533d2a7e22`.
The historical dependency closure also includes Jinja2, MarkupSafe, itsdangerous,
py and other packages; null old metadata does not mean no dependencies.
Each wheel or isolated source build requires its own exact hashes and review.

Python3.6 is a candidate common runtime, not evidence that the old pytest and
HTTPBin/plugin closure works. Old package classifiers generally stop at3.4/3.5;
those metadata alone do not certify3.6 behavior. Restore the real plugin API,
historical certificate semantics and target interpreter together in a separate
profile, without modifying protected tests or synthesizing expected warnings.
Even a successful runtime/fixture migration will not solve Requests3362's
`10.255.255.1` connect-timeout tests under network=none: they need a separately
reviewed hermetic topology with proven no external egress. No runtime version
should convert ENETUNREACH into a claimed timeout.

## Required next evidence, before acceptance

1. Admit exact donor manifest/config/layers and task image/base source; build
   the runtime in a new opt-in image without altering target source or tests.
2. Record all runtime/ELF/build-tool/dependency byte identities, complete
   package inventory, interpreter/module origins, source before/after and
   confirmed cleanup. Unknown outcome remains a hold.
3. Prove offline runtime startup and the intended AST/collections and real
   fixture behavior inside the accepted worker; retain failures.
4. Use a separately reviewed evaluator interpreter/native-runner profile,
   then fresh full base/gold/wrong triples with unchanged oracle data.

Nothing in this audit closes SymPy13971, Requests, pytest7168, Pylint7114,
the full P07 comparison, or a production gate. A common legacy donor may reduce
provisioning work; it cannot substitute for per-task calibration.
