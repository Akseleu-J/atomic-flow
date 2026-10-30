# Независимый review P06 — 25 октября 2026

Reviewer: отдельный subagent p06_closure_review, запущенный по прямому поручению
владельца. Автор packages 40/41 — parent agent; этот проход отделён от его
self-review. Reviewed base HEAD 5c8de452fa4b6e3c962c84469e2369ae3c257e3b,
предыдущий sealed runtime da67d89a86c917249216747f44e82b9239b72ca2.

Изменения этого review при сдаче ещё не закоммичены: normalized SHA256 каждого
изменённого src/test файла записан в
validation/p06-independent-review-20261025/reviewed-files.json. Итоговый published
SHA и mandatory CI обязан привязать интегратор; старый зелёный CI этого diff не
покрывает. Commit/push reviewer не делал. Новых live/provider calls и расходов=0.

## Критерии и метод

Проверен исходный PRODUCTION_ROADMAP.md §P06, ASSISTANT_HANDOFF.md §7,
ADR-0001, packages 35/40/41, runtime envelope/plan review/task loop, physical
accounting/repair/goal lineage/failure evidence, completion gate и pytest launcher.
Проверены исходники acceptance-matrix.json/verify.py и source-bound CI evidence
package 41. Вывод не сделан только из числа PASS или workflow badge.

| Исходное требование P06 | Проверенный контракт |
|---|---|
| Exact-plan default, opt-in envelope | schema-1 default сохраняется; explicit opt-in включён в review digest |
| Immutable authorization при mutable plan | exact path/tool/runtime/source grants сохраняются; изменение требует review |
| Ancestor obligations и accounting | replacement сохраняет original IDs/chains/caps; ambiguous mapping pause |
| Observe/frontier/act/verify/update/decide | real public multi-file failed-check/repair/checks/publish/graph parity journey |
| Typed failure и stop semantics | test/check evidence отличается от model critique, unavailable/timeout/unknown |
| Spend/call/replan/no-progress/deadline limits | spend/call/replan guards существовали; deadline gap найден и исправлен ниже |
| Credentials/MCP/hooks/repo data | opt-in поддерживает built-in host allowlist, capability injection отказывается; repo text не grant |
| Single writer/shared budget | writable workers не включены; read workers используют parent call scope/accounting |
| Crash/resume/no replay | interrupted effect/call требует reconciliation; persisted state не mint authority |

## Finding IR-01 — P1, отсутствовал общий task deadline

Исходные §P06.1 и handoff §7 требуют deadline в immutable envelope и проверку
на переходах. В da67d89 TaskConfig и ENVELOPE_FIELDS не содержали task deadline;
TaskRunner/TaskBudgetBackend проверяли call/spend/replan limits, но только
per-request и pytest timeouts ограничивали отдельные операции. После длительной
паузы task могла продолжаться без общего срока. Это missing required contract,
а не допущенный skip или feature, перенесённая на P09.

Локализация: src/atomic_flow/config.py::TaskConfig,
agents/envelope.py::make_envelope, agents/task_loop.py::resume/
_require_replan_runtime, agents/task_budget.py::_chat_physical. Наличие gap
подтверждено source audit базового runtime; отдельный старый Git checkout с
deadline test не запускался. Новые behavioral tests проверяют исправленный путь.

Исправление:

- TaskConfig.max_duration_seconds: finite 1–604800 seconds, default 3600;
  параметр относится к reviewed opt-in envelope и измеряется от task creation.
- Internal envelope v2 хранит absolute UTC deadline_at = created_at + reviewed
  duration. Значение стабильно между review/approve и не продлевается при
  replan/resume. Изменение duration меняет review/runtime digest.
- Legacy internal envelope v1 строго декодируется для inspection, но autonomous
  execution требует fresh review. Никакой молчаливой выдачи нового deadline.
- Общий safe-boundary callback проверяет cancel и deadline перед step/tool/check/
  publication; runtime renewal тоже проверяет deadline. Physical admission
  отдельно проверяет его до reservation и непосредственно перед send.
- При expiry после reservation unsent hold освобождается без call. После known
  response cost сначала сохраняется в task/daily ledger, затем continuation
  останавливается. Unknown outcome не становится known zero или retry authority.
- Expiry после final checks блокирует publication; expiry во время replan не
  оставляет обновлённого automatic approval.

