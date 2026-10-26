# Prospective Sphinx controls v4

The real historical dependency overlay made all four Sphinx gold rows pass.
Sphinx8474 also calibrated successfully. Three earlier negative controls remain
invalid: 8506 breaks an optional-value regression while its requirement passes;
8713 passes every observed test; 8721 fails its requirement but also breaks HTML
viewcode. Evidence is preserved in `validation/p07-budget20/sphinx-profile-v1/`
at runtime prefix `f3649ca`. This document does not relabel those outcomes.

`tools/prepare_p07_controls_v4.py` materializes a new file from immutable v3 and
the unchanged v2 corpus. It refuses an existing output, binds all three exact
gold SHA256 values, requires each mutation to match uniquely, and records the
predecessor and corpus Git-LF hashes. Earlier controls, gold, protected tests,
requirement IDs and acceptance rules are unchanged. The other 27 controls are
copied exactly. No control enters model-visible context.

| Task | Prospective production defect | Intended preserved behavior |
| --- | --- | --- |
| Sphinx8506 | Require the first option-name character to be non-bracket, while permitting subsequent brackets. This models incomplete leading-bracket grammar support. | The gold optional-value correction remains intact, including `-j[=N]`; ordinary option names retain their path. |
| Sphinx8713 | Consume grouped Other Parameters fields but use section-field output even when `napoleon_use_param` requires parameter fields. | Ordinary Parameters and the false configuration branch remain unchanged. |
| Sphinx8721 | Incorrectly narrow the EPUB family guard to `epub3`, omitting ordinary `epub`. | HTML generation, the singlehtml guard, and explicitly enabled EPUB retain their gold behavior. |

These are plausible narrow source defects, inferred from the exact production
patches and protected assertions. They do not inspect test names, test paths,
reproducer-only magic values, or runtime test markers. **All three are OPEN and
ungraded until a fresh isolated base/gold/wrong triple succeeds.** Assertions and
regressions must remain fully enabled. A passing wrong patch or additional
regression rejects the corresponding calibration again.

Generation command, with no inference or target execution:

```text
python tools/prepare_p07_controls_v4.py
```

The generator and provenance tests do not establish oracle behavior. Fresh
grading must use the separately admitted historical Sphinx dependency image,
the v2 corpus, and `benchmarks/stage4/p07-controls-v4.json`, preserving all logs,
image/profile identities and original earlier results.
