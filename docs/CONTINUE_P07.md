# Передача работы: atomic-flow / P07

Последний принятый runtime: `500a0fdea0ab9258269cc0ace45338607c8ce917`.
Requests3362 full synthetic TaskRunner: CI 40814165859 SUCCESS. Проверены
810 hashes, пять raw checks, один replan, публикация только probe, свежий
final PASS и graph sync. Main CI 40814163106 SUCCESS: по 3198 PASS / 14 SKIP
на Python 3.11/3.12, 392 source hashes; Windows/wheel/Docker. Все шесть ZIP
digests сверены. Evidence: requests3362-task-v1 и requests-integration-core-ci-v1.
Follow-up усиливает acceptance validator и добавляет 12 offline attack tests;
сверить HEAD/remote перед продолжением.

Следующий пакет: полный historical ordinary Requests3362 (пока проверены
четыре synthetic tests), затем function36 fixture/controller/receipt с новым
resource manifest «core + fixed CA». CA overlay пока pure, без execution wiring.
Generic dispatch разрешает только 3362-cn через root Linux owned controller;
другие cohorts отклоняются. Нужны все 30 ordinary profiles, равный driver,
paired runs, holdout/ablations и final review. P07 OPEN; provider calls = 0.

Обновление29октября: Requests3362 smokev3 принят на72bb7ef,
CI40813084358,779rawhashes/FAIL/PASS/requiredSKIP/cleanup проверены.
Отчёт: `P07_REQUESTS_BOUNDARY_ACCEPTANCE_2026-10-29.md`.
CA-overlay pure helper исправлен через отдельные core/resource manifests,
236PASS/3symlinkSKIP. Подготовлены Requests3362 ordinary dispatch и отдельный
full TaskRunner synthetic workflow; до приёмки его CI не считать интеграцию
завершённой. Нижние пункты о незакоммиченном skip fix и17FAIL — старый checkpoint,
заменённый этим обновлением. P07OPEN; новыхprovidercalls0.

Состояние проверено **29 октября 2026**. Цель — полностью закрыть P07 доказательствами; не объявлять production readiness по локальным тестам или синтетическим прогонам.

## Где работать

Репозиторий: `C:\Users\omirb\OneDrive\Документы\ChatGPT\atomic-flow 2\atomic-flow`.
Ветка `experimental`, GitHub `Akseleu-J/atomic-flow`, draft PR №2. Локальный HEAD и удалённая ветка совпадают:
`f173dd6c9bda35a99c2c024b110c3f26f2052c87`.
Начать с `docs/PROJECT_CONTEXT.md`; подробности — `docs/P07_STATUS_2026-10-28.md` и `docs/P07_PRIVATE_REQUESTS_EXECUTION_DESIGN_2026-10-28.md`.

## Подтверждено и что ещё осталось

P00–P06 приняты в пределах записанных host contracts. **30/30 отдельных сред калиброваны**, но это не 30 решённых моделью задач. Приняты отдельные synthetic TaskRunner/queue/Atomic–flat Docker journeys и прямой live API diagnostic Sonnet5. Историческое качество агента ещё не измерено.

Осталось: полный исторический Atomic/flat driver с равными лимитами; Requests6 и ordinary-профили всех30; 30 парных сравнений с независимым oracle; свежий holdout и ablations; итоговый анализ и независимый review. **P07 OPEN.**

## Непосредственный следующий шаг

Довести Requests3362 smoke до полной приёмки:

