# Polyglot-реконструкция Atomic Flow — карта модулей и приёмка

План подготовлен 3 ноября 2026 по прямому поручению владельца: сначала
TaskRunner и необходимая граница P07, затем реконструкция с отдельными
областями для параллельных агентов. Этот документ — план, не свидетельство
выполненной миграции или закрытого P07.

## Исходная точка

Основная копия: `C:/Users/omirb/OneDrive/Документы/ChatGPT/atomic-flow 2/atomic-flow`,
HEAD при исследовании `ff9ef351af3a2520478cbf3251031c76d7d66077`.
Существующая незакоммиченная правка
`tests/test_p07_sympy20212_setuptools_focus.py` не входит в эту работу.

Внешняя копия: `C:/Users/omirb/.zcode/workspace/7a9e7cc1-327f-4133-a7af-0ccfe5ee24e8/atomic-flow-dev`,
HEAD `8d0a33bc5267787212ea9a11918496acc77470a6`. Проверено read-only:

- `rust/` содержит 13 workspace crates и Cargo.lock. Это уже сделанная основа,
  которую следует переносить с проверкой, а не переписывать заново без причины.
- Dirty: `rust/af-agents/src/task_dag.rs`, `rust/af-llm/src/lib.rs`,
  `rust/af-loop/src/compact.rs`. HEAD не описывает эти рабочие bytes.
- `rust/af-py/` — самостоятельный PyO3 crate, исключённый из kernel workspace;
  его успешность не следует из результатов 13 workspace targets.
- Реальный интерфейс — `rust/af-cli/src/dashboard.html`, single-file HTML/CSS/JS,
  встроенный через `dashboard.rs` и обслуживаемый Axum `/dashboard`.
  Готового React/Tauri приложения в просмотренной структуре нет.
- `ts/af-client/src/index.ts` — TypeScript-клиент HTTP API; есть package.json,
  tsconfig и Vitest tests. Это полезная заготовка клиента, не вся Studio.

Предыдущий аудит полного Rust workspace дал 225 PASS / 1 IGNORED; это
сохранённое evidence из `P07_CONTINUATION_AND_RUST_REVIEW_2026-11-03.md`.
В этом планировании тесты не повторялись, source внешней копии не менялся.

## Архитектурное решение

**Rust — единственный владелец runtime authority. TypeScript — UI, клиент и
семантические адаптеры JS/TS. Python — compatibility SDK, benchmark/oracle и
научная среда. SQL — при необходимости язык производных индексов.**

Это выбор по границам ответственности, а не попытка добавить больше языков.
Go, C++, Java и отдельный сетевой микросервис сейчас не получают модуль только
ради разнообразия. Дублирование ядра в нескольких языках увеличит число
вариантов accounting/publication и затруднит проверку.

Вложенная UX/UI Specification v2.1 остаётся продуктовой и визуальной целью.
Её §18 «Сохранить Python Core в качестве источника истины» заменяется последним
прямым поручением о реконструкции: до cutover действующий Python runtime —
reference implementation; после принятого cutover authority принадлежит Rust.
Одновременных независимых владельцев одного mutable task/session быть не должно.

## Карта переноса

Пути назначения ниже находятся в основной копии. Столбец «основа» означает
файл/модуль во внешней Rust-копии; наличие основы не равно parity.

