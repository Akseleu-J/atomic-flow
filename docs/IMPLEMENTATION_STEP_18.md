# Пакет 18 — W1: корректность записи, admission контракты и основа routing/reconciliation

24 октября 2026. Основание: `audit-2026-10-23/PRODUCTION_PLAN.md` (W1) и находки
`runtime-audit.md` (RT-01, частично RT-02), `quality-scale-audit.md` (Q01, Q02, Q04),
`provider-routing.md` (B01–B03, B05). Четыре параллельные сессии внесли исходники;
эта сессия завершила их: сверила полным набором, превратила каждый repro в regression,
закрыла same-thread публикационное окно и зафиксировала границы. Каждый defect
из аудита теперь имеет regression, фикс и локальный Windows evidence; Linux/Docker
остаётся обязательным CI-шагом на пуш.

## RT-01 — сериализация мутаций исходников

`source_lock.source_mutation_lease(project)`: OS-level exclusive lock на
`<root>/.atomic-flow-lock` через `locking.exclusive_file`; повторный вход в том же
потоке разрешён (вложенные guard-вызовы `publish → replace_checked` легальны), но
`publication=True` (publish/recover) отказывает, если этот поток уже держит lease
корня — audit-интерливинг «полный publish B внутри окна A» теперь получает
SourceConflict и rollback к baseline, а не молчаливую потерю версии B. Кросс-потоковая
и кросс-процессная мутация одного корня взаимно исключена (msvcrt/flock). Lease
кооперативный: внешний редактор вне контракта, negative test это фиксирует.

Guard'ы: `ChangeSet.publish/recover` (project, publication), `restore_checkpoint`
(workspace), `replace_checked`, `file_transaction.apply/undo`, `UndoStack.undo`,
`rewind.apply_rewind/recover_rewind`, LSP `apply_preview/recover_lsp_edit`,
notebook `apply`, tools `edit_node`/`file_ops`. `confined_path` резервирует имя
`.atomic-flow-lock` как host state. `durability.py`: write_bytes (fsync файла и
каталога), atomic_bytes (уникальный temp + replace + fsync); supported crash model —
process failure, не power loss (Windows directory flush недоступен портативно).

Backups публикаций сохраняются и после конфликта: затёртая версия восстановима
вручную, automatic recovery трогает только собственные bytes.

Regressions: `tests/test_write_serialization.py` — same-thread интерливинг аудита
(обе версии сохранены, A откатился, B опубликовался после), кросс-процессный
конфликт (subprocess держит lease), реентерность вложенных helper'ов, сериализация
undo/file_transaction/rewind, reserved lock name, durability helpers (старый
контент переживёт прерванный replace, temp очищается).

## Q02/Q04 — владение index-коммитом и admission обхода

`save_tree`: exclusive `.atomic_flow/tree.lock`, уникальные `tempfile.mkstemp`
tmp-файлы (общий `tree.json.tmp` исчез), fsync+`os.replace`, generation CAS —
`_base_generation` (sha256 текущего tree.json) наблюдается ДО обхода и проверяется
под lock'ом; чужой коммит между build и save даёт retryable `TreeError` (stale
generation), а не потерю/переписывание чужих bytes. `load_tree` ограничен 128 MiB,
отвергает non-object JSON, восстанавливает generation в памяти. Прерванный коммит
оставляет предыдущую полную генерацию.

`project_resources.ProjectLimits` (max_files 10k / max_total_bytes 128 MiB /
max_nodes 100k / max_entries 100k / max_depth 64) и `source_files` — инкрементальный
scandir с cancel-чекпоинтами; `build_tree` считает skipped/read_bytes, отвергает
oversize-обход `ResourceLimitError` до копирования. Это admission границы, не
измеренные p95 (W7 открыт).

Regressions: `tests/test_index_commit.py` — реальная двухпоточная гонка writers
(ровно один saved, другой retryable, финал валиден, нет tmp-остатков), stale
generation → rebuild, прерванный коммит, corruption guards, все пять лимитов,
отмена на входе и внутри обхода, приватный state вне индекса.

