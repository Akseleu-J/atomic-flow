# Пакет 11: события, отмена, уточнения и SDK

22–23 октября 2026. Независимая реализация кандидатов **03, 04, 05, 08, 53, 56**.
Каталог после этого пакета: **35 implemented_scoped / 27 planned** из 62.
Это live control для существующего answer pipeline; durable jobs, полноценное
возобновление диалога и перенос выполнения на другую машину остаются в следующих пакетах.

## Отмена и исход HTTP

Все встроенные HTTP adapters используют общий context-local cancel check. Ожидание
DNS/connect/read и response/error body выполняется в daemon transport worker;
caller проверяет отмену и локальный deadline каждые 25 мс. ОС/DNS/provider могут
продолжить работу после отмены. Это **отмена локального ожидания**, без обещания
прервать remote inference или аннулировать его оплату.

Одновременно допускается 4 transport workers. Отменённый worker занимает слот до
реального выхода; если все зависли, новые вызовы получают `not_sent`, а не создают
неограниченные threads. Ответ ограничен 16 MiB; уже полностью полученный JSON имеет
приоритет при гонке с отменой, чтобы caller успел учесть его стоимость. Поздний ответ
покинутого worker не снимает unknown hold автоматически: нужна сверка usage.

`request_outcome`: `not_sent` до отправки/при admission refusal, `refused` для явного
HTTP rejection, иначе `unknown`. Автоматический retry разрешён только для 425/429
с ограниченным Retry-After/backoff. Ошибки сети, timeout, malformed success JSON и
5xx не повторяются: сервер мог уже выполнить оплаченный inference. Negotiation
response_format/temperature остаётся только после 400/422 refusal. Классификация
HTTP refusal предполагает обычную семантику endpoint; это не аудит счетов провайдера.

TaskBudgetBackend по-прежнему отключает retries, сохраняет reservation до вызова,
учитывает ответ до cancel boundary и блокирует resume неизвестного call. Отмена
проходит как TaskCancelled после сохранения unknown state. `not_sent/refused`
закрывают reservation нулём и отражаются в session journal.

Обычный `ask` теперь резервирует/settle каждый model call, без двойной записи
aggregate cost в finally. Ledger key `<request_id>_model_<n>` сопоставляется с
session request_id/call_id. При неизвестном результате hold остаётся; неизвестная
стоимость ответа блокирует автоматический follow-up/JSON repair при включённом
daily budget. При неизвестных configured prices reservation равен нулю с
`prices_known=false`: денежный потолок тогда не гарантируется. Оценки по bytes и
configured prices не заменяют provider billing. Task pricing gate остаётся строже.

## Уточнение без изменения начатого действия

`RunControl` принимает version 1 `cancel` или `steer(text)`: до 16 сообщений по 8000
символов в очереди, суммарно до 32000 применённых символов на run. Input не расширяет
edit opt-in, role tools, policy, workspace scope или hash checks.

Уточнения применяются перед следующим model request, до его admission; следующие
роли тоже получают их. Если уточнение пришло во время model call, ответ сохраняется
и оплачивается, но отбрасывается перед tool execution/принятием final JSON. Модель
пересматривает его с новым вводом. Dispatcher повторяет boundary после approval
и загрузки trusted factory, непосредственно перед tool.run. Ввод после этой границы
относится к следующему действию: уже проверенные args не переписываются на лету.
Запись, которую tool уже начал, не отменяется задним числом уточнением. Финализация
закрывает control атомарно; неприменённое позднее сообщение даёт явный failed run.

History indices сохраняются при обновлении clarification message, поэтому omission
старых tool results не теряет read/edit proof. Старые native tool IDs получают
прежнее host-owned denied pairing и не исполняются через replay. Повтор native ID
проверяется даже для отброшенного ответа. Если guidance обновилось во время critic,
нужен новый answerer; прежний high-scoring ответ не может выиграть. При исчерпании
attempt limit возвращается явная ошибка вместо старого ответа.

## Version 1 events и Python SDK

`runtime.py`: Pydantic `ControlMessage`, `RunEvent`; `sdk.py`: `RunError`,
`start_answer` → `AnswerHandle.events()/result()/cancel()/steer()`.
Envelope: version, run_id, sequence, timestamp, kind, call_id, optional task_id,
outcome, payload, dropped_before. Model/tool events используют те же call IDs,
что append-only journal; tool summary включает host verification/provenance.
TaskBudgetBackend task_id связывается с envelope при использовании этого backend.
Это не отдельный streaming TaskRunner или OpenTelemetry exporter.

