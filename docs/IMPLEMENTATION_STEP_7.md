# Пакет 7 — первые 12 кандидатов, объединённая реализация

22 октября 2026. База: `7b15406a9541330b374fe1d8a5ec48ac07efbdc1`.
Пользователь подтвердил объём: сначала **06, 09, 12, 13, 16, 19, 22, 23, 29, 31, 38, 40**
из `REFERENCE_OPPORTUNITIES.md`, а не все 62 кандидата.
Шесть субагентов реализовали отдельные пакеты в общей рабочей копии; основной агент
подключил их к runtime, проверил зависимости и исправил интеграционные разрывы.
Ни чужой код, ни внутренние промпты в проект не переносились.

## Что подключено

| Кандидат | Реализация и точка входа |
|---|---|
| 06 — вопросы | `ask_user`, `question_callback` в Orchestrator/TaskRunner; интерактивный CLI и journal question/answer. `--yes` не выбирает ответ. Без канала/ответа результат denied/pending |
| 09 — полный request budget | `llm/request_budget.py`, реальные backend settings в `RequestHistory`, planner/replanner/TaskBudgetBackend. Учтены сериализованные сообщения, текстовые tool schemas, framing и резерв ответа |
| 12 — большие результаты | `LargeResultStore` + `read_result`, preview после 32768 байт; сохранение исходного host audit, непрозрачные request-scoped ID и SHA-256 |
| 13 — фактическое чтение | `ToolContext.record_read/read_coverage/require_read`, `read_file` и полные страницы `read_node`. Runtime требует актуального полного чтения перед существующими файлами/узлами |
| 16 — инструкции | `llm/instructions.py`: пользовательский AGENTS.md, доверенные проектный/вложенные AGENTS.md, directory scope и provenance; подключены роли и planning |
| 19 — поиск текста | `search_text`: literal/regex, path/line/hash, pagination/усечение, bounded scans и отдельный процесс для regex timeout |
| 22 — фрагменты | `patch_file`: уникальное точное совпадение, обязательный исходный hash, проверки, rollback и undo |
| 23 — файловые операции | `create_file`, `delete_file`, `move_file`; transaction backups, изменения нескольких путей, changeset tracking/publish/recovery и сохранение Unix modes |
| 29 — tool contract | Эффекты read/write/execute/interact, cooperative deadline/cancel, output bytes/schema, interaction/idempotence/retry metadata; enforcement в dispatcher |
| 31 — policy layers | Host/user/project/session, запрет любого слоя сильнее allow; ask сильнее auto между слоями, scoped paths и audit source/reason. Недоверенная project policy не загружается |
| 38 — session journal | Версионированный bounded JSONL: input до model request, события model/tool/question, ID/корреляция/outcome, interrupted-tail detection/recovery и CLI inspect/export |
| 40 — state versions | Task schema v1, чтение legacy v0 без изменения файла, отказ будущим/повреждённым форматам; journal schema v1, уникальные временные файлы/replace/fsync |

Файловые операции входят в role allowlist и план/task verification. Все изменённые
пути, включая оба конца move и удаление с ожидаемым hash=None, проходят changeset.
Модель не может заменить эти проверки текстом об успехе.

## Как использовать

Новые tools выдаются соответствующим ролям автоматически. Запись по-прежнему требует
явного edit opt-in и положительного решения policy. Approval новых операций показывает
path/destination/hash и ограниченный redacted preview изменения.

`atomic session list`, `atomic session show ID`, `atomic session export ID new-file.json`
показывают сохранённые наблюдения. `atomic session recover-tail ID` явно сохраняет
повреждённый хвост отдельно и удаляет только незавершённую последнюю запись.
Ни одна из этих команд не повторяет model/tool действия.

Для модельного окна задать в backend config `context_window_tokens` и/или
`max_input_tokens`, при необходимости `request_framing_tokens`/`message_framing_tokens`.
Пустое значение окна означает **неизвестно**, а не безграничную модель.
Без отдельного tokenizer применяется консервативная оценка по UTF-8 байтам;
она явно обозначена как estimate, не точный подсчёт токенов и не гарантия счёта.
При отсутствии output limit обычный role call использует конечный лимит 4096;
task использует свой настроенный default. Это значение передаётся backend.

Пользовательская policy: `ATOMIC_FLOW_HOME/policy.yaml`; проектная —
`.atomic_flow/policy.yaml` доверенного проекта. Модельные project instructions
не изменяют policy, trust, sandbox или backend configuration.

## Найденные и исправленные интеграционные разрывы

- Приватный ATOMIC_FLOW_HOME защищён от чтения, но host-created task workspace
  внутри changesets допускается отдельной host capability; соседние state files
  не становятся исходниками.
