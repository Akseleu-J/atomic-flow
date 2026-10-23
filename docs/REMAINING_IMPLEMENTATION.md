# Оставшийся разрешённый scope

22 октября 2026. Пользователь разрешил реализовать весь оставшийся каталог с тестами.
Это continuation plan для следующих пакетов, **не список уже работающих подсистем**.
После пакета 13 реализованы 48 scoped candidates; остаются 14 из 62.
Источник статуса — `CANDIDATE_STATUS.json`; отдельные номера пакетов ниже не означают
закрытие одноимённых этапов `PRODUCTION_ROADMAP.md`.

## 11. Управление запуском и SDK: 03, 04, 05, 08, 53, 56 — реализовано scoped

Реализация и точные границы: `IMPLEMENTATION_STEP_11.md`. Live answer handles,
SSE/JSONL events, control boundaries и local-wait cancellation; durable jobs,
streaming TaskRunner и remote inference cancellation не объявлены реализованными.

Единый typed event stream для CLI/API/SDK, correlation model/tool/task/check,
входящие уточнения на безопасных границах и отмена ожидания model/network вызова.
Не обещать остановку удалённого inference: после отправки отменённый/потерянный
ответ оставляет unknown spend и запрещает replay. Retry различает pre-send refusal,
отклонённый запрос и неопределённый исход. SDK events/error/control versioned.

Acceptance: отмена во время DNS/connect/read/backoff и после оплаченного ответа;
bounded events/backpressure, сохранение terminal event, API auth/disconnect,
уточнение до/после mutation, отсутствие неучтённых retries и lost charges.

## 12. План, диалог и память: 07, 11, 17, 18, 39, 41, 44 — реализовано scoped

Реализация/границы: `IMPLEMENTATION_STEP_12.md`. Plan review и source/path authority,
blocked DAG steps, host summary/project memory, checked advisory resume/fork и source
snapshot rewind. Полный executable replay, global/model-written memory, rollback
расходов/внешних эффектов и HTTP write APIs не объявлены реализованными.

План review/revise/approve с hash-bound областью файлов; смена плана/replan
инвалидирует прежнее одобрение. Зависимости и blocked причины сохраняются явно.
Summary хранит цель/ограничения/решения/открытые вопросы и evidence references,
но не становится источником доказательства чтения или разрешением на запись.
Memory CRUD/selection с origin/date/scope, явной политикой записи и удаления.
Session resume/fork/export не повторяют actions. Rewind показывает diff и проверяет
внешние изменения, включая созданные/удалённые файлы.

Acceptance: stale plan approval, циклы зависимостей, useful work при blocked steps,
неподдерживаемая версия, corrupted tail, pending effect при fork/resume,
read-coverage после summary, memory deletion/limits и conflict-before-restore.

## 13. Jobs, credentials и execution: 33, 34, 42, 43, 45, 62 — реализовано scoped

Реализация/границы: `IMPLEMENTATION_STEP_13.md`. Durable host jobs и output cursors,
process-only host credential broker, фиксированные Docker profiles, private Git
worktree snapshot и remote Linux worker protocol. Remote worker требует доверенного
Linux/Docker host; внешняя cloud installation и attestation не объявлены выполненными.

Durable jobs с ID/cancel, курсорами вывода, terminal state и crash reconciliation.
Нельзя выдавать bounded tail за полный лог; при storage cap нужен явный исход.
Credential scope по конкретному connector/tool, expiry/revocation и отсутствие
ключей в prompts/artifacts. Sandbox profiles с фиксированной сетью/mounts,
без общего bypass. Git worktree backend сохраняет publication/hash gates.
Remote Linux executor — сменяемый worker с bundle/source/image hashes и evidence;
не обязательный Codespace и не Kaggle model backend.

Acceptance: lease/process crash, output cursor isolation/retention/cap,
expired/revoked credential, child env и отсутствие secret в archive, worktree
conflicts и recovery, отмена remote job и подмена bundle/result manifest.
Untrusted written code выполняется только в Docker/проверенном Linux worker.

## 14. Проектные форматы и workspace: 24, 25, 27, 28

Notebook cell API с устойчивыми cell IDs и отдельной output policy, без сырого
JSON patch. Watcher invalidation/debounce не заменяет проверку hashes перед use.
Несколько root IDs с явным разрешением каждого корня. LSP как дополнительный
bounded process/protocol: definitions/references/diagnostics и проверяемый WorkspaceEdit.

Acceptance: notebook reorder/output leakage/stale cell; editor writes во время
чтения/верификации; symlink/drive/root escape; rename с несколькими файлами,
stale LSP version, malformed frames и shutdown/cancel дочернего процесса.

## 15. Интеграции и workflows: 36, 48, 50, 51, 52, 54, 55, 60

MCP client/server, workflows, trusted plugin manifests, ограниченные hooks,
локальный аутентифицированный IDE bridge и docs web fetch/search с network policy.
Внешние данные получают source/trust metadata и не расширяют host permissions.
Hook не меняет неаудированные args и не обходит budgets; plugin manifest не делает
произвольный Python безопасным. Optional dependencies загружаются по требованию;
измерить cold start/RSS на слабом ноутбуке. Marketplace/auto-update не включать
без отдельного продукта и проверяемого trust/update контракта.

Acceptance: protocol fixtures и timeout/auth failures, malicious resource/tool
data, schema change и permission drift, private-network/redirect/URL attacks,
oversized output, IDE token/origin/workspace binding, hook failure после side effect,
lazy imports без скрытого запуска стороннего кода.

## 16. Изолированные workers: 46, 47

Сначала read-only worker/reviewer с отдельными context/tool scopes/workspace и
общим parent budget/cancel. Parent проверяет evidence и result, не считает worker
текст подтверждением изменения. Write workers требуют отдельного ownership и
конфликт-checked publication, без бесконтрольного общего filesystem.

Acceptance: handoff scope/cost/cancel, compromised worker output, stale hashes,
конфликт результатов, parent failure/recovery; затем измерить task-success benefit.

Для каждого пакета: regression tests, Windows CPU и Linux/Docker evidence,
обновление PROJECT_CONTEXT/index/status, commit от Omirbay Akseleu и push в
`experimental` через process-only token. Не сливать main автоматически.
Наличие реализации не закрывает independent multi-repository evaluation gate Stage 4.
