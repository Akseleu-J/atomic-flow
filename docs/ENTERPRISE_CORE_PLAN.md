# Корпоративное ядро Atomic-flow: дополнение к production roadmap

24 октября 2026. **E00–E08 OPEN: план, не существующие гарантии.**
Основание: запрос владельца, повторное чтение текущего runtime и двух критических
разборов. [Результаты сверки](CORE_CRITIQUE_RECHECK_2026-10-24.md).
Базовый [roadmap P00–P14](PRODUCTION_ROADMAP.md) и четыре открытых production-блока
сохраняются. Здесь — ядро/SDK/API/worker contracts; UI и маркетплейс не нужны для приёмки.

## 1. Профили и порядок работ

**Local core:** single-owner, один host, repository-scoped tasks, approved local
или isolated executor; локальное storage. **Corporate core:** несколько principals
и проектов внутри private deployment, проверенная resource authorization,
server-owned queue/accounting/storage, isolated worker и admin controls.
**Distributed corporate:** несколько API/worker hosts с shared transactional
service storage, fencing/fairness и отдельной failure/load acceptance.
Первый профиль не получает гарантии второго автоматически; третий не имитируется
общей SQLite папкой. Враждебная multi-tenant SaaS среда требует отдельного threat model.

Последовательность владельца остаётся: UA understanding → delta → DAG/planning →
автономность → измеренная скорость. Корпоративные contracts закладываются при P00/P01,
а rollout возможностей выполняется небольшими пакетами с прежними safety gates.

| Пакет | Когда проектируется/реализуется | Где принимается |
|---|---|---|
| E00. Core contracts и modularity | P00–P01, вынос adapters постепенно | До P06 и повторно P11 |
| E01. Evidence и честные verdicts | P00/P04 | До автономного quality acceptance P07 |
| E02. Retrieval/graph completeness | P01–P04; optimization после P07 | P07/P08 |
| E03. Principal/policy boundary | Schema на P01, execution на P06 | До corporate execution; E08 |
| E04. Durable run/scheduler | P05–P06, persistence hardening P09 | P09/E08 |
| E05. Data governance/isolation | Schema на P01; policy до enrichment | P10/P11/E08 |
| E06. Provider/extension/budget contracts | P00/P06, conformance P10 | P10/P11/E08 |
| E07. Operations/security/scale | Instrumentation P00, full P08–P11 | P12/E08 |
| E08. Corporate-core release | После accepted P00–P12 + E00–E07 | До заявления corporate-ready |

P12 остаётся single-owner Python RC. E08 — отдельный corporate-core RC без
зависимости от P13 IDE/browser/JS-TS. Прямое сравнение с коммерческими агентами
и доказательство преимущества остаются отдельными задачами P14.

## E00. Уменьшить связанность, сохранить границы ответственности

1. Зафиксировать ports: GraphRepository, SourceStore, EvidenceStore,
   TaskRepository/Scheduler, PolicyEngine, BudgetService, ModelAdapter,
   ExecutionAdapter, EventSink. Начать с существующих реализаций через interfaces;
   большой одномоментный rewrite запрещён как способ закрыть этот пакет.
2. Core не импортирует CLI/API/LSP/MCP/browser/Kaggle adapters. Entry points
   собирают dependency injection; adapter factories lazy, capability gated.
3. Общие identifiers: principal/workspace/project/task/subtask/attempt/call/job,
   source revision, policy/grant version и correlation IDs. Scope приходит из
   доверенного host context; имя проекта/UUID от клиента не является авторизацией.
4. Одна таксономия lifecycle/effects/errors; task хранит workflow, attempt — одну
   попытку, job — выполнение, remote — транспорт, specialist — advisory analysis.
   Общие persistence/lease primitives переиспользуются, authority доменов не смешивается.
5. Wire/persistence схемы strict/versioned/bounded. Сохранить finite numerics,
   exact bool/int semantics, duplicate-key handling и отказ неизвестным fields.
   Определить migration/compatibility policy и round-trip malformed-state corpus.
6. Постепенно извлечь части cli/orchestrator/task_loop по use case/state transition;
   dependency cycle checks и conformance tests, а не произвольный target LOC.