| Текущий Python-модуль или область | Язык и назначение | Существующая основа / что дописать |
|---|---|---|
| `graph/schema.py`, `identity.py`, `operations.py`, `store.py`, `delta.py`, `relocation.py`, `resolve.py`, `project.py`, `ingest.py`, `sync.py` | Rust, `rust/af-graph/` | Crate уже есть; перенести точные canonical bytes, CAS, generations, reverse indexes, clean-build/delta equivalence и explicit unresolved/omissions |
| `graph/adapter_ast.py`, `python_bindings.py`, `adapter_resource.py` | Rust, language adapters в `af-graph` | Python AST/resource основа есть; сохранить source spans, duplicate identities, bounded parsing и conservative resolution |
| Семантика Rust-проектов: modules/use/reexports/macros/diagnostics | Rust, отдельный Rust adapter и host-approved LSP bridge | Текущий `af-graph` зависит от rustpython-parser: это Python parser, не готовая Rust semantic coverage; unresolved macros не выдавать за resolved |
| Семантика TS/JS: imports, exports, tsconfig paths, types, diagnostics | TypeScript, `ts/af-language-typescript/`; JSON data-only bridge | Новый worker использует язык/проектный контекст TS. Парсить source, не import/execute код проекта; Rust проверяет root/revision/span/hash перед graph merge |
| `tree/*`, `activation/*`, `graph/read_plan.py`, `graph/impact.py`, `agents/context.py`, `llm/context_state.py` | Rust, `af-context` + `af-graph` | `af-context` уже есть; сохранить BM25F activation, graph spreading, ranking/tie-breaking, token/byte bounds, read proof и stale-source refusal |
| `sessions.py`, `state.py`, `dialogue.py`, `runtime.py`, `jobs.py` durable observations | Rust, `af-journal` + `af-exec` | Append-only integrity, schema migration, tail recovery и UNKNOWN уже частично представлены; довести persisted identity и crash boundaries |
| `budget.py`, `backends/usage.py`, `agents/task_budget.py`, `repair_budget.py`, `reconcile.py` | Rust, отдельный `af-budget` authority API + journal persistence | Не прятать accounting в transport. Reserve/checkpoint/send/settle, original day/owner/call identity, shared parent budgets, UNKNOWN holds — один владелец |
| `agents/task_loop.py`, `task.py`, `goals.py`, `frontier.py`, `rolling_plan.py`, `plan_review.py`, `replan_*`, `failure_*`, `completion.py` | Rust, `af-agents`, `af-agent`, `af-loop` | Основа есть, но не заявлена полная Python TaskRunner parity; перенести approved scope, lineage, bounded replan, terminal-state и fresh-final semantics |
| `agents/tools/*`, `agents/source.py`, `source_lock.py`, `workspace.py`, `agents/changeset.py`, `file_transaction.py`, `undo.py` | Rust, `af-sandbox` + новый `af-publication` | Разделить preview/CAS staging и publication authority; не переносить «успех» из произвольного tool JSON |
| `agents/execution.py`, `windows_job.py`, `job_worker.py`, `remote.py`, `remote_check_adapter.py`, `check_*.py` executable boundary | Rust, `af-exec` + host-owned check adapters | Перенести typed receipts, immutable image/source/inventory bindings, process-tree lifetime, cancel, cleanup, no-host-mount profiles; LocalTrusted остаётся явно trusted |
| `backends/*`, `credentials.py`, `kaggle/*` | Rust, `af-llm` + capability-scoped credential broker | Transport возвращает typed Completed/Refused/Unknown и usage; не решает самостоятельно разрешение расхода/повтора; provider secrets не попадают в UI |
| `watching.py`, `project_resources.py`, `notebooks.py`, `lsp.py`, `diagnostics.py` | Rust, `af-workspace` / language-host adapters | Root grants, UTF-16 ranges, notebook source-only operations, revision-bound diagnostics. Исполнение LSP executable только через host approval |
| `memory.py`, `summaries.py`, `llm/history.py` | Rust, `af-memory` + `af-loop/compact` | Advisory memory не переносит capabilities/read proof; сохранение anchors/tool lineage и bounded compaction |
| `integrations/mcp.py`, `integrations/ide.py`, `hooks.py`, `extensions.py`, `workflows.py`, `workers.py` | Rust, `af-integrations` + `af-program` | Trusted host extensions, grants и shared budgets. Data из MCP/IDE не становится authority |
| `api/server.py`, `cli.py`, `project_cli.py` | Rust, `af-api` + `af-cli` | Выделить Axum API из монолитного CLI server.rs; общие application services для CLI/HTTP/TUI. Совместимость старых API — отдельные adapters |
| `config.py`, `errors.py`, `output.py`, `execution_inputs.py`, `textutil.py`, `documentation.py` | Rust, shared configuration/errors/input admission и CLI presentation helpers | Layered trusted configuration, strict bounds/redaction и typed errors; форматирование UI не выдаёт runtime permission |
| `sdk.py` и Python public API | Python thin client, при необходимости `rust/af-py/` | Делегировать Rust authority через versioned API; PyO3 для узкого embedded kernel use-case, не вторая реализация task/budget/publication |
| Studio, web dashboard, context tree, claims/citations, diff, tasks, settings, command palette | React + TypeScript, `ts/af-studio/` | UX v2.1 — целевая структура; из HTML взять visual tokens/layout и проверенные сценарии, затем разложить на компоненты |
| Shared client и frontend state/event projection | TypeScript, `ts/af-client/` | Расширить имеющийся client: runtime wire validation, graph/tasks/evidence, bounded streaming, cursor replay/dedup, cancellation и typed holds |
| TUI / возможная desktop shell | Rust, `af-tui/`; Tauri shell только при необходимости | Presentation adapters поверх того же API. Ни TUI, ни desktop wrapper не нужны для доказательства корректности kernel |
| `qa_evaluation.py`, `qa_rubric.py`, `benchmarking.py`, `tools/evaluation*`, `benchmarks/*`, исторические oracle/calibration tools | Python evaluation/lab | Сохранить независимые graders и historical evidence; не переводить их одновременно с проверяемым runtime, иначе теряется независимый reference |
| Задания, история, retrieval/analytics read models | SQL migrations/queries под Rust storage owner, только при подтверждённой необходимости | Начать с текущих canonical stores/journal. SQLite/FTS может быть rebuildable index; не менять authority-format и storage engine одновременно с языком runtime |

