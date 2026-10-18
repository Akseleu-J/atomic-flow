# Пакет 1: достоверные результаты действий

Дата: 16 октября 2026. Реализовано локально поверх `820fc83`; коммит/публикация не выполнялись.
Это завершённый первый пакет `PRODUCTION_ROADMAP.md`, а не завершение всего production-плана.

## Изменения

| Где | Результат |
|---|---|
| agents/result.py::ToolResult | общий host-owned outcome success/error/denied, payload, verification и компактный audit summary |
| agents/loop.py::execute_tool | обязательный allowlist роли, проверка args до approval, audit всех early returns/исключений, отдельный edit opt-in |
| llm/orchestrator.py::_invoke_with_tools | передаёт allowlist и роль в dispatcher |
| llm/orchestrator.py::_answer | tool failure/denial добавляет warning и понижает итоговый ok до needs_retry |
| agents/task_loop.py::_execute_step | callback передаётся через публичный параметр Orchestrator; config=None получает defaults |
| agents/task_loop.py::_verify_step | ошибка/отказ/неполный результат не дают done; py_compile требует applied edit + syntax; заявленные edit/run_command должны состояться |
| agents/task_loop.py::_verify_step | pytest должен быть full-suite и после последней правки; иначе runner запускает полный pytest сам |
| agents/tools/edit_node.py | идентичная замена не считается применённой правкой и не создаёт backup |
| cli.py::cmd_task | отсутствующий/устаревший индекс строится и сохраняется до Orchestrator; rebuild ловит TreeError вместо всех Exception |
| README.md | контракт прав на запись и правила проверки шагов |

Право на запись: для ask нужны --approve-edits либо ATOMIC_FLOW_ALLOW_EDITS=1,
затем разрешение policy (с callback при ask). Trust и --yes сами по себе не включают edits.
Запуск/возобновление task включает edit capability, но не отменяет policy/approval.
Действие, запрещённое для роли, не доходит ни до callback, ни до tool.run.

Любой tool error/denial внутри шага приводит к StepFailure, даже если модель после него заявила успех.
Текущая политика консервативна: успешно исправленный позднее tool failure тоже проваливает этот шаг;
runner может перепланировать дальнейшие действия. Это явный компромисс до транзакционного task executor.
verify_with=none разрешён для отчётных шагов, но не скрывает tool failures/отсутствие явно заявленного edit/command.

## Проверка

- До исправлений: 26 новых regression cases провалились; лог `validation/step1-before.txt`.
  Часть проверяла новый интерфейс, часть воспроизводила прежние поведенческие ошибки.
- Итоговый штатный pytest: **111 passed, 1 skipped**, 15.93 s; `validation/step1-final.txt`.
- Дополнительные root-level v3 tests: **17 passed**; `validation/step1-root-v3.txt`.
- compileall src и git diff --check: PASS.
- Ruff на всех затронутых runtime/test-файлах: PASS; глобальные прежние замечания в других файлах не исправлялись.
- Сохранённые тесты используют fake/scripted backend; реальных LLM/Kaggle вызовов и расходов не было.

Новые integration tests используют настоящий TaskRunner + Orchestrator + dispatcher + tools:
разрешённая правка, отказ через переданный callback, rollback при реально провалившемся matching pytest,
запрет edit от Critic и свежий CLI task без tree.json. Отдельно проверяются malformed args,
unknown tool, policy denial, exception callback/tool, timeout/missing exit code, no-op edit,
отсутствие действия и stale/partial pytest evidence.
Старые FakeOrch fixtures обновлены: успешный результат теперь содержит outcome и syntax evidence.

Пропущен POSIX permissions test на Windows; warning касается депрекации httpx в Starlette TestClient.
Эти результаты подтверждают локальные execution properties, не качество решения реальных задач моделью.

## Остающиеся границы и следующий пакет

1. Изменения всё ещё применяются в рабочем проекте; нет task-wide changeset или sandbox выполнения тестов.
   Ошибка шага не откатывает автоматически все предыдущие успешные правки задачи.
2. Нет защиты от устаревших line ranges при нескольких edits, конфликта с внешней правкой и неверного scope undo.
   Следующий пакет: expected content hash + reindex/evidence refresh + транзакция changeset.
3. Planner/replanner, budget на успешном пути, настоящая cancel/resume, ID/path guards остаются отдельной работой.
4. Syntax/test PASS не доказывает произвольное текстовое expected_outcome. Смысловая приёмка задачи требует
   независимых acceptance checks; heuristics выбора matching tests пока сохранены.
5. Итоговый verdict понижается после failure, но текст LLM не переписывается автоматически;
   клиент должен показывать warnings/tool outcomes. В task эта ситуация не даёт done.
6. Изменённый внутренний execute_tool требует allowed_tools; старые внешние прямые вызовы нужно обновить.
   Старые audit-записи без outcome не принимаются как успешное доказательство нового шага.

Индекс `project-index.json` обновлён по рабочему дереву: HEAD остаётся базой, локальные изменённые
и новые Python-файлы отмечены отдельно. Публичный push не выполнялся.
