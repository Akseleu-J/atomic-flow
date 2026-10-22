# Пакет 10: discovery, provenance, diagnostics и завершение задачи

22 октября 2026. Самостоятельная реализация кандидатов **26, 35, 37, 49**.
Вместе с пакетами 7–9 каталог содержит **29 scoped implementations и 33 planned**;
полный актуальный список — `CANDIDATE_STATUS.json`. Реализация оставшегося каталога
разрешена пользователем; этот пакет не объявляет его целиком закрытым.

## Отложенное обнаружение инструментов (49)

`ToolRegistry.register_lazy` регистрирует имя, описание, Pydantic args schema,
host contract и доверенную host factory. Метаданные доступны без импорта/запуска
factory; по умолчанию её schema не занимает model prompt. `tool_catalog` ищет
только среди tools текущей роли. `include_schema=true` активирует schema в текущем
registry, а не выдаёт новые разрешения. Следующий native request получает обновлённые
function schemas с повторной admission-проверкой и prefix diagnostic.

Dispatcher теперь проверяет роль, arguments, edit opt-in, policy и approval **до**
загрузки реализации. Factory вызывается один раз под lock. Загруженные name,
description, args schema и effects/deadline/output contract обязаны совпадать с
host metadata; отличающаяся реализация не запускается. Возвращаемые schemas — копии.

**Граница доверия:** factories — доверенный Python-код, зарегистрированный host SDK,
не код произвольного репозитория/модели. Проверка metadata не является Python sandbox
и не доказывает, что произвольная factory соблюдает свой контракт. Загрузка untrusted
plugins/MCP и marketplace этим пакетом не добавлены.

## Происхождение данных (35)

Каждый dispatcher result и его audit summary получают host-owned `provenance`:
tool, role, index_revision и `trust=data_only`. Payload инструмента не может
переопределить эти поля. Summary/history сохраняют происхождение; tool/source
блоки явно обозначены как данные, которые не расширяют host policy.
Ни tool result, ни callback ответа пользователя не меняют edit opt-in/role permissions.
Это маркировка плюс host enforcement, не гарантия отсутствия prompt injection
или semantic poisoning. Маркировка будущих web/MCP источников потребует их интеграции.

## Диагностика с ревизией (26)

`agents/diagnostics.py`: bounded location diagnostics для Python syntax и plaintext
вывода Ruff/mypy. Поля: path, line/column, severity, source/code/message, content_hash.
Внешние/private/unindexed пути исключаются; `/workspace/` сопоставляется с Docker bind.
После команды hash проверяется повторно; stale locations не выдаются за актуальные.
До 100 diagnostics, вывод runner остаётся ограниченным tail; parser не гарантирует
полного покрытия произвольных форматов linter/LSP.

`verify_edit` компилирует именно прочитанные bytes без исполнения и без записи
`__pycache__`; diagnostic относится к их SHA-1. Структура проходит в edit/patch
results и rollback diagnostics. Non-Python files по-прежнему получают только
structural check. Полноценный LSP ещё не реализован.

## Итоговый host gate задачи (37)

Перед публикацией `TaskRunner` вызывает `assess_completion` и сохраняет
`Task.completion_report`. Проверяются окончание steps, учёт superseded steps после
replan, свежесть original, разрешённый diff, approved hashes и syntax всех изменённых
сохранившихся Python-файлов. Проверка модели с unresolved critique не закрывает step;
при этом конкретная tool/rollback ошибка имеет приоритет в причине отказа.

Если хотя бы один выполненный step требовал pytest либо host config содержит
`task.final_check=pytest`, полный pytest запускается **ещё раз на итоговом workspace**
через существующий managed runner. Потом повторно проверяются original/workspace
hashes: успешный exit code не разрешает неучтённые изменения тестового процесса.
Default `final_check=syntax` не включает лишние tests для чистого отчёта, но не
отменяет pytest, который требовался самим планом. Tests-not-run указываются явно.

Ошибка, timeout или недоступный executor не дают PASS. Task остаётся **paused**,
workspace сохранён, публикация не выполняется. Report survives reload и отображается
в `atomic task --show`. Cancellation проходит к прежнему rollback/cancel пути.
Completed published changesets при recovery по-прежнему проверяются по published
manifest; существующая семантика старого recovery не переписана.

Gate подтверждает declared checks и approved bytes, не семантическое выполнение
любой формулировки пользователя. Наличие `done` не заменяет независимые task evals.

## Проверки

Новые `test_tool_discovery.py`, `test_completion_diagnostics.py` и усиленный реальный
Docker acceptance case проверяют:

- discovery/schema activation без factory; denial/invalid args/disabled edits/ask
  до factory; несовпадение metadata/effects; role-filtered каталог и неизменность schemas;
- настоящий native discovery → schema activation → approved factory → result с IDs;
- невозможность подменить provenance или включить edits через tool payload;
- revision-bound syntax/lint diagnostics, containment, stale hashes и compile без исполнения;
- empty/unfinished task, неправильную финальную syntax, конфликт original,
  final pytest failure/unavailable, изменение workspace даже после pytest exit=0;
- сохранение completion_report и запрет публикации при final host failure.

Общий Windows suite **363 passed, 13 skipped**, `validation/step10-windows.txt`.
Changed-code Ruff/compileall PASS. Изменение CLI rendering отчёта проверено отдельно
после общего прогона. Новые model calls отсутствуют; scripted backends/HTTP only.

Runtime `e9c50bfc7e62cf187c2203fa1b10c56aed1e4f0a`: скачанный artifact push CI
`39793671877` содержит совпадающий `tested-commit.txt`, `results.json` с container PASS,
**353 CPU + 17 extra + 6 real Docker passed** и calibration PASS. Evidence сохранён
в `validation/ci-step10-20261022/validation-e9c50bfc7e62cf187c2203fa1b10c56aed1e4f0a/`.
Проверка done-task completion_report добавлена в настоящий Docker acceptance case.
Общий run conclusion был success при stale queued jobs-list; утверждение об исполнении
основано на скачанных SHA/logs/results. Отдельная job-list не использована как proof.
План оставшихся 33 требований — `REMAINING_IMPLEMENTATION.md`.
