# Пакет 26 — повторное использование индекса и пределы workspace

24 октября 2026. Основание: живой development pilot на `445f1b9` сообщил
дорогую индексацию SymPy и отказ Windows при глубоком `ATOMIC_FLOW_HOME`.
Число около 179 секунд — историческое измерение того пилота, а не новый
performance benchmark этого пакета.

## Что исправлено

`TaskRunner` больше не начинает каждый resume с холодного разбора всех файлов.
Существующий индекс staged workspace используется как parse cache. При первом
создании workspace cache можно взять из исходного проекта: все пути относительные,
а повторное использование конкретного файла разрешено только после чтения и
сравнения хэша его текущих байтов. Размер и mtime не заменяют хэш. Граф связей
перестраивается из текущего набора файлов. Cache commit сохраняет generation CAS;
операция держит source mutation lease и task lease. Неизменный cache не записывается
повторно. Индекс не выдаёт read proof или разрешение на изменение исходников.

Индекс staged workspace и индекс опубликованного исходного проекта нужны оба.
Финальную проверку исходников после публикации нельзя просто удалить как
«дублирующую». Этот пакет сокращает повторный AST-разбор; обход и чтение всех
допущенных файлов, перестроение графа и проверка publication остаются.

По ходу проверки найден отдельный дефект существующего incremental index:
`reference_ids` заменялись на resolved node IDs, а следующий refresh пытался
повторно разрешить эти IDs как исходные имена. Callback/wiring edges терялись
на тёплом проходе. Теперь сырые имена сохраняются отдельно в `reference_names`;
`reference_ids` пересчитываются заново. Индексы без маркера `reference_format =
names-v1` один раз перепарсиваются и сохраняют новый формат, даже если исходники
не изменились. Проверены холодный/тёплый parity и исчезновение ранее уникальной
связи после добавления неоднозначного определения.

Forward index builds TaskRunner теперь получают callback отмены задачи;
проверка выполняется также при обходе Python AST и при linking. Проверки графа
и cached records происходят сразу и затем не реже одного раза на 128 операций;
AST visitor проверяет каждые 256 visits. Это не превращает одну большую
индексацию в сотни тысяч обращений к файлу отмены. Recovery index refresh
сохраняет прежнее правило: отмена forward work не должна прерывать восстановление
checkpoint. Callback последующих forward операций остаётся действующим.

## Пути Windows и host limits

Добавлен optional host-only `ProjectLimits.max_workspace_path_chars` и
`TaskRunner(..., resource_limits=ProjectLimits(...))`. Предел сохраняется вместе
с changeset. До создания каталога snapshot проверяются известные пути workspace,
более длинные checkpoint paths и запас под atomic replacement suffix.
Единица — UTF-16 code unit, пригодная для выбранной Windows toolchain.
Например, host может задать legacy envelope 259, если такой предел действительно
нужен его Git/analyzer/executor:

```python
from atomic_flow.agents.task_loop import TaskRunner
from atomic_flow.project_resources import ProjectLimits

runner = TaskRunner(config, project, backend,
                    resource_limits=ProjectLimits(max_workspace_path_chars=259))
```

Значение по умолчанию — `None`: Python и отдельные инструменты могут поддерживать
extended paths, поэтому универсальный запрет после 259 символов не вводится.
Настоящий Windows path error получает подсказку использовать более короткий
`ATOMIC_FLOW_HOME` и настроить предел для проверенной toolchain. Опция не
перемещает app state автоматически, не расширяет filesystem authority и не
гарантирует поддержку длинных путей произвольными LSP/Git/worker программами.
Новые пути, не существовавшие при snapshot, также требуют обычной host scope
approval и остальных проверок; этот preflight не является резервированием путей.

## Проверка и оставшиеся gates

Первый focused run: **30 passed**, `validation/import-20261024/step26-focused.txt`.
Он покрывает cold/warm references, invalidation при одинаковых размере/mtime,
полный scripted TaskRunner journey без нового AST-разбора неизменного source,
cancellation/recovery, path preflight и соседние snapshot/capability tests.
После добавления host-конструктора и ограничения частоты cancel callbacks
final recheck: **34 passed**, `validation/import-20261024/step26-final.txt`.
Ruff PASS: `step26-final-ruff.txt`. Числа initial/final пересекаются и не
складываются как независимые проверки.

Independent review дополнительно потребовал отказ от частичного reuse повреждённых
cached records (одна неверная дочерняя запись теперь инвалидирует весь файл)
и соблюдение `ProjectLimits.max_file_bytes` индексатором. Совпадающий cache не
обходит этот host limit. Старый аргумент source-selection `max_file_bytes`
по-прежнему может отфильтровать большой файл, но не ослабляет host envelope.
Отремонтированный cache сохраняется также при неизменном source fingerprint:
проверяется равенство canonical node records, поэтому следующий Orchestrator
не загрузит прежнюю повреждённую дочернюю запись.
Отдельные regressions и review recheck сохранены в `step26-review.txt`.
Итог после всех трёх independent review замечаний: **21 passed** в
`step26-review-final.txt`, Ruff PASS в `step26-review-final-ruff.txt`.
Эти focused sets пересекаются с предыдущими и не суммируются.

Для одного воспроизводимого измерения реального pinned checkout добавлен
`tools/index_reuse_probe.py`. Он не скачивает, не импортирует и не исполняет
код измеряемого проекта, проверяет SHA/чистоту source, читает исходники на обоих
проходах и сохраняет только JSON metrics. Один sample cold/warm не даёт p95.
Подготовить checkout из уже существующего локального cache и измерить его можно так:

```powershell
git -c core.autocrlf=false clone --no-checkout --no-hardlinks .local-tools/stage4-sympy .local-tools/step26-sympy
git -c core.autocrlf=false -c core.longpaths=true -C .local-tools/step26-sympy checkout --detach b4777fdcef467b7132c055f8ac2c9a5059e6a145
.venv/Scripts/python.exe tools/index_reuse_probe.py --project .local-tools/step26-sympy --expected-commit b4777fdcef467b7132c055f8ac2c9a5059e6a145 --max-wall-seconds 160 --output validation/import-20261024/step26-sympy-index.json
```

Требуется свежий output path вне измеряемого checkout. Deadline кооперативный;
Git operations имеют отдельный timeout, жёсткая OS kill-гарантия не заявляется.
Source checkout в validation копировать не следует.

Это не закрывает W7 целиком. Не измерены реальные p95/RSS/queue latency,
длительный soak и поддерживаемый максимум нескольких репозиториев. Journal append
по-прежнему перепроверяет весь защищённый prefix; streaming read/recovery пакета
22 не устраняет квадратичную сумму чтений. AST parsing одной ограниченной по
размеру строки/файла, сериализация JSON и отдельные filesystem/OS calls остаются
кооперативно отменяемыми между границами; обещания жёсткого wall-clock kill нет.
Ни качество моделей, ни весь Stage 4, ни готовность release candidate этим
пакетом не заявляются.
