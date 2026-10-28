# P07 host runtime identity contract

`src/atomic_flow/check_runtime.py` adds a pure immutable identity registry for
host-reviewed runtimes. It does not execute Docker, provision an image, run
target code, admit a model command, or establish model quality. CheckSpec and
the check executor require a separate integration change.

Each identity binds the exact base `name@sha256` reference and base Docker image
ID, actual runtime image ID, interpreter, Python version, runner family, profile
bytes, reviewed helper bytes, dependency inventory bytes, and independently
verified provisioning receipt bytes. Canonical JSON and its SHA256 include all
fields. A frozen host-created registry resolves only complete identity digests.
No environment, shell, mount, credential, or network fields are accepted.

The admitted layouts come from existing source, rather than inferred image tags:

| Python | Observed interpreter | Private prefix | Reviewed launcher |
| --- | --- | --- | --- |
| 2.7.13 / 2.7.18 | `/opt/p07-python27/bin/python2.7` | `/opt/p07-python27` | `/opt/p07-runtime/python27` |
| 3.6.15 | `/opt/p07-python36/bin/python3.6` | `/opt/p07-python36` | `/opt/p07-runtime/python36` |
| 3.9.20 | `/opt/miniconda3/envs/testbed/bin/python` | none | none |

Sources: `tools/p07_python27_support.py`,
`tools/p07_requests863_python27_support.py`, `tools/p07_legacy_transplant.py`,
`tools/prepare_p07_httpbin_image.py`, and their stage4 profiles. This table
does not approve executing the existing launchers: Python2 bootstrap and
private loader behavior still need executor-specific enforcement. Python2
native SymPy is rejected because no corresponding runner is reviewed.

`validate_runtime_admission` takes host-collected Docker inspect objects and
exact retained evidence bytes. It checks all four evidence hashes, Linux amd64,
exact base RepoDigests and IDs, and a nonempty base RootFS layer prefix with
additional derived layers. An unchanged image ID requires identical layers.
It never infers ancestry from RepoTags or Docker's optional Parent field.

The host must collect inspect metadata by immutable image identity and verify
the provisioning evidence separately before constructing the identity. A
matching hash, unsigned historical report, or matching layer prefix is an
integrity/consistency check; none attests to a dishonest host, verifies runtime
probe results, or replaces source, dependency, cleanup, isolation and oracle
gates. Existing historical calibration inventories remain evidence at their
original scope and are not automatically admitted by this module.

The added unit tests cover canonical/frozen identities, exact version/layout
selection, registry confinement, evidence substitution, missing inspect data,
image/digest mismatches, architecture, and layer-prefix ancestry. They use
synthetic metadata only. This agent did not run tests, Git commands, Docker,
target code, provider APIs or paid calls; the parent integration owns validation.

Private launcher binding checkpoint: optional launcher_sha256 is omitted when absent, retaining legacy identity canonical bytes. Private execution eligibility requires reviewed exact private layout, a distinct derived image ID and a launcher SHA256. validate_runtime_admission accepts separate exact launcher bytes only when hash-bound, and rejects missing/substituted/unbound launcher evidence. This is pure contract eligibility, not executable core integration; CheckSpec and Docker dispatch still refuse private runtimes.
