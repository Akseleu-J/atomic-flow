# Пакет 12: план, зависимости, сводка, память и продолжение диалога

23 октября 2026. Кандидаты **07, 11, 17, 18, 39, 41, 44** реализованы независимо
тремя субагентами и интегрированы основным агентом. Каталог после пакета:
**42 implemented_scoped / 20 planned** из 62. Это ограниченный проверяемый
runtime, не утверждение production acceptance или feature parity.

## План и зависимости

`TaskRunner.review(task, allowed_paths=None)` возвращает canonical plan, task/project,
полный source manifest, состояние workspace, точные разрешённые пути и SHA-256 digest.
`approve(task, expected_digest, allowed_paths=...)` заново вычисляет его под task lease.
Другой план, query/rationale, проект, исходные bytes, scope или review workspace не
получают старое разрешение. `revise(task, Plan)` сбрасывает approval и completion report;
закрытые задачи и interrupted running steps нельзя сбросить этим API.

`prepare` создаёт review state без исполнения. `resume` не одобряет его автоматически;
cancel имеет приоритет перед approval/accounting. `run_plan(query, Plan)` — явный host
вызов: он review/approve именно переданный план перед исполнением. По умолчанию scope
содержит существующие файлы reviewed manifest. Новые файлы нужно назвать явно, включая
их ожидаемое отсутствие. Scope не является glob или разрешением на всю директорию.

Dispatcher проверяет write paths до approval callback и lazy factory: edit_node path
получается из host node, move проверяет оба пути. Неизвестный write path contract
не допускается в approved scope. После шага и перед публикацией проверяется весь diff
workspace, включая изменения от проверочных процессов. Нарушения host integrity/scope
останавливаются после rollback без model replanning. Это не новая ОС filesystem sandbox;
исполнение неизвестного кода по-прежнему требует Docker.

`PlanStep.depends_on` — список zero-based индексов; пропуск сохраняет последовательную
зависимость от предыдущего шага, `[]` явно означает независимый шаг. DAG отклоняет циклы,
self/missing/duplicate/negative references. Стабильные Step.index и blocked_reason
сохраняются. Runnable независимая работа продолжается, blocked работа не объявляется
готовой. Старые незавершённые steps после replan получают `superseded`; новый план
требует review и останавливается до следующих действий. Смена плана не повторяет done
steps. Final host gate учитывает эти статусы и прежние проверки syntax/pytest/diff.

**Task schema теперь 2.** v0/v1 мигрируют в памяти с пустым approval и консервативными
последовательными зависимостями; поле с названием approval в старом формате не даёт
разрешения. Будущие версии и повреждённые структуры отклоняются. Published/recovery
checkpoints могут только проверяться/восстанавливаться прежним путём, без replay.

```console
atomic task "Fix refund edge case" --dry-run
atomic task --review TASK_ID --scope-path invoice_service.py --scope-path new_test.py
atomic task --approve TASK_ID --plan-digest REVIEW_DIGEST --scope-path invoice_service.py --scope-path new_test.py
atomic task --resume TASK_ID
atomic task --revise TASK_ID --plan-file revised-plan.json
```

Fresh task с `--yes` одобряет показанный текущий review; `--resume --yes` не заменяет
review/approve нового или изменённого плана. SDK: `review_task`, `approve_task`, `revise_task`
работают offline и не создают model backend. Автоматический план остаётся мнением модели;
доля успешно решённых задач ещё требует независимого измерения.

## Структурированная сводка и память проекта

`StructuredSummary` schema 1 содержит goal, constraints, decisions, open_questions,
evidence_refs, origin/date/scope и неизменяемую маркировку data_only/no permissions/
no read evidence. Это bounded validated host fields, без автоматического превращения
model prose в решения. EvidenceRef не открывает файл и не создаёт read coverage.
Summary сохраняет provenance dates; ограничения и host decisions переносятся при
продолжении. Большие/повреждённые/неподдерживаемые структуры отклоняются.

