# Private runtime integration design — 28 October 2026

Status: implementation design only. The existing runtime registry is a pure
integrity contract; no private runtime is connected to CheckSpec or TaskRunner
by this document. No target code, Docker job, tests, Git operation, API call,
or paid evaluation was executed during this design pass.

## Existing boundaries and exact incompatibilities

`check_spec.py` admits three ordinary interpreter paths and an exact registry
image name at a manifest digest. `agents/execution.py::docker_command` currently
runs that image and interpreter directly. The private derived image has only a
local `sha256:<image ID>` identity, while the original image retains the exact
manifest reference. Those are different identities and cannot be substituted
by changing a tag or inventing a manifest digest for the derived image.

The actual reviewed private layouts are:

| Python | Interpreter | Launcher | Startup behavior |
| --- | --- | --- | --- |
| 3.6.15 | `/opt/p07-python36/bin/python3.6` | `/opt/p07-runtime/python36` | fixed donor ELF loader/library paths, PYTHONHOME, `-I -B` |
| 2.7.18 / 2.7.13 | `/opt/p07-python27/bin/python2.7` | `/opt/p07-runtime/python27` | fixed donor ELF loader/library paths, `-E -s -S -B`, fixed private sys.path, only `-c` or pip mode |

The Python2 launcher deliberately changes sys.argv to `['-c']` before executing
its `-c` payload. A new host payload must therefore carry its validated input
as canonical JSON embedded in the fixed template. It cannot depend on later
positional arguments surviving the launcher. The launcher does not accept
the current generic `-I -c` checker arguments.

The current SymPy bootstrap requires Python 3.8+, sets `sys.pycache_prefix`,
and starts bin/test using `sys.executable`. Merely reducing its version minimum
would lose the private ELF-loader environment in the native child. Python3.6
also does not implement the pycache-prefix feature. The accepted 13971
calibration instead verifies private dependencies and runner bytes, installs
fixed UserWarning/SyntaxWarning filters, adds `/testbed/bin` and `/testbed`,
and executes the exact unmodified bin/test with runpy in the same interpreter.
Its child `-RV` capability probe is recorded separately, including its known
native fallback; that historical probe is not successful execution evidence
for a new executor.

`agents/verify.py::verify_edit` currently compiles edited Python bytes on the
host before invoking the host ordinary-test obligation. `agents/completion.py`
also compiles changed Python files on the host. This rejects valid Python2
syntax and can accept syntax unsupported by Python3.6. Both sites need a
runtime-bound target-grammar syntax check. Skipping syntax checks, converting
source with 2to3, using ast.parse on the host, or treating failed syntax as an
ordinary assertion failure would weaken the current boundary.

## Contract changes with legacy byte preservation

Add one optional typed `runtime_identity` to CheckSpec. When absent, preserve
the existing accepted fields, exact as_dict result, canonical bytes, digest,
argv, validation and Docker command. Do not serialize a null runtime field or
change the legacy schema version. Existing pytest-self and modern native
contracts continue to use their current guards unchanged.

For a private contract, retain `spec.image == runtime.base_image` as the exact
original manifest identity, and require equality of runner and observed
interpreter with the runtime identity. Include the complete runtime identity
and its digest in the new contract serialization, so obligation/failure/repair
digests cannot silently substitute a derived image, dependency inventory,
bootstrap or fixture. Execution must select `runtime.image_id` explicitly;
the original reference remains provenance and must never be the silently
executed image for a private contract. A bare image ID is admitted only through
this typed private branch, never by widening the general image regex.

Keep CheckSpec.argv a host-reviewed logical ordinary-test command. The Docker
dispatcher alone maps it to the fixed private launcher and trusted bootstrap.
Its admission must compare the original requested arguments exactly as today.
Expose no new shell, arbitrary executable, environment, mount or network input.

