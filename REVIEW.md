# REVIEW: atomic_flow 0.1 → 0.2

Формат: **что слабо → где (файл) → почему это важно → как исправлено в 0.2**. Приоритеты: P0 — ломает работу или безопасность,
P1 — заметно портит качество ответов, P2 — надёжность/эксплуатация, P3 — гигиена.

## P0 — сломано или опасно

| # | Где | Проблема | Исправление в 0.2 |
|---|---|---|---|
| 1 | `tests/test_smoke.py`, `core/*`, `activation/zoom.py` | Мёртвый слой v0.0: импортирует `Settings`, `EncoderConfig`, `ActivationConfig`, `NodeKind`, которых нет в `config.py`/`node.py`; `build_tree` возвращает dict, а тест ждёт `.nodes`. Тесты падают при импорте, `import atomic_flow.core` падает. Единственный тест проекта не запускается. | Удалены; написаны 37 рабочих тестов без сети и ключей (`ScriptedBackend`). |
| 2 | `config.py::load_config` | **Проект может перенаправить ваш ключ.** `.atomic_flow/config.yaml` из клонированного репозитория переопределяет `backends.*.base_url`; ключ из `ATOMIC_FLOW_API_KEY` уйдёт на чужой сервер вместе с исходниками. | Trust-gate: `backends`/`default_backend` из проекта игнорируются до `atomic trust`. `base_url` валидируется (`file://` запрещён). |
| 3 | `api/server.py::chat` | `acknowledge_default_prompts=(mode != "hard")` → модель `atomic-hard` через `/v1/chat/completions` **всегда** отвечает 409. | Выбор `atomic-hard` = подтверждение. |
| 4 | `api/server.py` | `hmac.compare_digest(str, str)` с не-ASCII ключом → TypeError → HTTP 500. Плейсхолдер `replace-with-a-long-random-value` из `.env.example` принимался как ключ. | Сравнение по bytes; старт отклоняет ключ <16 символов и плейсхолдеры. |
| 5 | `tree/builder.py` + `activate._source_for` | **Устаревший индекс молча отдаёт не тот код.** Хранятся номера строк, но не хэши файлов; после правки файла срезы `lines[start:end]` указывают на чужие строки. `tree status` сравнивал только id узлов и не видел правок тела функции. | `content_hash/size/mtime_ns`; инкрементальный `refresh_tree`; `ask` обновляет индекс сам; `materialize` отказывается читать файл, чей хэш не совпал. |
| 6 | `builder.py` | `str.splitlines()` делит по `\x0c`, `\x1c`, `\u2028`…, а `ast` — нет → сдвиг всех номеров строк после такого символа. | `split_lines()` как у токенайзера Python. |
| 7 | `builder.py::_visit_definition` | Дубликаты `node_id`: `@property` getter/setter, `@overload`, условные переопределения затирают друг друга (`by_id`, `target_ids`). | Суффикс `#2`. |
| 8 | `router.py` + `config.py` | Все бэкенды получали дефолты OpenAI: блок `anthropic:` без `base_url` слал запросы на `api.openai.com/v1/messages`. Anthropic ключ не читался из env. | `BACKEND_DEFAULTS` по типу, `type:` для произвольных имён (ollama), `api_key_env`, `ANTHROPIC_API_KEY`. |
| 9 | `cli.py config get` | Маскировались только ключи, оканчивающиеся на `api_key`: `config get backends` печатал секрет целиком. `config set` писал в YAML *до* валидации (мусор оставался в файле), файл с ключом создавался с правами 644. | `mask_secrets` рекурсивно; validate → atomic write → chmod 600. |
| 10 | `kaggle/credentials.py` | Файл с Model Proxy-ключом создаёт Kaggle CLI с umask по умолчанию, `chmod 600` — после (окно гонки). `SECRET_FILE` вычислялся при импорте и игнорировал `ATOMIC_FLOW_HOME`. | `prepare_secret_file()` до запуска CLI; `secret_file()` лениво. |

## P1 — качество поиска и ответов (главные рычаги)

