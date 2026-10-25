# Package 37 — P05 rolling frontier (25 октября 2026)

Локальное продолжение поверх packages 35–36 и HEAD
`20cdd7767ad846ad42dd9d08fcd709c4d5c11b0d`. Без provider calls, commit или push.

## Поведение

`agents/frontier.py` вычисляет read-only проекцию существующего task DAG.
Scheduler использует тот же расчёт готовности вместо двух разных dependency
проверок. Порядок остаётся порядком approved plan, IDs — `task_id:step.index`.
Ready/blocked вычисляются из актуальных статусов, включая явную invalidation.
Superseded не удовлетворяет prerequisite. Failed work перепланируется после
доступных независимых шагов. Running work останавливает scheduling до сверки.

`TaskRunner.explain_next(task_id, horizon=3)` возвращает структурную готовность,
причины блокировок и до трёх подробных шагов. Проверяет project binding, не пишет
state и не выдаёт разрешение исполнять paused/unapproved task. Task validation
отвергает cycles/missing dependencies. Проекция вычисляется заново после resume.

Контекст текущего шага получает bounded frontier с expected outcome,
verification mode и request IDs prerequisites. Эти IDs указывают на существующие
host ledgers; пустая ссылка legacy done шага остаётся пустой. Режим `none` явно
виден. Это наблюдения, а не доказательство выполнения произвольных acceptance
criteria. Retrieval query остаётся прежним. Следующие шаги не исполняются внутри
текущего шага и не получают новых capabilities.

Horizon 1–3, descriptions/outcomes до 500 символов, verification до 80,
prerequisites до 20/шаг, ledger reference до 128 с explicit truncation.
При переполнении model context JSON заменяется компактной валидной проекцией.
Полный список ready IDs и blocked prerequisites остаётся доступен host API;
он ограничен существующим task state admission, а не horizon.

## Проверка и ограничения

Evidence: `validation/p05-frontier-20261025/` — JUnit, pytest output и source hashes.
Основная selection: **82 passed** за 273.73 s. После финальной правки reference
length повторены **12 frontier tests**, все PASS; это пересекающийся набор,
итог уникальных test IDs остаётся 82. Ruff/compile/diff PASS. Полный suite и CI
на этот working diff не запускались. Source hashes фиксируют последнее дерево;
main selection импортировала frontier до последней bound-only правки, которую
покрывает отдельный `junit-frontier.xml`.
Новый сценарий running+ready появился после независимого review. Другой review
пункт — ограничение ledger references до JSON serialization — также исправлен.
Первый exploratory run: 49 passed / 2 failed; оба новых теста ошибочно ожидали
paused для исходно running task и ValueError вместо ChangeSetConflict. Исправлены
fixtures/ожидания по существующим контрактам. Этот run не итоговая acceptance.

P05 остаётся OPEN: нет parent-goal hierarchy, bounded model decomposition,
связей code/test graph nodes, per-subgoal caps и revision-bound acceptance evidence.
Детализация horizon сейчас охватывает доступные шаги; будущие descendants
текущего running шага ещё не раскрываются заранее. Нет нового durable scheduler,
automatic execution replay или semantic verification. Production quality, scale,
release и isolated-worker acceptance остаются открытыми.

Дальше: parent goals/acceptance criteria в versioned plan с approval binding,
затем source/test-revision evidence и bounded horizon expansion. Полный graph
delta hot path из P04 также остаётся отдельным открытым пунктом.
