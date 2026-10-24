# Протокол реального API и Kaggle benchmark

Используйте этот порядок, когда проверка должна вызвать живую модель или выполнить задачу на Kaggle. Само слово «тест» не означает разрешение расходовать квоту: перед живым запуском владелец подтверждает аккаунт, модель, лимиты и передачу данных.

## 1. Сначала выберите тип запуска

| Цель | Путь | Что он доказывает |
|---|---|---|
| Проверить код без модели | целевые pytest/Ruff и CI на том же commit | Регрессию реализации в указанном покрытии |
| Проверить обычный ответ модели | `tools/evaluate_qa.py` через Kaggle Model Proxy | Только ответы выбранной модели на указанных QA fixtures |
| Проверить генерацию правки агентом | `tools/evaluation_live_pilot.py` с независимым grading | Поведение указанного agent arm на указанной задаче |
| Выполнить опубликованную на Kaggle benchmark-задачу | `kaggle benchmarks tasks push/run/status/download` | Удалённое выполнение конкретной версии Kaggle Task |
| Подготовить/проверить SWE-bench oracle | `tools/evaluation_stage4.py` | Подготовку и offline grading; эти команды сами модель не запускают |

Model Proxy вызов из локального процесса — это live API inference, но он сам по себе не запускает Kaggle Notebook/Task. Kaggle Task run, наоборот, планирует удалённое выполнение и должен проверяться через статус и скачанные outputs.

## 2. До передачи запроса

1. Перейдите в корень `atomic-flow/`, проверьте `git status --short`, текущую ветку и полный `git rev-parse HEAD`. Для измеряемого результата предпочтителен чистый commit. Если рабочее дерево изменено, сохраните полный diff и его SHA-256; не называйте только ветку.
2. Зафиксируйте цель проверки: какие новые изменения должны сработать, какие task/case IDs это покрывают, какие независимые oracle/tests подтверждают результат. Если тест не проходит через изменённый кодовый путь, он не проверяет это изменение.
3. Разделите локальные unit/mock tests, CI и live API attempts. Укажите отдельно, где модель реально вызывалась, а где `model_calls=0`.
4. Выберите одного явно разрешённого владельца Kaggle аккаунта. До запуска этот владелец подтверждает использование квоты и передачу конкретных fixtures/промптов Kaggle. Квоты и разрешения аккаунтов учитываются отдельно; не меняйте аккаунт, чтобы обойти отказ, ограничения или лимит.
5. Снимите квоту до запуска и выберите фиксированные provider/model, число физических вызовов, input/output caps, timeout/deadline и предел reported cost. Для Model Proxy проверяйте `kaggle benchmarks quota`. Это квота inference; `kaggle quota` показывает GPU/TPU часы и не заменяет её. Это отдельные ресурсы.

Если отправка могла состояться, но клиент получил timeout/disconnect, пометьте результат как `unknown`, сначала проверьте статус/attempt и квоту. Не отправляйте тот же запрос повторно, пока неизвестный исход не разрешён: ответ мог быть оплачен, хотя локального результата нет.

## 3. Kaggle API и Model Proxy — разные учётные данные

`KAGGLE_API_TOKEN` (или другой настроенный Kaggle CLI login) удостоверяет пользователя на платформе. `MODEL_PROXY_URL` и `MODEL_PROXY_API_KEY` нужны для локального inference через Model Proxy; `kaggle benchmarks auth` получает proxy credentials для разработки. Не путайте их с прямыми ключами Google/OpenAI/Anthropic.

Берите Kaggle API token только из одобренного хранилища владельца и подавайте дочернему процессу на время запуска. В этой рабочей копии есть локальный helper `.local-tools/with-kaggle.ps1`; он не включён в Git, и его доступность/поддерживаемый набор аккаунтов нужно проверить отдельно. В другой среде используйте её утверждённый secret manager или интерактивный Kaggle OAuth. Не вставляйте секрет в команду, notebook, git remote, fixture, PR, лог или отчёт.

Если получаете локальный Model Proxy credential через Kaggle CLI, задайте `--env-file` на отдельный приватный путь вне checkout, не используйте `.env` проекта. Передайте значения только дочернему процессу, после запуска удалите временный файл; в evidence оставьте лишь имя владельца/label и факт проверки, без token, URL query или headers. Credentials могут истечь; не сохраняйте их в artifacts.

