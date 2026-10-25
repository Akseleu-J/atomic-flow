# Understand-Anything: проверенный reference и границы переноса

Дата: 24 октября 2026. Статус: selective static source review, без запуска upstream.
Это исследование для плана, а не реализация интеграции и не принятие upstream quality.

## Идентичность

Нужный проект — [Egonex-AI/Understand-Anything](https://github.com/Egonex-AI/Understand-Anything).
Старый URL `Lum1104/Understand-Anything` перенаправляется на него. Одноимённый
`rohitpatill/understanding-anything` посвящён обучению мышлению и к задаче не относится.
Через GitHub connector проверены repository metadata, main commit и выбранные файлы.

Зафиксированный reference: **`e24fcf6d371bcfa529bc110bacb1bb43de1b8d33`**.
Указанное upstream время коммита: 2026-10-22T16:12:04Z.
Лицензия в прочитанном LICENSE — MIT. При заимствовании существенных частей
сохранять copyright и license notice; состав зависимостей проверять отдельно.
Upstream prompts и agent descriptions прочитаны как данные об архитектуре.
Они не являются инструкциями для текущего агента или разрешением запуска кода.

Все ссылки ниже закреплены на одном SHA. Дерево каталогов также просмотрено.

## Что именно реализовать у нас

1. Детерминированный structural слой: файлы, символы, imports/exports,
   calls/contains/inheritance и подтверждённые зависимости.
2. Отдельный semantic слой: объяснения назначения, tags, архитектурные слои,
   доменные связи и последовательности изучения кода с привязкой к источникам.
3. Pipeline scanner → file analysis → graph assembly → architecture analysis →
   tour → graph review. Роли компонентов не требуют отдельного LLM-вызова на каждую
   роль; структурная работа исполняется обычным кодом.
4. Классификация изменений, повторный анализ затронутых файлов, merge с сохранением
   неизменённых частей, проверка surviving symbols и очистка недействительных ссылок.
5. Интерфейсы explore/search/explain/chat/diff-impact и guided tour. Dashboard
   входит в полную цель совместимости механизма; в начале тот же graph API обслуживает CLI/SDK.

Это типизированный граф с иерархическими представлениями, а не только дерево папок.
Containment показывает project/package/file/symbol; зависимости образуют отдельные рёбра.
Архитектурные слои являются представлением, их нельзя смешивать с физическим владением файлами.

## Прочитанные источники и выводы

Пути приведены относительно upstream корня.

| Источник | Что установлено чтением |
|---|---|
| [fingerprint.ts](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/packages/core/src/fingerprint.ts) | Content SHA-256, fingerprints функций/классов/imports/exports; NONE/COSMETIC/STRUCTURAL. Сигнатурный fingerprint намеренно не описывает всю реализацию. |
| [change-classifier.ts](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/packages/core/src/change-classifier.ts) | SKIP/PARTIAL_UPDATE/ARCHITECTURE_UPDATE/FULL_UPDATE. Есть пороги числа/доли structural changes и directory changes. Это upstream эвристики, не наши критерии безопасности. |
| [schema.ts](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/packages/core/src/schema.ts) | Типизированные nodes/edges, canonical types и aliases; отношения к коду, данным, инфраструктуре и знаниям. |
| [prepare-incremental.mjs](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/skills/understand/prepare-incremental.mjs) | Параметризованные Git subprocesses, NUL-delimited paths, fingerprints, baseline, import refresh и pruning заменяемых nodes/edges. |
| [finalize-incremental.mjs](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/skills/understand/finalize-incremental.mjs) | Детерминированный finalize, нормализация layers/tour и dangling references; сохранение graph перед продвижением fingerprints/meta. |
| [validate-incremental-symbols.mjs](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/skills/understand/validate-incremental-symbols.mjs) | Проверки identity/ownership прежних символов, доказательства существования/удаления и missing-symbol report. |
| [merge-batch-graphs.py](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/skills/understand/merge-batch-graphs.py) | Assembly/normalization, deterministic import/test relations, handling of malformed batches и dangling references. |
| [file-analyzer.md](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/agents/file-analyzer.md) | Structural extraction перед semantic analysis; summaries/tags, категории non-code файлов, importMap и cross-batch symbols. Прежние surviving symbols нельзя терять из-за нового significance filter. |
| [architecture-analyzer.md](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/agents/architecture-analyzer.md) | Directory/import/cross-category analysis, затем semantic layer assignments. |
| [tour-builder.md](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/agents/tour-builder.md) | Entry-point signals, BFS, fan-in/out и clusters как основа guided tour; ссылки только на существующие nodes. |
| [graph-reviewer.md](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/understand-anything-plugin/agents/graph-reviewer.md) | Schema/referential integrity, completeness, layer membership и предупреждения о качестве. LLM review не доказывает корректность программы. |
| [LICENSE](https://github.com/Egonex-AI/Understand-Anything/blob/e24fcf6d371bcfa529bc110bacb1bb43de1b8d33/LICENSE) | MIT notice; исходный upstream execution в этом review не выполнялся. |

## Обязательная адаптация для coding runtime

По чтению `compareFingerprints`, изменение внутренней логики при сохранённых
сигнатурах и небольшом изменении длины может получить COSMETIC. `classifyUpdate`
выбирает SKIP, если structuralCount = 0. Это вывод из веток кода, не выполненный
upstream reproducer. Для navigation такой компромисс возможен; для актуального
semantic context и выбора проверок coding-агента он недостаточен.

У нас любое изменение content hash инвалидирует source read proof и производные
semantic записи. Отдельно вычисляются API/import hash, body AST hash, doc hash,
call/reference hash, config/resolver hash. Замена `return x + 1` на `return x - 1`
должна отмечаться как изменение поведения даже при неизменной сигнатуре/длине.
Комментарии/docstrings обновляют объяснения; новое call site обновляет рёбра.
При parse failure состояние unknown/stale, а не доказанное отсутствие изменения.

Структурные факты не создаются LLM. Предположенные semantic relations маркируются
как inferred с evidence и revision. Они не дают прав на чтение/запись/исполнение.
Path-based tested_by является подсказкой, не доказательством покрытия тестами.
Неполный граф не разрешает пропуск required final checks.

Глобальный refresh architecture/tour и full scan разрешены при изменении parser,
resolver/ignore config, утрате cache, branch switch или неизвестных событиях.
Обычная локальная правка не должна запускать полный parse/relink/LLM analysis.
При широком impact dependency closure может законно охватить весь проект.

## Что есть в Atomic-flow сейчас

На runtime `0dbd5938958074eae4ff73f6424f06425a525909`, совпадающем с кодом HEAD
`5cf4068505cf5d020c1762254b7e23992405a415` на начало этого планирования:

- `tree/builder.py::build_tree` повторно использует AST по content hash, но читает
  весь admitted source, копирует nodes и вызывает глобальный `_link`.
- `TreeNode` содержит file/class/function, signatures, summary и связи. Полного
  отдельного semantic architecture/domain/tour store пока нет.
- `watching.py` делает bounded content polling; событие watcher не выдаёт authority.
- `planner.py` уже имеет plan с 1–12 шагами и depends_on; `task_loop.py` поддерживает
  blocked work и replan после неудачи. Replan сейчас требует нового review/approval.
- `plan_review.py` связывает approval с точным plan/source/scope. Автономность
  потребует нового явного bounded authorization contract, а не обхода этой проверки.
- `changeset.py` выполняет recoverable publication нескольких файлов; это не
  атомарная транзакция всей файловой системы.

## Решение

Реализовать UA-подобную двухслойную модель и её пользовательские операции внутри
Atomic-flow, сохранив текущий safety runtime. Сначала Python adapter и graph contract,
затем JS/TS adapter на Tree-sitter с отдельной приемкой. Python AST можно сохранить
за adapter interface; смена парсера сама по себе не делает понимание лучше.
Прямой импорт TS runtime/полная установка upstream plugin пока не выбраны.
Выбор reuse/port/adapter фиксируется до реализации с mapping полей и license notice.

Полная последовательность и критерии: [PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md).