| # | Где | Проблема | Исправление |
|---|---|---|---|
| 11 | `activate.py::terms` | **Идентификаторы не разбиваются.** Запрос «refundable» не находит `is_refundable`; `snake_case`/`camelCase` — один токен. Это главная причина промахов BM25 по коду. Нет стоп-слов и стемминга. | `activation/text.py`: split по `_`/camelCase, стемминг (refund/refunds/refundable), стоп-слова; целое имя + части. |
| 12 | `activate.py` приоры | `t in node.path.lower()` — **подстрока**: стоп-слова `is/or/an/to` буст ×1.35 любому пути, где встречаются эти буквы. | Сравнение целых токенов. |
| 13 | `activate.py` | Нормировка `score / max` → лучший узел всегда 1.0 (zoom 3), даже если запрос ни с чем не совпал; «слабый поиск» неотличим от «сильного». | `SearchResult.weak` (доля термов запроса в топ-10), передаётся в промпт; в simple при weak — вызов Parser. |
| 14 | `activate.py` | Индекс (токенизация всех узлов, `term in list`) пересобирался на **каждый запрос**: O(Q·N·L). | Инвертированный индекс, один раз на дерево, LRU-кэш по `fingerprint`. |
| 15 | `activate.py` | `calls` — голые имена; `fan_in` по имени (`get`, `run`, `append` «самые важные»); `fan_in/out` нигде не использовались; `calls` (`append`, `join`) загрязняли BM25. | Разрешение вызовов в рёбра (тот же файл → импортируемые файлы → уникальное имя); spreading activation по рёбрам; `refund_message` тянет `is_refundable`. |
| 16 | `materialize` | `break` при первом узле, не влезшем в бюджет, обрывал весь контекст; класс при zoom 3 дублировался своими методами; python-файл попадал и как `file`, и как `module`; файл читался N раз; скелет обрезался с **хвоста** (последние файлы пропадали); в `--dry-run` скелет строился иначе, чем в реальном запуске. | Деградация zoom до влезающего; пропуск ребёнка внутри уже показанного родителя; нет `module`-узлов; кэш файлов; скелет с раскрытием релевантных файлов; единая логика. |
| 17 | `orchestrator.py` | Контекст шёл **внутри JSON-строки** (`\n`, `\"` экранированы): +токены, хуже читаемость. `node_id` вида `sha1:…:path::symbol` (~80 символов) — дорого и модель часто искажает. | Код — сырым текстом в `<project_context>`; короткие id `n12` с устойчивой обратной резолюцией. |
| 18 | `orchestrator.py` hard | `reparser` менял `intent`, но кандидаты **не пересчитывались**; `expander` мог выбрать узел вне top-80 — он молча не материализовался, а попытка тратилась; при пустом `fresh` цикл делал `break` вместо повтора с фидбеком; refiner-выбранные узлы с низким лексическим скором получали zoom 0 (одна сигнатура). | Повторный `search` после reparse; выбранные узлы рендерятся всегда, zoom ≥ 2; расширение пула соседями по графу; повтор с критикой даже без новых узлов. |
| 19 | `orchestrator.py` | Критик — **та же модель**, `citation_coverage` — её самооценка; «verifiable citations» в README — преувеличение. Любой невалидный JSON убивал уже оплаченный прогон. | `verify.py`: бэктик-идентификаторы, которых нет в контексте, снижают `grounding` и отменяют `ok`; один раунд JSON-repair на вызов; нестрогие схемы вывода LLM (`extra="ignore"`, enum-coercion). |
| 20 | `orchestrator.py` | `file_scope.lstrip("./")` съедает ведущие `.`: `.github/...` → `github/...`. Для запроса на русском поиск BM25 всегда пуст (нет общих токенов с кодом). | `normalize_scope`; Parser переводит термы в английские идентификаторы, simple-режим вызывает его только если поиск пуст. |
| 21 | `server.py::chat` | Retrieval-запрос = вся история чата с префиксами `USER:`/`ASSISTANT:` → BM25 тонет в шуме. | Поиск только по последнему user-сообщению, история — в тексте вопроса. |

## P2 — надёжность и эксплуатация

