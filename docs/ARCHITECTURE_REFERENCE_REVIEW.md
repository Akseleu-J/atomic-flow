# External architecture review — 21 October 2026

Follow-up (22 October): `REFERENCE_OPPORTUNITIES.md` expands this narrow initial
review into 62 discussion candidates. Its source-sampling scope and evidence grades
are explicit. The follow-up changes documentation only; it is not another implementation package.

Requested reference: https://github.com/777genius/claude-code-source-code-full

The repository describes itself as a mirror of a reportedly exposed source snapshot.
Its README distinguishes imported material from added documentation/tooling. This review
does not independently authenticate its provenance or treat it as the current official product.
Its LICENSE explicitly marks the material UNLICENSED / NOT FOR REDISTRIBUTION:
https://github.com/777genius/claude-code-source-code-full/blob/main/LICENSE

Scope inspected: README, LICENSE, architecture/tool/subsystem documentation through the web.
No clone, package install, MCP server, repository scripts or source snapshot were executed.
No source files, internal prompts, identifiers or algorithms from that mirror were imported
or translated into atomic-flow. Changing names/language is not the basis for reuse.
The implementation below follows independently specified atomic-flow requirements.
This is an engineering provenance note, not a legal clearance or formal clean-room certification.

## Useful design questions and our choices

| General mechanism | atomic-flow status / decision |
|---|---|
| Central tool loop with validated inputs and permissions | Already present; keep host-owned ToolResult, role allowlist and approvals |
| Explicit tool behavior / execution contract | Existing Python Tool/registry; capability metadata/native transport remains later work |
| Context/window management | Selected now: deterministic history admission and old-result body omission |
| Stop non-progressing execution | Selected now: repeated request guard scoped to role and source revision |
| Task/session state and interruption | Existing changeset/checkpoints, call accounting and task lease; continue hardening own implementation |
| Streaming UI, plugin/MCP/skill ecosystem | Deferred until a measured CLI pilot needs it; do not copy product surface |

Mirror architecture documentation (used only for the broad comparison above):
https://github.com/777genius/claude-code-source-code-full/blob/main/docs/architecture.md
https://github.com/777genius/claude-code-source-code-full/blob/main/docs/tools.md
https://github.com/777genius/claude-code-source-code-full/blob/main/docs/subsystems.md

The context-pressure problem is also described independently in public first-party docs:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context
https://claude.com/resources/articles/using-claude-code-session-management-and-1m-context

## Independent implementation: package 6

Our existing issue: `_Run` retained every tool result in every later role-loop prompt.
Read pagination bounded one response, but did not bound the resulting conversation.
The existing turn cap also allowed repeated identical searches/reads/checks until exhaustion.

Added `llm/history.py`, using our existing ToolResult summaries and text/JSON protocol:

- Default serialized message-list budget: 131072 UTF-8 bytes, **not a token/context-window claim**.
- Old tool result bodies may be omitted, oldest first, while retaining outcome, error,
  edit/check proof, revision/hash and pagination markers. Omissions are explicit.
- Initial instructions/query, assistant tool arguments and the latest two tool results
  remain intact. If these do not fit, raise ContextLimitError before calling the backend.
- The same canonical tool request is allowed three times per source revision in one role
  invocation. A fourth raises RepeatedToolCall before executing the tool. Different pages
  or a new source revision have separate counters.
- Full existing tool audit artifacts and task verification records remain intact;
  prompt history compaction does not turn a failed tool into success or change file state.
- Request metadata artifacts report the limit, peak admitted request bytes and omission count.
- Project-level untrusted config cannot raise these host limits.

Settings: `conversation.max_request_bytes`, `recent_tool_results`, `max_same_tool_call`.
These protect the serialized message list; provider envelope overhead, output capacity,
native protocol formats and model-specific token limits still need separate handling.
This is deterministic omission, not an LLM summary, persistent conversational memory,
global loop detection or measured improvement in real-task success.

Tests cover preserved instructions, retained error/edit proof, pagination/hash metadata,
snapshot stability, refusal without backend calls, canonical repeat detection and real
Orchestrator audit behavior. No paid model API or external repository code is executed.

## Validation

Windows Python 3.12: **202 passed, 9 skipped** in the full suite with pytest's base
directory outside OneDrive (`validation/step6-full-temp.txt`). Targeted history,
evidence and budgeting checks: **34 passed** (`step6-targeted.txt`). Changed-file
Ruff, compileall and diff whitespace checks passed.

The first full run inside OneDrive had **201 passed, 1 failed, 9 skipped**:
Windows denied `os.replace` of a task JSON file (`step6-full.txt`). All 19 task
execution tests passed on the immediate outside-OneDrive recheck, followed by
the passing full rerun. This does not establish reliable task storage on OneDrive.
No production task storage behavior was changed to hide that environment failure.

Runtime commit: `ac36bdc044753ea47f05656ffeb3fd542e095ae6`.
Push CI: https://github.com/Akseleu-J/atomic-flow/actions/runs/39647657813
PR CI: https://github.com/Akseleu-J/atomic-flow/actions/runs/39647664551
Both passed Python 3.11/3.12 and Real Docker acceptance. The Docker job passed
**188 CPU tests (6 skipped), 17 root tests and 6 real Docker tests**, with
`container=PASS`, zero model calls. Exact-commit evidence including image ID:
`validation/ci-history-20261021/validation-ac36bdc044753ea47f05656ffeb3fd542e095ae6/`.
The PR artifact separately identifies its synthetic merge commit `10005731aaf5e24d4831f91c290686b772d8b73d`.
These tests establish guard behavior, not real-model task quality or token/context sufficiency.

Exported text logs normalize trailing whitespace and final newlines; source/image identities and JSON results are retained.
