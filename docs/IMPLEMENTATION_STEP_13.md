# Пакет 13: durable jobs, scoped credentials и execution backends

23 октября 2026. Кандидаты **33, 34, 42, 43, 45, 62** реализованы независимо
в собственном runtime. Это bounded host APIs и проверяемые execution contracts,
не feature parity, hardware attestation или закрытый production Stage 4.

## Durable jobs и вывод

`JobStore(project)` / SDK `jobs(project)` / `atomic job` запускают Python в отдельном
trusted host worker. Перед запуском сохраняется отдельный проверяемый source snapshot;
изменения в нём не публикуются в исходный проект и не одобряют task plan. По умолчанию
исполнение Docker, без fallback; `--mode local` явно разрешает только доверенный код.
Безопасность local mode не эквивалентна контейнеру.

Job metadata и worker lease переживают закрытие клиента. Состояния: preparing,
queued, running, succeeded, failed, cancelled, timed_out, output_limit, interrupted.
Отмена отдельным durable marker, timeout и ошибки сбора вывода останавливают процесс.
Потерянная worker lease означает interrupted/effects_unknown, без action replay и
без kill по сохранённому PID. Docker cleanup использует неизменяемый CID; отсутствие
подтверждения очистки остаётся явной неопределённостью. Локальные detached descendants
на Windows не объявляются надёжно изолированными или гарантированно остановленными.

Вывод сохраняется потоково как bounded **prefix**, stdout/stderr имеют отдельные
HMAC-bound byte cursors, привязанные к project/job/stream. `data_base64` хранит точные
байты; convenience `text` может разрезать UTF-8 character на границе страницы.
`at_end` означает конец доступного сейчас prefix, а не завершение работы.
`terminal`, `output_complete`, `output_expired` и `discarded_bytes` различают полноту.
Общий cap stdout+stderr по умолчанию 1 MiB; достижение cap с потерей байтов даёт
output_limit, не успешный полный лог. Retention по умолчанию 7 дней; expiry явно
удаляет доступность output. Metadata/snapshot сохраняются для диагностики; авто-GC
всего хранилища не реализован. Есть count/storage/admission limits, при исчерпании
новые задания отказываются, а не молча вытесняют историю или неизвестные процессы.

```console
atomic job start --project . --timeout 120 --output-bytes 1048576 -- -m pytest -q
atomic job list --project .
atomic job status JOB_ID --project .
atomic job output JOB_ID --project . --stream stdout --limit 16384
atomic job output JOB_ID --project . --cursor NEXT_CURSOR
atomic job cancel JOB_ID --project .
atomic job reconcile JOB_ID --project .
```

Source snapshot ограничен разрешёнными source types/paths; hidden/private key,
credentials/secrets, ignored paths и application state не передаются как source.
Это не поиск произвольных секретов внутри разрешённого исходника: host должен
выбирать безопасный проект/файлы. Команды/результаты могут содержать произвольные
пользовательские данные; не передавайте ключи как Python arguments или source.
Процесс получает очищенное окружение, без provider/vault credentials.

## Фиксированные профили и credential grants

`execution.profile=checks` — текущие ограничения: network none, non-root,
read-only root filesystem, no capabilities/privilege escalation, workspace rw,
128 pids / 1 GiB / 2 CPU / 128 MiB tmpfs. `read-only` дополнительно запрещает
запись в workspace и ограничивает 64 pids / 512 MiB / 1 CPU / 64 MiB tmpfs.
Нельзя добавлять произвольные сеть/mounts/security bypass через project config.
Read-only профиль требует Docker. Untrusted project не может менять execution/task
settings; доверенный host выбирает профиль. Docker daemon/image остаются trust boundary.

`CredentialBroker.grant(project=..., connector=..., operations=[...], env_name=...,
approved=True, ttl_seconds=300)` выдаёт process-only capability, максимум на час.
`use(grant.id, ..., consumer=...)` проверяет точный project/connector/operation,
expiry/revocation **до** resolver и повторно перед callback. Grants не восстанавливаются
из metadata после restart. Revocation не отменяет уже начатую внешнюю операцию.
`CredentialValue` скрывает repr, не сериализуется и закрывается после callback.
Ошибки не содержат сообщения resolver/connector, JSON result bounded и отклоняет
случайно возвращённый raw secret. Никакого general child env injection нет.

Consumer — доверенный host Python, не sandbox стороннего connector. Он технически
может намеренно скопировать/закодировать/отправить reveal() value; этот API не делает
вредоносный Python безопасным. Model tools не получают broker или ключи. Существующие
provider adapters не объявляются автоматически переведёнными на broker.

## Git worktree workspace

`task.workspace_backend=git-worktree` выбирается только trusted config; default copy.
`ChangeSet.create(project, backend="git-worktree")` строит приватный bare repository,
snapshot commit и detached linked worktree из **текущих** managed dirty/untracked bytes.
История/remote credentials исходного Git не копируются; его HEAD/index/worktree registry
не меняются. Git hooks/filters/global/system config и переменные Git не наследуются.
Source linked worktrees/submodules отклоняются; поддерживается ordinary repository root.

Reopen/publish/recover/cleanup проверяют принадлежность `.git`, registry, private config,
HEAD и пути без symlink/junction. Все существующие approval/read/hash/scope/final check
и journaled publication/recovery gates остаются; публикация не делает git merge/reset.
`cleanup()` допускается для terminal workspace и сохраняет журнал/приватный snapshot;
abort очищает только проверенное собственное worktree. Сбой multi-file publication
восстанавливается прежним conflict-checked путём, без перезаписи третьей версии.