7. Slim core wheel: только обязательный runtime. Benchmarks/fixtures, optional
   LSP/MCP/API/encoder integrations имеют отдельные extras/distributions где нужно.
   Никакого auto-download models или загрузки executable plugins из repo config.
8. Новые CI artifacts — manifests + immutable content-addressed archives с index
   и retention. Старые evidence не удалять и не пересчитывать задним числом.

Выход: ADR/module dependency map, stable interfaces, migration plan и distribution map.
Gate: установленный core импортируется и запускается без adapters/heavy dependencies;
один supported journey проходит и через SDK, и через API adapter; нет изменения
no-replay/source approval/accounting semantics. Savings LOC/RSS/startup измеряются.

## E01. Evidence model вместо ложного «проверено»

1. Заменить двусмысленный grounding contract версионированным VerificationReport.
   Отдельно: identifier hygiene, citation validity, evidence coverage, syntax,
   tests, requirements и model critique. Их нельзя сводить к одному score 1.0.
2. Если checked=0, результат not_assessed/not_applicable с denominator=0, а не
   perfect grounding. Показатель hygiene не становится confidence probability.
3. Citation: source revision/hash, node/span, exact quote digest и реально
   доставленный evidence span. Name/node presence не доказывает истинность claim.
   Проверка quote/span детерминирована, semantic entailment имеет отдельный статус.
4. Retrieval/materialization возвращает omissions: stale/denied/unreadable,
   unsupported/truncated/budget_excluded и counters. Требование полного чтения
   операции проверяется host; нельзя выдать partial source за complete evidence.
5. Связывать acceptance criteria с verification obligations. Проверка каждого
   behavioral requirement — independent tests или явно human-reviewed evidence.
   Tests candidate не могут менять oracle или незаметно ослаблять required suite.
6. Critic — advisory self-review, может помогать искать ошибки. Вклад измеряется;
   verdict модели не переводит task в verified success. Для report-only задач
   acceptance отличается от edit tasks и не требует выдуманного code diff.
7. Миграция public API/result schemas: legacy field deprecated, capabilities/schema
   version объявлены, clients не интерпретируют null/not_assessed как zero или PASS.

Выход: typed evidence/verification schema, public result semantics, docs claims audit.
Gate: wrong answer без backticks, known-name wrong logic, fake quote, stale citation,
partial source, irrelevant citation, zero tests и syntax-only не дают verified
behavioral success. Корректный тест подтверждает только заявленный scope требования.

## E02. Retrieval и uncertainty графа

1. Ввести measured benchmark по реальным pinned repos и нескольким task types:
   exact symbol, semantic paraphrase, RU/EN, aliases/callbacks/dynamic dispatch,
   config/docs, negative/no-answer, follow-up «а где он вызывается?».
2. Отделить graph completeness от ranking score: resolved/candidate/unresolved/
   dynamic/unsupported edges, extractor/provenance и uncertainty reasons.
   Неоднозначные candidates не представлять однозначными calls.
3. При пустом match — explicit exploration fallback/weak retrieval. Выполнять
   bounded alternate search/read/clarification либо abstain. Hubs не повышают confidence.
4. Graph-aware context pack сохраняет coverage/omissions и source revisions.
   Unknown dependency не доказывает отсутствие impact и не отменяет tests.
5. Conversation-aware retrieval: bounded coreference/query resolution с ссылками
   на предыдущие упомянутые symbols. Старый текст не даёт новых source permissions.
6. Сделать embedding provider заменяемым optional adapter. Сравнить lexical,
   lexical+graph, UA summaries и hybrid dense+RRF/rerank на frozen corpus/caps.
   Принять dense только при измеренном выигрыше recall@token-budget и end-to-end
   utility с учётом indexing/calls/RSS/latency/privacy cost. Не обучать свой encoder
   до проверки простого готового baseline и не считать LLM summaries embeddings.
7. Semantic/vector cache namespaced по security scope, revision, model/extractor;
   ACL применяется до выдачи кандидатов/контекста, удаления/отзыв доступа инвалидируют
   caches. Внешняя embedding service не получает source без egress разрешения.
