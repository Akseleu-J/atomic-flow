# Package 38 — P05 hierarchy и acceptance metadata

25 октября 2026. Локальное продолжение packages 35–37 поверх
`20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d`. Без provider calls/commit/push.

## Реализовано

Planner принимает `goals` с локальными ID, parent_id, описанием и explicit
acceptance_criteria; шаги ссылаются на parent_goal и могут уточнять свои criteria.
Host проверяет max 32 goals на generation, depth 4, max 8 уникальных непустых
criteria по 300 символов на node. Goal обязан иметь исполнимый descendant step.
Cycles, duplicate/missing IDs, cross-generation parent и orphan goals запрещены.
Persisted admission повторяет проверки, общий historical cap — 128 goals.

В task IDs namespaced как `p{step_offset}:local_id`; вместе с task ID они стабильны
в пределах durable task. Revise/replan после выполненной работы добавляют новую
generation, сохраняя history. Полная замена ещё не начатого плана заменяет и goals.
Prepare/run_plan сохраняют те же metadata; второго scheduler не добавлено.

Canonical approval включает непустую goal/criteria metadata и goal_schema_version=1.
Legacy tasks остаются schema 2 с optional additive fields; при пустой metadata
canonical bytes не меняются. Legacy schema 0/1 по-прежнему мигрирует без authority.
Unknown persisted goal fields отвергаются. Изменение hierarchy/criteria делает
approval stale, invalid goal state отвергается до сравнения digest.

Current-step context содержит ancestor requirements и step criteria. Frontier
показывает metadata ближайших шагов и агрегаты активных дочерних steps. Состояние
all_active_steps_done — наблюдение; criteria_status всегда not_assessed в этом
пакете. Final completion report явно отделяет declared checks от независимой
проверки смыслового выполнения criteria. Существующие final checks сохранены.

Replan prompt получает structured requirements remaining steps и criteria goals
с выполненными steps как неоценённые observations. Завершённые effects не просятся
к повтору. Model JSON output/repair явно описывает goals. Контекст ограничен
32000 символами, overflow приостанавливает задачу без усечения требований.
Исторические goals без active/done descendants (все superseded) остаются в state
и review, но не включаются в model requirements context.

Internal replan при старой или новой hierarchy/criteria metadata консервативно
требует fresh review. Модель не может автоматически отбросить criteria. Это
осознанное ограничение автономности до независимого requirement evaluator.

## Проверка

Evidence: `validation/p05-hierarchy-20261025/`. Новые behavioral tests охватывают
roundtrip, stable IDs, ancestor context, stale approval, legacy canonical parity,
migration, persisted limits, invalid decomposition, revise history, replan refusal,
context overflow и distinction done/semantic acceptance. Соседние tests проверяют
approval, envelope, invalidation, completion, workspace publication и CLI.

Первый narrow exploratory run: 75 PASS. Расширенный первый run: 100 PASS / 5 FAIL
с пустым `workspace rollback failed` в соседних сценариях. Два случая отдельно
повторены: 2 PASS. Добавлен тип recovery exception в diagnostic вместо пустой
строки. Следующий полный selection: 107 PASS / 1 FAIL: новый fixture менял
parent_goal так, что оставлял orphan; host корректно отвергал структурно до stale
digest. Fixture исправлен на валидную перестановку hierarchy. Исходные failing
JUnit/logs сохранены; причина первых пяти intermittent failures не установлена,
они не объявляются доказанно исправленными данным пакетом.

Финальная неизменённая selection: **107 PASS / 1 FAIL** за 60.13 s. Failure:
`test_multistep_task_publishes_all_or_none[True]`, task.error=`MemoryError:`.
Windows сразу после run: FreePhysicalMemory=77544 KiB, FreeVirtualMemory=283112 KiB
(около 76 MiB / 276 MiB). Давление памяти хоста подтверждено; связь с первыми
пятью failures остаётся предположением, а не установленной причиной. Final manifest
сохраняет status=failed. Ruff/compile PASS, полного suite/CI/live acceptance нет.
Не повторять тяжёлые checks до освобождения ресурсов; safety limits не ослаблялись.

## Открытый остаток P05

Это не последний шаг P05 и не production acceptance. Остаются:

1. Revision-bound step evidence и связи с code/test graph nodes, использование
   source/graph revisions и known failures в следующих planning decisions.
2. Bounded rolling expansion общего плана с детализацией descendants ближайшего
   horizon, per-subgoal caps/scope и полная planned state/cancel семантика.
3. Независимая acceptance вертикали: resume/migration/dependencies/invalidation
   и evidence-linked outcomes на exact-source CI. Семантический requirement
   evaluator относится к quality/utility gates и не заменяется Step.done.

Четыре production-блока quality/scale/release/isolated-worker acceptance остаются.
