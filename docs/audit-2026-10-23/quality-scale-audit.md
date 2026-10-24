# Quality, scale, API and integration audit — 2026-10-23

Source inspected: `8a18fbe837b5c82597f782b6455e96b708607f36`, branch `experimental`.
Read `PROJECT_CONTEXT.md`, `AGENTS.md`, `project-index.json`, the competitive acceptance plan and current runtime before judging earlier findings. Package 17 R01–R07 fixes are not reclassified as open defects. This pass adds no runtime changes, paid inference, external server executions or full test-suite runs. UI and ergonomics are out of scope.

## Executive finding

The principal production barrier is that individually guarded components have not yet been accepted together as one durable coding runtime. There is substantial safety/accounting implementation and historical CI evidence, but no independently measured multi-repository live edit success, no enforceable whole-project resource envelope, and no supported multi-process API job ownership. New API validation and shared index temporary-file defects were reproduced offline.

| ID | Priority and status | Barrier | Evidence |
|---|---|---|---|
| Q01 | P2, reproduced defect; P1 before public HTTP deployment | Chat shim accepts unbounded nested content and turns malformed/oversized input into HTTP 500 | `api/server.py:43–52,250–262`; `quality-scale-probes.json` |
| Q02 | P2, reproduced concurrency defect | Concurrent index writers share one temporary filename | `tree/builder.py:325–330`; deterministic fault interleaving |
| Q03 | P1 production gate, static confirmation | Durable TaskRunner cannot receive the host capabilities supported by Orchestrator/SDK | `agents/task_loop.py:31–39,415–424`; constructor inspection |
| Q04 | P1 large-project gate, static confirmation | Snapshot and indexing have no whole-project byte/file/disk/cancellation envelope | `agents/changeset.py:94–117,131–158`; `tree/builder.py:263–318` |
| Q05 | P1 for multi-process deployment, documented design limitation | API admission and live run handles are per-process and volatile | `api/server.py:79–82,153–195,212–246` |
| Q06 | P1 quality gate, evidence gap | G01 live edit / independent evaluation remains open | `EVALUATION_PILOT.md`, `COMPETITIVE_ACCEPTANCE.md`; current retrieval probe |
| Q07 | P2 integration/retrieval gate, scoped limitation | Python-first retrieval and synthetic MCP/LSP/provider acceptance cannot establish broad compatibility | `tree/builder.py:142–146`; integration fixture tests |

Priority is conditional on the intended supported deployment. Q05 does not prevent an explicitly single-process local tool. Q01 is not evidence of a demonstrated system-wide denial of service: this pass measured acceptance and HTTP errors, not sustained load or memory exhaustion.

## Q01 — validate the HTTP boundary before internal conversion

`ChatRequest.messages` is a bounded-length list of unconstrained dictionaries (`api/server.py:43–46`). Its content values and content parts have no byte or string-size bounds. `_text` joins arbitrary `text` values without type validation (`49–52`). The shim then creates `AnswerRequest` inside the route (`261`), after FastAPI's request validation has completed.

Offline TestClient results, with no model or network calls:

- Schema accepted one message containing **2,000,000 characters**. This is a schema check, not a claim of successful model processing.
- `/v1/chat/completions`, user message **50,001** characters: **500**, inner Pydantic `ValidationError` on query and retrieval_query.
- The equivalent `/v1/answer` query: **422**, as expected for its own schema.
- Chat text content part with numeric `text: 123`: **500**.

Even several individually short messages can exceed the aggregate composed-query limit once history and formatting are added. Thus fixing only last-user length is insufficient.

Proposed correction: strict discriminated text-message schemas; per-part, per-message and aggregate limits; check composed query before creating internal request; a bounded HTTP body reader with a consistent 413 response for over-limit bodies; enforce total role-prompt bytes on the native endpoint too. Use limits that protect allocation before model admission, not only after the JSON body is parsed. Record sanitized rejection types, not raw submitted text.

Exit gate: every invalid shape and boundary length yields documented 4xx with zero backend calls; both Content-Length and streamed/chunked oversize bodies are bounded; valid boundary requests retain expected semantics. This is technical service admission work, not UI work.

## Q02 — atomic rename is not concurrent transaction ownership

`save_tree` writes every caller to `.atomic_flow/tree.json.tmp`, then renames it. A deterministic two-writer probe orders writer 0 then writer 1 writing the shared temp file, lets writer 0 replace first, then writer 1 replace:

