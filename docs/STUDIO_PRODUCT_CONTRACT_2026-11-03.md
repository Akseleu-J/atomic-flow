# Atomic Flow Studio: product and presentation contract

Status: implementation contract, 3 November 2026. This document does not mark
Studio implemented, P07 closed, Rust parity accepted, or a model result verified.
Implement Studio after the agreed TaskRunner/P07 engineering gate. The source
specification is user-provided v2.1, Dark Carbon Edition; the latest user request
sets the sequence: finish TaskRunner/necessary P07, assign modules to appropriate
languages, then reconstruct in separately owned parallel slices.

## Source and precedence

The complete attachment was read:
`C:/Users/omirb/.codex/attachments/76d13d84-3a71-485d-b6b4-16ec0c812aec/Вставленный текст.txt`,
SHA256 `ea79db38a4978a15bf8cffb6d00158ba4f2d001fb1bc9917324400ea602d02b6`.
The local reference image was visually inspected:
`C:/Users/omirb/Downloads/Тёмная панель Atomic Flow Studio.png`, SHA256
`0d2a15c143e3b283114741a7195e173de3fee23d87198538ef2c8e0679867564`.

The text is normative for component behavior, colors and proportions. The image
is a visual reference, not live product state or evidence: its green badges,
free/keyless provider, task counts, spending, example files and timestamps must
not become seeded production facts. Its different budget displays are not a
budget contract. Preserve its quiet three-column composition and matte surfaces;
prefer the text's compact rail and contextual actions over copying every control.

Section 18 says to retain Python Core as source of truth. The user's newer
instruction explicitly requests Rust/TypeScript and other appropriate languages,
so **the Python-only implementation choice is superseded**. The important
architectural rule survives: one host application owns business state, policies,
budget, verification and mutations. Rust is the target host authority; Python may
remain an explicitly bounded compatibility/evaluation adapter during migration.
React/TypeScript, a Rust TUI and an optional Tauri shell are clients of the same
contract, not independent authorities. Detailed module ownership belongs to
`POLYGLOT_RECONSTRUCTION_PLAN_2026-11-03.md`.

## What exists in the Rust checkout

Read-only snapshot:
`C:/Users/omirb/.zcode/workspace/7a9e7cc1-327f-4133-a7af-0ccfe5ee24e8/atomic-flow-dev`,
HEAD `8d0a33bc5267787212ea9a11918496acc77470a6`; dirty tracked files were
`rust/af-agents/src/task_dag.rs`, `rust/af-llm/src/lib.rs`,
`rust/af-loop/src/compact.rs`. This task does not change that checkout.

| Existing item | Useful starting point | Remaining product boundary |
|---|---|---|
| `rust/af-cli/src/dashboard.html` | Single-file Dark Carbon layout, exact palette, toolbar/rail/explorer/chat/summary/composer | Inline JS, limited keyboard support; no separate React app found in the inspected file inventory |
| `rust/af-cli/src/dashboard.rs` | Embeds HTML served by `af serve` at `/dashboard` | Static asset delivery is not complete Studio behavior |
| `rust/af-cli/src/server.rs` | Local HTTP, sessions, edit journeys, jobs, graph, POST chat SSE | No complete durable Studio event/reconnect/cancel contract in the inspected stream handler |
| `ts/af-client/src/index.ts` | Fetch-injected typed client for health/sessions/journey/jobs | Narrow interfaces, unchecked `as` response casts, no complete Studio entities or streaming client |

The implementation choice is to preserve the existing visual tokens and useful
Rust transport/kernel work, then replace the embedded UI with a typed component
application. It is unnecessary to throw away the layout or keep a large inline
script as the final production UI.

Concrete observed gaps to remove before cutover:

* `dashboard.html:731` labels a successful chat stream `Verified — scripted/kernel`
  without claim evidence. A successful response is not a verified claim.
* `dashboard.html:729` interpolates streamed model text into `innerHTML`; model
  text must be rendered as text or through a sanitized Markdown renderer.
* Task mode records `user_input`; it does not demonstrate a full task lifecycle.
  `snapshotSession()` appends `snapshot_created` without snapshot bytes;
  `recoverNote()` shows text. Neither is a completed snapshot/recovery operation.
* The SSE handler uses unbounded channels and emits delta/final/error without
  durable event IDs, replay cursors or a caller cancellation operation. Typed
  casts in the current SDK do not validate incoming data.
* Current UI derives multiple check labels from one `report.verified` boolean;
  show individual check receipts and their scope instead.