## Q01 — HTTP admission до внутренней конвертации

`api/server.py`: strict typed схемы (`strict=True`, `extra=forbid`) для
chat/answer; per-part/per-message/aggregate лимиты (Text ≤ 50 000, части ≤ 100,
агрегат ≤ 200 000 bytes); composed-query guard до создания внутренних запросов;
`BoundedBodyMiddleware` — Content-Length и chunked body > 1 MiB → 413, мусорный
Content-Length → 400; `RequestValidationError` handler отдаёт санитизированный 422
(первые 16 ошибок, без эха промптов). Живой сервис — single-process контракт:
guard `WEB_CONCURRENCY/UVICORN_WORKERS`, lifespan lock на проект, `/ready`.
Все invalid-формы дают 4xx без единого вызова `make_backend`.

Regressions: `tests/test_api_admission.py` — таблица 4xx (oversized/malformed/
extra/role/parts/aggregate), 413 + 400 middleware (включая chunked через прямой
ASGI-drive), composed limit, zero-backend-call счётчик, single-worker guard,
`/ready`, strict-типы, санитизация.

## B02/B03/B05 — provider usage/error contracts и routing v1

`backends/usage.openai_usage` нормализует usage ДО парсинга контента: скалярный
`usage.cost` (OpenRouter) включая явный numeric 0.0; `cost_usd`/`total_cost_usd`;
nanodollar (nested выигрывает, частичная запись → invalid); malformed cost
(строка/bool/NaN/отрицательный) → `invalid_provider_usage` и никогда не
перезаписывается configured 0/0. `OpenAICompatibleBackend` парсит usage первым и
при malformed content поднимает `ResponseShapeError(outcome="completed",
usage_result=settled)` — accounting settle'ит usage и только потом пропагирует
ошибку (`task_budget`, `orchestrator`), завершённый ответ никогда не триггерит
failover/replay.

`http.retry_after_seconds`: delta-секунды или HTTP-date, без укорачивания (макс,
не min), `post_json` держит общий deadline: Retry-After 120 при таймауте 5 с
возвращает исходный refusal вместо раннего retry; HTTP-date больше не игнорируется.

`backends/routing.RoutingBackend` + `QuotaStore` (file-locked, durable pending/
minute/day windows/cooldown): routing v1 только по именованным host-маршрутам,
`free_only` пропускает платные/просроченные/неподтверждённые free-маршруты (zero
paid sends), failover только после подтверждённого 429-refusal с cooldown
max(settings, Retry-After), unknown outcome держит pending-slot до host
`reconcile(attempt, evidence_sha256)` (идемпотентен), per-attempt атрибуция
(`route_attempt_id`/`logical_call_id`/`quota_scope`, `ChatResult.resolved_*`),
учёт бюджета/резерваций ведёт вызывающий owner (TaskBudgetBackend/Orchestrator),
цены/лимиты каждой физической попытки берутся из выбранного маршрута. `routing`
секция конфига — host-only: project yaml её отбрасывает, `UNTRUSTED_PROJECT_KEYS`
расширен. Routing требует text_json на каждом маршруте; native transcript
migration и model override не поддерживаются сознательно.

Regressions: `tests/test_provider_contracts.py` (usage-таблица, malformed-cost,
ResponseShapeError settle в task budget, bool-cost unknown, Retry-After контракт,
никогда не укороченный cooldown, сохранение ретраев с малым Retry-After,
host-only routing config, default make_backend) и `tests/test_routing.py`
(атрибуция, 429-failover + persisted cooldown, unknown-hold + reconcile,
charge/effect независимость через completed-ошибку, free_only skip-матрица,
unknown prices skip, RPM cap, max_attempts/deadline, refusal overrides,
context-limit skip).

## RT-02 ground work — host reconciliation observations

