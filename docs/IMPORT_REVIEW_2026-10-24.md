# Проверка и интеграция второй рабочей копии — 24 октября 2026

Источник: `C:/Users/omirb/.zcode/workspace/7a9e7cc1-327f-4133-a7af-0ccfe5ee24e8/atomic-flow`,
ветка `experimental`, commit **c8820ff445b318c3fbf1c3cf76451ebe30a27774**.
Приложенный пользователем отчёт проверен по Git и исходникам. В источнике семь
коммитов после общей базы `8a18fbe837b5c82597f782b6455e96b708607f36`.
Внешняя копия не изменялась. Исходная текущая работа сохранена в
`.local-tools/integration-backup-20261024/` (235 source/test/tool/doc файлов и binary diff).
История источника доступна локально через `refs/remotes/zcode/experimental`;
fetch выполнен через локальный bundle, без обращения к GitHub и credential vault.

В эту рабочую копию перенесены выбранные source/tests/tools/CI и документы
пакетов 18–21. Общий код не заменялся слепым reset; затем внесены перечисленные
ниже corrections. Изменения остаются локальными. Старые audit и CI artifacts
не перезаписывались. Заявленные во внешнем отчёте **850 passed / 33 skipped**
не считаются проверкой итоговой версии после этих corrections.

## Что принято

- Блокировка cooperative writers на уровне корня, отдельная защита nested
  publication, durable source/backups/journal writes, subprocess serialization regression.
- Unique index temp paths, generation CAS, process lease, bounded index traversal
  и строгая HTTP admission. Single-process API scope и readiness стали явными.
- Numeric provider usage до разбора content, сохранение полученной стоимости
  при malformed response, текстовый routing с физическими attempt IDs и квотами.
- Host-only reconciliation и CLI; staged capability factory TaskRunner.
- Execution manifest/identity, owned Windows Job Object, jobs collection/tombstones.
- Installed-wheel CI и smoke; исходники и tests source-копии использованы как база.

Подробный review трёх независимых зон: `IMPORT_RUNTIME_REVIEW.md`,
`IMPORT_EXECUTION_REVIEW.md`, `IMPORT_ROUTING_REVIEW.md`.

## Что требовало исправления до принятия

| Область | Ошибка в source-кандидате | Итоговое поведение |
|---|---|---|
| Ledger | Settlement и обычный поздний ответ писались в текущий день. | Отыскивается точная task/call identity; запись идёт в исходный день. Несколько дней/чужая identity отклоняются. |
| Reconciliation | Повтор с другим amount мог вернуть новую сумму, сохранив прежнюю; task становился completed до ledger/session. | Amount/evidence неизменяемы; durable pending intent остаётся unknown до записи всех частей; повтор чинит crash gaps без повторного списания. |
| Effects | Effect reconcile принимал несуществующие/неподходящие call IDs. | Требуется реальный unresolved effect и совпадающий project; charge/effect разрешаются отдельно. |
| Routing | free_only возвращал фактически платный ответ; no-send budget failure оставлял quota pending. | Ненулевой/неизвестный cost останавливает работу с сохранением usage; явные not_sent освобождают слот и локальные счётчики. |
| Attempts | Optional parameter negotiation мог отправить несколько запросов под одной routed reservation; deadline устаревал после ожидания admission. | У routed OpenAI attempt один send; абсолютный deadline повторно проверяется перед send, включая Anthropic. Standalone negotiation сохраняет общий deadline. |
| Accounting | Preflight и завершённый ответ с последующей ошибкой могли выглядеть как неизвестная отправка. | Call state различает not_sent/unknown/completed; полученная стоимость сохраняется в task и outer usage без двойного ledger charge. SDK сообщает completed. |
| Windows cleanup | Exit родителя/taskkill 128 считались доказательством очистки дерева. | Ошибка helper сохраняет uncertainty. Windows MCP/LSP запускаются через owned job до workload; active_processes==0 подтверждает завершение. Ошибка close остаётся защёлкнутой. |
| Wheel CI | Неверный SHA checkout-action и glob с `[api]` блокировали pipeline. | Восстановлен корректный SHA; wheel выбирается отдельно от extra. Smoke запускает installed console command, CI требует API extra. |