- writer 0 returns `saved`;
- final JSON contains writer **1**;
- writer 1 raises **FileNotFoundError** because the shared temp path was consumed.

The probe changes scheduling through `Path.write_text` and `Path.replace`; it does not change save_tree data or implementation. This establishes a valid interleaving, not a measured frequency under normal load. The first unconstrained simultaneous interleaving did not reliably fail, so the retained reproduction explicitly orders both write and rename boundaries.

Scope qualification: Orchestrator auto-refresh is protected by `project_lock` (`llm/orchestrator.py:703–706`). That lock is a Python `threading.Lock` (`64`), not inter-process protection. CLI tree build/refresh (`cli.py:442–444`), activation refresh (`485`), direct public builder calls, and ToolContext refresh (`agents/context.py:156–194`) do not obtain that particular lock internally. The private TaskRunner workspace reduces cross-task overlap but does not make the public builder safe for concurrent callers.

Proposed correction: unique temporary paths plus a shared project index commit lock or generation compare-and-swap. Unique temps remove temp-file collisions; they alone do **not** prevent an older snapshot replacing a newer one. Validate or reject stale generations, retain a rebuildable cache model, and use bounded lock wait/cancellation. Readers must see a complete known-format generation or a typed recoverable cache error.

Exit gate: thread and two-process build/refresh races, interrupted write, stale generation, Windows rename contention; no writer publishes another writer's bytes or reports an unexplained raw filesystem error.

## Q03 — one durable capability composition boundary

`TaskRunner.__init__` only accepts config/project/backend/approval_callback/question_callback. Its `_execute_step` constructs an Orchestrator with staging, source approval and accounting, but has no arguments for `workspace_session`, `lsp_client`, `documentation_client`, `hooks`, `tool_registry` or `role_tools`.

The Orchestrator accepts all six (`llm/orchestrator.py:604–607`) and injects clients into ToolContext (`737–738`); `sdk.py:37–57` exposes them for live `start_answer`. This is an actual API composition gap, not merely missing documentation. It does not mean the durable task path lacks budgeting or staged publication; those already exist.

A naive pass-through is unsafe: a client bound to the original repository cannot automatically write into the private changeset workspace. The required abstraction is a host-owned capability factory receiving the effective staged root, source revision, task budget, cancellation, effect journal and explicit grants. Restart must recreate only approved host bindings; persisted model output must never become executable callback authority. Keep multi-root publication limited until there is a specified cross-root transaction contract.

Exit gate: one approved durable task can perform a staged source edit, a pinned real LSP operation and one approved external MCP read under the same task accounting/cancel/scope, then survive cancellation/crash without bypassing plan approval, replaying uncertain calls or publishing partial state. Include schema drift and stale analyzer results. Do not force every optional integration into the default installation.

## Q04 — bounded single-file indexing is not bounded project execution

Existing protection: builder ignores configured paths, reads at most `MAX_FILE_BYTES + 1` per indexed source (`tree/builder.py:293–303`), skips invalid encoding and reuses parsed nodes by content hash. It is deliberately source-free on disk. These are useful protections.

Remaining scale costs:

- `ChangeSet._files` hashes complete admitted files using `read_bytes()` (`agents/changeset.py:115`), including non-indexed extensions; it has no per-file or total project admission. A large binary may be ignored by the source index but still hashed/copied into the task snapshot.
- Snapshot creation copies every baseline file and reads the copy again (`154–155`); failure retains creation evidence and a partial workspace. No disk reservation or whole-project bound prevents filling the staging volume first.
- `build_tree` walks and hashes every eligible file on refresh (`278–307`). "Incremental" means incremental parsing, not zero-read or change-local refresh. The correctness fix for same-size/mtime edits should be preserved.
- There is no cancellation callback in `build_tree` or `ChangeSet.create`; an existing cancellation check around the operation cannot bound the time spent within one large traversal/copy.
- `ToolContext.refresh` rebuilds the tree without previous nodes (`161`) and reconstructs ActivationIndex (`193`) after edits.
- `ActivationIndex.search` runs personalized PageRank over the adjacency graph (`activation/activate.py:128`; `tree/pagerank.py:35–48`) for up to 15 iterations. The lexical top-k does not bound graph traversal size.
- `_get_index` caches up to eight indexes by count (`llm/orchestrator.py:165–176`), not bytes; parallel misses may each construct the same large index before storing it.

