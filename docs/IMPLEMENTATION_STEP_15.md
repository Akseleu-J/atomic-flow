# Пакет 15 — интеграции, расширения и документация

23 октября 2026. Пункты **36, 48, 50, 51, 52, 54, 55, 60** реализованы в
описанных ниже границах. Код написан независимо; исходники и prompts внешнего
зеркала не импортировались. Совместно с пакетом 16 каталог достигает
**62 implemented_scoped / 0 planned**. Это статус ограниченных реализаций,
а не feature parity, production acceptance или результат live edit evaluation.

## MCP client/server — 48, 50

`integrations/mcp.py`: stdio JSON-RPC MCP **2025-06-18**, явное разрешение
абсолютного host executable. Чистое окружение, отдельный временный HOME/cwd,
bounded frames/queues/notifications, timeout/cancellation и закрытие процесса.
Сервер — доверенный host code, не sandbox. Никакого запуска executable из
model output, HTTP request или repository discovery.

`MCPClient(project, command, approved=True)` / SDK `open_mcp(...)`:

- `list_tools()` получает до 128 descriptors; pagination отклоняется явно.
- `allowed_tools={name: schema_hash(descriptor)}` закрепляет имя и input schema.
  Серверная schema проверяется перед каждым вызовом, включая уже загруженный tool.
  Host hash вычисляется до redaction и заменяет одноимённое поле сервера.
- `call_tool()` / `read_resource()` требуют отдельный явный grant и host approval
  конкретной операции. Remote strings redacted; results только data, без permissions,
  read proof, checks или подтверждения выполненной записи.
- `register_mcp_tool()` — lazy host alias с собственной Pydantic schema и
  ToolContract; обычные dispatcher role/policy/approval gates сохраняются.
  Default effect — execute: `readOnlyHint` сервера не ослабляет policy.
- Неопределённый исход не повторяется автоматически. Sampling/prompts, HTTP MCP,
  управление секретами внешнего сервера и автоматическое повышение прав не включены.

`serve_mcp(project)` / `atomic mcp serve --project DIR` предоставляет offline
source tree/read/resources. Private/ignored/link paths отклоняются; source bounded,
redacted и hash-checked. Запись, shell и model calls сервер не экспортирует.
`atomic mcp tools --project DIR --command-json '[ABSOLUTE_EXE,...]' --yes`
предназначен для явного discovery доверенного сервера.

