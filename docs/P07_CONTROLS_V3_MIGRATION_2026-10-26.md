# P07 controls v3: prospective negative controls

This package changes five source-only negative controls. It does not modify the
v1/v2 controls, the v2 corpus, any gold/test patch, requirement ID, regression ID,
or acceptance rule. **All five new controls are ungraded and OPEN.** The prior
v2 results remain failed calibrations, not retroactively accepted cases.

The new file is `benchmarks/stage4/p07-controls-v3.json`. Its predecessor's
Git-LF SHA256 is
`b7f2df4bda63befd1736f55191c2fdade5ce0c9eed8ef0f7a9020bd09ac86ea0`.
Source evidence is workflow `40309674235` at
`71c97802544472e105035c1561fb3d9259a4e582`, preserved in
`validation/p07-budget20/calibration-v2/` (task JSONs plus original logs in ZIPs).
The v2 manifest's Git-LF SHA256 is
`ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd`.

## Changes and evidence

| Task | Why v2 is invalid | Prospective v3 defect and expected boundary |
| --- | --- | --- |
| pytest-5227 | Wrong removes logger name but passes the three requirement tests and all regressions. Protected assertions match severity at the start of the log line. | Keep filename/line first, then severity, logger and message. This resembles a partial format migration: it changes the production format but retains the exact ordering defect checked by the protected tests. It is not the original format and not a test-specific predicate. Fresh grading must establish whether all regressions remain green. |
| pytest-7432 | `if False and (` disables skip-location handling globally and causes unrelated skip regressions. The protected parametrization fails only for `--runxfail`. | Gate the corrected independent branch on `not item.config.option.runxfail`. Ordinary skip behavior is retained; the incomplete handling under the public runxfail option should reproduce the protected failure. Any additional regression still rejects it. |
| pytest-11148 | Changing `suppress(KeyError)` to `suppress(TypeError)` breaks cache misses broadly. Base already demonstrated exactly the two required import-cache failures with preserved regressions. | Leave `suppress(KeyError)` intact, but discard the cache lookup instead of returning it. A plausible forgotten return leaves the old repeated-import behavior while retaining correct missing-cache handling. The inserted expression has no intended observable effect beyond a suppressed KeyError, providing a stronger basis than a broad exception mutation. |
| pytest-11143 | Admitting bytes while excluding strings breaks valid module-docstring behavior and does not fail the required non-string constant case. The protected reproducer starts with integer `0`. | Accept `(str, int)` rather than only `str`. Valid string docstrings still work; the integer Constant is misclassified. The predicate describes a language type category, not a literal value, path or protected test name. |
| SymPy-17139 | Inverting `not is_real` changes ordinary real-exponent simplification and causes multiple unrelated regressions. Gold succeeds; base produces TypeError in the two protected complex-exponent tests. | Guard only `is_real is None`; real exponents retain gold/base behavior, unknown realness retains gold behavior, explicitly nonreal exponents retain the defective base behavior. Fresh grading additionally requires the separately reviewed source-bound native requirement-exception receipt; this control cannot accept an untyped ERROR. |

The new controls are inferred from protected tests and complete base/gold/wrong
evidence. They are oracle-side calibration data and must never enter model
prompts or agent workspaces. No negative control may match a protected test name,
test path or a magic literal copied solely from a reproducer.

## Validation and fresh grading

`tests/test_p07_controls_v3.py` checks immutable predecessor/corpus identity,
the exact five-task migration, unchanged gold hashes, unique patch mutation,
unchanged removed/context lines and unchanged production path scope. These are
provenance/containment tests, not evidence that a wrong candidate fails a real
requirement. The author of this package did not run pytest or target tests.

Run a fresh isolated base/gold/wrong triple for each changed task using
`--manifest benchmarks/stage4/corpus-p07-v2.json`
and `--controls benchmarks/stage4/p07-controls-v3.json`. Preserve complete raw
outputs, tested runtime, control/manifest hashes and original v2 evidence.
Acceptance requires base requirement failure, gold complete success, wrong
requirement failure and preserved regressions. If any control passes or creates
an extra regression, that task remains OPEN and needs another prospective
version; never weaken the classifier to accept the mutation.
