# Независимая сверка пилота и production-статуса

24 октября 2026. Проверена текущая копия `atomic-flow/`, исходный HEAD проверки
`445f1b9f036111de0c2113884cc9eb65e78749e1`, ветка `experimental`.
Проверка отдельным AI-reviewer: чтение исходников, Git history, JSON/log/patch
артефактов и пересчёт их чисел/хэшей. Новых model API-вызовов, обращения к vault,
запуска candidate-кода, exploit/reproducer-проверок или полного suite не было.
Это не внешний человеческий аудит и не security certification.

Локальная история подтверждает наличие четырёх названных в переданном отчёте
коммитов: `2399ebf`, `d67579b`, `5dfa9f8`, `445f1b9`. Отчёт описывает уже эту
рабочую копию; повторный перенос тех же пакетов не требуется. Состояние GitHub CI
нужно проверять отдельно по конкретному source/merge SHA: эта read-only сверка
не переисполняет CI и не выдаёт исторические результаты за новый прогон.

## Подтверждённый результат пилота

Пилот не получил успешного исправления задачи. Отрицательный результат сохранён,
а не исключён из evidence. Он относится к одной уже раскрытой задаче
`sympy__sympy-21614`, target base
`b4777fdcef467b7132c055f8ac2c9a5059e6a145` и одной модели
`google/gemini-3.1-flash-lite-preview`.

В `validation/import-20261024/stage4-public-pilot-v2/calibration.json` действительно
сохранены base FAIL / gold PASS / plausible wrong FAIL, с exit codes 1/0/1.
Это калибровка конкретного оценщика на конкретной задаче, не оценка модели и не
доказательство защиты оценщика от произвольного враждебного Python-кода.

Финальный baseline patch независимо оценён как `requirement_failed`:
`stage4-live-pilot-baseline-attempt3-grade/grade.json`. Сверены SHA-256 реальных
файлов с полями этого grade:

| Файл | Подтверждённый SHA-256 |
|---|---|
| `stage4-live-pilot-baseline-attempt3/candidate.patch` | `54c5e29844729c2ecca2a1c45ab8b57bfa71595db59dfc0654207a59626c22cc` |
| `stage4-live-pilot-baseline-attempt3-grade/sympy__sympy-21614-baseline-oracle.json` | `0247c32e398975d3ca7decf7da3c8149cc9de8c5aba807c259fd112f4ae01397` |
| `stage4-live-pilot-baseline-attempt3-grade/sympy__sympy-21614-baseline.log` | `10836f9ac4901d24be551fec8b6f965c4d863879ce5f590b885024d4c2b1c1d2` |

Raw log показывает 1 failed / 7 passed. Первая упавшая проверка — скалярная:
`Derivative(comm_x, comm_x).kind is NumberKind`. Причина — `TypeError` при делении
`_NumberKind / _NumberKind`. Формулировка переданного отчёта «TypeError на матричном
требовании» неточна: baseline остановился раньше матричных assertions. Wrong-patch
калибровка отдельно проверяет чувствительность к отсутствию матричного поведения.

Финальный atomic run имеет 6 model-response records и task status `paused`,
`error = task model-call limit reached`. В сохранённом
`atomic-state/runs/req_d51f7eb50f73/01_answerer_tool_3.json` запрос `patch_file`
получил `SourcePreconditionFailed` до эффекта: `effect_not_started = true`.
Модель не прочитала текущий источник полностью и передала placeholder `...`.
Кандидат отсутствует; независимого успешного grade для atomic нет.

## P1: в итогах нужно учитывать все расходы и отличать run от call

Пересчитаны все committed `stage4-live-pilot-*/report.json` и `attempts.json`.
Сохранены **23 response records с известной стоимостью** на общую сумму
**USD 0.0364695**. Разбиение:

| Направление | Charged runs | Model calls | Reported cost, USD |
|---|---:|---:|---:|
| Atomic: первый run и attempts 2–6 | 6 | 19 | 0.030702175 |
| Baseline: три named runs и отдельный debug-parser run | 4 | 4 | 0.005767325 |
| Всего с известными charges | 10 | 23 | 0.0364695 |

