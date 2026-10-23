# Пакет 14: notebooks, LSP, watcher и несколько корней

23 октября 2026. Кандидаты **24, 25, 27, 28** реализованы в ограниченном host/runtime
контракте. После пакета **52 implemented_scoped / 10 planned**. Independent Stage 4
multi-repository code-edit evaluation и production acceptance остаются открытыми.

## Notebook cells — 24

`NotebookDocument.load/read/preview/apply` и инструменты `notebook_read/notebook_edit`
работают с bounded nbformat4: до 1 MiB файла, структурная проверка cells/outputs/IDs,
без duplicate JSON keys, NUL, невалидного Unicode и чрезмерной вложенности.
Это собственный structural validator, не заявление полной JSONSchema совместимости
со всеми extensions nbformat. ID уникальны и сохраняются при перестановке ячеек.
Legacy notebooks без IDs читаются, но требуют отдельной явной host-нормализации.

Модель получает redacted transcript **только cell IDs/types/source** с character
pagination. Metadata, outputs, attachments и raw notebook JSON не выдаются как
source. Общие read_file/patch_file и source index не допускают `.ipynb`; notebook
нужно назвать явно. Notebook-only repository не получает новый retrieval index:
CLI/SDK работает напрямую, обычный answer требует существующий непустой source tree.
Hash всего файла связывает read/preview/apply, включая внешнее изменение outputs.
До/после rendering проверяется свежесть; отменённый read не оставляет нового proof.

Операции: `set_source`, `insert`, `delete`, `move` по устойчивому cell_id, без raw JSON
patch и произвольного изменения metadata/output. Первый notebook_edit возвращает
preview token; второй применяет точную операцию по текущему hash и token после
полного текущего notebook-source read. Dispatcher проверяет edit opt-in, роль,
policy/approval и exact task path scope до lazy factory. При любой source-операции
очищаются **все code outputs/execution counts** и trust/signature markers, поскольку
перестановка или изменение могут сделать устаревшими результаты последующих ячеек.
Kernel не запускается, Python/magic/другие языки внутри cell не объявляются проверенными.

Запись использует durable file transaction, compare-before-write, revalidate после
refresh, rollback без перезаписи чужой версии и существующий `atomic undo fileedit_ID`.
TaskRunner отслеживает notebook applied hashes, не считает preview выполненным edit;
final gate отдельно проверяет структуру/IDs. Cell execution всегда `not_run`.
Dialogue checkpoint принимает whole-file notebook hashes, без переноса read authority.

```console
atomic notebook read analysis.ipynb --project .
atomic notebook preview analysis.ipynb --project . --operation-json '{"action":"set_source","cell_id":"cell_a","source":"value = 2\n"}'
atomic notebook apply analysis.ipynb --project . --operation-json OPERATION_JSON --expected-hash FILE_SHA1 --preview-token TOKEN --yes
atomic notebook normalize-ids legacy.ipynb --project .
# Повторить normalize-ids с --expected-hash/--preview-token/--yes для явного migration.
```

Host SDK: `notebook(project,path)` возвращает document. `preview_normalize_ids()` и
`apply(..., allow_normalization=True, approved=True, preview_token=...)` — явный
migration; чтение само IDs не добавляет. Preview metadata не раскрывает cell source.

## LSP — 25

`LSPClient(project, command, approved=True)` запускает только явно разрешённый
absolute trusted executable/argv. Project config не выбирает executable, нет
автоустановки, автопоиска и серверных actions/commands. Server — доверенный host code,
не новая sandbox для произвольного plugin. Он получает очищенное окружение/private
HOME. Остановка касается принадлежащего Popen; detached descendants доверенного
сервера не объявляются ОС-изолированными. Для недоверенного кода нужен Docker worker.