Официальные справки: [Kaggle CLI: Model Proxy и Benchmarks](https://github.com/Kaggle/kaggle-cli/blob/main/docs/benchmarks.md), [Kaggle CLI: аутентификация](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/auth.md), [Kaggle: создание и запуск Benchmarks](https://www.kaggle.com/docs/benchmarks).

## 4. Локальная проверка через живую модель

Для QA fixtures сначала запустите local/mock проверки. Затем владелец задаёт разрешённые Model Proxy credentials в окружении только процесса и явно запускает `tools/evaluate_qa.py`. Например, узкая проверка одного случая:

```powershell
.venv/Scripts/python.exe tools/evaluate_qa.py `
  --yes `
  --model google/gemini-3.1-flash-lite-preview `
  --cases invoice-subtotal `
  --max-calls 4 `
  --max-reported-cost 0.05 `
  --output validation/live/<RUN_ID>/qa.json
```

`--yes` подтверждает, что эта команда потребит Kaggle inference quota. Папка `<RUN_ID>` должна быть новой: не перезаписывайте старый отчёт. Для текущего fixture runner модель и разрешённые провайдеры проверяются самим CLI; не подменяйте model slug незаметно.

QA smoke подтверждает только транспорт и заданный QA-путь. Он не доказывает успешность правки исходного кода. Для agent edit нужен отдельный live edit run с независимой oracle-проверкой. Текущий `evaluation_live_pilot.py` ограничен подготовленным публичным SymPy кейсом и должен запускаться с явными `--arm`, `--mirror`, `--output`, одинаковыми caps для baseline и atomic и отдельной новой папкой на каждый arm. Этот раскрытый кейс — development only, не blinded confirmation.

`evaluation_stage4.py --validate-only`, `--prepare`, `--candidate-patch` и `--paired-report` работают offline и не вызывают модель. Подготовленный каталог из `--prepare` не является запущенным benchmark. Для Stage 4 сначала нужны откалиброванные base FAIL / gold PASS / plausible-wrong FAIL, затем генератор, отдельная от него oracle и полный отчёт каждой arm. Не объявляйте 30 задач/60 arms завершёнными только потому, что manifest создан.

Установите `max_retries=0`, сохраняйте каждый физический attempt до parsing ответа и оставляйте usage/cost `unknown`, если провайдер не сообщил их достоверно. Configured/reported cost limit — стоп-сигнал приложения, не гарантия конечного счёта провайдера. Не используйте платные OpenAI/Anthropic API для проекта без отдельного явного решения владельца.

## 5. Задача на инфраструктуре Kaggle Benchmarks

Сначала проверьте команду установленной версии через `kaggle benchmarks tasks --help`. Создайте новый slug для нового набора условий либо запишите версию, которую обновляет `push`: повторный push того же slug меняет task version. Отправляйте только проверенный source и fixtures, которые разрешено передавать Kaggle; не включайте секреты или oracle/gold tests в видимую модели задачу.

```powershell
kaggle benchmarks quota
kaggle benchmarks tasks push <task-slug> -f benchmarks/kaggle_tasks/<task>.py --wait
kaggle benchmarks tasks status <task-slug>
kaggle benchmarks tasks run <task-slug> -m <model-slug> --wait
kaggle benchmarks tasks status <task-slug> -m <model-slug>
kaggle benchmarks tasks download <task-slug> -m <model-slug> -o validation/kaggle-runs/<RUN_ID>
```

Если модельный раннер устанавливает atomic-flow из GitHub, закрепите полный commit SHA, а не плавающую ветку. Сохраните task slug/version, API run/model IDs, точный source SHA, расписание/конфиг, статус, outputs и их SHA-256. `QUEUED`/`RUNNING` означает, что результат ещё не получен; при `ERRORED` скачайте доступные логи и классифицируйте ошибку как кодовую, инфраструктурную, авторизационную или quota failure. После неопределённого ответа на `push`/`run` сначала проверьте `status`/список задач; не создавайте дубль вслепую. Публикация task (`publish`) — отдельное действие, не шаг обычной проверки.

## 6. Когда запуск можно считать подтверждённым

Сохраните в новой папке запуска `run.json` и оригинальные отчёты/логи без секретов. Минимальные поля: run ID/time, repo commit и dirty/diff hash, task IDs и fixture/manifest hashes, arm, provider/model, authorized account label, лимиты, версия Python/dependencies, точная команда/конфиг, каждый attempt с outcome/usage/cost, quota до/после, oracle/test results, Kaggle task version/run ID (если был) и SHA-256 скачанных файлов.

Обозначайте отдельно: `LOCAL_PASS`, `CI_PASS`, `LIVE_API_RESPONSE`, `REMOTE_TASK_COMPLETED`, `ORACLE_PASS`. Только `REMOTE_TASK_COMPLETED` с загруженными output-файлами подтверждает завершение удалённой задачи; только oracle/tests подтверждают исправность изменения. Модельный текст «успешно» и зелёный статус очереди этого не подтверждают. Сохраняйте неудачные и неизвестные attempts, не суммируйте перекрывающиеся тестовые наборы как уникальные и сравнивайте baseline/atomic при одинаковых задаче, модели, source, prompts и caps.