Before connecting RuntimeIdentity, add an optional exact launcher-bytes SHA256
field for new private identities. Serialize that field only when supplied, so
existing registry identity bytes remain unchanged. Registry objects with no
launcher hash remain valid data but are unavailable for private execution.
The existing helper hash binds reviewed host helper/materializer bytes; it is
not a substitute for the exact generated launcher-file hash. Private execution
must also reject an unchanged base/derived image ID, and require the private
dependency inventory and runtime-probe evidence for the exact Python version.
Do not add pytest-self to this private family until its own legacy bootstrap
and dependency inventory are reviewed.

Host admission collects original and derived Docker inspect data by immutable
identity, checks Linux amd64 and exact base RepoDigests/image ID, and requires
the original RootFS layers as a nonempty prefix with added derived layers.
Verify the exact launcher, private runtime files, wheel/installed-file inventory
and provisioning receipt bytes separately. Each run verifies the selected
derived image ID again. Image labels, tags, a copied report, an unsigned hash,
or a successful previous job do not attest actual runtime provenance. Retain
raw reports, inspect objects and hashes at their original evidence scope.

The existing `environment_digest` remains an independently admitted host
binding. Bind its runtime and fixture digest to the extended CheckSpec; do not
replace it with an arbitrary report SHA or allow a model to instantiate a
runtime/fixture contract. Resume/replan/rebind must preserve the whole extended
template and require fresh host admission when any identity changes.

## Target-grammar syntax precheck

Add a separate immutable syntax request and receipt bound to the CheckSpec
digest, runtime digest, current source revision, relative Python path and exact
file SHA256. Its input is created by the host edit/final-check boundary.
Construct it from admitted source bytes; never accept a model-provided script.

The fixed syntax bootstrap runs through the same derived image, private
launcher, nonroot user, readonly source/root, network none, memory/PID/CPU/tmpfs,
bounded output, cancellation and immutable-container cleanup as ordinary checks.
It verifies interpreter version/prefix/flags and reads the single confined
file as bounded raw bytes. It calls compile(raw, relative_path, 'exec', ...)
without exec, import, setup, bytecode writes, service startup or source changes.
The Python2-compatible template must contain no annotations, f-strings,
pathlib dependency or Python3-only stdlib assumptions. Preserve source encoding
cookies by compiling bytes. Return only a bounded typed syntax diagnostic,
file/runtime/source identities, and a distinct nonce-bound syntax receipt.

Syntax success never satisfies an ordinary-test obligation. A syntax error is
a deterministic syntax failure, while loader, probe, timeout, malformed output,
identity drift and cleanup uncertainty remain infrastructure failures. Do not
construct an ordinary completed-check receipt or grant repair evidence from
an unavailable syntax probe. Source equality is checked again after execution.

Use this precheck in both verify_edit and final completion only for private
runtime obligations. The default host compile path remains byte-for-byte
compatible for existing contracts. Tests must include Python2 `print 1` and
`except ValueError, error`, Python3.6-compatible code, a post-3.6 construct
rejected by the target, encoding cookies, and source/hash/runtime substitution.
Target-grammar acceptance requires real Docker CI evidence; host unit tests
cannot establish it.

## Runner and service lifecycle

Start with private Python3.6 native SymPy13971: no HTTP fixture is required.
Use a separately reviewed versioned private native bootstrap, retaining exact
runner source hashes, SymPy/mpmath module paths/version/inventory, warnings,
test argv and complete native output parsing. Run the exact bin/test through
runpy in the private interpreter as the accepted calibration did. Adapt output
capture with the same admitted stream limits rather than emitting an unbounded
native transcript. Do not reuse a `sys.executable` child that bypasses the
private launcher. Reject zero tests, required skips, partial counts, exceptions,
truncation and unmatched files exactly as the current completed-check gate does.

Requests Python3.6/Python2.7 require a distinct typed fixture identity before
historical test integration. Bind the service helper bytes, installed files,
certificate bytes/SANs, fixed host mappings, URLs, exact plugin/version inventory,
ports and health-policy version. No arbitrary Docker options or environment
dictionary may be supplied by the model or ordinary CheckSpec fields.

