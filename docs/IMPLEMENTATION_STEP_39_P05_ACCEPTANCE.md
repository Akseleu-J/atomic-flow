# Package 39 — P05 core completion and acceptance

25 октября 2026. Локальный пакет собирает ранее не опубликованные packages 35–38
и завершает host contracts P05 поверх HEAD `20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d`.
CI acceptance для нового commit фиксируется отдельно после публикации.

## Что закрыто в ядре

| P05 contract | Реализация |
|---|---|
| Stable task/subtask IDs и hierarchy | task ID + append-only step indexes; namespaced goal IDs, depth 4, 32 goals/generation, history 128 |
| Scope, caps, links | Exact file scope и declared code/test node links; ancestor max calls / reserved USD shared across child steps and expansions |
| Versioned states и migration | Existing task schema 2 + goal/receipt schema 1; pending/blocked/running/done/failed/cancelled/superseded; ready derived, done означает выполненные host checks |
| Bounded decomposition | Existing Plan/Step and strict DAG validation; expansion 1–3 children, history 128 steps, max 8 expansions |
| Rolling planning | Ready frontier + next dependency horizon 1–3, explicit `expand_plan`, CLI proposal file; approval clears before expanded execution |
| Revision/evidence context | Host receipts bind task/step contract, before/after source hashes, scope, current graph fingerprint, read-plan context revision and ledger reference |
| Failure/budget context | Replanner gets current workspace/graph/context revisions, known step states, receipt digests, remaining calls, recorded costs and criteria |
| Explicit invalidation/no replay | Stale direct prerequisite receipt pauses; completed work changes only by explicit invalidation; cancelled terminal tasks cannot resume |

Goal.scope_paths=None наследует scope, [] запрещает записи. Step.scope_paths=[]
наследует goal/task scope. Scope нельзя расширить в дочерней цели/шаге; write-tool
gate и полный diff шага независимо проверяются. Нарушение через команду вызывает
rollback. Это не добавляет изоляцию trusted-local исполнению.

Goal USD=0 запрещает платный reservation, None не добавляет лимита. Admission
проверяет caps всех ancestors до отправки; фактическая стоимость сохраняется до
overspend pause. Unknown charges/effects сохраняют прежние holds. Доказанный
not_sent не расходует call cap, refusal/реальные попытки расходуют. Replan явно
атрибутируется всем оставшимся goals, без случайного наследования последнего step.

В receipt нет raw model/tool args/output/errors: только bounded host metadata и
digest. Unkeyed SHA256 не является подписью или разрешением. Runtime всегда
использует whole-manifest freshness: retrieval skeleton и auto-pytest могут видеть
файлы, отсутствующие в tool transcript. Поэтому даже unrelated source edit может
консервативно остановить зависимый шаг. Narrow premise API готов для будущего
host-proven coverage, но runtime его сейчас не использует.

Historic receipts могут стать stale после последующих согласованных edits;
финальный отчёт показывает это отдельно от независимых final checks текущего
snapshot. Criteria остаются not_assessed: выполненные steps и passed pytest не
объявляются универсальным доказательством смыслового выполнения требований.
Связи code/test nodes — проверенные по форме ссылки, не утверждение, что resolver
доказал семантику каждой ссылки. Неизвестный context revision остаётся пустым.

## Host API и CLI

`TaskRunner.expand_plan(task, index, plan)` под task lease сохраняет проверенное
предложение и приостанавливает задачу для нового source-bound review. Вызывает
тот же scheduler; tool/verification/criteria/scope не расширяются. Завершённые
эффекты не повторяются. Это механизм планирования, не новый автономный агент.

`atomic task --expand-step INDEX --task-id TASK_ID --plan-file proposal.json`
работает offline; `--yes` не утверждает expanded plan.
`atomic task --explain-next TASK_ID` показывает ready/blocked/horizon/revisions и
freshness evidence без создания provider. Модель сама не выдаёт approval.

## Локальная проверка и independent review

Evidence: `validation/p05-closure-20261025/`. Probe: 87 PASS. Первый integration:
192 PASS / 2 FAIL — transient Windows PermissionError при atomic replace внутри
OneDrive и устаревшее ожидание cancelled=failed в composition fixture. Последнее
обновлено под новый terminal state. Не ослаблялись execution или rollback gates.

Повторный integration вне OneDrive: **194 PASS** за 77.97 s. После последних review
регрессий CLI/receipt/cancel: **24 PASS / 2 SKIP** за 6.32 s. Дедупликация JUnit:
**208 PASS / 2 SKIP (210 unique cases)**. Скипы: POSIX process-group guarantee на
Windows и opt-in real Docker run. Ruff и compileall src/tools PASS. Исторические failures package 38 не
перезаписаны и не выдаются за доказанно исправленную ОС-проблему.

Review исправил incomplete premise binding (retrieval/auto-pytest), receipt scope
binding и согласованность cancellation prepare/resume. Windows PowerShell/.NET
также не стартовали при paging-file pressure; native Python/Git без PowerShell
позволили провести bounded проверки, параметры памяти ОС не менялись.

`tools/capture_p05_acceptance.py` сохраняет JUnit outcomes, raw/normalized source
SHA256 и checkout SHA с explicit runtime dirty flag. CI Python jobs сохраняют эти
artifacts для точного tested checkout. Непубликованный working diff не объявляется
прошедшим CI по старому HEAD.

## Граница принятия

P05 functional host contracts реализованы; exact-source CI остаётся обязательным
последним gate этого пакета. Automatic invocation/approval of hierarchical rolling
expansions, независимый semantic requirement evaluator, measured model utility,
скорость и scale, полный graph delta hot path, release и isolated-worker acceptance
не подменяются этим результатом: это следующие P06–P14 и прежние production gates.
