# pytest6116 xdist 1.18.2 source and build candidate

The public test inventory contains 11 skips importing `xdist.boxed`. PyPI only
published the selected xdist 1.18.2 as an sdist; this is a candidate for a
separate worker, not an accepted runtime, historical environment or allowlist.
`tools/p07_pytest6116_xdist_prepare.py` inspects archive bytes without extracting
or executing them. It verifies the pinned archive SHA256, safe tar members,
the `xdist/boxed.py` hash, static requirements and all three pytest11 entries.
The retained local receipt is
`validation/p07-budget20/pytest6116-xdist-source-v1/preparation.json`.

The source egg metadata declares `execnet>=1.1` and `pytest>=3.0.0`; setup.py
also requests `setuptools_scm` at build time. The prospective builder pins
publisher wheels setuptools 57.5.0, wheel 0.37.1 and setuptools-scm 3.3.3;
the prospective runtime pins execnet 1.9.0 and uses the existing candidate
pytest distribution metadata 5.2.2. The preparer validates exact publisher
SHA256, wheel RECORD, Python 3.8 wheel tags and applicable metadata edges; it
does not prove that all static or runtime imports are present. Three pytest11
entry points activate xdist and can change collection or unrelated tests.

## Reproducible offline build gate

1. Freeze the source archive, four publisher wheels, Python 3.8.20 image
   digest, the image architecture and build controls. Verify all byte hashes
   independently on the host without importing source code. Keep a private,
   fresh build context containing only the reviewed wheelhouse and archive.
2. Build a **builder image** with a separate read-only-input install of the
   three fixed build wheels using `--no-index --no-deps --no-compile` and
   network disabled. Independently inspect image ancestry and the installed
   build tools, Python, pip and sysconfig, and reject any mismatch.
3. Run source extraction and build **only in an owned disposable container**:
   network none, non-root, no added capabilities, no-new-privileges, read-only
   root, fixed resource limits and bounded writable tmpfs. Use fixed `HOME`,
   `PATH`, `LANG`, `TZ`, `PYTHONHASHSEED`, `SOURCE_DATE_EPOCH`, `umask` and
   exact build command; do not let a package manager access the network.
   The locally extracted paths must be prevalidated, all regular files remain
   under a fresh directory, and no source symlinks or links are admitted.
   setuptools-scm requires exact, tested handling of the release archive's
   generated version; fail if the produced metadata is not exactly 1.18.2.
4. Perform two independently created builds under the same controls and
   compare complete wheel bytes, digest and metadata. If nondeterministic,
   retain both results and fix the cause; do not normalize files or compare
   only semantic metadata while claiming reproducible bytes. Confirm both
   containers were removed; unresolved cleanup is a failure.
5. Independently inspect the resulting wheel as data: name/version, SHA256,
   safe paths, complete RECORD hashes and sizes, exact xdist.boxed bytes,
   runtime `Requires-Dist` and exactly three pytest11 entry points. Bind both
   builder input digests and wheel output digest into an immutable receipt.
6. In a **separately versioned runtime candidate**, install the built wheel and
   hash-pinned execnet offline; reconcile all installed files and generated
   metadata against the wheel. Preserve plugin discovery, network isolation,
   source identity and resource constraints as explicit profile inputs.
   A successful isolated import of `xdist.boxed` is only a compatibility
   smoke, not proof the public test inventory passes.
7. Execute the full public test set with node/phase/status evidence; report
   all failures, skips, xfails, xpasses and collection changes. Check plugin
   auto-discovery effects, child-process isolation, PTY and process limits.
   Requiring a fixed complete envelope means missing tests, extra unreviewed
   skips, timeout, truncation or cleanup uncertainty are not acceptance.

The above is a design, not a claim that a wheel has been built or that the
selected versions work together. Generated console scripts, isolated source
build, build reproducibility, installed-byte provenance, native full-suite
behavior, TaskRunner integration and independent oracle grading remain open.
No paid calls are authorized by these diagnostic artifacts.