8. Deprecated activate(nodes) helper заменить explicit index handle/cache keyed
   by revision либо оставить совместимость с diagnostic warning; исключить из hot path.

Выход: resolver coverage report, retrieval evaluation и ablation decision.
Gate: measured task/retrieval quality без снижения safety; absence/no-answer cases
и stale cache не маскируются высокой релевантностью или готовым semantic summary.

## E03. Identity и authorization в самом ядре

1. Trusted PrincipalContext: actor/service identity, workspace/project memberships,
   session/grants, policy version. CLI single-owner тоже передаёт этот context.
2. Policy decision учитывает actor/action/resource/source revision/executor/egress
   и task envelope; deny-by-default для неизвестных actions/scopes.
3. Для corporate API: auth adapter к существующему IdP/OIDC или short-lived service
   credentials. Проверять issuer/audience/expiry и revocation; transport identity
   не доверяет произвольным forwarded headers или principal JSON от клиента.
4. RBAC для coarse roles + resource/attribute checks: read/edit/execute/approve/
   publish/reconcile/admin. Поддержать optional separation of duties для risky publish.
   Reviewer не получает право переиграть неизвестный provider call.
5. Recheck grant/policy version перед effects и на resume; отзыв во время queued
   task останавливает следующий effect. Решение и policy version попадают в audit.
6. Проверить ACL на всех paths: list/search/graph/vector/artifact/events/logs,
   resumable cursor, continuation, fork/rewind, worker download и reconcile.
7. TLS/private network termination — deployment contract; trusted proxy boundary
   явный. App-level resource authorization сохраняется за reverse proxy.

Gate: два principals с разными project scopes не читают чужой graph/cache/artifact,
не управляют jobs/runs и не используют чужие credential grants. Tenant/project IDs
не угадываемость UUID, а проверяемая boundary. Проверить revocation mid-task,
confused deputy, stale grants, denied listing и API/SDK bypass attempts.

## E04. Durable runs, scheduling и bounded execution

1. API handles поверх durable Task/Attempt state, а не отдельная вечная in-memory
   orchestration. Submit/status/result/events/cancel/resume имеют typed contracts.
2. Idempotency key связывается с authenticated principal, operation и request digest.
   Повтор ключа с другим payload отвергается. Retry HTTP не создаёт новую job.
3. Transactional admission → budget reservation → queue record/event. Для отдельных
   stores — explicit outbox/inbox/reconciliation, без fake cross-store atomicity.
4. Fencing epochs для scheduler/worker leases. Потерявший lease worker не имеет
   права commit/publish; side effects после submission могут остаться unknown.
   Exactly-once arbitrary external execution не обещается. Дедупликация не повторяет
   неизвестный effect; при недоказанной уникальности остановка/host reconciliation.
5. Per-principal/project quotas, bounded queue length, concurrency, weighted fairness,
   backpressure/retry hints/deadlines. Потребление CPU/RAM/disk/output/files/tokens
   ограничивается независимо от цены model route.
6. Cancel contract разделяет request accepted/local wait stopped/owned process
   stopped/provider completion unknown. Deadline распространяется на scan/copy/queue/
   checks/publish/recovery; adapter объявляет cooperative/killable capabilities.
7. Durable events с sequence/cursor/reconnect; consumer видит gaps/retention expiry,
   а не молчаливую потерю. Redaction/ACL применяются к event stream и terminal result.
8. Local single-host store и corporate server store за repository port. Последний
   выбирается ADR (например transactional SQL service) и проходит isolation tests.

Gate: duplicate submit, process restart, queue saturation, lease expiry, lost worker,
out-of-order response, double delivery/cancel, mid-flight policy revoke и unknown
submission не дают duplicate effects/false terminal success. Fairness и cancel
latency измерены при заявленной нагрузке, клиенты не обходят server admission.

## E05. Данные, секреты и изоляция проектов

1. Data inventory: source snapshots, graph/summaries/vectors, prompts/results,
   checkpoints, logs/artifacts, usage и credentials; classification/ownership/
   allowed providers/regions/network paths задаёт host policy.
