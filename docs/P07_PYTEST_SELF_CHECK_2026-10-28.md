# P07: pytest repository self-check execution

28 October 2026. Implemented host contracts; real Docker acceptance pending.

CheckSpec supports a separate pytest-self runner with an immutable entrypoint
identity, exact Python/pytest version and source-root layout. The isolated
bootstrap imports pytest and _pytest from the actual edited checkout. Protected
anchors are automatically sealed by CheckObligation; implementation modules stay
editable. Every derived spec binds the current full source revision. Default
legacy serialization omits the optional identity and remains unchanged.

The receiver requires full collection, nonzero tests, exact declared files,
finished session and all collected tests completed through teardown. Required
skip, collection/setup/teardown failure, timeout, truncation, wrong module/version
or duplicate receipt fails closed. A complete ordinary assertion failure can
reach bounded repair only through the existing current-source/obligation guard;
its actual runner is retained in version2 evidence. Target Python can lie about
its own hooks; this is not independent oracle proof or remote attestation.

Host-only related checks:291 passed in the first combined run with one test
expecting the misleading legacy pytest alias. That expectation was corrected:
pytest-self uses command=check and command=pytest refuses. Updated self/queue
integration neighbors34 passed. Ruff passed after import formatting fixes.
No target code was executed on the Windows host.

The real isolated smoke copies full source bytes from the pinned accepted
pytest6116 image, probes metadata only in Docker, then runs synthetic assertion
FAIL -> edited PASS -> required SKIP refusal using the actual core executor.
It retains raw receipts/output, source/anchor hashes and cleanup evidence. It
is an executor boundary, not a historical issue fix or full TaskRunner journey.
Generic runtime-registry/private-runtime/service integration and full P07 paid
pairs/holdout/ablations remain open. No new provider inference was made.