* `budget.daily_limit_usd` нигде не применялся → `budget.py` (локальный ledger.jsonl, `BudgetExceeded`); цена по токенам (`price_*_per_mtok`) даёт стоимость там, где провайдер её не отдаёт. `cost_usd=None` из-за одного вызова без цены — теперь нижняя оценка + `cost_complete`.
* `openai_compatible.py`: `"payload" not in locals()`, температура 0.1 жёстко (ломает reasoning-модели), нет `max_tokens`; `anthropic.py`: без ретраев, пустая `system` → ошибка API. → общий `backends/http.py` (backoff + jitter + Retry-After), `temperature: null`, `max_output_tokens`, `truncated`.
* Артефакты `~/.atomic_flow/runs/` содержат исходники, не чистятся; у проваленных запусков нет следов → `artifacts.save/keep_days`, `atomic runs prune`, `error.json`.
* `request_id` = имя каталога без проверки (path traversal при публичном использовании) → regex.
* `save_tree`/`write_config` не атомарны → tmp + replace.
* Benchmark: подстрока `"cent"` матчит «percent»; `citations_valid` всегда истинно (ответ уже отфильтрован); 4 кейса на одной крошечной фикстуре, нет негативных/многошаговых; метрика retrieval смешана с метрикой генерации; одна ошибка авторизации давала 4 одинаковых «провала»; нет повторов (LLM недетерминирован). Фикстуры и `cases.json` дублировались в двух местах. → `atomic benchmark retrieval` (бесплатно: hit@k/MRR), 9 кейсов с `expected_nodes`, регэкспы с границами слов, кейс на отказ отвечать, `--repeats`, fail-fast на `ConfigurationError`, один источник фикстур.
* Kaggle-задачи: в `refund_window.py` регэксп `r"(?i)0"` совпадает с «0» внутри «30» — проверка нижней границы **всегда проходила**. Исправлено; в остальных задачах усилены проверки. (Замечание: они тестируют модель на встроенном коде, а не atomic_flow.)
* CLI: `main()` на 300 строк → handlers; `--edit-prompts` по докам «открывает и выходит», в коде продолжал запрос; `except KeyError` маскировал баги → `--debug`.
* `/v1/*`: нет лимита параллелизма → `BoundedSemaphore`, 429; `created: 0`; нет `/v1/models` (OpenAI-клиенты его зовут).

## P3 — гигиена

* `poetry.lock` не соответствует `pyproject.toml` (build-backend setuptools, в lock — typer/rich/pydantic-settings/pathspec как main-зависимости, extras только `dev`/`encoder`, без `api/kaggle/benchmarks`). **Удалите или перегенерируйте**; в архив он не включён.
* `BM25FallbackEncoder` использовал `hash(str)` — зависит от `PYTHONHASHSEED`, «детерминированность» из докстринга ложна (модуль удалён; при возврате — `zlib.crc32`).
* Пустой раздел `## Current scope` в architecture.md; фраза «Publish it with» про race_condition, но команда для invoice-subtotal.
* `.gitignore`: добавлены `.mypy_cache`, `kaggle.env`, `.atomic_flow/tree.json`; `.env.example` обещал `.env`, но ничего его не читает → пояснено.
* Нет `[tool.pytest]`, `[tool.ruff]`, `[tool.mypy]`, лицензии/`urls` в `pyproject.toml`.

## Что осталось слабым (честно)

1. BM25 не знает семантики: «как обновляется сессия» ≠ `refresh_token`. Следующий шаг — dense-эмбеддинги (ваш `atomic-core` encoder) + rank fusion (RRF) с BM25; точка встраивания — `ActivationIndex.search`. Измеряйте `atomic benchmark retrieval` до/после.
2. Разбор только Python; для TS/Go/Rust нужен tree-sitter.
3. `grounding` проверяет только идентификаторы в бэктиках; нет проверки утверждений о значениях (числа, условия). Идея: требовать от Answerer `quote` строки кода и сверять её с исходником.
4. Эвристики (`weak < 0.2`, веса полей, 0.55/0.40/0.35 spreading) подобраны на 9 кейсах — расширяйте набор перед тем, как им доверять.
5. Локальный бюджет не защитит, если провайдер не отдаёт стоимость и цены не заданы.