These are source observations, not browser reproduction or an independent audit.
Reviewed bytes: dashboard HTML
`a065bc0e5cec4cd7f1805a446f3376a88937a5705a9065ab769341a1865f8af2`,
server `6aa1ea38607e881a6ebdd271df31512f7cfbabf434897485ba1e5335520ac27f`,
TS client `0a68e219ae169a08b4aa84081204908f4a792306a29a7b96fdcb2f7eb5b1b31c`.

## Visual contract

Positioning: **The AI Development Environment You Can Verify.**
Product sequence: **Context → Evidence → Verification → Changes → Publication**.
Visual direction: **Dark Carbon × Moody Violet**; matte panels, fine separators,
small surface brightness differences, restrained shadows and gradients. Avoid
large violet fills, constant neon glow, glassmorphism and equally strong accents
everywhere. Glow is restricted to active/focused elements. Primary actions use
a dark violet gradient; emerald/amber/red communicate status alongside text/icons.

| Semantic token | Exact dark value |
|---|---|
| Main Background | `#090A0D` |
| Secondary Background | `#0D0E13` |
| Primary Panel | `#111218` |
| Elevated Panel | `#171820` |
| Secondary Surface | `#1C1D27` |
| Hover Surface | `#23232F` |
| Primary Border | `#292832` |
| Secondary Border | `#373342` |
| Primary Accent | `#625080` |
| Deep Violet | `#49365F` |
| Muted Violet | `#79658F` |
| Soft Lavender | `#A18BB9` |
| Success | `#28C981` |
| Warning | `#DDA64C` |
| Error | `#D65C62` |
| Information | `#69A9D8` |
| Primary Text | `#E5E3EA` |
| Secondary Text | `#A5A1B0` |
| Muted Text | `#74717F` |

Inter is the interface family; JetBrains Mono is for code, paths, hashes and
technical values. Bundle fonts or provide system/monospace fallbacks so offline
startup does not depend on the current dashboard's Google Fonts request. Text
hierarchy uses light gray rather than pure white. Color alone never conveys a
state. A light theme is required, but the source specifies no light palette:
define semantic light tokens and validate them separately instead of inverting
the dark palette or claiming an already approved light design.

Use a shared spacing scale and Compact/Comfortable densities across components.
Each component defines Default/Hover/Active/Focus/Disabled/Loading/Success/
Warning/Error states where meaningful; loading is not a success placeholder.

## Layout and navigation

| Region | Source size / behavior |
|---|---|
| Global Navigation | Persistent left rail, 64–76 px |
| Context Explorer | 270–330 px |
| Agent Workspace | Highest width priority, 50–60% of available width |
| Verification Summary | 290–340 px |
| Global Status Bar | 30–36 px |
| Contextual Action Bar | Floating graphite surface above Composer, present only for a concrete changeset |
| Global Toolbar | Global project/session/provider/budget controls; source does not prescribe its height |

These are desktop targets, not simultaneous rigid constraints on every viewport.
Side panes can resize, collapse/restore independently and persist layout locally
per workspace/user. On constrained widths collapse side panes into accessible
drawers/tabs before starving the center; restore a valid layout after viewport
changes. Exact responsive breakpoints are an implementation decision, to be
accepted through browser QA rather than presented as source requirements.

Navigation: Home, Tasks, Workspaces, History, Analytics, Index, Settings. The rail
footer exposes active project, branch, connection, budget and profile. Toolbar:
logo/project/version left; sessions/agents/task navigation/command palette center;
provider status, budget, session settings and search right. Avoid simultaneously
showing competing duplicate budget controls on every surface; the status bar's
budget is a compact projection of the same value, not another ledger.

The bottom bar shows only system Ready/Running/Error, branch/Git changes, Core
connection, session, budget and indexing. Current task progress belongs to task
and agent views rather than another duplicate bottom progress display.

## Components and interactions