## Общие контракты до параллельной записи

Интегратор владеет `schemas/`, корневыми workspace manifests/lockfiles,
генерацией типов и совместимостью. Сначала фиксируются:

1. `Task`, `Session`, `Workspace`, `RootGrant`, `Plan`, `Step`, `CheckSpec`,
   `CheckReceipt`, `FailureEvidence`, `PublicationReceipt`, `BudgetReservation`,
   `ProviderOutcome`, `ContextNode`, `Claim`, `Citation`, `VerificationReport`.
2. `StreamEvent`: schema version, session/task/run IDs, monotonic sequence,
   opaque replay cursor, event ID, source revision, terminal flag, typed payload.
   Reconnect не повторяет команды; replay и dedup действуют на observations.
3. Claim verification отличается от test execution, syntax, model self-report,
   source-byte check и publication status. `not_assessed`, `partial`, `unknown`,
   `stale`, `failed`, `verified_in_scope` не схлопываются в один boolean.
4. Command requests и observation events разделены. Клиент не может своим
   `appendEvent` создать авторитетные verified/snapshot/published records.
5. Generated Rust/TS DTOs + runtime validators + canonical fixture vectors.
   Typescript `as SomeResponse` сам по себе не проверяет wire data.

JSON Schema описывает shape, не выдаёт grants и не аутентифицирует worker.
Business state machine и решения об эффектах исполняются только в Rust host.

## Что исправить в текущей Rust-основе до cutover

Пять блоков из предыдущего code review сохраняются как проверяемые задачи:

