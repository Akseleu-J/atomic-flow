# Пакет 8: discovery, context, permissions и диагностика

22 октября 2026. Пользователь разрешил продолжить весь оставшийся каталог с тестами.
Активная ветка переименована из `codex/execution-safety-ci` в **experimental**.
GitHub закрыл исходный draft PR №1 при rename; новый draft PR №2 сохраняет описание.
Новые коммиты: author/committer **Omirbay Akseleu**, email из аккаунта `Akseleu-J`;
push выполняется его процессным GitHub token. Старые коммиты не переписаны.

## Реализованный scope

| Кандидат | Реализация и границы |
|---|---|
| 02 | `backend_capabilities`: фактический text-JSON transport, unsupported native/streaming/schema, timeout-only cancellation, явно неизвестные model limits. Это offline описание адаптера, не live probe модели. |
| 10 | `ConversationConfig.compact_at_ratio=0.85`: старые tool bodies сжимаются до достижения hard cap; текущие результаты/инструкции остаются, повторное сжатие того же тела не выполняется. Нет semantic summary. |
| 14 | SHA-256 system/model prefix по роли и причина локального изменения; provider-reported cached input/write usage. Fingerprint не обещает cache hit и не раскрывает prompt. |
| 15 | Точное разбиение serialized request bytes на system/tools, source, tool results, query/state/history и JSON escaping; output reserve отдельно. Не tokenizer breakdown. Причины retrieval selection остаются в существующих artifacts. |
| 20 | `find_files`: POSIX glob, `**` пересекает ноль и более каталогов, ограниченные страницы по отсортированному индексу, hashes помечены indexed. Игнорируемые/private paths не выдаются. |
| 21 | `read_file` start_line/end_line: 1-based inclusive в redacted view; страницы по символам внутри диапазона. Read coverage записывает фактически возвращённые spans всего файла, а не считает кусок полным чтением. |
| 30 | Экспериментальный `read_many`: максимум 8 операций / 4 workers, только фиксированные safe reads и текущая роль; auto-policy preflight всех детей, без callbacks на worker threads. Отдельные контексты и итоговая проверка hash/revision; coverage merge только после проверки размера выдаваемого payload. |
| 32 | `atomic policy explain TOOL --args JSON` и `atomic policy lint`: offline trace всех совпадений, выбранное правило/слой, conservative lint одинаковых predicates. Нет доказательства пересечения произвольных regex/globs. |
| 57 | `AnswerResult.usage` и `usage.json`: вызовы/ошибки, роль, input/output/cache, known cost и unknown cost calls, prefix changes. Task budget events сохраняют cache/cost source. Нет сведений о скрытых provider retries; configured estimates не выдаются за invoice. |
| 58 | Расширенный doctor: место на диске, metadata-проверка доступа к state, наличие Docker CLI, незакрытые holds, повреждённые Task JSON. `--json` строго offline, не запускает daemon/image/model. До 100 Task файлов, ledger до 16 MiB. |
| 59 | `atomic config explain`: masked effective values из того же resolver, источники leaf values, provider defaults/env, отброшенные untrusted keys, наличие credential env без значений, capabilities. |
| 61 | `experimental.parallel_reads=false`: настоящий opt-in для регистрации read_many; недоверенный project config не может включить функцию. Обычный CLI не импортирует worker module до opt-in. |

Исправлен double counting у proxy, который возвращает одну стоимость nanodollars
одновременно во вложенном `usage.cost` и на верхнем уровне usage: nested field wins.
Anthropic input count включает отдельно возвращённые cache reads/writes; стоимость
по общей configured input price остаётся оценкой без cache-specific pricing.
Платные модели не вызывались: transport/usage изменения проверяются mock responses.

## Использование

```sh
atomic policy explain read_file --args '{"path":"src/example.py"}'
atomic policy lint
atomic config explain
atomic doctor --json
atomic config set experimental.parallel_reads true
```

В tool protocol `read_file` принимает `start_line`, `end_line` и relative `offset`
внутри выбранного диапазона. Следовать `next_offset`, сохраняя тот же диапазон.
Read coverage может накопиться из нескольких диапазонов одной redacted ревизии;
stale hash не переносит покрытие на новую ревизию.

`read_many` не допускает edit/execute/ask/recursion. Каждый child проходит dispatcher
с собственной записью роли/policy, но одинаковой исходной ревизией. Политика ask/forbid
любого child отклоняет batch до чтений; неизвестный/неразрешённый child тоже.
Ошибка одного чтения делает весь outer result ошибочным; успешные возвращённые
reads могут сохранить coverage. При oversized payload coverage не сохраняется.
Это потоковый параллелизм доверенных встроенных reads, не изоляция стороннего Python-кода.

## Проверки

Новые тесты: `test_runtime_discovery.py`, `test_context_usage_batch.py`.
Проверяются containment/ignores, Unicode, glob semantics, partial/full/stale reads,
ошибки policy, маскирование и origins, corrupt state/holds, proactive omission,
byte partition, cache usage и дублированная цена, неизвестные расходы, параллельные
разрешения/child audit, переполнение payload без ложного read coverage и opt-in trust gate.
Общий Windows suite: **334 passed, 13 skipped**; `validation/step8-windows-final.txt`.
Первый общий прогон выявил несовместимость нового keyword у factory с существующим
injection-тестом: default path снова вызывает `default_registry()` без kwargs,
opt-in path передаёт flag. Полный повтор PASS. Changed-code Ruff/compileall/diff PASS.
Новая `.github/workflows/checks.yml` включает push на `experimental`.

Актуальный статус всех 62 кандидатов — `docs/CANDIDATE_STATUS.json`: 24 scoped
implementations в пакетах 7/8, 38 запланированы. Это tracking требований, не заявление
о production готовности. Источники cache usage: публичные API-контракты
[Chat Completions usage](https://platform.openai.com/docs/api-reference/chat/object)
и [Anthropic input/cache accounting](https://docs.anthropic.com/en/docs/about-claude/pricing).

Наличие кода/тестов не означает, что все остальные кандидаты закрыты. Живой QA-пилот
до этого пакета остаётся отдельным измерением; новые функции не проходили live task evaluation.