Отмена также передаётся внутрь index refresh из Orchestrator и ToolContext,
а после получения ответа до tool effects проверяется фактическая стоимость.
Это не закрывает bounded snapshot всей changeset или поддержку всех провайдерских
форматов pricing.

## Проверки итоговых изменений

Все model transports scripted; model/paid calls=0. Docker не устанавливался и
не запускался. Полный suite повторно не запускался. Числа ниже перекрываются;
их нельзя суммировать как уникальные tests.

| Проверка | Результат / artifact |
|---|---|
| Routing + provider contracts + cross-layer accounting | **67 passed**, `validation/import-20261024/routing-accounting-focused.txt` |
| Ledger day identity + reconciliation + task budgets | **43 passed**, `runtime-ledger-cross-day.txt` |
| Reconciliation/composition/source serialization | **24 passed**, отдельный final-task-save crash **1 passed**; `runtime-final-scoped.txt`, `runtime-final-task-save-gap.txt` |
| Execution/profiles/MCP | **34 passed / 3 skipped**, `execution-*` logs |
| LSP/profiles | **38 passed / 1 skipped / 4 initialization timeouts** под высокой нагрузкой; повтор ровно четырёх cases — **4 passed** за 17.32 s, без изменения source/deadlines |
| Parent integration run | **98 passed / 1 failed**: прежний error-message contract; формулировка сохранена. Admission/index recheck: **20 passed / 1 SDK timeout** под нагрузкой. Этот один SDK case затем **1 passed** за 5.57 s с исходным deadline. |
| Ruff changed source/tests/tools | **PASS**, `combined-ruff.txt` |
| Wheel build | **PASS**, `wheel-build.txt`; wheel SHA-256 `ada4036eadccf28b3da571687a10a46561dff42862e20318202f745b89cd6bc6` |
| Installed wheel smoke | **PASS**: site-packages вне checkout, packaged fixtures, index roundtrip, SDK import, API health/ready и установленный `atomic --help`; dependencies переиспользованы явно |
| Git diff whitespace / static project index | **PASS**; индекс обновлён, missing absolute internal imports = 0 |

Raw failures и setup errors сохранены. Sandbox дважды запрещал pytest/pip
temporary files до тела tests; offline проверки выполнены вне этих filesystem
ограничений. Исторический полный suite source-копии и наши targeted results
имеют разный scope.

Wheel содержит exact bytes текущих Python sources (проверено архивом, mismatches=0).
Результат installed smoke и его dependency scope сохраняются отдельно в
`wheel-installed-smoke.txt` / `wheel-install-scope.json`. Локальная проверка
установленного пакета с повторно используемыми dependencies не доказывает
чистую dependency-resolution acceptance; этот gate оставлен CI.

## Что остаётся открытым

- Linux/Docker и wheel CI на GitHub для итоговых bytes, Windows/macOS release matrix,
  locked/tested dependency constraints и чистая установка всего dependency graph.
- Настоящий pinned внешний LSP/MCP в полном task journey; synthetic transport
  и Windows lifecycle checks не заменяют acceptance независимых серверов.
- Live provider availability/native tool quality; авторитетные distributed quotas;
  дополнительная нормализация Anthropic usage/cache pricing.
- Нативные sandbox backends: Job Objects дают process lifecycle, а не filesystem/network
  isolation. Docker-free core возможен, untrusted local sandbox ещё не заявлен.
- Общий snapshot resource envelope, нагрузочные p95/RSS/disk measurements,
  transactional journal/ledger index. Journal prefix hashing и ledger identity scans
  пока имеют стоимость по сохранённой истории.
- Stage 4 live edit quality, confirmation study и release candidate acceptance.

Эта интеграция принимает полезную работу и устраняет найденные regressions;
production readiness всего framework не заявляется.
