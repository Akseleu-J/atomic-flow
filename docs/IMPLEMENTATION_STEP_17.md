# Пакет 17 — исправления семи находок независимого критика

23 октября 2026. Основание: `CRITICAL_REVIEW_2026-10-23.md`, воспроизведения
на runtime `58b3dd4`. Пользователь разрешил исправления; три субагента начали
независимые блоки, после остановки из-за лимита основной агент завершил и
проверил сохранённые изменения. Исторические evidence не перезаписывались.

## R01 — execution inputs и output

`execution_inputs.private_credential_path` — общая политика исключения явно
private/credential paths для source admission и ChangeSet. Credentials/secrets
directories/files, private keys, `.env` variants и encrypted/key containers
не попадают в snapshot по умолчанию. Произвольные gitignored build inputs не
считаются секретами автоматически и сохраняются; это отдельная execution policy,
а не слепое применение `.gitignore` к сборке.

Model-facing `run_command` вне managed task создаёт отдельный ChangeSet, проверяет
его и никогда не публикует test writes. Уже приватная task workspace не копируется
второй раз. После проверки snapshot закрывается; copy workspace/journal сохраняются
по существующему retention контракту для inspection. Это добавляет snapshot overhead
для ordinary ask; resource admission/retention improvement G03 остаётся открытым.
Тот же `checked_workspace` применяется к matching tests после edit_node/файловых
правок и удаления/move. Это закрывает исходный checkout и для автоматической
post-edit проверки, а не только для явного run_command.

`output.sanitize_output` копирует JSON-like данные, применяет распознавание token/
key forms и маскирует credential-shaped string fields. Границы: decoded command
stdout/stderr, diagnostics/final test tail, dispatcher result/record и `_Run` artifact
writes/events. Regression canary проверяется до следующего provider-bound сообщения.

Это защита распознаваемых секретов, не гарантия обнаружения произвольного/encoded
секрета в любом файле. Trusted host callbacks и явно unsandboxed local code имеют
доступ к host; binary host job output остаётся отдельным raw-output контрактом,
не автоматически безопасным model context. Docker network/FS restrictions и
input exclusion остаются основными границами выполнения недоверенного кода.

## R02 — host unresolved-effect latch

`agents.effects.EffectState` принадлежит ToolContext/run, не истории prompt.
После uncertainty/rollback_failed/cancel во время write/execute устанавливается
необратимая в этом API блокировка. Dispatcher отклоняет последующие write/execute/
interact **до policy callbacks и lazy factory**, независимо от роли и args.
Read-only host inspection остаётся доступным через dispatcher, но Orchestrator
завершает run до следующего model call и не выдаёт успешный финальный ответ.
Text-JSON и native tool transport проходят одну границу.

Событие `tool_effect_unknown` входит в session schema 1 как additive event и
остаётся unresolved даже при наличии terminal error. Если обычные artifacts
выключены, минимальный safety journal всё равно сохраняется. Повтор того же
request ID блокируется. Existing session resume/fork не разрешает unresolved state.
TaskRunner сохраняет отдельный task-level marker, восстанавливает step checkpoint,
останавливает независимую работу/replan и публикацию. Переключение artifacts,
новое plan approval или revision не снимает блокировку task. Journal failure
не превращается в retryable tool error.

Новый тип `UnresolvedEffects`: CLI concise exit 2, synchronous API HTTP 409,
SDK typed failure. Автоматический reconciliation/clear не добавлен: сначала
нужно проверить внешний исход и принять новое host решение. Новая независимая
задача не объявляется доказательством разрешения предыдущего эффекта; глобальная
идентичность внешних бизнес-операций этим локальным runtime не устанавливается.

## R03 — доверенный checker launcher

`run_python([-m, pytest|ruff|mypy, ...])` сохраняет публичный API, но запускает
isolated Python `-I` bootstrap. Установленный checker импортируется до включения
project imports; root `pytest.py`, `ruff.py`, `mypy.py` не заменяет package.
Pytest получает workspace target, затем доступны необходимые imports test modules.
Явные positional matching-test targets сохраняются: launcher не добавляет root
к выбранным tests, а без targets pytest использует cwd по своему обычному правилу.
Host plugin читает итоговый testscollected; exit 0 без collection превращается
в exit 5. Одинаковый путь используется в local/Docker, run_command и final completion.

Это подтверждает identity launcher и проверенные collection/exit сценарии,
не независимость от злонамеренного conftest/plugin, способного менять собственный
interpreter. Независимый quality evaluator должен запускать grader вне candidate
authority, как описано в Stage 4 plan. В CI добавлен настоящий Docker canary/
shadow-module regression; Windows skip этого теста не считается его успешным запуском.

## R04 — completed и uncertain effects workflows

Reports теперь содержат `completed_effects` (IDs успешных effectful steps),
`uncertain_effects`, `has_effects`. Совместимый conservative флаг
`side_effects_may_have_occurred` равен `has_effects`: завершённый write не исчезает,
если следующий read отменён/упал. Неопределённая операция не помечается completed.
Это report фактов и uncertainty, не rollback и не разрешение action replay.

## R05 — replan после независимой работы

TaskRunner использует один bounded `_replan_failed_step` как после немедленного
отказа, так и после завершения независимых runnable steps. Done work сохраняется,
remaining исключает done/superseded, новые зависимости remapped. Budget/max_replans/
cancel gates сохранены. Replan инвалидирует approval и останавливает выполнение
до fresh review. Integrity failure/unknown effect не отправляется в replanner.

