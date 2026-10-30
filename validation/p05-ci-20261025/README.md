# P05 exact-source acceptance

Accepted runtime head: `91d22e2d1ed3e4d415bbb72076503cc4c6bf2fe3`.
Tested PR merge: `be9fb63a075274bdbf7dd8e40671053f01daf34a`.
Both have Git tree `c95e2dbdd4dfb90098a6e7f1601e8b64c1d1978a` (fetched and compared).

Both required workflows succeeded:

- [Project and container checks](https://github.com/Akseleu-J/atomic-flow/actions/runs/40175334384).
- [External interoperability and installed release](https://github.com/Akseleu-J/atomic-flow/actions/runs/40175334261).

Python 3.11 and 3.12 each: **1197 passed / 14 skipped**. Counts overlap; do not sum.
12 installed wheel jobs and real Docker acceptance passed. Three external real
LSP/MCP journeys passed: publish, cancellation, source conflict. Models scripted;
accounting fixture dollars in composition.json are not live provider spending.

The two downloaded host-test archives were checked against GitHub's ZIP digests,
their JUnit hashes and every normalized src/tests Python source hash (255 files).
The external composition archive's ZIP digest, tested commit and script hash were
also checked. `verify_evidence.py` reruns these checks using the archived bytes.
Individual 12 wheel archives were not downloaded in this package; their CI jobs
and artifact metadata establish the stated installed-matrix result.

`verified.json` records evidence and limitations. Raw archives are kept for
reproducibility; they contain no provider credentials. P05 core acceptance is not
production, measured model utility, semantic requirement acceptance or isolated
worker deployment acceptance. Four production blocks remain open.