Отдельно сохранён baseline environment failure: 0 calls, `FileNotFoundError`,
стоимость неизвестна в report; нельзя заменять её на известный ноль без отдельной
доказательной классификации. Она не включена в сумму известных charges.

«Atomic — 6 попыток» означает шесть отдельных запусков с различными caps;
«6 вызовов» относится только к последнему запуску. Baseline также потратил квоту
на parser-debug run, который нельзя терять из общей стоимости.

В семи сохранённых per-run `kaggle-quota.json` разница `after.used - before.used`
согласуется с reported cost соответствующего run с максимальным расхождением
менее `1e-14` USD в представлении float по daily/monthly counters. Это числовое
согласование, не банковская
гарантия точного биллинга и не «совпадение до цента» для субцентовых сумм.

Не сохранены отдельные quota snapshots для первого atomic run,
`baseline-attempt2` и `debug-parser`. Их reported costs в сумме дают
USD 0.003051575 — столько уже использовано в первом доступном quota snapshot.
Последний daily counter `0.03646950000000082` согласуется с суммой всех известных
charges. Поэтому подтверждено агрегатное согласование; утверждение «каждый run
индивидуально сверен с собственной дельтой квоты» сильнее имеющихся артефактов.
Quota snapshots называют `owner-account-2`; вызовы аккаунтов участников этим
пилотом не подтверждены и в отчёте не заявлены.

## P1: source/config provenance недостаточна для формального сравнения

`tools/evaluation_live_pilot.py` сохраняет target base SHA, model, caps и hashes
request/response/candidate. Но текущая схема report не сохраняет framework HEAD,
dirty source manifest, версии runtime/dependencies, точные declared tariffs и
полный digest host config для каждого run. После исправлений parser/CRLF и смены
caps один итоговый commit не доказывает, какие именно framework bytes обслуживали
каждый предыдущий вызов. Исторические sidecars нельзя переписывать как будто эта
provenance была измерена в момент запуска.

Для следующего measured pilot нужны pre-send provenance manifest и отдельная
запись изменений между runs. Поле `human_interventions = 0` в каждом run сейчас
не учитывает вмешательства между runs: исправления программы, смену caps и
environment setup. Его допустимо читать только как отсутствие зарегистрированного
вмешательства внутри данного run, не всего эксперимента.

Плечи имеют разные interaction paths и разные caps после повторов. Baseline
получает `source_excerpt`: максимум 550 строк класса `Derivative`, ограниченных
32,000 bytes; это не весь исходный файл или репозиторий. Финальный baseline
получил один ответ, atomic — planner и последовательный tool loop. Эти данные
допускают диагностику, но не matched comparison, success-rate claim или вывод
о бесполезности фреймворка на других задачах.

## P1: wall limit был превышен, это реальный операционный барьер

Atomic attempt 3: `max_wall_seconds = 600`, measured wall ≈973.484 s.
Atomic attempt 6: такой же cap, measured wall ≈701.406 s.
Схема report честно называет enforcement cooperative. Ограничение model sends
не является гарантией end-to-end deadline: tree build, copy/recovery и cleanup
должны проверять cancellation либо выполняться на отдельно управляемом worker.
Локальные p95/RSS/soak и поддерживаемый workload пока не подтверждены.

## P2: текущую карту проекта нужно отделить от истории

На исходном HEAD проверки `PROJECT_CONTEXT.md` одновременно содержал новый live
pilot и старые текущеподобные утверждения: HEAD `8a18fbe`, незакоммиченный import,
последний Docker runtime `78f0df5`, model calls = 0 и отсутствие real integration.
Это исторические записи, но читаются как актуальная карта проекта. Их следует
отделить явно помеченным архивным разделом и вынести единственный current status
с tested SHA, artifact identity и оставшимися gates. W1/W2/W3/W5 «реализованы»
означает scoped implementation; исходные W1–W9 acceptance criteria шире наличия
файлов или focused pass counts.

