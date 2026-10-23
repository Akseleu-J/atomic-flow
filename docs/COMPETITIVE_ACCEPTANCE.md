# От каталога функций к проверяемому преимуществу

Основание: `CRITICAL_REVIEW_2026-10-23.md`, runtime `58b3dd4`.
Это выбранное направление следующей работы. Gates ниже — **предложения для
проекта**, а не опубликованные отраслевые нормы. На дату аудита они открыты.
Платные model API, новые cloud sessions и передача приватных репозиториев не
требуются для первых трёх блоков.

Обновление: пакет 17 реализует конкретные R01–R07 с targeted regressions;
контракты, ограничения и exact-source результаты см. `IMPLEMENTATION_STEP_17.md`
и PROJECT_CONTEXT. Это не закрывает следующие блоки B–E.

## A. Сначала закрыть подтверждённые дефекты

1. **R01: execution confidentiality.** Разделить admission исходников и явно
   разрешённых execution inputs, исключить private paths, закрыть raw output/
   diagnostics/artifact/history каналы. Один synthetic secret canary проверяется
   от snapshot до provider-bound message. Локальное поведение и реальный Docker
   проверяются отдельно; редактирование output не заменяет input exclusion.
2. **R02 + R04: единый учёт effects.** Run/task-scoped unresolved state, запрет
   дальнейших effects до host reconciliation; сохранить completed effects в
   workflow reports. Проверить повтор args, новые args, смену роли, steering,
   compaction, cancel и resume. Read-only диагностика может оставаться доступной
   по явной политике без выдачи новых прав на запись.
3. **R03: доверенный checker launcher.** Проверить identity установленного инструмента,
   устранить module shadowing, отделить zero collection от test pass. Покрыть
   run_command и final completion. Статус tests-passed требует result evidence,
   а не произвольного exit 0.
4. **R05–R07: scheduler и честные оценки.** Исправить delayed replan, failed-attempt
   usage и ложный rubric; добавить regression именно на найденные ошибки.

Выход из A: каждый воспроизводимый дефект имеет regression, исправление и
зафиксированный результат. Проверять изменённые места и соседние risky paths;
не повторять весь Windows suite без нового основания. Обязательный Linux/Docker CI
остаётся отдельным exact-source gate. Не называть A завершённым только по числу тестов.

## B. Сделать один поддерживаемый рабочий продукт

- Один host entry point durable task с capability injection и staged project
  bindings; общий budget/cancel, scoped approvals и unknown-effect recovery.
- До snapshot: enforceable file count, per-file/total-byte/disk bounds; streaming
  hashes/copy и проверяемая cancel latency. Сначала задать workload/лимиты,
  затем измерять; не обещать конкретные p95 без данных.
- Небольшой набор настоящих user journeys: установка → индекс → review плана →
  правка → тесты → publish; конфликт пользователя; отмена; crash/recover;
  notebook source edit; реальный LSP rename; внешний pinned MCP fixture server.
- Установить выбранный реальный LSP analyzer и проверить совместимость отдельно
  от synthetic JSON-RPC tests. IDE client и write workers не добавлять ради
  каталога, пока основной durable path не принят.

Выход из B: journeys воспроизводятся из инструкции, имеют source/config/version
и artifacts; ограничения очевидны из результата команды, а не только из пакета docs.

## C. Release, который можно установить и воспроизвести

Build wheel/sdist один раз; clean install за пределами checkout; smoke CLI/SDK/API,
packaged fixtures и migration. Source не должен незаметно подменять installed package.
Задать tested dependency constraints, base image digest, support matrix, checksums,
SBOM и build provenance. Реально проверить verification procedure потребителем.
Не объявлять SLSA level или соответствие OWASP без отдельного assessment.

## D. Измерить полезность — Stage 4

Начальный бюджетный эксперимент: **30 независимых задач из пяти Python repo**,
с development/holdout split по repository и фиксированными base SHAs. Это
exploratory pilot, не статистическое доказательство общего преимущества. RU/EN
формулировки и повторные seeds не считаются независимыми задачами.

Включить bugfix, signature/callers, config/import/new file, негативные задачи,
abstention и conflict/recovery. Из исходного base отдельно применить candidate
patch, затем запускать скрытые requirement tests и исходные regression tests.
Изменения grader/tests кандидатом не могут ослаблять результат. Для каждого case
calibration: base FAIL, gold PASS и plausible wrong patch FAIL; integrity failures
инфраструктуры отмечаются отдельно, но не исчезают из end-to-end report.

Первая пара: capable plain coding agent и full atomic. При доступности backend —
pinned mini-SWE-agent; различия permissions/runtime честно сохранять. Одна frozen
model/provider/config, одинаковые задачи, максимально разрешённые token/call/time/
quota caps; одинаковые retry правила. Фактическое потребление может различаться —
это и измеряется. Чередовать/рандомизировать порядок пар, сохранять все failures,
timeouts, human interventions и unknown usage.

Первичная метрика: **полное независимое выполнение требования без regression**.
Вторичные: false-success reports, ручные исправления, полная стоимость на успешную
задачу с затратами неуспехов, tokens/calls, wall median/p95, RSS, indexing time.
Бесплатная quota cost equivalent не называется оплатой или provider invoice.

Затем последовательно ablate graph retrieval, critic, compaction и workers.
Не запускать полный combinatorial grid, пока основной baseline не работает.
Performance отдельно измерить на pinned small/medium/large repos: cold/warm index,
одна внешняя правка, token-budget recall, resource growth и cancel under load.

## E. Что будет считаться преимуществом

До доступа к confirmation holdout выбрать **одну** основную гипотезу. Возможные
практические пороги, предложенные для обсуждения/предварительной регистрации:

- Efficiency: не хуже baseline более чем на **5 процентных пунктов** success,
  при этом минимум **20% ниже cost per verified success**.
- Или quality: минимум **5 процентных пунктов выше success** при сопоставимом
  максимальном бюджете.
- Дополнительный guardrail: p95 не выше **1.25× baseline**, если именно такой
  tradeoff приемлем продукту; безопасность и scope violations не компенсируются
  средним выигрышем качества.

Это не thresholds, которые уже достигнуты. Для заявления нужны paired intervals,
учёт clustering по task/repository и отдельный powered confirmation experiment.
Объём подтверждения рассчитывается по наблюдаемой paired discordance и выбранной
мощности; 30 задач или произвольное фиксированное N не гарантируют значимость.
При пересечении interval с выбранным margin результат **неопределённый**.
Пять repo недостаточны для широких population claims; включить независимые repo
в confirmation. Сравнение с закрытым продуктом/другой моделью — отдельное исследование.

До D/E корректный статус: реализация и отдельные контракты проверены,
**конкурентное преимущество и production task quality ещё не измерены**.