| Component / area | Required behavior |
|---|---|
| `NavigationItem` | Selected rail section and keyboard focus are distinct; a thin active stripe, graphite hover, muted violet icons |
| `ContextTreeNode` | Name/type/relevance/usage/citation count; hierarchy lines, no colorful block for every node |
| `RelevanceIndicator` | Relevance is separate from verification; missing scores display unknown, not `0%` or fabricated percentages |
| Explorer Files | Project hierarchy, files/directories, relevance, usage |
| Explorer Context | Nodes actually selected for the current request, order, context size and selection reasons |
| Explorer Sources | Citations, exact line ranges and source-to-claim relations |
| Explorer Graph | File/component links and semantic dependencies with unresolved edges kept visible |
| Inline explanation | Expand a file to show score if measured, reason, semantic link, matched/used fragment counts and exact bytes/fragments sent to the model |
| Context actions | Include/Exclude/Pin/Inspect/Explain; the host accepts/rejects mutations and returns a new context revision and metrics |
| `AgentTab` | Main/Research/Code/Review roles; actual state/provider/tokens/budget/result; no invented active agents |
| Workspace | Conversation, code, diff, sources/evidence and task outputs; Markdown/code highlighting/tables/collapsible blocks/code copy/file-line links/tool outcomes |
| `EvidenceChip` / `VerificationBadge` | Verified/Partial/Unsupported/Contradicted text+icon; Verified means supported by available context, not universal truth |
| `EvidencePopover` | Source text, file identity, range, file link, status and explanation; available through keyboard/click as well as hover |
| Citation navigation | Open correct workspace/root/revision/file, scroll and highlight exact range, show related claims; stale/missing source is explicit |
| `VerificationSummary` | Actual counts of supported/partial/unsupported/contradicted claims; selection navigates to the corresponding claim |
| Changes summary | Actual changed files and added/removed lines with secondary full-diff action |
| `Composer` | Ask/Edit/Task, auto-height Markdown input, Attach, file/agent mentions, Send, cancel generation, shortcuts |
| `DiffViewer` | Revision-bound source and proposed changes with file navigation; cannot itself grant apply/publication authority |
| `TaskCard` | Durable task lifecycle and real check/publication/recovery state; List and Board views |
| `CommandPalette` | New Conversation, Create Task, Search Files/History, Reindex, Show Diff, Create Snapshot, Recover Session, Switch Provider, Open Settings |
| `StatusBar` | System state projected from host connection/session/index state, no synthetic readiness after an unknown operation |

Explorer usage values from the source are Used/Retrieved/Verified/Excluded/
Rejected. They are orthogonal to claim verdicts. A host must provide what a file
level Verified means and its evidence scope; retrieval relevance never upgrades
a file or claim to Verified. Absent verification data has an explicit not-assessed
presentation, in addition to the four assessed claim verdicts.

Do not show hidden model reasoning. Show admitted actions, outputs, concise
statuses, uncertainty and evidence. Model content cannot create a host receipt.

The Composer does not permanently expose provider selection, budget controls,
detailed generation options or duplicate spending readouts. Those live in global
session/provider settings. Ask/Edit/Task commands remain distinct in both UI
and API; an Edit conversation is not an applied edit.

## Change management and authority

| Host changeset state | Contextual primary action |
|---|---|
| No concrete changes | Bar hidden |
| Draft exists | Review Changes |
| Checks accepted for this revision | Apply Changes |
| Checks rejected | Fix Issues |
| Changes applied | Create Snapshot |
| Ready for publication | Publish |
| Interrupted publication | Recover |

The source example `[Review Diff] [Snapshot] [Apply Changes]` is a stateful action
bar, not three always-enabled effects. One action is primary. Every effect uses
an authoritative host capability bound to workspace, changeset and revision.
Review approval is invalidated when source, scope, plan or check obligation changes.
The browser must not send a trusted `host_approved: true` boolean as sufficient
proof. Approval is a host operation with the reviewed revision recorded.

Task sections: Active, Draft, Verified, Ready to publish, Published, Rejected;
List and Board representations. Each task contains description, original request,
context, plan, executed steps, changed files, checks, Git diff, publication and
operation history. User-facing Draft → Verified → Published is a simplified
projection of the host state machine. Preserve Failed/Interrupted/Rejected/
Recovery Required and pending/unknown operations; do not invent a terminal success.

Recovery reconciles which operations actually completed and preserves uncertain
effects. A journal marker alone is not a snapshot; a recover note alone is not
restoration. Before an irreversible operation, present the concrete reviewed
operation and obtain the applicable user decision. Reversible edits and ongoing
tasks still follow the authority already granted; this UI requirement does not
add redundant assistant permission prompts to the current authorized work.

## Host API and client contracts (proposed target)

The following target interface is a design proposal, not a claim that the existing
Rust routes implement it. Freeze schemas before parallel implementation. Keep
the existing `/v1` compatibility surface or version a breaking replacement; do not
silently change receipt semantics under an unchanged version.

