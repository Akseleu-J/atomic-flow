# Возможности для atomic-flow: расширенный каталог

Дата: 22 октября 2026. Это список для обсуждения, **не утверждённый план реализации**.
Продуктовый код в рамках этого обзора не менялся.

Дополнение: после обзора пользователь выбрал первые 12 для реализации. Пакет 7
и его фактический scope/validation описаны в `IMPLEMENTATION_STEP_7.md`.
Таблицы ниже сохраняют исходную оценку на базе 7b15406; они не являются текущим
implementation status. Остальные 50 кандидатов этим пакетом не закрываются.

## Что именно проверено

- Наш checkout: `7b15406a9541330b374fe1d8a5ec48ac07efbdc1`, ветка `codex/execution-safety-ci`.
- Зеркало: [777genius/claude-code-source-code-full](https://github.com/777genius/claude-code-source-code-full),
  commit `021277ac7bad4954e7806e5a9bfc122a533607e2` (8 октября 2026).
- Получен полный каталог через GitHub API: 2486 записей, `truncated=false`.
  Прочитаны README и шесть документов; выборочно просмотрены объявления,
  комментарии и релевантные участки 34 исходных файлов. Метаданные этих файлов:
  `REFERENCE_INSPECTION.json`. Это **не построчный аудит всего репозитория**.
- В atomic-flow сверены карта проекта, полный список runtime-файлов и контракты
  backend, tools, policy, tasks, source I/O, history, CLI/API, retrieval и бюджета.
- Содержимое зеркала не запускалось, не устанавливалось и не импортировалось.
  В каталог не перенесены чужие исходники или внутренние промпты.

Ограничения источника: README называет его reportedly exposed snapshot;
происхождение и соответствие актуальному продукту независимо не подтверждены.
Документация зеркала добавлена его авторами и местами ошибается: например,
`QueryEngine.ts` в проверенном снимке — 1297 строк / 46632 байта, а не ~46 тысяч строк.
В `entrypoints/agentSdkTypes.ts` некоторые экспортированные функции, включая
query и создание/восстановление сессии, бросают `not implemented`.
Поэтому ниже различаются **наблюдаемый контракт**, **описанная возможность**
и **наша оценка полезности**; наличие файла не доказывает готовность функции.

[LICENSE зеркала](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/LICENSE)
помечает материал UNLICENSED / NOT FOR REDISTRIBUTION. Вопросы о конкретных
допустимых способах использования и независимой реализации разбираем следующим
шагом; этот каталог не даёт прав на перенос кода, перевод или переименование реализации.

## Обозначения

- **Частично** — у нас есть основание, но указанной законченной функции нет.
- **Не найдено** — в исследованном runtime atomic-flow такой подсистемы не обнаружено.
- **Есть основа** — сохраняем существующий механизм; кандидат — его конкретное расширение.
- **A** — сильный кандидат для ближайшего single-agent CLI; порядок внутри группы ещё не утверждён.
- **B** — полезно после надёжного основного сценария и измерений.
- **C** — отдельное продуктовое направление, пока отложить.
- Источник **S** означает просмотренный исходный контракт/релевантный участок;
  **D** — документацию; **I** — только каталог путей. Ссылки перечислены ниже.
- Колонка «Что нужно нам» — самостоятельное требование atomic-flow, **не описание
  точного чужого алгоритма**. Приоритет и польза — наша инженерная оценка.

## 1. Диалог, выполнение и протокол модели

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 01 | Native tool calls с сохранением text-JSON fallback | Частично: общий backend, но только строковый chat и JSON в тексте | Типизированные вызов/результат с ID; использовать native transport там, где он действительно поддержан | A; S01, S02; `backends/base.py`, `agents/loop.py` |
| 02 | Описание возможностей backend/model | Частично: backend выбирается по конфигурации | Явно знать tools, streaming, JSON schema, context/output limits и cancellation; не предполагать одинаковые возможности провайдеров | A; S01, S02; `backends/base.py` |
| 03 | Поток событий выполнения | Не найдено; API явно отвергает stream | Показывать начало/конец вызова, прогресс tool, бюджет и проверку без ожидания финального ответа; один поток для CLI/API | B; S02, S03; `api/server.py` |
| 04 | Отмена сетевого/model call | Частично: есть cancel marker и остановка управляемых проверок; синхронный chat заканчивается или ждёт timeout | Общий сигнал отмены для transport, tool и фонового worker; отдельное состояние неопределённых расходов | A; S01, S02; `agents/control.py`, `agents/task_budget.py` |
| 05 | Уточнения пользователя во время работы | Не найдено: нет session queue | Принять «этот файл не трогай» в процессе, применить на безопасной границе, определить отмену/ожидание для текущего действия | B; S04, S01 |
| 06 | Структурированный вопрос пользователю как tool | Частично: approval callback отвечает да/нет | Агент может запросить недостающую спецификацию, сохранить ответ и продолжить; ожидание не превращается в разрешение | A; S05; `cli.py::_make_approval_callback` |
| 07 | Отдельное состояние plan/review/execute | Частично: task dry-run строит и сохраняет план | Возможность обсудить/изменить план и область файлов до исполнения; разрешения связаны с текущим планом, а не бессрочным общим согласием | A; D01; `agents/planner.py`, `cli.py::cmd_task` |
| 08 | Политика retry по типу вызова и исходу | Есть основа: backoff/Retry-After, task transport retries выключены | Различать отказ до отправки, неизвестный исход и безопасный повтор; не размножать необязательные вызовы при перегрузке | A; S06; `backends/http.py`, `agents/task_budget.py` |

## 2. Контекст и память

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 09 | Бюджет полного запроса с учётом модели | Частично: retrieval token estimate и отдельный UTF-8 byte cap истории | Учитывать инструкции, tool schemas, историю, резерв ответа и модельный tokenizer/счётчик; оценку отделять от точного usage | A; S07, S08; `textutil.py`, `llm/history.py` |
| 10 | Упреждающее управление заполнением контекста | Частично: только omission старых tool bodies и отказ при превышении | Предупреждать заранее, оставлять место для завершения и прекращать бесполезные попытки compaction | A; S08; `llm/history.py` |
| 11 | Структурированное состояние после сжатия | Не найдено: текущая history не делает semantic summary | Сохранять цель, ограничения, принятые решения, открытые вопросы и ссылки на доказательства; summary не подменяет исходное доказательство | B; S09; `llm/history.py` |
| 12 | Большие tool results вне prompt с повторным чтением | Частично: полные audit-файлы есть, но модели не выдан универсальный способ перечитать сохранённый результат | Короткий preview + ID результата + постраничное чтение оригинала; retention и запрет произвольного чтения host-файлов | A; S10; `llm/orchestrator.py` |
| 13 | Учёт того, что модель действительно прочитала | Частично: hashes, pagination и evidence context | Различать подпись/фрагмент/полный файл, покрытие страниц и ревизию; не считать summary или обрезанный read полным прочтением | A; S11; `agents/context.py`, `agents/tools/read_node.py` |
| 14 | Стабильный prompt и диагностика cache | Не найдено: cache usage/причины сброса не выделены | Не менять постоянные части без причины; измерять cache hits и стоимость, если backend их поддерживает; не гарантировать caching всем провайдерам | B; S12; `backends/base.py` |
| 15 | Объяснение состава контекста | Частично: selection/context artifacts и byte statistics | Показывать, сколько заняли инструкции, исходники, память, tool results и резерв; почему конкретный узел попал/не попал | A; D02; `activation/activate.py`, `llm/history.py` |
| 16 | Runtime-загрузка проектных инструкций по области файлов | Частично: есть role prompts; наш AGENTS.md читает внешний coding agent, не runtime atomic-flow | Пользовательские/проектные/вложенные инструкции, область действия, источники и доверие; не превращать произвольный текст репозитория в host-policy | A; D03, S01; `llm/prompts.py` |
| 17 | Память проекта и пользователя между сессиями | Не найдено: audit и Task не равны управляемой памяти | Отдельные факты/предпочтения с источником, датой, областью и возможностью исправить/удалить; записи по явной политике | B; D03, S13 |
| 18 | Выборочная загрузка памяти вместо всей папки | Не найдено | Индекс заголовков/кратких описаний, релевантный поиск и лимит чтения; отслеживание свежести и уже загруженных записей | B; S13; `activation/activate.py` как возможная основа |

## 3. Поиск, чтение и изменение проекта

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 19 | Поиск по исходному тексту с regex | Частично: search_tree ранжирует индекс, это не полный grep содержимого | Найти точную строку/конфиг/неиндексируемый символ; ограниченный scope, результаты с path/line/hash и признаком усечения | A; S14; `agents/tools/search_tree.py` |
| 20 | Glob и явный обзор файлов | Частично: дерево файлов есть, отдельного tool нет | Быстро найти файл по имени/маске без лексической релевантности; соблюдать ignores и границы проекта | A; D01; `tree/builder.py` |
| 21 | Универсальный read по диапазону строк | Частично: read_node умеет страницы узла | Читать импорты, конфиги и соседний контекст без AST-узла; единые markers неполного результата и ревизии | A; D01, S11; `agents/tools/read_node.py` |
| 22 | Контролируемое редактирование небольшого фрагмента | Частично: edit_node требует всю функцию/класс с прежним именем | Правки импортов/конфигов/комментариев, минимальный diff, проверка уникального совпадения и исходного hash | A; S15; `agents/tools/edit_node.py` |
| 23 | Создание, удаление и перенос файлов | Не найдено как законченный tool-путь | Реальные изменения пакетов/тестов/конфигов; поддержать операции в changeset, journal, publish и rollback, включая конфликт существующего файла | A; D01; `agents/changeset.py` |
| 24 | Работа с notebook по ячейкам | Не найдено | Для Kaggle/Colab-проектов: cell ID, тип/порядок ячеек, отдельная политика outputs; не делать это строковым патчем JSON | B; D01 |
| 25 | LSP для definitions/references/rename | Частично: собственный Python AST и приближённые связи | Более точные ссылки и поддержка других языков; LSP как дополнительный источник, с ограничением процесса и версий | B; D03, S16; `tree/references.py` |
| 26 | Диагностика после правки | Частично: syntax/pytest и разрешённые проверки | Структурированные ошибки type checker/linter/LSP с path/range/severity; относить результат к конкретной ревизии | A; S16; `agents/verify.py` |
| 27 | Изменения файлов извне и обновление индекса | Частично: hash checks и refresh после собственных edits | Инвалидация при правках редактора/другого процесса; debounce watcher и проверка свежести перед использованием | B; D04; `tree/builder.py`, `agents/context.py` |
| 28 | Несколько корней workspace | Частично: один project root, добавлять пути произвольно нельзя | Читать связанные библиотеки/монорепо с явным разрешением каждого корня; права и индекс привязать к root ID | C; D02; `agents/source.py`, `api/server.py` |

## 4. Контракты инструментов, разрешения и проверка

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 29 | Полное описание свойств каждого tool | Частично: schema, description, role allowlist, отдельные special cases | Тип результата, эффект/область записи, timeout, допустимость повтора, интерактивность и реакция на отмену; проверяемый host-контракт | A; S01; `agents/base.py`, `agents/loop.py` |
| 30 | Ограниченный параллелизм безопасных reads | Не найдено: последовательный loop | Выполнять независимые чтения одной ревизии параллельно с лимитом; редактирование/refresh/общий state требуют отдельной синхронизации | B; S01; `llm/orchestrator.py` |
| 31 | Приоритет и область policy rules | Частично: есть auto/ask/forbid и first-match | Разделить host/user/project/session, защитить запреты от переопределения; явно определить семантику конфликтов | A; S17; `agents/policy.py` |
| 32 | Explain/lint для разрешений | Частично: audit хранит matched reason | Показать, почему принято решение, откуда правило и какие правила недостижимы/конфликтуют; тестировать конкретный вызов offline | A; S18; `agents/policy.py`, `cli.py` |
| 33 | Профили sandbox по задаче | Есть основа: строгий Docker default и explicit local | Контролируемые профили проверки/установки зависимостей/сетевых операций с сохранением реальной изоляции; не общий bypass | B; D03; `agents/execution.py` |
| 34 | Область действия credentials | Частично: backend env, redaction и очищенный runner env | Минимальная выдача токенов конкретному connector/tool; управлять истечением и отзывом, никогда не давать секреты prompt/skill | B; D04, D03; `config.py`, `agents/execution.py` |
| 35 | Явное происхождение внешних данных | Частично: source hashes, redaction и trust config | Отличать пользовательскую инструкцию от текста файла, сайта, plugin/MCP результата; запрещать данным изменять права или команды host | A; собственное требование на границе D03/S01; `activation/redact.py`, `config.py` |
| 36 | Hooks до/после tool и жизненного цикла | Не найдено как расширяемая подсистема | Подключить проектные проверки и форматтеры через ограниченный контракт; hook не может расширить разрешения, изменить неаудированные args или обойти budget | B; S19, S20; `agents/verify.py` |
| 37 | Проверка завершения всей задачи | Частично: строгая Step verification и critic | Отдельный host gate для итогового diff/требуемых проверок/открытых ошибок; обоснованный done, а не только текст модели | A; S21; `agents/task_loop.py::_verify_step` |

## 5. Сессии, фоновые задачи и агенты

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 38 | Единый журнал сессии | Частично: отдельные run artifacts, Task JSON и budget events | Сохранить ввод до сетевого запроса, ID событий и связи tool call/result; восстанавливать состояние без повторного исполнения действий | A; S02, S22; `llm/orchestrator.py`, `agents/task.py` |
| 39 | Resume, fork и экспорт именно диалога | Частично: resume Task, а не разговорной сессии | Продолжить обсуждение, создать ветку с неизменяемой историей и понятной связью с исходниками; export с redaction | B; S22, D02; `agents/task.py` |
| 40 | Версии схем и миграции состояния | Частично: dataclass load не является версионируемым persistence protocol | Обновление программы не ломает старые задачи, журналы и конфиги; тесты миграции и отказ на неизвестную версию | A; D05, I01; `agents/task.py`, `config.py` |
| 41 | Пользовательский rewind к контрольной точке | Частично: undo одной правки и внутренние step checkpoints | Выбрать этап и посмотреть diff до восстановления; проверка внешних изменений, создание/удаление файлов и границы необратимых эффектов | B; S23; `agents/undo.py`, `agents/changeset.py` |
| 42 | Фоновые команды как durable jobs | Частично: управляемый subprocess, но нет пользовательского job API | Запустить долгую проверку, получить ID, продолжить диалог, остановить job и корректно восстановить сведения после сбоя | B; S24; `agents/execution.py` |
| 43 | Постраничный output/tail фоновой работы | Частично: bounded capture итогового subprocess output | Читать прогресс по cursor/offset, не терять полный вывод, исключить утечки между jobs; завершение — отдельное событие | B; S24, D01; `agents/execution.py` |
| 44 | Зависимости задач и обработка blocked | Частично: линейные steps и replan | Явные зависимости и статус причины ожидания; сначала полезно даже без нескольких агентов | B; D03; `agents/task.py` |
| 45 | Git worktree как дополнительный workspace backend | Частично: собственная staged копия и journaled publish | Дешевле работать с большими Git-репозиториями, видеть ветку/diff; сохранить checks, policy и защиту публикации | B; S25; `agents/changeset.py` |
| 46 | Изолированные специализированные subagents | Не найдено: роли одного orchestrator не равны самостоятельным workers | Отдельный контекст/tool scope/budget, сначала read-only исследователь или reviewer; измерять выгоду и независимость проверки | C; D03, I02; `llm/orchestrator.py` |
| 47 | Контролируемый handoff и результаты workers | Не найдено | Передавать задачу, ограничения, ссылки на evidence и итог; parent учитывает расход/отмену и сам проверяет результат, общий filesystem не разделяется бесконтрольно | C; D03, S01; `agents/task_budget.py` как основа |

## 6. Расширения и интеграции

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 48 | MCP client | Не найдено | Подключать внешние инструменты/resources через стандартный протокол с собственными permissions, timeout, auth и аудитом | B; S26, D03 |
| 49 | Отложенное обнаружение tools | Не найдено; пока всего пять встроенных tools | При росте интеграций искать нужные возможности и подгружать schema, а не забивать весь prompt; сначала нет смысла усложнять текущие пять tools | B; S27; `agents/base.py` |
| 50 | MCP server для возможностей atomic-flow | Не найдено | Отдать наш структурный поиск/чтение другим клиентам; отдельный API-контракт, не открывать произвольный shell | B; D03; `tree/`, `activation/` |
| 51 | Skills / переиспользуемые workflows | Частично: role prompt overrides не равны workflow | Наши review/debug/verify сценарии с manifest, областью tools, аргументами и загрузкой по необходимости; текст workflow не увеличивает права | B; S28; `llm/prompts.py` |
| 52 | Управляемые plugins | Не найдено | Manifest, версии, проверяемый источник и разрешённые расширения; сначала локальные доверенные пакеты, marketplace/auto-update отдельно | C; D03 |
| 53 | Headless SDK и стабильный event protocol | Частично: Python Orchestrator и HTTP shim, не стабильный session API | Встраивание в CI/редактор с typed events, controls, errors и versioning; в зеркале часть SDK только объявлена/заглушена | B; S29, S02; `backends/base.py`, `api/server.py` |
| 54 | IDE bridge | Не найдено | Получать selection/diagnostics и показывать diff/approval в редакторе через локальный аутентифицированный канал; не зависеть от чужого облака | C; D04 |
| 55 | Web search/fetch для документации | Не найдено | Читать внешнюю документацию с источниками, размерными лимитами, URL/network policy и маркировкой недоверенных данных | B; D01; `agents/base.py` |

## 7. Наблюдаемость и эксплуатация

| № | Кандидат | Что у нас сейчас | Что нужно нам / зачем | Приоритет; источник |
|---|---|---|---|---|
| 56 | Единая трасса выполнения | Частично: request/role/tool/meta artifacts | Корреляция task/session/model/tool/check, длительности, причины остановки; локальный trace без обязательной отправки телеметрии | A; D05, S02; `llm/orchestrator.py` |
| 57 | Подробный отчёт usage/cost | Частично: durable task costs и daily reservations | Раздельно input/output/cache, роли, retries и неизвестные расходы; стоимость успешной задачи, ask/API в общем контроле | A; S12, D02; `agents/task_budget.py`, `budget.py` |
| 58 | Расширенный offline doctor | Есть основа: doctor проверяет Python/tree/backend | Добавить sandbox, права каталога, свободное место, неизвестные holds, испорченный state, подключённые сервисы; live probes отдельно | A; D05; `cli.py::cmd_doctor` |
| 59 | Объяснение эффективной конфигурации | Частично: layered config и masked output | Показывать источник каждого значения и игнорируемые untrusted настройки, ошибки и последствия изменения; проверять до запуска задачи | A; D05, S17; `config.py` |
| 60 | Загрузка необязательных компонентов по необходимости | Частично: API extra и некоторые lazy imports | MCP/LSP/UI/remote не должны ухудшать запуск обычного CLI и память на слабом ноутбуке; измерять cold start/RSS | B; D05; `api/server.py` |
| 61 | Явные экспериментальные возможности | Частично: отдельные config flags, общей модели maturity нет | Отделить стабильный runtime от экспериментальных transports/agents; default off, видимое состояние и путь отключения | B; D05; `config.py` |
| 62 | Сменяемый удалённый executor | Частично: Kaggle backend для модели и локальный Docker runner — разные сущности | Контролируемый Linux worker для проверок с bundle/hash, возвратом evidence и отменой; это не обязательная зависимость от Codespace | C; D04, D03; `agents/execution.py`, `kaggle/` |

## Что уже есть — не выдавать за новые заимствования

Валидация входов tools, центральный dispatcher, role allowlist, approval callbacks,
host-owned ToolResult, строгая проверка шага, hashes/reindex, staged changeset,
checkpoint/rollback, Docker по умолчанию, лимиты/отмена управляемых процессов,
task lease, task budget events/reservations, audit artifacts, старые-result omission
и ограничение одинаковых вызовов уже есть. Они **не равны всем перечисленным
подсистемам** и не подтверждают полноту/равенство реализации с внешним продуктом.

## Что пока не включать в рабочий план

Голос, собственный браузерный/desktop control, темы/Vim/анимации, социальные команды,
корпоративный MDM/SSO/общекомандная память, marketplace с auto-update, проактивные
агенты и расписания — отдельные направления с низкой текущей пользой для первого
single-user Python CLI. Наличие их названий в зеркале — не причина внедрять их сейчас.
Внутренние feature flags, частные backend endpoints, чужая OAuth-схема и bypass
разрешений не являются кандидатами на перенос.

Универсальный shell/REPL тоже не предлагается автоматически включить целиком:
расширение допустимых команд нужно рассматривать вместе с sandbox, policy и jobs,
а не превращать текущий ограниченный run_command в выполнение любой строки.

## Предварительный короткий список для обсуждения

Сначала предлагаю обсудить 12 позиций: **06, 09, 12, 13, 16, 19, 22, 23, 29,
31, 38, 40**. Они закрывают практические ограничения текущего агента:
уточнение задачи, управляемый контекст, корректное чтение/изменение проекта,
проверяемые права и восстановление состояния. Это предложения, не автоматический
выбор всех двенадцати на ближайшую итерацию.

Возможные зависимости для следующего обсуждения: 01/02/29 перед native/streaming;
38/40 перед 39/42; 23 перед полноценным 41; 31/35 перед 36/48/51;
изолированный workspace и общий budget перед 46/47.

Отдельно от заимствований остаётся наш обязательный evaluation gate: реальные
задачи с независимой проверкой, одинаковыми моделью/лимитами и baseline.
Он нужен, чтобы решить, какие из 62 кандидатов повышают task success или снижают
стоимость, а не только добавляют подсистемы. Новых измерений качества в этом обзоре нет.

Следующий шаг после обсуждения списка: для выбранных пунктов определить допустимую
основу (публичная спецификация/документация/самостоятельные требования), контракт
atomic-flow, зависимости, сложность и критерии приёмки. Реализация — затем.

## Источники и степень подтверждения

Все ссылки ниже закреплены на одном commit. S означает выборочную статическую
проверку, не запуск и не аудит полной реализации. D/I подтверждают только описание/наличие.

| ID | Источник |
|---|---|
| D01 | [Tools catalog](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/docs/tools.md) |
| D02 | [Commands catalog](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/docs/commands.md) |
| D03 | [Subsystems](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/docs/subsystems.md) |
| D04 | [Bridge](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/docs/bridge.md) |
| D05 | [Architecture](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/docs/architecture.md) |
| I01 | [Migrations directory](https://github.com/777genius/claude-code-source-code-full/tree/021277ac7bad4954e7806e5a9bfc122a533607e2/src/migrations) |
| I02 | [Agent tool directory](https://github.com/777genius/claude-code-source-code-full/tree/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/AgentTool) |
| S01 | [Tool contract](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/Tool.ts) |
| S02 | [QueryEngine](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/QueryEngine.ts) |
| S03 | [Async stream](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/stream.ts) |
| S04 | [Message queue](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/messageQueueManager.ts) |
| S05 | [AskUserQuestion](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx) |
| S06 | [Retry handling](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/api/withRetry.ts) |
| S07 | [Token estimation](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/tokenEstimation.ts) |
| S08 | [Auto compact](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/compact/autoCompact.ts) |
| S09 | [Session-memory compaction](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/compact/sessionMemoryCompact.ts) |
| S10 | [Tool result storage](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/toolResultStorage.ts) |
| S11 | [File state cache](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/fileStateCache.ts) |
| S12 | [Prompt cache diagnostics](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/api/promptCacheBreakDetection.ts) |
| S13 | [Memory selection](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/memdir/findRelevantMemories.ts), [memory scan](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/memdir/memoryScan.ts) |
| S14 | [Grep](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/GrepTool/GrepTool.ts) |
| S15 | [File edit](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/FileEditTool/FileEditTool.ts) |
| S16 | [LSP diagnostic feedback](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/lsp/passiveFeedback.ts) |
| S17 | [Permission loading](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/permissions/permissionsLoader.ts) |
| S18 | [Shadowed rules](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/permissions/shadowedRuleDetection.ts) |
| S19 | [Tool hooks](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/tools/toolHooks.ts) |
| S20 | [Hook schema](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/schemas/hooks.ts) |
| S21 | [Stop hooks](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/query/stopHooks.ts) |
| S22 | [Session reading/index](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/sessionStoragePortable.ts) |
| S23 | [File history](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/utils/fileHistory.ts) |
| S24 | [Task types](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tasks/types.ts), [shell task state](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tasks/LocalShellTask/guards.ts) |
| S25 | [Worktree entry](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/EnterWorktreeTool/EnterWorktreeTool.ts) |
| S26 | [MCP transport/config types](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/services/mcp/types.ts) |
| S27 | [Deferred tool search](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/tools/ToolSearchTool/ToolSearchTool.ts) |
| S28 | [Skill loader](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/skills/loadSkillsDir.ts) |
| S29 | [SDK declarations and stubs](https://github.com/777genius/claude-code-source-code-full/blob/021277ac7bad4954e7806e5a9bfc122a533607e2/src/entrypoints/agentSdkTypes.ts) |