The readonly root prevents dynamically changing `/etc/hosts`; specify only the
reviewed task-specific loopback mappings through fixed Docker construction.
Binding ports 80/443 as a nonroot user requires an explicit reviewed namespace
sysctl or an equivalently qualified image setting. Do not add capabilities,
switch to root, change expected URLs/ports, or enable an external network.

Run fixture verification, servers, real HTTP/CA-verified HTTPS health, ordinary
tests, and shutdown within one owned container and lifecycle. A health probe
that stops its servers does not prove services exist for the following check.
Retain the old wrong-CA rejection, hostname/redirect semantics, plugins and
TCP topology requirements. Fixed Python2 services use the reviewed 2.7 helper,
not the Python3.6 helper with its version assertions removed. Do not silently
clear PYTEST_DISABLE_PLUGIN_AUTOLOAD and enable all installed plugins: verify
the exact private entrypoint inventory and explicitly load the reviewed plugins.

Historical Requests calibration appends a fixture CA to requests/cacert.pem.
That write cannot occur after sealing the readonly source obligation. It also
cannot silently become a published model edit. If this overlay remains needed,
prepare a separate disposable execution snapshot before sealing, retain original
and overlaid CA hashes, prove all other candidate bytes identical, bind the
overlay and fixture digest into execution identity, and discard it after use.
The publication source and its revision remain the original candidate; the
execution snapshot needs its own explicit revision/overlay receipt. A readonly
mount hiding that change from the manifest is not acceptable. An alternative
without a CA overlay needs its own full calibration rather than an assumption
that REQUESTS_CA_BUNDLE reproduces historical behavior.

Always stop/close both servers, join their threads and record successful stop
before emitting an accepted ordinary completion receipt. On startup failure,
test failure, timeout, cancellation or host interruption, existing immutable-ID
container cleanup still applies. Missing server-stop or container-absence proof
is unavailable/unknown, not a passing ordinary check and not a retry license.
Every fresh step/final check needs a fresh nonce, source binding and lifecycle.

## Concrete file assignments and acceptance order

| Assignment | Exclusive files | Deliverable |
| --- | --- | --- |
| Runtime contract | `check_runtime.py`, `tests/test_check_runtime.py` | Conditional launcher hash; private execution eligibility; unchanged legacy identity serialization |
| Spec and Docker dispatch | `check_spec.py`, `agents/execution.py`, focused existing executor tests | Optional runtime binding; exact original/derived distinction; immutable-ID launcher selection with unchanged legacy commands |
| Private bootstrap and grammar | new `check_private_runtime.py`, new `tests/test_check_private_runtime.py` | Fixed Python2/3.6 syntax mode and private runner templates; typed nonce/source/runtime completion validation |
| Host check integration | `check_execution.py`, `check_obligation.py`, `agents/verify.py`, `agents/completion.py`, focused obligation/verification tests | Target syntax routing, extended obligation preservation, private native admission and completion; no weakening of modern guards |
| SymPy evidence | new private SymPy smoke tool/workflow and pure validator tests | Fresh Python3.6 syntax + full-source FAIL/PASS/required-SKIP boundary, then native TaskRunner journey |
| Requests fixtures, after separate design review | new `check_fixture.py`, new private Requests smoke tool/workflow; assign shared Docker changes to dispatcher owner | Immutable fixture identity, real health/test/stop lifecycle and explicit execution snapshot overlay |

The root integrator should own shared execution/spec changes serially. Delegate
only the new bootstrap module and pure tests independently after agreeing the
request/receipt interface. Land runtime+syntax+private native SymPy first, prove
it on Linux Docker with full raw artifacts, then integrate fixture-dependent
Requests. Preserve every failed CI attempt and inspect artifacts before retry.
No new paid calls are needed for these executor acceptance gates. A passed
synthetic boundary does not close historical TaskRunner, all-30 integration,
matched pairs, independent oracle grading or final P07 utility evaluation.
