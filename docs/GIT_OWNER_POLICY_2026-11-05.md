# Owner Git policy — 5 November 2026

The owner explicitly requested removal of Codex branches and Codex-authored
commits, continuation on Zcode's existing `experimental` branch, and use of
`Omirbay Akseleu <omirbajakseleu5@gmail.com>` with the owner's process-only token.

- Removed all five `codex/*` branches from `Akseleu-J/atomic-flow` and both
  `codex*` branches from its configured `Akseleu-J/atomic-flow_R` remote.
- Removed the two local Codex branches and stale Codex remote-tracking refs.
- Replaced the author/committer identity on eleven historical Codex commits.
  Descendant hashes changed as required by Git; trees, messages and dates were
  preserved. The former native candidate `6a3262e` maps to `41a569a` with an
  identical tree. This metadata operation is not a new runtime test acceptance.
- Applied the same identity cleanup to the original local checkout, preserving
  its exact HEAD tree and dirty `test_p07_sympy20212_setuptools_focus.py`.
  Removed its 29 obsolete `refs/codex/` checkpoint/evidence refs after backup.
- Private bundles and old-to-new commit maps are stored under each checkout's
  ignored `.local-tools/identity-cleanup-20261105/`. Historical SHA references in
  archived evidence remain historical provenance rather than rewritten claims.

Verified the active local refs and surviving remote branches have no Codex/OpenAI
author or committer identities. Surviving GitHub branches are `experimental` and
`main`; current development is on `experimental`, not a new branch. `main` was
not advanced with new runtime work. Native CI now triggers on `experimental`.
Credentials were neither committed nor included in the cleanup records.
