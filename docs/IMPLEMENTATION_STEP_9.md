# Пакет 9: native tool transport с text-JSON fallback

22 октября 2026. Независимая реализация кандидата **01** по публичному
[Chat Completions function calling](https://platform.openai.com/docs/guides/function-calling)
контракту, без переноса кода/промптов внешнего зеркала.

Для включения требуются оба host/trusted-config параметра:

```yaml
experimental:
  native_tools: true
backends:
  openai-compatible:
    tool_protocol: native
```

По умолчанию transport прежний `text_json`. Kaggle явно остаётся на fallback;
Anthropic adapter пока не поддерживает native tools. Флаг означает согласие
пользователя использовать протокол на своей модели, а не обнаружение её возможностей.
Внешних/платных model calls при реализации и тестах не было.

## Контракт и интеграция

- `NativeToolCall` содержит provider ID, имя и ограниченный JSON-текст аргументов.
  На native API передаются role-allowed function schemas; `parallel_tool_calls=false`.
  Поддержан **один native call на ответ**. Несколько вызовов/неизвестные типы
  отклоняются, а Task accounting оставляет расход неизвестным и блокирует replay.
- Внутренний canonical loop использует прежнюю validated tool representation.
  Host metadata связывает реальный assistant tool call с конкретным tool result ID.
  Текст ответа модели не может сам создать это metadata. Непарные/повторённые ID
  отклоняются, повтор ID проверяется до следующего исполнения инструмента.
- Native и text calls проходят **тот же dispatcher**: роли, args, edit opt-in,
  layered policy/approvals, read coverage, hashes, verification и journal.
  Изменение transport не расширяет filesystem/execute разрешения.
- Если call отложен для загрузки scoped instructions, wire получает явный
  denied/non-execution result перед новым user message. Некорректные args
  получают repair без исполнения и сохраняют расход уже полученного ответа.
- RequestHistory проверяет уже отрендеренные native messages и schemas.
  TaskBudgetBackend учитывает serialized messages+schemas в reservation,
  сохраняет успешную usage и оставляет неизвестные расходы при transport failure.
- Journals сохраняют native IDs в model_response/tool result; `provider_call_id`
  доступен в audit. Prefix diagnostic включает schemas. Cache/backend descriptors
  не обещают поддержку remote model или фактический cache hit.

## Проверка и ограничения

`tests/test_native_transport.py`: реальные Orchestrator/dispatcher + scripted HTTP,
native round-trip ID pairing, edit denial, duplicate ID, default fallback,
schema admission, durable spend/unknown failure, host-only pairing, malformed args,
Kaggle refusal. Полный Windows suite: **344 passed, 13 skipped**;
`validation/step9-windows.txt`. Changed-code Ruff, compileall PASS.
Нет live provider native acceptance или измерения task quality этого transport.
Native token streaming, несколько native calls на ответ и сетевой cancel — отдельные
кандидаты, этим пакетом не объявляются реализованными.