These are static complexity/resource findings. No 10k/100k-file performance or OOM claim was measured in this audit.

Proposed sequence: project admission config (files, bytes per file, bytes total, nodes, staging disk reserve), streaming hashes/copy with cancellation checkpoints, bounded partial-state retention, process-shared index generation, then benchmark before optimizing. Add admission reports distinguishing unsupported/skipped source from empty context. Optimize changed-file parsing/postings and bounded graph neighborhoods only if measurements show their value; do not introduce a database or vector service merely to add a technology.

Exit gate: pinned small/medium/large repositories plus adversarial deep trees, huge binaries and many small files; measure cold/warm refresh, single edit, task snapshot, search p50/p95, peak RSS/disk, cancellation latency, and concurrent requests. The workload envelope and hardware belong in the report. Thresholds must be chosen before acceptance, then met without silently disabling correctness checks.

## Q05 — single-process API is a valid scope, not a durable distributed job service

At app creation the semaphore, run dictionary and event-consumer set are process-local (`api/server.py:79–82`). Completed runs expire after 600 seconds; max retained run count is 32 (`153–174`). SSE consumes a live buffer and retains a terminal event rather than a replayable event log (`212–246`). These limits are explicit and useful for a local server.

With N independent workers, each has its own concurrency allowance. A POST landing on one worker followed by a GET/control/SSE on another can produce run_not_found; restart loses live handles. Durable execution jobs and task/session journals elsewhere do not turn this in-memory API registry into a shared job registry. No multi-process live probe was run, so these are consequences of source ownership, not measured load results.

Near-term supported contract: single process per project/service, explicit startup guard for unsupported worker modes, readiness distinct from process health, bounded HTTP admission and documented cancellation/reconnect behavior. If multi-worker or service recovery is a requirement, add shared durable run metadata, leased ownership, fencing and a supervisor before horizontal scaling. Preserve unknown charge/effect recovery rather than automatically replaying a lost run. Choose a lightweight local transactional store first when sufficient; a distributed queue is not a prerequisite for the local framework.

Exit gate: routing across two processes, killed worker/restart, cancellation while queued/running, expired ownership, SSE disconnect/reconnect and exhausted capacity. Verify no double model call, double budget reservation, or duplicate publish. Operational metrics should include queue/admission, stale leases, unknown costs/effects, execution outcomes and failed verification, without prompts/secrets by default.

## Q06 / G01 — measure useful edits independently

The existing evidence is valuable but narrower than production quality:

- Historical calibration: six known changes, base FAIL/gold PASS; not generated fixes and not independent held-out tasks.
- Live QA pilot: eight questions, one fixture corpus; reported 7/8 atomic versus 6/8 full-context on the historical heuristic grader. Package 17 later tightened the finite rubric; do not silently reinterpret old successes as newly graded acceptance.
- Current offline retrieval probe: **13 files / 101 nodes / 13 scored cases; recall@5 0.9231, hit-all@5 0.9231, MRR 0.8269**. The one current miss is `atomic-ops-backward-flow`. One local run took 0.3333 seconds for fixture tree/index/search combined. This tiny single-run timing is not a latency SLO or a scale measurement.
- Current finite rubric (`benchmarking.py:74–90`) explicitly requires independent review for other wording. A citation ID proves identity, not entailment. Unit tests cannot establish a live agent's task success distribution.

Retain the existing `COMPETITIVE_ACCEPTANCE.md` plan: 30 independent tasks from five repositories as an exploratory pilot; repo-level development/holdout split; base FAIL/gold PASS/plausible-wrong FAIL; independent hidden requirement and regression checks; full atomic versus capable plain-agent baseline at equal maximum call/time/cost limits. Include abstention, source conflicts, failed recovery, new files/imports/config changes and runtime failures. Alternate run order and retain every failed/unknown attempt. Add a locked-model/provider cohort first; a separate failover-policy cohort must measure changed model quality and cost, rather than mixing them into one model's score.

Primary endpoint: requirement completed with no regression and no unauthorized effects. Secondary: false success, human intervention, total cost per verified success including failures, wall p50/p95, calls/tokens, resource use. Existing proposed efficiency/quality margins are hypotheses, not achieved thresholds. Five repos and 30 tasks cannot establish general superiority; use paired intervals and a larger preregistered confirmation set when exploratory evidence warrants it.