`MemoryStore(project)` хранит записи в `ATOMIC_FLOW_HOME/memory` вне source project,
с project binding, id, content hash, timestamps, origin/category. Host CRUD требует
явного `approved=True` либо CLI `--yes`. Модель не получает tool для скрытой записи.
По умолчанию: 128 записей, до 8192 bytes content на запись, до 1 MiB store.
Leased atomic snapshot, защита от symlink/junction, проверка всего store; повреждённая
запись не пропускается молча. Удаление исключает запись из будущей выборки; старые
session/artifact observations, куда текст уже попал, отдельно этим не стираются.

Выборка только по явным IDs или query. Answer допускает до 10 entries / 16 KiB;
общий advisory context до 48 KiB и проходит обычный request admission вместе с
инструкциями/source/tools/output reserve. Без опции memory store вообще не читается.
Маркировка и host gates не дают памяти включить edits, расширить tool scope или
выдать `read_complete=true` за настоящее чтение. Это не гарантия отсутствия semantic
prompt injection: модель по-прежнему может неправильно истолковать чужие заметки.

```console
atomic memory add "Keep the public API stable" --project . --yes
atomic memory list --project .
atomic memory select --id MEMORY_ID --project .
atomic ask "Explain refund" --memory-id MEMORY_ID --mode simple
atomic memory update MEMORY_ID "Use focused diffs" --project . --yes
atomic memory delete MEMORY_ID --project . --yes
```

Эта product memory не читает и не изменяет глобальные воспоминания Codex.
Известные secret formats/env assignments редактируются, но произвольный неизвестный
секрет как обычный текст автоматически распознать невозможно.

## Продолжение и fork без воспроизведения действий

Успешный обычный answer при включённых artifacts пытается записать host checkpoint:
ограниченная сводка, source/mutation hashes (включая отсутствующие файлы), project
binding и наблюдённая journal sequence. Сверяются именно observed hashes, не новые
bytes вместо старого наблюдения. Private task workspaces не получают auto checkpoint.
Если неизвестен расход, source устарел, состояние не помещается в bounds либо есть
неопределённые эффекты, result предупреждает `checkpoint unavailable`; пригодного
для resume состояния не создаётся. После явной проверки старого законченного диалога
есть offline `session checkpoint`; unresolved effects/charges этим не обходятся.

`prepare_continuation` требует последний целый/current checkpoint того же проекта:
corrupted tail, pending model/tool call, unknown/invalid cost, rollback_failed или
`side_effects_may_have_occurred` блокируют resume/fork. Такие признаки сохраняются
и в compact tool summaries. Ошибка обычного read/denial без эффектов сама по себе
не блокирует новый вопрос. Новые journal observations инвалидируют старый checkpoint.

Continuation — новый request_id, ledger calls, свежий ToolContext и новая retrieval.
Старые prompts/tool arguments/results не проигрываются. Advisory note не восстанавливает
permissions, policy approvals или read proof. Parent source/mutation references,
constraints и host decisions не теряются при следующем checkpoint.

Fork создаёт новый journal с родителем и проверенным advisory checkpoint, с явным
`execution_performed=false`; он пригоден для нового resume, без фиктивного model turn.
Export сохраняет observations и `replay_supported=false`. Это не восстановление
стека выполнения модели или самовольное продолжение прерванного tool.

```console
atomic session fork SESSION_ID --project . --query "Inspect another case"
atomic session resume SESSION_ID "Explain the same rule" --project . --json
atomic ask "Another question" --resume-session SESSION_ID --mode simple
atomic session checkpoint SESSION_ID --project . --goal "Reviewed goal" --constraint "Offline only" --source-path invoice_service.py
```

