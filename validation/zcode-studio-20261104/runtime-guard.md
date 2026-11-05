# Runtime editing guard — 4 November 2026

Base: zcode checkout 09d06c5. Changed only af-loop/src/edit.rs,
af-loop/src/lib.rs and af-agent/src/lib.rs. No providers, commits or push.

- Generic command exit-zero is a diagnostic result. Current edit entry point
  returns Unverified, never Completed/Verified or automatic Git commit, until
  an admitted completed requirement checker is implemented. No edits also
  remain not_assessed; a failed/timeout/truncated/source-changing diagnostic
  remains a failed check, with before/after source revision fingerprints.
- Exact host write paths default to empty; with_write_scope binds reviewed
  paths. with_reviewed_replacement separately binds an explicit HTTP-request
  replacement so a model cannot substitute different bytes inside the path.
  Check cwd must equal the registered root; sandbox read-proof/CAS checks stay.
- Editing requires a durable journal. Effectful KernelTool implementations also
  declare this through a trusted trait, preventing bypass via a raw TurnLoop.
- Unknown tool effects are a trusted host latch, not a JSON flag. The loop
  persists tool_effect_unknown, retains the durable hold and stops remaining
  tool calls and further model sends. Malformed/non-object model tool arguments
  are refused before execution. Final diagnostic execution is checkpointed too.
- af-agent Journey reports applied/local integrity separately: CAS and supported
  Python/Rust/JSON parser observations never become requirement verification.
  TypeScript parser checks remain not_assessed in this particular journey.

Validation: rustfmt (skip_children) and git diff --check passed. Serialized
cargo test -p af-agent -p af-loop is running; see runtime-tests.txt for raw
output. Root owns source inventory refresh, overall integration and CI.

Known boundary: this package does not supply a completed requirement checker,
provider billing integration or independent oracle. The underlying sandbox
transaction still needs durable pre-write backups/intent and verified rollback
before full publication safety can be accepted; this was reported to root.
