# Пакет 20 — W2/W4 остатки: host reconciliation операция и executor manifest wiring

24 октября 2026. Основание: `audit-2026-10-23/PRODUCTION_PLAN.md` W2 («Восстановление»)
и W4 (executor contracts). Пакет 18 внёс observation-слой (`host_reconciliation`
session event, `QuotaStore.reconcile`) и `execution_manifest`; этот пакет добавляет
единую host-only операцию и CLI-доступ.

## W2: `atomic_flow.reconcile.reconcile_call`

Evidence-backed settlement ровно одного unknown hold'а:

- `scope="charge"`: task budget event со статусом `unknown` → `completed` с
  явным `cost_usd` (0.0 = подтверждённо бесплатно, не «забыто»); `budget_spent`
  корректируется на дельту; ledger-строка `settle` под тем же `call_id`
  (last-row-wins в `today_total`, резервация заменяется фактом); session
  `host_reconciliation` observation. Settlement не блокируется daily limit —
  фиксация уже понесённого расхода не может «отказать».
- `scope="effect"`: только session observation; money-учёт не трогается;
  требует существования session-наблюдений для call.
- Evidence: bytes файла (хэшируются, не сохраняются) + обязательные actor и
  reason; `reconciliation_id = reconcile_<sha256(task|call|scope|evidence)>` —
  детерминирован для переигровки.
- Идемпотентность/крэш-границы: операция под task lease (сериализована с
  runner'ом); каждый артефакт пишется durable; повтор с тем же evidence
  дозаписывает только отсутствующее (task уже reconciled → ledger/session
  repair, без дублей); другое evidence на уже reconciled call → отказ;
  изменённый settlement не «перезаписывается молча».
- Ни provider, ни tool вызов не повторяется; модель/план/новая задача не
  являются evidence (остаётся норма пакета 17).

CLI: `atomic task --reconcile CALL_ID --task-id ID --scope charge --cost-usd X
--evidence-file F --actor A --reason R` (печатает JSON-результат; ошибки —
обычный CLI exit 2).

W2 остаток: typed state transitions для jobs/remote crash-reconciliation и
формальный crash-model документ (power-loss границы уже заявлены в
`durability.py`); эти границы перечислены ниже.

## W4: executor manifest wiring

`execution_manifest(config, requirements, probe)` (пакет 18) теперь вызывается
из `run_python` (вместо дублированных проверок) и из `job_worker.run_worker`:
manifest identity сохраняется в job state при `start` и свериется перед
запуском — изменение runtime identity между подготовкой и исполнением
отказывает ДО запуска (regression: marker never executed). Job state пишет
явный `cleanup_confirmed`. Windows local runs идут через owned Job Object;
taskkill-неудача и job-assignment отказ фиксируются как unknown effects.
Trusted-local остаётся НЕ sandbox'ом (норма docs/EXECUTION.md); native
Linux/macOS executors и Docker-free целевая схема — будущая работа по W4,
не заявленная этим пакетом.

## Regressions

- `tests/test_reconcile.py` (8): settlement всех трёх артефактов, daily-limit
  не блокирует settlement, идемпотентность (один session event, стабильный
  reconciliation_id), crash-gap repair (session-запись восстановлена),
  input/state валидация (нет cost/отрицательный/пустой actor, чужой call,
  уже completed, другое evidence), effect-scope независимость, effect без
  session → отказ, CLI wrapper (успех + exit 2 на отсутствие --task-id).
- `tests/test_host_reconciliation.py` (пакет 18): manifest capabilities/
  refusal/digest, worker identity refusal, jobs collect — остаются зелёными.

## Границы

- Reconciliation операция не synchronizes внешние провайдеры/бизнес-эффекты:
  evidence предоставляет и отвечает за него host-владелец.
- `SpendLedger.settle` не проверяет daily limit сознательно; аудит суммы —
  задача хоста. Unknown quota-слоты провайдеров закрываются отдельно через
  `QuotaStore.reconcile` (пакет 18).
- W4 native executors (namespaces/bubblewrap, Job Objects как ЧАСТИ изоляции,
  AppContainer) не реализованы; `ExecutionRequirements` — точка расширения.
- Полный W2 crash-model (fsync-упорядоченность между task/ledger/session при
  power loss) не заявлен; supported model — process failure.