2. Egress allowlists и data-boundary checks применяются до любого enrichment,
   embedding, MCP/plugin/tool call, а не только к final answer. Redaction — второй
   слой, не разрешение передать весь snapshot внешнему provider.
3. Secret broker интегрируется с deployment secret store: operation/TTL/scope grants,
   rotation/revocation и исключение secrets из persistence/archives. Независимое
   подключение своего account/provider не меняет corporate policy.
4. Для distributed corporate — encrypted transport/storage и managed key adapter,
   rotation/backup/restore. Key custody/admin rights/threat model документированы;
   собственная криптография внутри graph runtime не разрабатывается.
5. Retention/export/delete для source-derived data, включая vectors/caches/logs
   и backup policy. Delete event не объявляется немедленным удалением старого backup;
   срок и исключения явно отражены в policy и отчёте.
6. Telemetry по умолчанию без source/prompt/output/tool argument payloads;
   opt-in content capture отдельной policy с access controls/retention.
7. Live SQLite/WAL размещать на supported local persistent volume вне sync папок
   checkout. Online backup использует согласованный snapshot; export JSON — portable
   artifact. Multi-host access к общему SQLite-файлу не поддерживается.
8. Algorithm-tagged SHA-256 для нового source/evidence identity contract; миграция
   старых SHA-1 records не переписывает evidence и не восстанавливает read authority.

Gate: cross-project cache leak, malicious repo instructions, secret canaries,
denied egress, revoked access, artifact export/delete и backup restore проверены
сквозным journey. Полнота защиты от произвольной prompt injection не обещается.

## E06. Providers, extensions и budgets

1. Versioned adapter capability protocol: native/text tools, context/token counting,
   streaming, cancellation, usage/pricing, outcomes/refusal, max input/output,
   retry semantics и data egress. Contract suite для built-in и custom adapters.
2. Один server-side BudgetService для corporate admission: hierarchy principal/
   project/task/call, atomic reservation/settlement, original owner/day identity,
   per-physical-attempt cost и durable unknown holds. Client ledger — local profile.
3. Unknown tariff под positive monetary cap — refusal до inference; zero price
   только explicit verified route. Reconciliation требует trusted evidence,
   administrator не очищает hold одним model-generated текстом.
4. Provider timeout/rate/quota failover только по proven not_sent/pre-inference
   refusal, с общим deadline/accounting. Provider bill может превысить estimate;
   гарантированный денежный cap возможен только с подходящим provider-side contract.
5. Approved registry extensions по digest/version, trusted factory и permissions
   manifest. Signature/integrity не равны sandbox. Недоверенный executable extension
   запускается в отдельной принятой boundary либо не допускается.
6. Plugins/MCP/LSP/hooks отдельно по capabilities/scope/timeout/byte limits;
   update/revoke инвалидируют grants, cache и resume compatibility по контракту.
7. Форматы ошибок/result/events стабильны; adapters не используют строки ошибок
   как команду к retry/escalation. Retry/repair оплачиваются тем же владельцем.

Gate: concurrency quota races, malformed/unknown prices/usage, duplicate settlement,
midnight/resume, cancellation after send и plugin update не обходят accounting,
policy или no-replay. Conformance test failures запрещают claim adapter support.

## E07. Наблюдаемость, release security и эксплуатация

1. OpenTelemetry-compatible traces: task/subtask/call/tool/check/publish и bounded
   metric labels. Source paths/task IDs — trace/audit context, не unbounded labels.
   Sensitive content capture выключен по умолчанию; trace exporter локален/настраиваем.
2. Metrics: queue age/depth, active/held tasks, latency p50/p95/p99 и sample count,
   cancellation, RSS/disk/cache hit, unknown costs/effects, success/interventions,
   graph freshness/omissions. Model wait и runtime overhead измеряются раздельно.
3. SLO/SLA scope, max workload и resource limits замораживаются до acceptance.
   Задать RPO/RTO, measurement method, disk-full/corruption alerts и incident runbook;
   значения выбираются deployment owner, затем демонстрируются restore drill.
4. Fuzz/property tests на parsers/wire/persistence, paths, malformed protocol frames,
   duplicate events и state transitions. Golden source-delta parity и fixtures
   не допускают test weakening в целях pass. Adversarial cases отдельно от task score.