1. [Первая попытка 40750378642](https://github.com/Akseleu-J/atomic-flow/actions/runs/40750378642): три HTTP/TLS health-теста прошли, затем pytest2.8 пытался писать кэш в read-only `/testbed`. Исправление `-p no:cacheprovider` уже закоммичено; 124 focused tests прошли.
2. [Вторая попытка 40751447691](https://github.com/Akseleu-J/atomic-flow/actions/runs/40751447691): завершённые FAIL → edited PASS и остановка серверов подтверждены; весь smoke **FAILED**, потому что pytest2.8 проигнорировал современный `@pytest.mark.skip`.
3. **Локально уже исправлен** `tools/p07_requests3362_check_smoke.py`: используется `pytest.skip(...)`. Этот фикс ещё НЕ закоммичен/запушен, третья попытка НЕ запущена. Проверить diff, опубликовать и запустить `.github/workflows/p07-requests3362-check-smoke.yml`; затем скачать архив, сверить SHA/raw outputs, source/cleanup и отказ принимать required-SKIP. Generic Requests dispatch пока выключен.

Артефакты двух попыток: `validation/p07-budget20/requests3362-check-v1/` и `requests3362-check-v2/`. Вторая папка пока untracked; не потерять её.

## Незавершённая параллельная работа

Новые untracked файлы `src/atomic_flow/check_requests_execution_overlay.py` и `tests/test_check_requests_execution_overlay.py` готовят отдельную execution-копию для Requests1963/2148/2317 с единственным CA append. **Пакет не готов:** последний завершённый тест — 17 FAIL / 43 PASS / 2 SKIP; после него могли быть дополнительные правки.

Причина: core source manifest исключает `.pem`, а реализация ошибочно ожидает там `requests/cacert.pem`. Не расширять глобально индексирование PEM и не отдавать ключи модели. Требуется явно разделить core source revision и отдельный manifest ресурсов «core + единственный фиксированный CA», сохранив неизменность кандидата и строгую проверку execution-копии. Существующий `ExecutionOverlay`/его описание политики тоже надо согласовать. Docker/controller/receipt integration этого пути ещё нет.

## Рабочие ограничения

Общий разрешённый paid-бюджет **$20**, подтверждённый расход **$0.035931375**; новых paid main pairs нет. Модель `anthropic/claude-sonnet-5@default`. Ключи/пароли не переносить в документ или логи; существующий локальный оператор — `.local-tools/p07-operator.mjs`, credentials process-only. UNKNOWN paid attempts не повторять вслепую.

Тесты запускать последовательно: Windows испытывала MemoryError. Использовать `.venv/Scripts/python.exe -m pytest <нужные tests> -p no:cacheprovider -q`, затем `.venv/Scripts/ruff.exe check <изменённые файлы>`. Перед коммитом обновить `tools/update_project_index.py` и дождаться завершения. Не делать `git add .`: в checkout много исторических untracked evidence и `.atomic_flow/tree.lock`. Ничего не удалять, не merge/release. Core/client Docker-free; исполнение недоверенного кода требует изолированного worker.

## Хранилище и доступ

Зашифрованный vault: `C:\Users\omirb\OneDrive\Документы\ChatGPT\computer_control\shared-access\.env.enc`.
Загрузчик и инструкция находятся рядом: `shared-secrets.ps1`, `README.md`. Нужен PowerShell **7.2+**. Использовать именно этот vault, не `C:\.env.enc`.

**Пароль доступа владелец передаёт преемнику отдельно через защищённый канал. В этом Git-документе его нет.** В прошлой сессии владелец разрешил разблокировку для работы с переменными; это не разрешение публиковать пароль или передавать все ключи любому получателю документа. При переносе на другой компьютер владелец отдельно предоставляет нужный доступ.

Общий загрузчик импортирует переменные только в текущий процесс и дочерние процессы, без вывода значений:

```powershell
. 'C:\Users\omirb\OneDrive\Документы\ChatGPT\computer_control\shared-access\shared-secrets.ps1'
Import-SharedSecrets   # пароль вводится в скрытом интерактивном prompt
```

Предпочитать узкие helpers вместо импорта всех секретов:

- `.local-tools/with-github.ps1`: достаёт только `GITHUB_TOKEN` и передаёт его дочернему `git`/`gh`.
- `.local-tools/with-kaggle.ps1`: параметры `-Password <SecureString> -TokenName <имя> -ScriptFile <проверенный Python-файл>`; поддерживаемые имена — `Kaggle2_TOKEN`, `Kaggle3_TOKEN`, `Kaggle4_TOKEN`. Поддержка имени не означает разрешение использовать аккаунт: сначала сверить реальную identity и полномочия владельца.

Пример безопасного push из корня репозитория:

```powershell
$p07VaultPassword = Read-Host 'Пароль vault' -AsSecureString
try {
    & .local-tools/with-github.ps1 -Password $p07VaultPassword `
        -GitArguments @('push', 'origin', 'experimental')
} finally { $p07VaultPassword.Dispose() }
```

Не выводить environment целиком, расшифрованный vault, headers, `gh auth token` или proxy credentials. Не помещать пароль в командную строку, историю, исходники, notebook или artifacts. Kaggle login token и Model Proxy credentials — разные сущности; proxy grant может истечь, его обновление не означает разрешение повторить inference.

## Инструменты, которыми велась эта сессия

- `.local-tools/p07-operator.mjs`: локальный Node-оператор. Использовались `github(path, method, body)`, `git(args)` и `artifact(metadata, freshZipPath)`. GitHub paths относительны `repos/Akseleu-J/atomic-flow/`. `artifact` сверяет SHA256 с digest GitHub. Kaggle-контур этого оператора закреплён за owner2 `akseley`; это не переключатель аккаунтов.
- `.local-tools/p07-zip.mjs`: чтение ZIP с проверками путей/дубликатов/CRC. Извлекать только в свежую папку с проверкой confinement.
- Node runtime: `C:\Users\omirb\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`. Python/Ruff — проектные `.venv/Scripts/`.
- `.local-tools/`, сам vault, содержимое памяти процесса и Node REPL **не переносятся обычным Git clone**. Не коммитить их автоматически. Другой исполнитель должен проверить наличие helpers и получить минимальный доступ от владельца; старые JS-переменные сессии не считать существующими.

## Протоколы, которые соблюдать

Все ссылки ниже относительны этому документу:

| Область | Документ |
|---|---|
| GitHub, vault-helper, staging, draft PR | [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) |
| Живая модель, Kaggle, квота, неизвестные исходы | [REAL_API_BENCHMARK_PROTOCOL.md](REAL_API_BENCHMARK_PROTOCOL.md) |
| Общий справочник и исторические границы приёмки | [ASSISTANT_HANDOFF.md](ASSISTANT_HANDOFF.md) |
| Равное сравнение, queue, provenance, независимый oracle | [P07_MATCHED_QUEUE_EXECUTION_DESIGN_2026-10-28.md](P07_MATCHED_QUEUE_EXECUTION_DESIGN_2026-10-28.md) |
| Резервирование, durable ledger и remote checkpoint | [P07_PAID_QUEUE_PREPARATION_2026-10-28.md](P07_PAID_QUEUE_PREPARATION_2026-10-28.md) |

Практические правила:

1. Пользователь уже разрешил продолжение P07, осторожные push и CI, параллельных субагентов и суммарный paid-потолок $20. Сохранять эти разрешения; новая модель, расширение бюджета или чужой аккаунт из этого не следуют. Цифры в бюджетном proposal не являются замороженным исполняемым профилем.
2. Перед paid-send зафиксировать cohort/owner/model/profile/caps и учесть старый расход. Все роли и неудачные попытки входят в бюджет. Не менять аккаунты для обхода квоты; participant3 `umirbayulgaisha` — отдельный авторизованный контур. Историческую account quota не считать текущим балансом кампании.
3. Каждый физический вызов — durable reserve → remote checkpoint ACK → единственный send → actual usage settlement. UNKNOWN блокирует новые paid-вызовы до сверки; таймаут не доказывает отсутствие списания. Сохранённый ответ можно повторно опубликовать без inference. Data/checkpoint branch `codex/p07-model-queue-paid-20261028-01` не откатывать и не удалять.
4. После push сверить полный remote SHA. Workflow dispatch делается на `ref: experimental`, затем сверяется фактический `head_sha`. У PR-запуска checkout может быть merge SHA: проверять `tested-commit` внутри artifacts. SUCCESS без скачанных и сверенных outputs не закрывает приёмку. Новые попытки хранить отдельно, старые не перезаписывать.
5. Не смешивать unit/mock, CI, remote execution, реальный model call, обычный check и независимый hidden grade. Очередь/HTTP200/ответ модели не доказывают исправление задачи. Не давать модели gold/test_patch; full public source scope не выводить из solution. Holdout выбирать metadata-only с заранее фиксированными исключениями/seed; раскрытый кейс больше не blind.