Время — trusted host UTC clock; это не гарантия provider cancellation/invoice
или защита от dishonest host, переписывающего часы/state. Эти пределы совпадают
с остальными authority/accounting boundaries.

## Finding IR-02 — P2, queued worker терял запуск на observer lease

Root обнаружил в final-doc Docker CI реальную ошибку, независимо проверенную
reviewer по коду и retained red/green evidence. JobStore.reconcile кратко берёт
worker.lock и для queued task. Detached run_worker использовал nonblocking lock,
поэтому мог выйти с ValueError(worker active) во время обычного status poll,
не дойдя до queued→running. Это подтверждённая race, а не названный без анализа flake.

Root patch в src/atomic_flow/job_worker.py: worker ожидает worker.lock, затем
под metadata lease перечитывает state. Только queued может перейти running;
terminal немедленно завершает worker, иной claimed state не исполняется. Lock
order остаётся worker.lock → state lease, duplicate submit/PID ownership не
вводятся. Поздний claimant не отменяет reconciliation и не replay эффект.

tests/test_job_claim.py использует реальную файловую lease, worker thread и
scripted execution: обычное ожидание и terminal transition до claim. Прочитаны
worker-claim-red.xml/txt: 2 FAIL с worker active; затем root selection
worker-claim-green.xml: 34 PASS / 2 SKIP (Docker отсутствует локально). Evidence
находится в validation/p00-p05-strict-audit-20261025/. Reviewer не выдаёт эти
scripted checks за real Docker acceptance — его проверит итоговый CI.

## Остальные проверенные границы

Original goal identity binding не сбрасывает расход при flat/hierarchical
replacement. Scope intersection консервативен; lost linked test/code obligations
не дают automatic authority. Новые/переименованные goals или изменённые caps
требуют review. Нельзя получить разрешение из descriptions, summaries или repo text.

Completion подтверждает current declared syntax/pytest checks и source integrity.
Step receipt current/stale остаётся diagnostic provenance, а не самостоятельной
semantic acceptance: зависимый step не выполняется со stale premise, final
required checks работают на current bytes. Declared criteria явно not_assessed
при отсутствии independent semantic oracle. Это не скрытый успешный grade.
Общий semantic utility evaluator относится к P07/E01, не подменяется этим review.

Fresh private bytecode prefix в installed pytest bootstrap проверен по коду и
regressions обоих направлений false FAIL/false PASS. -B сам по себе проблему
чтения старого .pyc не решает. Snapshot/mutation audit и final verification
сохраняют отдельные source checks. Dishonest test code не считается доверенным oracle.

## Выполненные reviewer проверки

- deadline-focused.xml: **66 PASS / 0 SKIP**, 54.71 s, соседние
  deadline/envelope/internal replan/goal/repair/public journey suites.
- deadline-final.xml: **8 PASS / 0 SKIP**, 9.50 s, включая expiry после final
  checks и в replan renewal. Selections пересекаются; counts не складываются.
- Ruff, compileall и git diff --check: PASS для reviewer src/tests. Первоначальный
  Ruff import-order finding исправлен; ruff-initial.txt сохранён.
- Первая shell-команда не создала папку stdout evidence, поэтому tests не
  запускались. Папка создана до корректного command; этот no-run не считается PASS.
- Никаких Docker installs, full local suites, новых paid calls, blind SymPy replay,
  Git commit/push, изменений common handoff/status/index reviewer не делал.

## Независимое решение

**Прямые P06 runtime/safety критерии: локальный independent review PASS после
исправления IR-01.** Дополнительных открытых P0/P1 findings в проверенном контракте
не найдено. IR-02 root patch независимо просмотрен и сохраняет no-replay claim
contract. Новые deadline cases не skipped и не deferred.

Для финального P06 acceptance интегратор должен: проверить reviewer patch,
зафиксировать исправленный source, принять ADR с deadline v2/migration wording,
получить зелёные обязательные workflows на exact source, включить deadline cases
в matrix verifier и проверить свежие JUnit/source/archive hashes. Старый runtime
CI не заменяет этот шаг. После него отдельный independent-review gate P06
может считаться выполненным без заявления о live model utility/production.

Dependency boundary: roadmap §4 требует принимать этапы по порядку; P06 depends
on P05, тот — на P04. Этот review не закрывает OPEN P00–P05 prerequisites и не
позволяет назвать всю ordered цепочку до P06 завершённой. Проверенные прямые
критерии P06 и незавершённые upstream этапы должны быть названы отдельно.