Buffer по умолчанию 128 events (host range 2–4096), payload до 64 KiB; oversized
payload заменяется явным omitted marker. Slow reader вытесняет старые observations;
следующий event сообщает число пропусков, terminal хранится отдельно. Полный result
доступен через handle/result endpoint даже при omitted terminal payload. Это
ограниченный trace, не обещание lossless streaming. Raw model prompts/source не
включены в live events; локальный session journal сохраняет прежний bounded/redacted
контракт. Event payload проходит JSON validation/secret-pattern redaction; не следует
передавать неизвестные секреты как обычный пользовательский текст.

SDK ограничен 4 активными runs и одним consumer на handle. Built-in HTTP и managed
processes поддерживают cancellation; произвольный host Python backend/callback
должен сотрудничать с cancel check. SDK не может безопасно прервать arbitrary Python.
Пример с уже выбранным backend/config:

```python
from atomic_flow.sdk import start_answer

handle = start_answer(config, project, backend, "Explain the refund rule", mode="simple")
for event in handle.events():
    print(event.model_dump_json())
result = handle.result()
```

## CLI и HTTP

`atomic ask "..." --events --mode simple` выводит тот же version 1 JSONL, включая
terminal result/error; Ctrl-C отправляет cancellation и дожидается terminal. Interactive
approval/question callbacks не читают stdin из worker: events mode использует explicit
host approval options, ask_user без callback получает существующий denied result.
Обычный CLI сохраняет прежний interactive режим.

- `POST /v1/runs` с прежним AnswerRequest возвращает 202 и run/events/control/result URLs.
- `GET /v1/runs/{id}/events` — SSE observations и terminal, один consumer. Disconnect
  запрашивает отмену. Reconnect получает оставшиеся события/retained terminal и не
  повторяет model/tool calls; Last-Event-ID replay не реализован.
- `POST /v1/runs/{id}/control` — ControlMessage; accepted означает queued, не applied.
- `GET /v1/runs/{id}` — 202 running либо result/error. Unknown/expired ID — 404.

Все /v1 endpoints проходят существующую API-key защиту и project-root checks;
серверный ключ — общая доверенная область клиентов, не multi-tenant ACL. Общая server
concurrency применяется и к обычным, и к streaming runs. Хранятся до 32 handles,
завершённые — максимум 10 минут, с возможным более ранним eviction. Restart теряет
live handles; журналы не исполняются повторно. OpenAI chat shim token streaming
по-прежнему не включён: события этапов не выдаются за token deltas.

## Проверки и границы доказательства

`tests/test_run_control.py`: локальные transport fault injections для DNS/connect/read,
error body/backoff; нулевая отправка до cancel; saturation, unknown outcome/no retry;
425/429 refusal, 400 optional-parameter negotiation и redaction; task/ask ledger holds;
полученный charged response перед cancel; SDK/session IDs; event overflow/terminal/
version/size; steering до mutation и после начала tool; сохранение history/admission;
API auth/root/control/result/reconnect и настоящий ASGI disconnect; CLI JSONL.

Paid OpenAI/Anthropic APIs не использованы. Fault injection доказывает локальное
управление ожиданием и учёт, не поведение remote cancellation конкретного провайдера.
Windows: **391 passed / 13 skipped**, 28 новых control tests включены в общий набор;
Ruff changed files и compileall PASS. Raw final log: `validation/step11-windows-final.txt`.
Предыдущий failed log сохранён как `step11-windows-before-fixture-fix.txt`: runtime
верно отклонил повтор ID, но тест проверял счётчик исходного mock вместо его копии;
fixture исправлен до финального полного PASS. Его wall time не является benchmark.
CI для runtime **`39e359aa85bd32d931800b5acde6380c40cc1166`**:
push [39862268360](https://github.com/Akseleu-J/atomic-flow/actions/runs/39862268360)
и PR [39862274999](https://github.com/Akseleu-J/atomic-flow/actions/runs/39862274999)
завершены success. Python 3.11/3.12 PASS. Скачанный Docker-job artifact подтверждает
**381 CPU passed / 6 skipped + 17 extra passed + 6 real Docker passed**,
compile PASS и calibrated=true для прежних шести historical changes; model_calls=0.
Image: `sha256:d36468372521b3c52a12f08ddf0388a26a07124d45fc68a9cec67bbc7a12fce8`.
Evidence: `validation/ci-step11-20261023/`; manifest содержит SHA-256 всех 12
исходных artifact files и 6 ключевых Git source blobs. Bytes логов не нормализованы.
CI проверяет новый runtime и прежние реальные container boundaries; сетевые
отмены используют fault injection, не реальный model provider.

Stage 4 independent multi-repository edit evaluation остаётся открытым.