| Задача | Требуемое поведение и негативная проверка |
|---|---|
| `af-agent` journal error propagation | Disk-full/lease/append failure прекращает дальнейшие эффекты. `let _ = journal.append(...)` в authority path убрать; уже выполненные эффекты остаются в recovery/unknown accounting |
| `af-loop/edit` final gate | `exit 0` без полного host-owned check receipt не даёт task verification/publication; zero-test, missing test, required skip, stale revision и forged receipt отклоняются |
| `af-exec` Docker profile | Immutable image/ID, non-root, explicit memory/CPU/PID/tmpfs/output/time bounds, network/mount policy, inspect/cleanup. Проверяется реальный Linux worker, не только построенный argv |
| `af-sandbox` path admission | Portable POSIX-relative path → native Path components; nested paths и symlink/junction escapes проверяются на Windows и Linux. Замена `/` на `\\` не годится для Unix |
| `af-program/eval` aggregation | Passed-vs-NotAssessed не считается доказанной победой. Comparable/incomplete arms и unknown cost отдельно; исходные результаты не отбрасываются |

Дополнительно по текущему interface/client source:

- SSE использует unbounded channels; нужны backpressure, cancellation и bounded
  retained replay. В прочитанном endpoint не было event IDs/cursor recovery.
- AfClient пока не покрывает graph/SSE/tasks/claims и использует unchecked casts.
- HTML badge зависит от глобального `lastJourney?.verified`: в Studio статус
  должен быть привязан к конкретному claim/source revision/check scope.
- HTML Snapshot сейчас добавляет `snapshot_created` event. Новый action обязан
  вызывать реальную host snapshot service и показывать её receipt.

## Импорт существующей основы без потери чужой работы

1. Внешнюю копию оставить нетронутой. Записать HEAD, dirty diff и per-file SHA256
   в новый import manifest; секреты и build output не переносить.
2. Перенести проверенные `rust/` crates, `Cargo.lock` и `ts/af-client/` в main
   отдельным reviewable пакетом. Не копировать `target/`, node_modules, caches,
   user config, credential files, generated private runtime artifacts.
3. Три dirty файла импортировать только как явно обозначенный source snapshot
   с их SHA256 и diff к donor HEAD. Не называть его чистым `8d0a33b`; не выполнять
   destructive checkout/reset/clean над donor. До записи интегратор сверяет,
   что donor bytes ещё соответствуют manifest.
4. Зафиксировать обе реализации как side-by-side backends. Переключатель runtime
   привязан к task/session manifest; нельзя молча менять runtime на resume.
5. Переносить модуль вместе с контрактами отказа и acceptance fixtures, затем
   переводить consumers. Старый Python implementation остаётся reference,
   пока этот scope не принят. Исторические архивы и SHA manifests сохраняются.

Проверенные в этом исследовании donor SHA256:

| Файл | SHA256 |
|---|---|
| `rust/Cargo.toml` | a4102169e04418633255597ba83ae8b4a587de29ab16966683706f936373df15 |
| `rust/Cargo.lock` | 59541aa2657ea0f66e908cf1fb66f163fe29fdfd6bed62b0faaa77fb96aa49fd |
| `rust/af-cli/src/server.rs` | 6aa1ea38607e881a6ebdd271df31512f7cfbabf434897485ba1e5335520ac27f |
| `rust/af-cli/src/dashboard.html` | a065bc0e5cec4cd7f1805a446f3376a88937a5705a9065ab769341a1865f8af2 |
| `ts/af-client/src/index.ts` | 0a68e219ae169a08b4aa84081204908f4a792306a29a7b96fdcb2f7eb5b1b31c |
| `rust/af-agents/src/task_dag.rs` (dirty) | 43119679818cc1e8b483515a6805853369c49fa7a2bf92dd7f67e476ac0825a7 |
| `rust/af-llm/src/lib.rs` (dirty) | 348dd7795d540203608d68e71adc6af09191c8a5054c056c049b642e6f9aadfc |
| `rust/af-loop/src/compact.rs` (dirty) | 1e32b7a188c1d427f56d3461d73842c0a99bd87762c866451ef55788b7f34d0a |

Эти строки — snapshot выбранных файлов, не полный import manifest.

## Параллельные агенты: области без конкурирующих владельцев

На текущем executor доступно три child slots. Интегратор выполняет общие
manifest/schema изменения; агенты не редактируют чужую область и не делают
независимые push/merge. Каждая волна начинается после контрактов её зависимостей.