- Сохранение результата в storage не считается чтением его тела моделью. Ограниченные
  страницы read_file/read_node/read_result не выгружаются вместо доставки, в том числе
  при многобайтном Unicode. Поиск и signature не дают full-source evidence.
- Полное source body из materialize считается прочитанным только после успешного
  model response с этим контекстом и при совпадении hash; усечённое тело не считается.
- На Windows учитывается регистр при deny matching; закрыты private/hidden directory
  aliases, trailing-dot/space и reserved-device source paths.
- Наблюдаемый terminal tool result записывается перед необязательным audit/storage I/O.
  Ошибка дополнительного файла не стирает факт успешно выполненной записи.
- Если task-журнал был создан, но процесс умер до user_input, input дописывается до
  первого запроса. Уже имеющиеся calls без input или несовпадение query блокируют работу.
- Суммы task/daily ledger сохраняются до transcript response I/O; журнал не является
  источником истины для стоимости и не разрешает replay неизвестного списания.
- Старые test doubles дополнены hashes/reads там, где они имитировали отсутствующие
  обязательные доказательства. Runtime checks ради тестов не отключались.

## Границы

- Это самостоятельная первая реализация выбранных требований, не паритет с Claude Code
  и не доказательство production-качества. Остальные 50 пунктов каталога не закрыты.
- Request admission зависит от заданного окна/счётчика/framing. Provider billing caps,
  native tools, streaming и transport cancellation этой итерацией не добавлялись.
- Deadlines для Python tools cooperative; принудительное безопасное прекращение
  произвольного Python кода не заявляется. Managed subprocess runner остаётся прежним.
- Файловые tools работают с индексируемыми UTF-8 текстовыми типами до 1 MB,
  не с произвольными binary/hidden/private файлами. Каждый mutation проверяется сразу;
  сложные взаимозависимые multi-file refactors могут требовать будущего batch-edit контракта.
- Undo file transaction привязан к конкретному project root/hash. Undo staged workspace
  не является пользовательским rewind уже опубликованной задачи в исходном checkout.
- Instructions целиком включаются в лимит 32 KiB либо пропускаются; oversized/linked/
  private/invalid files не импортируются. Открытые роли не получают project guidance
  недоверенного репозитория. Полная система persistent memory/skills сюда не входит.
- Result store — на один live request, до 1 MiB/result, 8 MiB и 64 results. Read offsets
  обозначают Unicode characters сериализованного JSON. Store не является cross-session
  restore API. В retained run хранится redacted original, но его ID registry живёт в запросе.
- `artifacts.save=false` отключает transcript; result store временный и очищается после run.
  При сохранении runs применяется прежняя retention runs; общий session GC пока отдельная работа.
- Журнал сохраняет наблюдения, не executable state для автоматического resume диалога.
  Task resume по-прежнему определяется Task/changeset/budget. Конфиги и changeset journal
  ещё не объединены в единый migration framework с Task schema.
- Redaction обнаруживает известные ключи/шаблоны, а не любой произвольный секрет.
  При полном отказе storage возможны состояния, требующие ручной проверки; fsync/replace
  не объявляются распределённой или многoфайловой атомарной транзакцией.
- Реальных model calls, платных API, Kaggle/Codespace сессий и локальной установки Docker нет.

## Проверки

Windows Python 3.12, полный объединённый suite: **292 passed, 13 skipped**, 65.83 s,
`validation/step7-full.txt`. Ruff затронутых runtime-модулей и интеграционных тестов,
compileall и diff whitespace checks прошли. `atomic session --help` smoke прошёл.
Runtime-коммит: `0bae3ade4124e3772f20160aa3c8b0c565c497a3`.
Push CI [39723522714](https://github.com/Akseleu-J/atomic-flow/actions/runs/39723522714)
и PR CI [39723526172](https://github.com/Akseleu-J/atomic-flow/actions/runs/39723526172)
прошли: Python 3.11/3.12, Real Docker acceptance.
Docker job: **282 passed, 6 skipped** CPU, **17 passed** root-level tests,
**6 passed** настоящих контейнерных проверок. `container=PASS`, model_calls=0.
Источник, image ID, версии и логи:
`validation/ci-first12-20261022/validation-0bae3ade4124e3772f20160aa3c8b0c565c497a3/`.
В экспортированных текстовых логах нормализованы trailing whitespace/final newlines;
идентичность source/image и JSON results сохранена.
`validation/step7-full-initial.txt` и `step7-integration-repairs.txt` сохраняют ранние
интеграционные прогоны с ошибками; это не PASS evidence.