| Shared entity | Required identity and fields |
|---|---|
| Session / Agent | Stable IDs, parent relationships, actual lifecycle and unresolved effects |
| Workspace / ContextNode | Root/workspace IDs, source revision, path/node identity, retrieval selection and measured relevance if available |
| Citation | Citation ID, source identity/revision/hash, exact range, excerpt and claim relations |
| Claim / VerificationReport | Claim/message IDs, assessed verdict or not-assessed, reason, evidence refs, scope, checker/profile/version and source revision |
| Task / Diff | Task/changeset IDs, base/current revisions, planned/changed paths, real checks, operation/publication receipts and recovery state |
| ProviderStatus | Configured provider handle, mode/capabilities, connection/quota observation timestamp and explicit unknown/unavailable values |
| Budget | Host ledger identity, currency, known spend, unknown charges/holds, reservation, approved cap and observed usage; unknown is not zero |
| StreamEvent | Schema version, stream/session/task IDs, stable event ID, monotonic sequence, correlation/operation IDs, timestamp and typed payload |

Presentation owners:

* Rust host: durable state, source admission, plans/scopes, execution, check
  receipts, budgets, providers, command authorization, CAS changes, publication,
  recovery, replay and event persistence. It redacts before serving data.
* React/TypeScript: typed projections, rendering, navigation, layout, command
  composition and recoverable client caches. No independent verification or
  budget authority in component state.
* `af-client`: shared request/response decoding, error/hold variants, cancellation,
  bounded event parsing, cursor/dedup/reconnect behavior. Generate wire types from
  one canonical schema and validate untrusted responses at runtime.
* Rust TUI: same host operations and schema, adapted for terminal focus/navigation.
  Tauri, if selected, is an OS integration shell rather than another agent core.

Proposed operation families:

| UI requirement | Host operations / events |
|---|---|
| Startup and project | Capability/health handshake; open admitted workspace; workspace/index snapshot + progress |
| Context | Get context snapshot; include/exclude/pin command with expected revision; context selection changed |
| Conversation / agents | Create/resume session; submit Ask/Edit/Task with admitted provider handle; message/tool/agent state events |
| Evidence | Read revision-bound source range and verification report; claim assessment updated/stale |
| Change review | Read changeset/diff/checks; request recorded approval; apply expected revision; source/check/changeset state events |
| Tasks | Task snapshot/list; start/pause/cancel/replan within granted scope; authoritative task state transitions |
| Snapshot/recovery | Create real snapshot; inspect pending effects; reconcile or restore through host; durable operation receipts |
| Provider/budget | Read configured capability/usage/quota ledger; configure through trusted host settings; changed/unknown/hold events |
| Analytics | Read measured, timestamped series with corpus/configuration/count/scope, or unavailable response |

No browser-supplied arbitrary provider URL, credential environment variable name,
shell command, external root or forged journal event may grant authority.
Provider credentials remain on the host; clients select an admitted opaque
provider handle. Authenticated local commands require explicit origin/session
policy, including loopback deployments; desktop exposure is not assumed safe
merely because it is local. Authentication material never belongs in URL query
strings, logs or persisted UI settings.

SSE/reconnect requirements: schema negotiation; event IDs/cursor; bounded parser
and backpressure; resumable read-only stream; duplicate event suppression;
sequence-gap detection followed by authoritative resync; explicit disconnect/
truncation states. Reconnecting to events must never replay a model send, edit
or publication. Unknown post-send outcomes retain host holds. Cancel is a real
operation with an acknowledged outcome; closing the browser is not proof that
execution stopped. A consumer unable to decode a critical event stops effect
commands and requests a compatible snapshot rather than guessing state.

## Provider, analytics and onboarding truth

Expose Keyless/OAuth/API Key/Local/Offline as supported modes/capabilities, not as
a guarantee that all are configured. Show legal keyless access only when a real
source is available. Offline startup can open/index/inspect permitted local
projects without a model credential; Send explains unavailable inference rather
than simulating a live answer. Do not attach real model branding to a scripted
backend response.

`atomic studio` is the target entry point. First launch selects/opens a project,
analyzes structure, starts indexing, detects available provider capabilities,
then permits a first request within those capabilities. Provider configuration
is not mandatory to launch. Resource limits and consent still apply to external
inference or untrusted execution; zero-config does not mint credentials or grants.