Контракт соответствует ограниченному набору [LSP 3.17](https://github.com/microsoft/language-server-protocol/blob/gh-pages/_specifications/lsp/3.17/specification.md)
и [WorkspaceEdit](https://github.com/microsoft/language-server-protocol/blob/gh-pages/_specifications/lsp/3.17/types/workspaceEdit.md):
stdio Content-Length JSON-RPC, UTF-16 позиции, full-sync open/change. Frames до 2 MiB,
32 открытых документа до 512 KiB каждый, суммарный retained document/preview cap,
bounded queues, deadlines/cancel, fragmented frames и orderly shutdown. Другие
position encodings, incremental-only server, resource operations, annotated edits,
ambiguous same-position insert ordering и server `workspace/applyEdit` отклоняются.

`read_document` — явное полное host-чтение. `definition/references` возвращают только
проверенные project-relative locations/ranges с текущими source hashes. Diagnostics
принимаются только для известного version/hash; stale/unversioned push не выдаётся
за актуальный. Locations/diagnostics — server data, не чтение body и не доказательство
корректности кода. Private/ignored/external/encoded escape URI не открываются.

Rename: все targets нужно открыть/прочитать заранее. `preview_rename` получает данные
сервера и строит immutable client-owned preview, проверяя версии, UTF-16 offsets,
surrogates, overlap и file hashes. `apply_preview(..., approved=True,
expected_digest=preview.digest, context=...)` проверяет полное текущее чтение,
exact scope/opt-in при переданном ToolContext, компиляцию заменяемого Python,
все paths до записи и после refresh. Запись recoverable multi-file transaction,
не OS atomicity; чужая третья версия сохраняется даже при rollback failure.
`recover_lsp_edit(...,approved=True)` восстанавливает только принадлежащие journal bytes.
Host API не запускает автоматически full pytest и не закрывает task-quality gate.

```python
from atomic_flow.sdk import open_lsp

with open_lsp(project, [absolute_server_executable, *trusted_args], approved=True) as client:
    client.read_document("a.py")
    client.read_document("b.py")
    locations = client.definition("a.py", 0, 0)
    preview = client.preview_rename("a.py", 0, 0, "new_name")
    # Host reviews preview.summary() and the proposed TextChange bytes.
    client.apply_preview(preview, approved=True, expected_digest=preview.digest)
```

CLI: `atomic lsp definition|references|diagnostics|preview-rename PATH --project .
--command-json '["ABSOLUTE_EXECUTABLE", ...]' --yes`; zero-based line/UTF-16 character.
Rename apply остаётся SDK host operation: CLI preview не хранит capability для будущего
процесса. `atomic lsp recover FILEEDIT_ID --project . --yes` — отдельное recovery.
Read-only model tools работают только с заранее переданным host `lsp_client`; сами
сервер не запускают. `operation(cancel_check=...)` связывает отмену dispatcher с запросом
и ожиданием serial lock; после операции callback восстанавливается.

Проверки используют synthetic protocol fixture, не настоящий Python/TS analyzer.
Совместимость с конкретным production language server и выгода rename ещё не измерены.

## Внешние изменения — 27

`SourceWatcher` — portable bounded polling с source hashes, coalescing/debounce,
sequence/fingerprint и проверяемым acknowledge. Никакой daemon не стартует скрыто.
По умолчанию 0.25s interval/0.2s debounce, максимум 10k source files/20k directory
entries/64 MiB source и cooperative scan deadline 5s. Пустые каталоги также считаются.
Private/ignored paths не попадают в observations; notebook outputs никогда не выводятся,
отслеживается только hash файла. `.gitignore` hash меняет source universe.

`tree.watch_external=true` явно включает опрос на tool boundaries. Изменённые пути
сразу теряют read proof/evidence; пока debounce не готов, tool не вызывается.
После refresh fingerprint пересканируется; гонка не acknowledge-ится как успех.
CLI `atomic watch --project . --interval 0.5 --debounce 0.2` выдаёт version1 JSONL;
Ctrl-C останавливает, `--iterations N` ограничивает число ticks. SDK `watch(project)`.
Event — advisory data, не permission/read proof. Это polling на safe boundaries,
не realtime OS event subscription или обещание отсутствия editor races.

Incremental tree теперь всегда сверяет bytes hash, даже при прежних size/mtime:
перезапись одинаковой длины с восстановленным timestamp не переиспользует stale AST.
Parsing прежних bytes по-прежнему переиспользуется, но I/O scan нужен. Hash checks
read/write/final publication остаются обязательными и без watcher. Масштабный cold-start
benchmark не выполнен; optional watcher default off на слабом ноутбуке.

## Несколько roots — 28

`WorkspaceRegistry(workspace_id)` хранит максимум 8 явных root grants в private
application state. Каждый grant требует approved=True/CLI --yes; read-only по умолчанию,
write_paths — точные относительные файлы. Overlap/nested aliases, symlink/junction,
private state roots, скрытые/credential paths отклоняются; canonical directory identity
и generation связывают authority с исходным каталогом. Отозвать можно и offline root.

`registry.session()` хранит только ephemeral root-qualified full-read proof и preview.
Одинаковый `main.py` в двух корнях не делит authority. Reads/pages/list возвращают
workspace_id/root_id/hash_algorithm и проверяют grant после чтения; SHA-256 root proof
не записывается в основной ToolContext SHA-1 proof. Read tool требует явный host session.
Model-facing `workspace_read` также не записывает proof внутри host session:
host publication требует отдельного явного `session.read()`.
Ответы, journal summary и provenance сохраняют root namespace. Никакой grant из модели,
project config или persisted summary не создаётся.

```console
atomic workspace grant app ABSOLUTE_DIRECTORY --workspace demo --write-path main.py --yes
atomic workspace list --workspace demo
atomic workspace read app main.py --workspace demo
atomic workspace revoke app --workspace demo --yes
```

SDK: `workspace("demo").session().read(root_id,path)`; `.preview(root_id,{path:text_or_none})`
требует точный write scope и полное свежее чтение (включая absence для нового файла).
`.apply(token, approved=True, final_check=host_callback)` проверяет одноразовый
root/generation-bound token и полный root manifest, stage/approve/publish через ChangeSet,
проверяет grant перед каждой записью/после finalization; отзыв вызывает rollback.
Python replacement компилируется. Full pytest только через явный final_check; без него
проверены syntax/hash/scope, а не поведение проекта. Cross-root atomic transaction и
multi-root TaskRunner здесь **не реализованы**; одна публикация затрагивает один root.
Model-facing root mutation tool не добавлен.

Перечисление/manifest bounded scandir: 20k entries/5s cooperative, manifest 8 MiB/file
и 32 MiB total; truncated enumeration явная, неполный manifest не даёт preview.
Предварительный bounded scan не добавляет строгий ресурсный bound внутрь старого
ChangeSet: конкурентный рост после него обнаруживается integrity gates, но может
увеличить время существующего snapshot/publication. Это отдельная эксплуатационная граница.

## Общие gates и проверка

Новые схемы инструментов deferred до явного `tool_catalog` discovery, чтобы не расширять
каждый default prompt. Role/policy/approval/admission/budget gates сохранены. SDK
`start_answer(...,workspace_session=...,lsp_client=...)` передаёт только host-owned
capabilities; HTTP API не принимает executable/grants из request. Структурированный
read-tool result, отклонённый dispatcher после отмены/ошибки/oversize, не оставляет
новый ToolContext read proof.

Пакет не использует paid model API, cloud sessions или настоящие секреты в тестах.
Промежуточный полный Windows прогон: **641 passed / 22 skipped**;
`validation/step14-windows-final.txt` относится к тому срезу, до последних integration
изменений. Последующий focused run пакетов 14–16 проверяет новые и затронутые механизмы;
точные результаты и Linux/Docker source evidence см. `PROJECT_CONTEXT.md`.