5. Release gates: dependency locks/hashes, vulnerability/license/SBOM checks,
   build provenance, artifact integrity/signing integration, clean offline consumer
   rehearsal и supported OS/Python/adapter matrix. Upgrades имеют staged rollout,
   feature flag/kill switch, schema compatibility и rollback/reconciliation procedures.
6. Security ownership/disclosure/update policy, triage severity и сроки реакции
   фиксируются организацией. NIST SSDF используется как checklist practices,
   не как сертификация соответствия. Независимый человеческий review нужен до
   claims о внешнем pentest; AI audit не заменяет его.
7. Crash/OS/worker test matrix, process-kill/host-reboot/power-loss claims разделены.
   Disaster restore не повторяет external effects и сохраняет audit/event identities.

Gate: monitored soak, declared failure matrix, restore drill и installed-release
journey. Нет unresolved critical/high impact findings в заявленном scope либо scope
сужен и принят явно; один exception не скрывается внутри среднего task success.

## E08. Корпоративная приёмка ядра без UI

1. Включить P00–P12 и E00–E07 evidence в acceptance registry; закрыть отдельно
   local profile и corporate profile, при необходимости distributed profile.
2. Independent consumer поднимает выбранный профиль из immutable artifacts,
   подключает IdP/secret/model/worker adapters по инструкции и не использует source
   checkout или private monkeypatches. Headless SDK/API достаточно для проверки.
3. Два principals/несколько repos: ACL/queue/quota/cancel/revoke/restart/restore;
   полный understand→DAG→autonomous edit→independent tests→approved publication.
4. Измерить utility/performance в тех же deployment policies, что приняты для
   production. Local unrestricted benchmark не подтверждает corporate throughput.
5. Подписанное owner decision содержит supported scope/limits, exact versions,
   evidence hashes, known limitations и rollback/incident procedures. Даты и
   acceptance targets заморожены до проверки, отсутствие результата = OPEN.

Выход: headless корпоративное ядро с воспроизводимой установкой, проверенными
identity/data/execution boundaries и evidence-bound reliability/utility.
Статус не означает SOC 2/ISO/SLSA compliance или общее превосходство над конкурентами.

## 2. Следующие конкретные пакеты

1. **P00 + E00/E01 design:** coverage/claims inventory, core ports, typed identity
   и VerificationReport contract. Исправление checked=0/omissions отдельным маленьким
   runtime package с result schema compatibility и regressions.
2. **P01/P02 + E02/E05 schema:** UA graph, SHA-256 migration, uncertainty и revision
   identities, delta indexes/overlay; structural parity без global hot-path rebuild.
3. **P03/P04:** incremental semantic understanding, exact reading/patching,
   context completeness и test obligations; evaluation cost включает enrichment.
4. **P05/P06 + E03/E04/E06:** DAG/frontier и bounded autonomous replan с host policy,
   durable runs/admission/accounting; permissions не берутся из task text.
5. **P07:** calibrated utility при уже принятой isolated execution boundary;
   затем P08/P09/E07 performance/reliability и P10/P11/E08 deployment/release.

Broad UI, marketplace, собственный encoder, произвольные write workers и много
языков не вытесняют эти пакеты. Каждый пакет сохраняет текущие защитные контракты.

## 3. Первичные внешние ориентиры, проверенные 24 октября

- [NIST SP 800-218 SSDF 1.1](https://csrc.nist.gov/pubs/sp/800/218/final): practices secure SDLC/provenance; ориентир release/security process.
- [OpenTelemetry GenAI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md): sensitive model content не записывается по умолчанию; версии conventions фиксировать при реализации.
- [SQLite WAL](https://www.sqlite.org/wal.html): single-host/shared-memory boundary, один writer и ограничения cross-database atomicity.
- [SQLite network caveats](https://www.sqlite.org/useovernet.html): server-owned DB/service boundary для multi-host access.

Применение этих ориентиров к Atomic-flow — проектное решение, которое требует
собственных tests и acceptance; наличие ссылки не закрывает ни одного gate.