| Волна | Агент A | Агент B | Агент C | Gate интегратора |
|---|---|---|---|---|
| 0 | Read-only audit | Read-only audit | Read-only audit | TaskRunner/нужный P07 scope принят; donor import manifest; schemas и ownership table frozen |
| 1 | `af-journal`, новый `af-budget` | `af-graph`, `af-context` | `af-sandbox`, `af-exec` | Общие schema vectors; journal/UNKNOWN/receipt/path/execution parity; реальные Linux controls |
| 2 | `af-llm`, capability credential broker | `af-agents`, `af-agent`, `af-loop`, `af-publication` | `ts/af-language-typescript`, новый Rust bridge crate | Один budget owner; TaskRunner lifecycle; source-bound multi-language graph; integration check |
| 3 | `af-api`, `af-integrations`, `af-cli` | `ts/af-client` | `ts/af-studio` | Real API/client/component journeys; SSE reconnect/cancel; no business authority in UI |
| 4 | Packaging/installed Rust consumer | Python thin SDK / optional af-py | Optional TUI/desktop adapter | Installed consumer outside checkout, migration/rollback drill; supported scope documented |

Чтобы wave 3 была параллельной, client API и event schemas замораживаются до её
начала; изменения контракта возвращаются интегратору. UI может использовать
stub для разработки, но приёмка требует реальный API. Сборку Rust на этой
Windows-машине выполнять с `-j 1`; не запускать три одновременные тяжёлые build.
CPU-local fixtures отделять от Linux Docker evidence и live provider calls.

## Пилот и конечная граница реконструкции

Пилот должен содержать Rust и TypeScript одновременно: например, маленький
Rust service + TypeScript client со связанным versioned contract. Задача меняет
оба слоя, требует native Rust и TS checks, проверяет graph impact через языки,
публикует только утверждённые пути и выполняет fresh final на финальных bytes.
Python-only pytest6116 journey остаётся regression/acceptance evidence своего
scope и не переименовывается в polyglot pilot.

Минимум для cutover конкретного модуля:

1. Одинаковые frozen fixtures проходят Python reference и Rust implementation;
   сравниваются semantic outcomes и canonical bytes там, где это часть контракта.
2. Проверяются отказы: stale source, forged/incomplete receipt, required skip,
   denied scope, quota reservation, unknown-after-send, lost checkpoint, cancel,
   disk-full и publication/recovery conflicts. Green happy path недостаточен.
3. Публичный scripted полный TaskRunner journey доказывает механику; live model
   generation и независимый oracle grade учитываются отдельно. UNKNOWN никогда
   не снимается для ускорения миграции и не становится разрешением новой send.
4. Rust+TS pilot исполняется нативными toolchains в принятых isolated workers,
   с фиксированными versions/locks, source hashes, outcomes, CPU/RSS/wall time.
   Trusted local execution не называется изоляцией.
5. Реальный browser journey: source citation открывает точные revision/lines;
   draft → review → apply → fresh verification → publication/recovery;
   disconnect/replay не дублирует command или provider attempt.
6. Installed consumer вне checkout и recovery/rollback проверены. Cutover receipt
   называет module/supported scope, exact commit, old/new storage identities и
   оставшиеся gaps; старое implementation не удаляется до такого receipt.

Новый bounded engineering exit P07 должен быть явно оформлен интегратором как
изменение плана по текущему поручению владельца. Прежние 30 paired study,
holdout и ablations нельзя автоматически отметить выполненными. Их можно
сохранить отдельной исследовательской программой без блокирования безопасной
реконструкции, но claims о measured advantage требуют соответствующих данных.

«Полностью переписано» допустимо только после инвентаризации всех поддерживаемых
capabilities и их cutover receipts. Наличие Rust crates, 225 локальных tests,
React shell или красивой Dark Carbon панели само по себе этим результатом не
является.
