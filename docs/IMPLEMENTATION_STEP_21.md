# Пакет 21 — W6 начало: capability composition durable TaskRunner

24 октября 2026. Основание: `audit-2026-10-23/runtime-audit.md` RT-04 и
`quality-scale-audit.md` Q03: `Orchestrator`/SDK принимают workspace_session,
lsp_client, documentation_client, hooks, tool_registry, role_tools, а
`TaskRunner` — нет; единственным путём было наследование/подмена `_execute_step`
с потерей общих budget/lease/cancel границ.

## Реализация

`agents/task_loop.py`:

- `TaskCapabilityContext` (frozen): task_id, project (исходный корень),
  workspace (staged корень этого шага: changeset workspace при активном
  staging, иначе исходный корень), phase.
- `TaskCapabilities`: шесть опциональных полей, ровно сигнатура Orchestrator.
- `TaskRunner.__init__(..., capability_factory=None)`: host-owned фабрика
  вызывается заново на КАЖДЫЙ шаг (`_execute_step`) с контекстом текущего
  staged корня; возвращённые capabilities передаются в Orchestrator этого шага
  без изменений. Возврат не `TaskCapabilities` → `StepFailure` до создания
  Orchestrator/модельных вызовов.
- Бюджет/lease/отмена/verification не изменились: step по-прежнему идёт через
  `TaskBudgetBackend`, изменения трекаются через result_summary и changeset.

Regressions (`tests/test_capability_composition.py`):
1. Фабрика наблюдает staged binding: workspace — task-owned changeset
   workspace, project — исходный корень; capabilities доезжают до step
   Orchestrator неизменными (tool_registry is, role_tools).
2. Инжектированный read-only tool выполняется в шаге под ТЕМ же task budget:
   budget_events step-фазы completed, ledger total == task.budget_spent
   (один accounting owner, без отдельного бюджета).
3. Некорректный возврат фабрики → StepFailure до любого Orchestrator/модельного
   вызова (recording-подкласс не сконструирован).

## Границы (честно)

- Это composition boundary, а не acceptance реальных интеграций: pinned
  реальный LSP analyzer, внешний MCP server и перепривязка root-bound клиентов
  на staged workspace остаются открытыми (RT-04/Q03 acceptance полностью не
  закрыт). Фабрика — trusted host code: инжектированный tool получает ctx
  staged корня, но host Python не сендбоксится.
- Capabilities не персистятся: после crash-recovery их восстанавливает только
  доверенный host, повторно вызвав фабрику; persisted task state никогда не
  содержит этих объектов.
- Полный W6 journey (plan→review→edit→checker→publish с LSP/MCP операцией,
  schema drift, stale analyzer) — следующая работа; W7/W8/W9 не начаты.