## Q07 — retrieval and real integration support matrix

The parser builds Python symbols; non-Python files receive a file node and first useful line (`tree/builder.py:142–146`). Accepted JS/TS/Go/Rust/etc suffixes therefore do not mean symbol/call-graph parity. Lexical fields are names/path/signature/summary/callees/imports, not full body semantics (`activation/activate.py:60–67`). Tests receive a fixed 0.25 multiplier (`27–36,116`), which may be counterproductive for test-maintenance requests. Graph linking is name-based and takes up to three same/imported-file candidates (`builder.py:205–224,227–240`); it is not a type-resolved call graph.

Action: explicitly support Python first; add task-conditioned test retrieval, exact source search fallback and benchmark failure analysis before expensive semantic reranking. Add another language only with parser/source-range/edit-checker acceptance and its own held-out tasks. Ablate lexical-only vs graph and context compression on token-budget recall and verified edit outcomes. A high fixture score alone should not choose a new retrieval architecture.

`tests/test_lsp.py:16,30–31,45–56` launches the local synthetic LSP fixture; `tests/test_mcp_integration.py:15–29` launches the in-repo stdio fixture. These validate real subprocess transport and hostile protocol cases, but not interoperability with an independently maintained analyzer/server. Native provider tool and cancellation paths likewise need pinned live contract acceptance separately from scripted HTTP tests (provider audit owns details).

Minimum integration matrix: one real pinned Python analyzer; one independently packaged pinned stdio MCP server; each declared native/provider transport; notebook source-only edit retained; one approved remote worker if advertised. For each record versions, protocol capabilities, framing/encoding, diagnostics freshness, timeouts/cancel, schema drift, credential boundary and unsupported features. The absence of live acceptance must remain visible, not inferred from fixture test names containing "actual" or "real".

## Roadmap dependencies and closure gates

1. **Reliability patch package:** Q01 schema/body admission + Q02 atomic index ownership; focused regressions and exact-source Linux/Windows CI. No model quota needed.
2. **G03 resource envelope:** before snapshot admission, enforce limits and cancellation; measure reproducible scale fixtures. Define the supported single-process API deployment in the same package.
3. **G03 durable integration:** host capability factories bound to staged workspace and shared task controls; real pinned LSP/MCP acceptance. This is a prerequisite for evaluating integrated task success honestly.
4. **G02 release:** isolated wheel/sdist install, CLI/SDK/API and packaged fixture smoke across supported platforms/dependencies. The separate Docker/release audit owns detailed changes; do not close G02 on editable installs.
5. **G01 evaluation:** freeze candidate and run exploratory paired live edits with consented public fixtures/approved quota. No UI milestone is required. Select improvements from failure clusters and only then run confirmation.
6. **Optional service expansion:** shared durable run registry and multi-worker admission only when that deployment is in scope. Multi-root atomic publication/writable workers stay explicit future contracts.

Docker removal changes the execution backend; it does not close Q01–Q07. The quality gates should accept any backend that supplies independently verified isolation/resource/cleanup properties for generated code. A native trusted-repository mode can remain separate, but do not compare it as equivalent containment. Indexing, retrieval, API schema checks, planning and offline evaluation setup in this report need no Docker.

## Reproduction and limitations

Artifacts in this folder:

- `quality_scale_probes.py` / `quality-scale-probes.json`: HTTP schema/errors, deterministic shared temp-file race, API signatures.
- `retrieval_probe.py` / `retrieval-probe.json`: current retrieval on existing source fixtures, no file execution or model calls.
- `quality-probe-workspace/.atomic_flow/tree.json`: harmless final writer marker from the race.

Run from repository root using `.venv/Scripts/python.exe docs/audit-2026-10-23/quality_scale_probes.py` and `.../retrieval_probe.py`. The race probe expects its scratch directory to exist; create it first with PowerShell `New-Item -ItemType Directory -Force docs/audit-2026-10-23/quality-probe-workspace/.atomic_flow`. The initial Python temporary-directory attempt hit this environment's WinError 5 permissions and did not establish a runtime failure; the retained successful probe uses the workspace scratch folder. An installed Starlette warning requests httpx2; it did not change measured status results. No production deployment, broad load test, external server installation, new Docker run or paid/live model call was performed.