Протокол сверен с официальными
[transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports),
[lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle),
[tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
Проверки используют настоящий локальный stdio process с синтетическим сервером;
совместимость со всеми внешними MCP servers не измерялась.

## Hooks, workflows, plugins — 36, 51, 52

`hooks.HookManager`: до 32 явно разрешённых callbacks на before_tool/after_tool/
run_start/run_end/run_error. Immutable JSON observations до 32 KiB; before hook
может veto, но не заменить args. Callback не получает ToolContext или registry.
Host callbacks доверенные: cooperative deadline проверяется после возврата,
принудительно остановить зависший Python callback этот интерфейс не может.

Dispatcher вызывает before hook после разрешения role/schema/scope/policy и
до lazy factory; after hook получает summary, без source body. Ошибка after hook
сохраняет возможные эффекты и возвращает error; отмена остаётся отменой и не
оставляет новый read proof. Не повторять запись из-за failed hook. Lifecycle hooks
подключены в Orchestrator и SDK; HTTP request не регистрирует callback.

`workflows.Workflow` и SDK `execute_workflow(...)`: versioned статический manifest
до 32 calls/64 KiB, явный digest и approval, предварительная проверка всех
steps/schema/role contracts. Вызовы идут через обычный dispatcher; его policy может
отклонить действие. Нет template interpolation, eval/shell, model planning или retry.
До действия journal сохраняет intent; после — result. Error/cancel останавливает
цепочку, сохраняет progress/effect uncertainty и не replay выполненные steps.
Большие args заменены SHA-256 в отчёте; summaries bounded/truncated явно.

`extensions.PluginManifest` / `PluginManager`: manifest version/id/release/
source SHA-256/capabilities/tool contracts/schema, до 128 KiB. Approval закрепляет
digest. Host сам предоставляет доверенные Python factories и `source_check`;
source hash, metadata и revocation проверяются при загрузке и каждом cached call.
Manifest не содержит исполняемый import path, не устанавливает package и не делает
произвольный Python безопасным. Marketplace, auto-update и auto-download не включены.

`atomic plugin MANIFEST` / `atomic workflow MANIFEST` проверяют manifest и выводят
digest **без исполнения**. Исполнение — явный SDK host operation. `start_answer`
принимает host-owned `hooks`, `tool_registry`, `role_tools`; новые grants никогда
не читаются из содержимого модели или remote tool descriptions.

## IDE bridge — 54

SDK `ide_bridge(project, token=..., allowed_origins=[...])` возвращает unstarted
HTTPServer; host владеет lifecycle. CLI `atomic ide --project DIR --origin ORIGIN`
берёт token из process environment `ATOMIC_FLOW_IDE_TOKEN` (или `--token-env`).
Token не выводится. Только IPv4 loopback `127.0.0.1`; Bearer >=32 ASCII chars,
обязательный exact Origin, project SHA-256 и instance nonce; authorization до source
access. Duplicate headers, oversized JSON/response, foreign project/nonce и unknown
endpoints отклоняются; request socket имеет deadline, access logging выключен.

POST `/v1/status` и `/v1/read` — authenticated read-only source inspection.
HTTP writes, WebSocket UI, готовое расширение редактора и remote binding отсутствуют.
Сервер однопоточный; это bounded local bridge, не публичный web service. Nonce/project
ID не заменяют bearer token; клиенты должны получить их от доверенного host lifecycle.

## HTTPS documentation — 55

`documentation.DocumentationClient` / SDK `documentation(hosts, approved=True)`:
до 16 exact host grants, только HTTPS:443 без ambient proxies/auth/cookies;
credential query keys, embedded credentials, fragments и controls отклоняются.
Все DNS addresses должны быть public; numeric IP закреплён до socket connect,
TLS проверяет исходное hostname. Каждый redirect проходит admission повторно.
Private/reserved/mapped addresses и downgrade запрещены. DNS ожидание cancellable,
не более четырёх outstanding resolvers; underlying OS DNS нельзя forcibly kill.

Response до 1 MiB, до трёх redirects, plain/html/json UTF-8, без compressed bodies.
HTML scripts/styles исключаются, текст redacted; pagination, raw-byte SHA-256,
retrieval time и external-data provenance явные. Это не source read proof.
Timeout bounded; синхронный socket ждёт до оставшегося timeout между cancel checks.

`search(query, urls)` — lexical search по первым 12000 redacted characters каждого
из **1–8 явно выбранных страниц**, с incomplete_pages. Это не общий поисковый индекс
Интернета. `docs_fetch` / `docs_search` deferred tools требуют host-injected client;
CLI `atomic docs fetch URL --host HOST` и `docs search QUERY --host HOST --url URL`.
URL/network/redirect tests используют deterministic DNS/HTTP fixtures; external-site
end-to-end compatibility в этом пакете не измерялась.

## Optional components — 60

`sdk.components()` регистрирует host-owned loaders API/LSP/MCP/IDE/docs/workers,
но не импортирует соответствующие модули и не запускает service. Explicit `.load()`
инициализирует символ один раз; failed/reentrant initialization не повторяется.
Diagnostics различает deferred/loaded/failed. Установка dependencies не выполняется.
`atomic components [NAME]` показывает статус или загружает только named symbol.

`tools/measure_startup.py` запускает свежие процессы, проверяет отсутствие optional
imports до explicit load, измеряет elapsed и peak RSS без services/inference.
`validation/step15-startup-windows.json`: три samples на каждый режим; median SDK/
catalog **0.701 s / 29.36 MiB**, explicit LSP **1.366 s / 32.25 MiB**, MCP
**1.049 s / 30.04 MiB** на этом ноутбуке. Это измерение import workload при данном
OS/cache состоянии; не сравнение с прежней версией и не server/model memory.

## Проверки

Protocol/auth/schema drift/output caps, callback veto/failure-after-write/cancel,
plugin source drift/revocation, workflow preflight/journal uncertainty, SSRF/redirect/
DNS cancellation и clean-process deferred imports проверяются локально.
Итоговые focused Windows и exact-source Linux/Docker результаты записываются в
`PROJECT_CONTEXT.md`; промежуточные subagent runs не складываются в число уникальных тестов.
Paid model calls: **0**. Stage 4 live edit / multi-repository quality остаётся открытым.


Итоговая проверка runtime `58b3dd494ee038f5303386d3e527012150444e34`: push/PR CI
`39938719112` / `39938724913` PASS, Python 3.11/3.12; скачанный Docker artifact:
735 CPU passed / 10 skipped, 17 extra passed, 25 execution acceptance passed
(9 real Docker, 16 supporting CPU/local jobs), compile/calibration PASS, model_calls=0.
Raw files и hashes 48 изменённых source/test/tool blobs сохранены в
`validation/ci-step14-16-20261023/`. Windows focused initial 360 passed / 4 skipped /
1 outdated-test failure; исправление expectation и последние интеграции перепроверены:
47 passed / 1 skipped. Production Stage 4 остаётся открытым.