## R06 — grader перестал выдавать semantic PASS по ключевым словам

Версия rubric: `fixture-calibrated-answers-v2`. Regex/citation/identifier checks
остались диагностикой. Автоматический PASS возможен только для конечного набора
полных вручную сверенных с fixture EN/RU answer forms и соответствующих citation IDs.
Новая формулировка/некалиброванный case не получает автоматический success и
требует независимой проверки (`needs_review`, либо failed diagnostic). Текст с
добавленными claims не совпадает с разрешённой полной формой.

`confidence=low`/missing_context сами по себе больше не означают abstention.
Проверены исходные semantic inversion, irrelevant citation, low-confidence
fabrication и disclaimer с последующим выдуманным ответом; положительные EN/RU
forms также калибруются. Нельзя называть этот whitelist общим semantic evaluator:
он намеренно неполон. Прежний `grounded` оставлен для compatibility как identifier
diagnostic; `identifier_grounded`/`grading_scope` явно фиксируют ограничение.
Исторические 7/8 и 6/8 не пересчитаны и не превращены в новые результаты.

## R07 — расходы учитываются до parsing

`evaluation_meter.EvaluationMeter` используется legacy benchmark и paired QA.
Каждый фактический backend chat/native call записывает intent **до отправки**,
usage **до parsing**. Sidecar `OUTPUT.attempts.json` атомарно checkpoint/fsync
сохраняет все наблюдаемые попытки, включая failures; existing ledger нельзя
перезаписать повторным запуском с тем же output path. Это не automatic replay/resume.

Summary различает known lower bounds и complete totals. Unknown cost/tokens
не превращается в ноль; unknown spend прекращает дальнейшие вызовы. Явные
not_sent/refused учитываются как known non-inference zero. Failed final parsing
не вычитает уже полученные usage, cost per success включает расходы failures.
Transport retries отключены/отклоняются; HTTP parameter negotiation ниже backend
boundary отдельно не наблюдается и указана в disclosure. Ledger не списывает
расходы второй раз: meter только наблюдает, runtime billing accounting сохранён.

## Проверки и оставшаяся работа

Все model/backend ответы в этом пакете scripted, платных API calls **0**,
реальные credentials для тестов не использовались. GitHub token нужен только
для согласованной публикации кода/CI. Новые regressions и changed/risk Windows
результаты сохранены в `validation/step17-*.txt`; точный итог CI после публикации
будет добавлен в PROJECT_CONTEXT.

Первый expanded risk run: **337 passed / 12 skipped / 3 failed**; failures —
изменившийся текст unknown-model-charge ошибки, диагностика восстановлена.
Последующий recheck: **134 passed / 1 skipped / 2 failed**; оба отказа — ошибки
нового synthetic native fixture (literal и adapter-normalized content), исправлены.
Отдельный ранний new-regression run **85 passed / 1 skipped** предшествовал
последним integration правкам и не выдаётся за полный final-source suite.
Финальный affected-area recheck: **81 passed / 1 Docker-only skipped**,
`validation/step17-final-windows.txt`; Ruff/compileall PASS. В том числе проверены
все новые regression tests, budget accounting, text/native effect latch и CLI/API.

G01 independent live edit evaluation, G02 reproducible release/wheel acceptance,
G03 масштаб/snapshot admission и capability composition остаются открытыми.
Исправленные конкретные regressions не доказывают production readiness или
превосходство над внешними агентами.

Первый push CI `39968031957` на `a0a4de5` показал **788 CPU passed / 11 skipped /
1 failed**, **17 extra passed**, **26 execution acceptance passed**, compile PASS;
calibration не запускался из-за общего отказа. Скачанные исходные 11 artifact files
и hashes — `validation/ci-step17-before-fix-20261023/`. Отказ: pure read-before-edit
preflight у patch_file выбрасывал исключение, dispatcher консервативно считал его
неопределённым effect. Теперь Patch/Delete/Move возвращают обычный error только
из отдельного preflight до `_mutate`; post-mutation exceptions не ослаблены.
Прямой CreateFileTool contract для private/OS aliases сохранён. Проверены actual
read → repair → apply и прежняя memory authority regression: **36 passed**,
`validation/step17-preflight-final-windows.txt`. Промежуточные четыре assertion
failures при изменении CreateFileTool return/exception устранены сохранением API;
лог `step17-preflight-recheck-windows.txt` оставлен отдельно.
Дополнительная проверка trusted checker/matching-test targets и task final gates:
**39 passed / 1 Docker-only skipped**, `validation/step17-checker-final-windows.txt`.
Повторный CI `39969434678` на `d86a439`: **790 CPU passed / 11 skipped / 1 failed**,
17 extra и 26 execution acceptance PASS, calibration skipped. Причина — прямой
PatchFileTool API должен сохранять SourceConflict для stale source. Итоговое решение:
`SourcePreconditionFailed(SourceConflict)` поднимается только внутри чистого
preflight. Dispatcher фиксирует `effect_not_started`, не устанавливая uncertainty;
обычные post-action exceptions по-прежнему блокируют effects. SourceConflict/OSError
контракт прямых callers сохранён. Raw CI evidence в
`validation/ci-step17-intermediate-20261023/`.
Последние affected checks: **73 passed / 4 skipped** (source API/advisory/effect
contracts и execution privacy), **54 passed / 1 skipped** (post-edit checks/task
publication), отдельные logs `step17-contracts-final-windows.txt` и
`step17-verification-final-windows.txt`. Повторный exact-source CI ожидается.
