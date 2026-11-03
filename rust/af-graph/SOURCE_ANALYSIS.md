# Source analysis contract, wave 1

Status: narrow syntax/source-binding capabilities, not complete language semantic
parity, verification, executable analysis or production cutover. Python reference
runtime remains independently accepted in its existing scope.

`source-analysis/1` output has exact top-level semantic fields:
`schema`, `root_id`, `source_revision`, `sources`, `nodes`, `edges`, `imports`,
`omissions`, `verification`. `verification` is always `not_assessed`.
`source_revision` is a host-supplied lowercase SHA256 of the complete host snapshot;
these extractors bind it but do not independently reconstruct a whole-workspace
revision from a partial file request. `sources` is `{path, sha256}[]`. Every graph
node carries that same file digest and the existing graph-v1 identity. The
envelope binds all graph facts to a root and host snapshot without altering
Python graph-v1 persisted fields. A consumer must compare root/revision and
source hashes to its own admitted context before accepting a sidecar result.

Nodes retain graph-v1 file/module/class/function kinds, `source.digest`/
`algorithm`, revision line coordinates, `facts`, `fact_status` and `facts_class`.
Contains edges are extracted syntax; TS relative imports and direct symbol calls
are inferred, never runtime-observed. Import records are
`{path, source_sha256, record}`; their per-language record includes an exact source
range and specifier. Unresolved imports are records/omissions, not fake file edges.

Rust: `syn = 2.0.119`, `proc-macro2 = 1.0.107` with span locations. Extracts named
ASCII top-level/inline-module functions, structs, enums, traits and modules and
use syntax (groups/rename/glob). Raw identifiers preserve graph-safe identity.
Macro expansion, cfg evaluation, external modules, impl/trait methods, constants,
type aliases, non-ASCII names, type/call resolution are explicit omissions.
Only UTF-8 `.rs`, <=1 MB/file, no NUL or unsafe path. Parse errors retain the file
and digest with `parse_ok=false`; no invented symbols. Existing Python resolver
does not run on Rust nodes. `.rs` enters the regular graph collection; TypeScript
has its own source-only process and is not silently interpreted by Python.

TypeScript: `ts/af-language-typescript`, exact compiler 5.8.3. One strict bounded
JSON request per process; compiler Program/TypeChecker with a virtual in-memory
host. It does not read the repository, tsconfig, package files, default libraries,
plugins, node_modules, environment-selected executables or source code at runtime.
It extracts named declarations, admitted relative file imports and unambiguous
direct compiler symbols. Config/package/path aliases/dynamic dispatch, type
correctness, non-ASCII or `$` names incompatible with graph-v1, computed names,
variable/arrow declarations and broader semantic coverage are not claimed.
Input paths are strict POSIX; traversal/absolute/drive/backslash/duplicate paths,
hash mismatch, malformed JSON and over-limit data fail. The process is intended
to run inside the host's bounded worker; JSON bounds are not OS isolation.

Limits: 256 source files, 1 MB each, 8 MB aggregate source; TS request 12 MB,
response 24 MB, visited syntax 100000, symbols 50000, edges 100000. The host must
also bound process time, memory, output and cancellation; merely launching the
sidecar does not establish admission. No runtime flag accepts arbitrary compiler
options. Shared omission values are human-readable protocol values, not grants.

The existing Rust Python adapter is `python-ast/1`; the current Python reference
is `python-ast/3`. `tests/fixtures/python-reference-v3.json` retains a full current
Python reference vector. `tests/python_reference.rs` checks only the named common
identity/hash/symbol/line/signature projection and explicitly preserves version
differences. It must not be cited as v3 import-binding/shadowing semantic parity.

`af-context::source_bound_context_pack` carries root/revision/source hashes and
omissions, validating graph node source bindings. Retrieval reasons/scores remain
retrieval evidence, never Verified. Its byte budget is the previous selection-entry
budget; metadata and total response/token budget are explicitly outside that count.

Platform path handling: native POSIX input paths are rejected rather than globally
rewritten. The graph collector rejects literal backslash filenames on Unix, which
would otherwise collide with nested paths after Windows-style normalization.
The separate SandboxRegistry path admission fix belongs to its owner, not this
crate. Graph-v1 legacy ID helper behavior remains stable for reference vectors.

Local validation performed by this agent: pinned TS tarball SHA512 matches the
existing toolchain lock; TS compiler build/typecheck and five node unit tests
passed. Tests include actual relative alias/call resolution, malformed requests,
missing packages, syntax errors and source text with a top-level throw that is
parsed without execution. Rust tests were added but not built in parallel with
the active workspace compiler slot; integration owner runs them sequentially.
