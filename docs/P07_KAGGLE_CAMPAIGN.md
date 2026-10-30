# P07: одна кампания, 30 отдельных workspace, Claude Sonnet 5

P07 **OPEN**. Этот пакет готовит запуск, сам не отправляет платные запросы.
Готовый актуальный файл: `validation/p07-sonnet5-prepared/pilot3-final/campaign_task.py`.
Каталоги `pilot3` и `ready-pilot3` — предыдущие offline drafts, не запускать их.
Финальная локальная проверка: 41 PASS (campaign, source pilot, calibration,
provider observer), Ruff PASS. Новых model calls — 0. Новый campaign runtime
пока не запускался на Kaggle и не имеет собственного CI acceptance.
Первый размер — **3 задачи**, затем 30 после ремонта сред и проверки пилота.
Один приватный Kaggle Task получает 30 корневых каталогов. В каждом отдельные
`baseline/workspace`, `atomic/workspace`, state, исходники, попытки и отчёты.
Модельные arm выполняются последовательно: это обеспечивает единый durable
budget ledger без гонок. Параллельность 30 модельных arm сейчас не заявляется.

Модель подтверждена read-only каталогом Kaggle 26 октября 2026:
`Claude Sonnet 5`, model version id `152`, scheduler slug
`claude-sonnet-5-default`, proxy slug `anthropic/claude-sonnet-5@default`.
Другая модель автоматически не подставляется. `temperature` не передаётся.
Стоимость $2/$10 за миллион входных/выходных токенов взята из
[официальной карточки](https://platform.claude.com/docs/en/models/sonnet-5/overview).
Это резервная оценка, не гарантия счёта Kaggle. Для продолжения требуется именно
provider cost; отсутствующая/неполная стоимость блокирует всю кампанию.

## 1. Калибровка сред без модели

Калибровка — base FAIL / gold PASS / wrong FAIL. Модель для неё не нужна.
Она запускает чужие тесты только в Docker Linux worker: без сети, host mounts,
capabilities и provider credentials. Отдельные каталоги Kaggle не являются
security isolation. На Kaggle выполняется только чтение исходников и генерация;
target setup/imports/tests там не запускаются.

Сейчас все 30 прежних архивов проверены, принято **6/30**, остальные **24 OPEN**:
`validation/p07-20261026/calibration-round7/summary.json`.
Нельзя оплатой Sonnet исправить отсутствие pytest-plugin/fixture или неверный
отрицательный контроль. Непринятые задачи не допускаются к модели.

Команда полного независимого повторения на подходящем Linux host:

```bash
python -m pip install . swebench==3.0.15
python tools/p07_oracle_batch.py --count 30 --workers 3 --output /work/p07-calibration-new
```

Каждая задача получает свежие base/gold/wrong containers, собственный каталог,
driver log и JSON. Итог: `summary.json`, 30 `<instance_id>.json`, хэши, один ZIP.
Падения/timeout не удаляются. Код не делает автоматических повторов.

## 2. Подготовка малого Kaggle-пилота

Из корня `atomic-flow/`, в установленном окружении проекта:

```powershell
.venv/Scripts/python.exe tools/prepare_p07_kaggle.py --count 3 --budget 4.8 --arm-budget 0.8 --slug atomic-p07-sonnet5-pilot3-20261026-final --output validation/p07-sonnet5-prepared/pilot3-final
```

Пилот: pytest-6116, pylint-6506, SymPy-20212. Все три имеют принятую калибровку.
Каждой arm: 30 физических вызовов, 600000 aggregate input estimate,
4096 output/call, 900 секунд и $0.80 reported-cost stop.
Шесть arm дают объявленный потолок $4.80 в пределах ранее разрешённых $6.
Ранее учтено $0.035611375; подготовка этого пакета стоит $0 новых model calls.
При неизвестном исходе даже одного вызова остальные задачи прекращают admission.
Перед каждым вызовом сохраняется intent, а общая reservation проверяется до send.
Заданный порог — stop policy приложения, не внешний billing cap провайдера.

Готовый `campaign_task.py` самодостаточен: содержит проверяемый runtime payload,
полный SHA фреймворка, 30 task records и калибровочные receipts. Gold patch и
hidden test patch не передаются модели. Writable path предобъявлен отдельно;
это source-scoped development test, а не оценка полного поиска места изменения.
Обе arm читают одинаковый набор production Python исходников той же версии.
Исходники берутся через `git show`; тесты, symlinks, target setup и imports исключены.
Framework pip install выполняется отдельно от target; dependencies записаны framework SHA,
но pip resolution пока не dependency lock — release gate остаётся открытым.
Baseline тоже строит структурный индекс для штатных проверок current hash;
планирование и graph retrieval в baseline не используются. Это обязательная
host-проверка безопасности, а не дополнительное модельное знание.

## 3. Загрузить и запустить позднее

`KAGGLE_API_TOKEN` должен быть уже безопасно установлен только в окружении
разрешённого owner 2. Не вставлять token в команду, notebook или Git.

```powershell
kaggle benchmarks tasks push atomic-p07-sonnet5-pilot3-20261026-final -f validation/p07-sonnet5-prepared/pilot3-final/campaign_task.py
.venv/Scripts/python.exe tools/launch_p07_kaggle.py status --bundle validation/p07-sonnet5-prepared/pilot3-final --receipt validation/p07-sonnet5-prepared/status-01.json
.venv/Scripts/python.exe tools/launch_p07_kaggle.py schedule --bundle validation/p07-sonnet5-prepared/pilot3-final --receipt validation/p07-sonnet5-prepared/schedule-01.json --yes
```

Push создаёт zero-call warmup: любой actor кроме точного Sonnet 5 отказывается
до prompt. Дождаться окончания validation/warmup. Scheduler делает один POST,
фиксирует run ID и квоту. При timeout исход неизвестен: сначала `status` с новой
receipt, не повторять `schedule`. `Validating` с явным runSkippedReason — отказ
платформы создать run, не выполненная генерация. Не менять slug/версию без записи.

После terminal state скачать все результаты одной командой, подставив run ID:

```powershell
.venv/Scripts/python.exe tools/launch_p07_kaggle.py download --bundle validation/p07-sonnet5-prepared/pilot3-final --run-id RUN_ID --receipt validation/p07-sonnet5-prepared/download-01.json
```

Скачивается оригинальный ZIP Kaggle с SHA256 receipt. В нём единый
`p07-<id>-results.zip`, `summary.json`, per-task status, per-arm reports,
raw provider envelopes, prompts, usage и candidate patches. Распаковывать в новый
каталог, не запускать файлы из результатов. Не перезаписывать предыдущие attempts.

## 4. Независимая оценка и разбор

На isolated Linux worker после получения кампании:

```bash
python tools/p07_oracle_batch.py --count 3 --workers 1 --campaign /work/downloaded/p07-ID --output /work/p07-candidate-oracle-new
```

Каждый кандидат проходит свежую base/gold/wrong calibration и затем собственный
контейнер oracle. Только `candidate.verified_success` даёт результат исправления.
Разбирать причины отдельно:

- `preflight_not_admitted`: среда/manifest/source, модель не оценивалась.
- `ResponseShapeError`, `message:null`, HTTP: transport/provider protocol, не доказательство слабости reasoning.
- `model_critique`/pause: planner, чтение, grounding, источники цитат и bounded repair; смотреть реальные prompts/tool calls.
- `candidate_ungraded`: модель создала patch, качество ещё неизвестно.
- oracle assertion FAIL с принятыми контролями: неправильная правка в этой модели и scaffold.
- oracle infrastructure_error: проблема среды/тестового раннера, не quality score модели.
- unknown charge/effects/timeout: quarantine, сверка квоты/remote outputs до любого повтора.

Одна модель на трёх задачах не отделяет вклад модели от scaffold причинно.
Для такого вывода нужен заранее фиксированный повтор с другой моделью при тех же
задачах, tool authority и лимитах; старые Gemini SymPy-only прогоны не matched comparison.

## 5. Переход к 30

После принятых 30 calibration receipts повторить `prepare_p07_kaggle.py` с
`--count 30 --calibrations /path/to/new-calibrations`, новым slug/output и явно
заданным бюджетом. Builder проверяет приёмку каждой задачи и что бюджет покрывает
все 60 arm caps. При $0.80/arm нужно $48, что **не разрешено сегодняшним $6**.
Урезание caps до $6 изменит эксперимент и не гарантирует достаточного числа шагов.
Не расширять запуск автоматически после трёх задач. P07 остаётся OPEN независимо
от успешного завершения Kaggle Task или появления общего ZIP.
