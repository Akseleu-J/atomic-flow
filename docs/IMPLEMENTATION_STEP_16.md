# Пакет 16 — read-only specialists и durable handoff

23 октября 2026. Пункты **46–47** реализованы scoped: изоляция контекста и
инструментов read-only workers, общий parent budget/cancel и checked advisory
handoff. Вместе с пакетом 15 — **62 implemented_scoped / 0 planned**.
Worker не получает write/execute инструменты или отдельный лимит расходов.

`workers.WorkerCoordinator(project, parent_budget_backend)` и SDK
`worker_coordinator(...)` требуют **тот же TaskBudgetBackend**, тот же project
и parent task. `.run(instructions, role='specialist'|'reviewer', source_paths=[...],
max_calls=4)` создаёт отдельный неизменяемый snapshot. До 16 admitted файлов,
64 KiB/file и 128 KiB total; private/ignored/credential/link paths запрещены,
source redacted. Worker может только `read_source` выбранных путей и bounded JSON
final answer. Он не получает registry, parent conversation, host credentials или
возможность создать дочерних workers.

Все worker model calls проходят тот же durable reservation/settlement, daily
ledger, max_model_calls, token/byte admission, parent cancellation и unresolved-charge
gates. `TaskBudgetBackend.call_scope()` сериализует весь handoff с другими model
calls родителя на этом экземпляре, включая native accounting. Ожидание lock
cancellable. Копирование/создание независимого backend для обхода бюджета не
является разрешённым способом запуска worker. Отдельные task runners по-прежнему
требуют существующий OS task lease; новый lock не заменяет межпроцессную task lease.

Durable records хранят prompt/snapshot/result SHA-256 и состояния pending/completed/
failed/cancelled/unknown под OS-held coordinator lease, до 128 records на task.
Intent сохраняется до вызова. `.status(id)` offline; `.recover(id)` переводит
незавершённый handoff в unknown, не повторяет model/action. Unknown/pending блокирует
новый handoff до внешнего разрешения неопределённости. Автоматического replay нет.

`WorkerResult` содержит summary/findings и evidence только реально прочитанных
worker source paths. `.accept(result, parent_read_hashes={path: SHA1})` сверяет
durable completed record, все manifest hashes, актуальные bytes и независимое
чтение родителя для snapshot. Stale/tampered/foreign result отклоняется.
Принятый текст остаётся advisory data: permissions и read proof не переносятся;
вывод worker не подтверждает правку или успешный check.

Это **не OS sandbox** для Python backend: host/backend callbacks доверенные.
Writable workers, branch ownership/conflict publication, автоматический router,
отдельный worker model provider и измеренная task-success выгода не включены.
CLI worker execution не добавлен: host должен предоставить уже существующий
бюджетный backend через SDK, а не создать из команды независимый бюджет.

Проверки: source/context scope, private/ignored/link admission, call limits/cost/
unknown/cancel, crash recovery без replay, result tampering/stale parent hashes,
конкурирующий parent model call и отмена ожидания общего lock. Локально блок
worker+existing task budget дал **49 passed**; итоговый focused run и exact-commit
Linux/Docker evidence см. `PROJECT_CONTEXT.md`. Fixtures scripted, paid calls **0**.
Stage 4 live edit / multi-repository quality и benefit измерения остаются открыты.