`SessionJournal` принимает событие `host_reconciliation` (validated: scope
charge|effect, `reconcile_<64hex>`, evidence sha256, actor, reason, call_id).
`unresolved_call_ids` теперь разделяет charge/effect множества: reconciliation
стоимости не снимает effect-hold и наоборот; обычный success-ответ по-прежнему
ничего не снимает сам. Incremental append (`_append_position`) валидирует только
новые записи и перехэширует префикс (same-size tamper пойман), interrupted tail
блокирует append c исходной формулировкой «never replay». Это observation-layer;
полная идемпотентная reconcile-операция с согласованием task/ledger/session —
W2, не закрыта этим пакетом.

Regressions: `tests/test_host_reconciliation.py` — валидация события, независимость
scope'ов, чужой call не очищается, same-size prefix tamper, incremental interrupted
tail, `JobStore.collect()` (tombstones с сохранением identity/status, retention/
pending/uncertain/worker-active skip, идемпотентность), `execution_manifest`
(capabilities/refusal/digest, trusted-local ≠ sandbox, immutable image),
worker отказывает при изменении `resolved_execution` identity ДО запуска
(marker never executed).

## Прочее

- `agents/execution.py`: `ExecutionRequirements` + `execution_manifest` — явная
  capability-negotiation точка до model run (W4 ground work); Windows local runs
  идут через `windows_job.WindowsJob` (owned Job Object, KILL_ON_JOB_CLOSE,
  verified terminate-before-exit); taskkill returncode≠0 и job-assignment отказ
  (exit 125) фиксируются как uncertain, а не как cleanup success.
- `jobs.py`: `collect()` GC с tombstones; 64-cap и 4096-scan считают только
  uncollected payload'ы; `cleanup_confirmed` пишется явно.
- `test_run_control.py::test_steering_during_critic_requires_new_answer`: бюджеты
  ожидания увеличены (3→20 с) — тест проверяет семантику steering, не latency
  планировщика; на нагруженном 3.4 GiB ноутбуке прежний бюджет флапал.
- `test_planner.py::test_plan_rejects_empty_steps`: ранее ловил IndexError
  исчерпания fake-ответов вместо ошибки валидации; теперь два ответа и
  `ResponseFormatError` — проверяет заявленное.
- `docs/audit-2026-10-23/*_probes.py` — исторические evidence-скрипты аудита;
  часть гейтится на старое имя tmp-файла/поведение и сохранена как artefact
  аудита, не как живой тест.

## Границы (не закрыто этим пакетом)

- W1 Windows evidence локальное; двухпроцессная публикация проверена реальным
  subprocess'ом на этой машине, Linux-проверки — CI.
- Routing v1 — offline fault matrix; live provider acceptance (Groq/Gemini/
  OpenRouter/Mistral) не выполнялся, режимы `capped_paid`/`free_until` опираются
  на host-подтверждённые тарифы.
- Reconciliation — observation + QuotaStore scope; durable согласование
  task/ledger/session и CLI-операция — W2.
- Index admission — лимиты без нагрузочных измерений (W7); fsync-модель —
  process failure, не power loss.
- Multi-repo live edit quality (G01/Stage 4) и wheel acceptance (G02) остаются
  открытыми; W5 — следующий пакет.

## Evidence

Локальный полный набор Windows (Python 3.12.14, этот ноутбук, итоговое дерево
пакетов 18–21): **850 passed / 33 skipped / 0 failed** (≈6.5 мин), ruff чист.
Промежуточные прогоны выявили один нагрузочный флейк MCP stdio-теста: новый
fail-closed cleanup-контракт (_kill_tree/WindowsJob) требовал подтверждения,
но доказательством служил exit code taskkill, который транзиентно ненулевой,
пока целевой процесс teardown'ится. Фикс (пакет 18 follow-up, коммит
«Prove process-tree cleanup by owned-process exit»): доказательство —
реальный выход owned-процесса (proc.wait), uncertainty только если процесс
пережил forceful kill; regression обновлён на оба сценария. Linux/Docker
exact-source evidence — обязательный CI на пуше, локально не выполнялся.
Model calls=0.