SDK `resume_answer` вызывает тот же pipeline. `/v1/answer` и `/v1/runs` принимают
resume_from, memory_ids, memory_query под прежними auth/project-root checks.
HTTP write APIs для памяти, планов и rewind этим пакетом не добавлены.

## Rewind как подтверждённое восстановление source snapshot

`rewind snapshot` сохраняет явно перечисленные supported source paths, включая
ещё отсутствующие пути для последующих create/delete/move. Не более 64 paths и
16 MiB snapshot bytes; private/hidden/ignored/outside/link paths отклоняются.
Root/parent/manifest/backup/lock состояния тоже проверяются на links.

Preview показывает изменённые пути, hash/mode, redacted diff и preview_token,
связанный с проектом, snapshot и текущим состоянием **всех** выбранных файлов.
Diff строится ограниченно: до 24000 символов, большие файлы получают явный omitted
marker и hashes вместо выдачи обрезанного diff за полный. Apply требует этот token
и явное подтверждение. Изменение любого current файла инвалидирует старый preview.

До первой записи сохраняется durable restore intent. Проверяются все paths до
восстановления, каждый путь перед записью и все hash/mode ещё раз перед applied.
Чужие bytes не заменяются recovery; после прерывания остаётся restoring journal
и restore_id для явного recover. Это recoverable multi-file operation, не OS atomic
transaction и не откат уже отправленных model calls, расходов, git history или внешних
эффектов. Source index проверяется/обновляется обычным tree refresh при дальнейшем use.

```console
atomic rewind snapshot before-fix invoice_service.py created.py --project .
atomic rewind preview SNAPSHOT_ID --project .
atomic rewind apply SNAPSHOT_ID --preview-token TOKEN --project . --yes
atomic rewind recover RESTORE_ID --preview-token TOKEN --project . --yes
```

## Проверки

Новые suites: plan_review_dependencies, memory_summaries, dialogue_rewind,
advisory_integration, package12_surfaces. Покрыты stale approval, новая file authority,
DAG/blocked independent work, version migration, unknown effects/charge, fresh read
coverage, explicit deletion/selection limits, настоящие CLI/SDK/API пути, stale rewind,
create/delete/move, crash-between-write-and-journal и editor change во время restore.

Проверки используют scripted модели и локальные fault fixtures, без paid API calls.
Windows final: **490 passed / 16 skipped**, changed-code Ruff и compileall PASS;
`validation/step12-windows-final.txt`.

CI runtime **`394aedcdae2ed8b06cf2b82a894780f5d22b3ceb`**:
push [39889275052](https://github.com/Akseleu-J/atomic-flow/actions/runs/39889275052)
и PR [39889279922](https://github.com/Akseleu-J/atomic-flow/actions/runs/39889279922)
завершены success; Python 3.11/3.12 PASS. Docker-job artifact подтверждает
**483 CPU passed / 6 skipped, 17 extra passed, 6 real Docker passed** и compile PASS.
Прежняя калибровка шести historical changes calibrated=true, model_calls=0;
это не новая оценка task quality. Image:
`sha256:994242129def0f068a5dd4bea801dcf959cb419f4c77d3efbfb1debf84a8f183`.
Evidence `validation/ci-step12-20261023/`: исходные bytes 12 artifact files и SHA-256
manifest, а также hashes всех 29 изменённых source/test Git blobs.
Из первой интеграции сохранён failed log: cancel-priority и explicit new-path fixture
исправлены; Windows taskkill timeout отдельно повторно PASS без ослабления таймаутов.
Windows local execution остаётся best effort; гарантии контейнерных границ проверяются
отдельными реальными Linux/Docker acceptance tests.
Второй промежуточный лог сохранён отдельно: новые прямые journal fixtures сначала
передавали dataclass tuples вместо JSON, тогда как настоящий `_Run.event` уже выполняет
нормализацию. Исправлены fixtures; строгая JSON validation не ослаблена.

Stage 4 independent multi-repository edit evaluation и product acceptance остаются открыты.