Analytics sections: Project Overview (index/files/tasks/budget/system), Context
Analytics (graph/relevance/sources/context/index history), Retrieval Benchmarks
(Recall@K/Precision@K/MRR/latency/config comparisons), Task History (verification,
changes/publication/recovery). Show absent data as not measured. Metrics retain
scope, corpus/configuration/version, N and timing boundaries; historical/static
fixtures never become current production success rates.

## Accessibility and shortcuts

Accessibility is an implementation requirement, not a claim of current compliance.
Use semantic navigation/landmarks, labeled controls, proper tree/tab/dialog and
separator semantics, keyboard-operable resizing/collapse, visible focus, focus
restore, screen-reader labels and text/icon status equivalents. Popovers work
with focus and touch. Expose async status through bounded live announcements,
without announcing every token. Respect reduced motion and text scaling; measure
contrast for actual foreground/background pairs in both themes. Muted Text is
not a blanket approval for low-contrast body copy. No inaccessible hover-only
action or color-only error state is accepted.

Shortcuts from the specification:
`Ctrl+K` palette; `Ctrl+P` file search; `Ctrl+Shift+P` commands;
`Ctrl+R` history search; `Ctrl+B` sidebar; `Ctrl+J` bottom panel;
`Ctrl+Enter` send. Resolve browser shortcut conflicts only inside the application's
intended scope; support platform-equivalent modifiers where appropriate and show
them in help. Enter/Shift+Enter behavior is a documented Composer setting; the
spec's explicit send shortcut is Ctrl+Enter, not an accidental double-send through
both global and textarea key handlers. IME composition must not submit a message.

## Acceptance cases and implementation sequence

Cases below are required behavioral acceptance work, not tests already run.

| Case | Observable result |
|---|---|
| Dark/Light × Compact/Comfortable | Shared semantic tokens, stable layout, legible contrast, complete focus/loading/error states; no permanently glowing surfaces |
| Desktop to constrained viewport | Independent pane resizing/collapse/restore, central workspace usable, valid persisted layout and keyboard access |
| Context retrieval and mutation | Actual fragments sent to the model can be inspected; Include/Exclude/Pin yields host revision or refusal; relevance never turns into Verified |
| Claim with supporting/conflicting/missing/stale evidence | Correct distinct verdict and popover; exact source/range navigation; aggregate counts match the same report |
| Successful response without verification receipt | Text shown with not-assessed status, never a green Verified badge |
| Model HTML/script payload | Rendered safely as text/sanitized content; cannot execute HTML/JS or create authoritative controls |
| Concrete changeset | Only relevant primary action shown; stale source/check/approval prevents Apply/Publish; failure and unknown remain explicit |
| Snapshot and recovery | Real host receipt and retained bytes/revision; interrupted publication reconciled, never a cosmetic completion marker |
| SSE split frames/duplicates/gap/disconnect/reconnect | Bounded decode, dedup/resync, no automatic repeated effect or provider call, honest hold/cancel result |
| Host unavailable or incompatible schema | Offline/disconnected state; effect controls unavailable; reading retained data does not grant stale authority |
| Missing provider/cost/quota | Explicit unavailable/unknown values; no free provider promise or `$0` substitution for unknown spend |
| Full keyboard/screen-reader/IME journey | Project/context/source/diff/task/palette/confirmation reachable; focus predictable; no duplicate send |
| Rust/Python migration parity | Same fixture corpus compares refusals, unknown effects, budget settlement, source binding, publication and recovery; UI does not choose its preferred backend result |
| Mixed Rust/TypeScript project pilot | Real task reads both source families, native checks are distinct receipts, one rejected check prevents publication; scripted versus model evidence separately reported |

Preserve the source's six delivery phases: (1) Core Studio/navigation/explorer/
workspace/summary/composer/SSE; (2) Context Intelligence/tree/explanation/previews/
citations/popovers/inline verification; (3) Change Management/diff/snapshot/apply/
task lifecycle/recovery; (4) Multi-Agent streams/shared context/handoff/control;
(5) Analytics; (6) Production Polish/shortcuts/recovery/accessibility/performance/
large-project tests/packaging. Accessibility and honest states begin in phase 1;
phase 6 validates completeness, it is not permission to postpone basic access.

Start by freezing the shared contracts and extracting reusable tokens/layout from
the existing dashboard. Implement and accept one real vertical slice before broad
visual completion: workspace → context → request/task → evidence → reviewed diff
→ host check → authorized apply → durable result/recovery. All decorative sample
content must be visibly isolated from production state. Studio acceptance and
research/model-quality acceptance remain separately named.