## Документированные границы доверия

`evaluation_stage4.run` запускает reviewer-selected команду в отдельном процессе,
очищает environment и применяет timeout; это не OS sandbox. `evaluation_pytest`
импортирует candidate package под тем же OS-пользователем в процессе оценщика.
Хэши файлов и независимый процесс полезны для воспроизводимости обычных reviewed
исправлений, но сами по себе не образуют границу доверия для произвольного
враждебного Python-кода. Этот предел уже описан в package 24 и должен сохраняться
в W8/W9 acceptance. Проверок эксплуатации или инструкций по подмене не проводилось.

Read-only inspection дополнительного вопроса об unknown charge не выявил обхода
в atomic path: `TaskBudgetBackend` сохраняет unknown accounting и безусловно
выбрасывает `BudgetExceeded` до возврата response с неизвестной стоимостью,
независимо от включения денежного cap. PilotBackend отдельно прекращает следующий
вызов после unknown charge. Прямой baseline производит только данные candidate;
его output не является разрешением выполнить или опубликовать source.

## Порядок продолжения

1. Исправить текущие отчёты и агрегатный учёт без изменения исторических evidence.
2. Закрыть измеренный cancellation/tree rebuild барьер и сохранить regressions.
3. Расширить W6 composition acceptance на declared failure/recovery journeys.
4. Подготовить остальные 29 historical environments и reviewed evaluator workers,
   затем base/gold/wrong calibration; не расходовать модельную квоту до этого.
5. Заморозить model/config/caps и source provenance следующего pilot; сохранить
   failures, unknowns и человеческие вмешательства на уровне всего эксперимента.
6. Выполнить W7 real-repository measurements/soak и W9 release rehearsal по
   точному source/wheel manifest. До этих gates production readiness не заявлять.

## Воспроизводимый offline пересчёт evidence

Для повторной проверки добавлен `tools/audit_live_pilot.py`:

```powershell
.venv/Scripts/python.exe tools/audit_live_pilot.py validation/import-20261024 --output validation/import-20261024/live-pilot-audit.json
```

Он читает только непосредственные run reports, attempt records, quota receipts и
опциональные candidate bytes для SHA-256. Не вызывает провайдеры, не получает
credentials и не импортирует candidate. Существующие run artifacts не изменяет.
Отчёт сохраняет каждый failed/unknown/zero-call run, totals по arm, coverage
квитанций и digest каждого прочитанного evidence-файла. Одинаковые request/response
fingerprints отмечаются без удаления records: одинаковые ответы не доказывают
одинаковый physical send. Неизвестные charges остаются неизвестными, а сумма
известных расходов называется нижней границей.

Несовпадения call count, complete cost, quota delta или candidate hash приводят
к ненулевому exit code. Отсутствие receipt — coverage gap, а уменьшение quota
counter — возможный reset; они сохраняются явно и сами по себе не объявляются
доказательством неверного списания. Audit не доказывает отсутствие иной активности
аккаунта и не превращает exposed pilot в matched comparison.

Проверка инструмента: **7 passed** (`live-pilot-audit-tests-final.txt`), Ruff PASS
(`live-pilot-audit-ruff.txt`). Первый тестовый прогон имел 6 passed / 1 ошибку
ожидаемого synthetic count (2 + 2 было ошибочно записано как 5); ожидание исправлено,
исходный лог сохранён в `live-pilot-audit-tests.txt`. Итоговый фактический пересчёт
в `live-pilot-audit.json`: 11 reports, 23 attempt records, USD 0.0364695 known cost,
один zero-call/unknown-cost environment failure, семь quota receipts, три charged
runs без индивидуальных receipts, ноль внутренних inconsistencies. Пять warnings
сохраняют четыре отсутствующих receipt и отсутствие attempts.json у environment
failure; они не исключают эти runs из отчёта.