## Сменяемый Linux worker

`remote.py` содержит `WorkerTransport.call(action,payload)` и `RemoteExecutor`.
`build_bundle(project, paths, args, image="sha256:...")` выбирает явно 1–64 source files,
до 4 MiB total / 1 MiB each. Структурированный JSON/base64 bundle без archive extraction:
schema, request ID, project hash, source SHA-256, immutable image ID, fixed profile,
timeout/output cap. Private paths, collisions и повреждённые bytes отклоняются.

`RemoteWorker` работает только на Linux с доступным pinned Docker image. Перед
исполнением проверяется bundle, материализуется private source и заново сверяется
job snapshot. Durable request journal записывает preparing до child start. Повторный
request ID с другим содержимым запрещён; существующее submitted receipt читается
без повторного исполнения. Interrupted preparing не запускается автоматически.

HTTPS transport отключает redirects/proxies, использует bounded JSON и один запрос
без retry. Plain HTTP разрешён только явно для loopback проверки. Worker server
слушает только loopback, для внешней машины нужен TLS reverse proxy. Token не короче
32 printable characters читается process-only. Число соединений/threads ограничено,
timeout применяется до чтения headers; body/auth/operations также проверяются.

```console
# На доверенной Linux-машине с заранее установленным image ID и token в environment:
atomic worker --image sha256:IMAGE_ID --port 8766
```

```python
from atomic_flow.credentials import CredentialBroker
from atomic_flow.remote import build_bundle
from atomic_flow.sdk import connect_worker

broker = CredentialBroker()
grant = broker.grant(project=project, connector="remote-worker",
    operations=["submit", "lookup", "status", "output", "cancel"],
    env_name="ATOMIC_FLOW_WORKER_TOKEN", approved=True)
client = connect_worker(project, "https://your-worker.example", broker=broker,
                        grant_id=grant.id)
bundle = build_bundle(project, ["main.py"], ["main.py"], image=pinned_image_id)
# Caller persists bundle.model_dump() privately before sending, for lookup after restart.
receipt = client.submit(bundle)
status = client.status(receipt)
page = client.output(receipt)
client.cancel(receipt)
# После RemoteOutcomeUnknown: client.reconcile(bundle), только journal lookup, без submit.
```

Receipt и status/output manifests привязаны к request/bundle/source/image/job identity;
подмена receipt/source/result hashes отклоняется. Потерянный/повреждённый submit response
сохраняет `RemoteOutcomeUnknown`, в том числе через scoped credential adapter. Worker
trusted: hashes проверяют целостность/согласованность, **не честность удалённой машины**.
Это swappable execution backend для выбранного source, не Kaggle model backend и не
автоматическая публикация remote edits. Live cloud endpoint/инсталляция на отдельной
машине не выполнялись; CI проверяет протокол с настоящим Docker на Linux через loopback.
Проверки CPU с fake worker отделены от этого Linux acceptance.

## Проверка

Focused Windows regression: **74 passed / 8 skipped**, Ruff/compileall PASS. В полном
первом прогоне 544 passed / 20 skipped / 2 transient failures; raw log сохранён.
Исправлены Windows helper cleanup fallback и отдельный bounded startup regex worker,
без увеличения пятисекундного лимита вычисления regex; добавлены regressions.
Финальный Windows: **548 passed / 20 skipped**, 735.21s,
`validation/step13-windows-final.txt`. Raw initial failed log остаётся отдельно;
финальный прогон включает оба новых startup/cleanup regression tests. Ruff,
compileall и staged whitespace check PASS.

Runtime **`dea7fa742e9af5220cb4a8e67841aaf9f8dfd7a0`**, CI push
[`39923289564`](https://github.com/Akseleu-J/atomic-flow/actions/runs/39923289564) и PR
[`39923295523`](https://github.com/Akseleu-J/atomic-flow/actions/runs/39923295523)
completed/success; Python 3.11 и 3.12 PASS. Скачанный Docker artifact подтверждает:

- CPU: **541 passed / 10 skipped**, 28.12s; extra: **17 passed**.
- Execution acceptance: **25 passed**, 12.52s: **9 настоящих Docker tests** и
  **16 вспомогательных CPU/local job tests**. Число 25 не выдаётся за 25 Docker tests.
  Реальные новые сценарии: read-only workspace, HTTP worker submit/status/output/cancel
  с pinned image и job crash/reconcile cleanup по CID без replay.
- Compile PASS; historical grader calibration PASS (6 известных исторических изменений,
  не новая task-quality evaluation); **model_calls=0**, GPU не запрашивался.
- Image ID: `sha256:16a1faebd1207bda37d77cf05f2139208f2edfc8364753bc03aa747746bbd586`.

Исходные tested-commit/results/logs сохранены в `validation/ci-step13-20261023/`.
Manifest SHA-256 охватывает **12 artifact files** и **20 изменённых source/test/tool
Git blobs**; staged bytes сверены с ним. Документирующий commit не меняет runtime.
Скрипт `tools/colab_validate.py --require-docker` включает новые read-only/remote/job
acceptance. Внешние платные API, vault model keys и Codespace/Colab/Kaggle сессии
для пакета не использовались. Внешний cloud endpoint и Stage 4 edit quality остаются
отдельными открытыми проверками.
