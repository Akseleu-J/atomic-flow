# Повторная сверка критики ядра и корпоративных требований

24 октября 2026. Проверен HEAD `5cf4068505cf5d020c1762254b7e23992405a415`,
runtime без изменений относительно CI-tested `0dbd593`. В рабочем дереве уже были
новые roadmap/reference документы предыдущего шага; они сохранены и дополнены.
Это выборочная проверка исходников, offline probes и focused regressions,
не полный аудит, внешний pentest или production acceptance.

## Материалы и метод

Прочитаны оба приложенных текста: «Анализ ядра atomic-flow (ветка experimental)»
и «Излишки и сильные места». Они рассматриваются как гипотезы внешнего reviewer.
В первом тексте прямо указана недоступность полных orchestrator/builder/task_loop,
поэтому выводы о всём ядре требуют проверки. Дата и exact source SHA внешнего
анализа неизвестны; числа и historical claims не переносятся автоматически.

Заново просмотрены activation/materialization, verify/orchestrator/schemas,
completion/source/budget, graph linking/references, API, LSP, jobs/workers/remote,
locking/durability, packaging и существующие tests. Source hashes и результаты
простых probes: [recheck.json](../validation/core-corporate-review-20261024/recheck.json).
Воспроизводимый script: [recheck.py](../validation/core-corporate-review-20261024/recheck.py).
Новые provider calls = 0; candidate-код внешних проектов не запускался.

Focused recheck: **11 passed in 26.36 s**, [log](../validation/core-corporate-review-20261024/pytest-focused.txt).
Проверены missing-price admission (task/daily), unknown-charge hold/no replay,
stale materialization, invalid citations, grounding evidence refresh и ledger price guard.
Первый probe с TemporaryDirectory и первый pytest в desktop sandbox отказали
из-за Windows temporary-directory ACL/WinError 5; это infrastructure errors, не
product assertion failures. Probe перенесён в ignored workspace state; те же
pytest cases повторены вне sandbox. Это не новая полная suite/CI/remote acceptance.

## Сопоставление четырёх открытых production-блоков

| Блок | Уже был в roadmap | Дополнение после повторной проверки |
|---|---|---|
| Качество: 29 калибровок и matched baseline/atomic | P07, с provenance на P00 | Отдельные answer-evidence/retrieval-coverage gates E01/E02; tests агента не подменяют tests его изменений. |
| Масштаб: p95/RSS/soak/journal | P08–P09 | Queue/fairness/cancellation contracts E04, telemetry E07; performance не сводится к скорости AST. |
| Release: locks/SBOM/provenance/consumer | P11 | Slim core distribution, extension compatibility, vulnerability/update process и enterprise consumer profile E00/E06/E08. |
| Acceptance: worker/crash/OS/review | P09–P12 | Untrusted live evaluation требует isolation уже до P07; enterprise identity/data boundaries E03/E05 и corporate RC E08. |

Все четыре блока были учтены, но первый roadmap целился в single-owner Python
release. «Максимальное корпоративное ядро» требует дополнительной программы,
не только закрытия тех же четырёх блоков.

## Проверенные замечания

| Замечание | Результат сверки | Решение |
|---|---|---|
| Нет backticks → grounding 1.0 | **Подтверждено probe.** И заведомо неправильный ответ без backticks, и неверная логика с известными именами получают 1.0. | E01: checked=0 → not_assessed, разделить identifier hygiene, citation integrity и behavioral verification; менять output schema с migration. |
| verify.py — всё verifiable core | **Неверное обобщение.** Есть independent host completion/source/scope/test gates. Они тоже не доказывают семантическое выполнение любого требования. | Сохранить host gates; убрать двусмысленные claims о доказанной корректности ответа. |
| Critic той же модели — независимая проверка | **Независимость не установлена.** Critic verdict/coverage являются модельным мнением; deterministic name check только может их понизить. | Independent requirement evaluator и measured critic ablation; не принимать self-grade за ground truth. |
| Retrieval лексический, без dense retrieval | **Подтверждено для текущего ActivationIndex.** Есть graph spreading/PageRank; LLM understanding layer UA ещё планируется. | E02: hybrid candidate, frozen retrieval/utility comparison; отсутствие embeddings само по себе не доказывает провал. |
| Веса точно подогнаны на 9 кейсах | Есть hardcoded constants и небольшой fixture corpus; **история подбора конкретно на 9 кейсах не доказана**. | Version/calibration provenance, held-out evaluation и sensitivity tests; не повторять неподтверждённое обвинение. |
| Динамический/неоднозначный graph неполон | **Подтверждено ограничение.** Нынешний `_link` в ряде веток берёт до трёх same-file/imported candidates; нет полноценной edge-confidence/coverage schema. | P01/P02/E02: distinguish resolved/candidates/unresolved/dynamic, evidence и conservative verification closure. |
| Aliased imports/callbacks целиком выпадают | **Слишком широко.** references.py собирает loaded names и import aliases; поддержка неполна, но она существует. | Проверять конкретные resolver cases, не объявлять все отношения отсутствующими. |
| Empty-match fallback скрыт | Hubs действительно возвращаются; probe подтверждает `weak=true`, `top_raw=0`. **Сигнал провала уже есть.** | E02: явный exploration reason и abstention/next-search behavior, не считать hubs положительным match. |
| Stale source молча исключается | **Частично подтверждено.** `_Sources.lines` логирует warning и возвращает None; материализация не возвращает полный rejection report. | E01/P04: missing/stale/denied/truncated evidence coverage в result и stop/refresh policy. |
| Hash до чтения, затем используется другой текст | **Не подтверждено в просмотренном path.** `_Sources.lines` читает bytes, считает hash тех же bytes и декодирует их; read_checked тоже проверяет возвращаемые bytes. | Сохранить byte-bound reads. Arbitrary filesystem race/symlink threat требует отдельного тестирования и contract; общей race-free гарантии нет. |
| activate() заново строит index | **Подтверждено helper path.** Docstring уже рекомендует cached ActivationIndex. | Deprecation/warning или explicit revision-scoped index handle; hot-path instrumentation и migration consumers. |
| Неизвестная цена обходит daily budget | **Опровергается текущим task path.** TaskBudgetBackend отказывает до вызова при включённом task/daily cap и неизвестной цене; SpendLedger.reserve имеет отдельный guard. | E06: сохранить guard и проверить все новые ingress/adapter paths; local ledger не является enterprise billing service. |
| Отмена не гарантирует остановки remote inference | **Подтверждено документированное ограничение.** Local wait cancellation и remote completion/cost — разные факты; custom backend обязан выполнять contract. | E04/E06: отдельные cancel_requested/wait_stopped/worker_stopped/provider_unknown; no replay и unknown holds. |
| API достаточно для корпоративного сервиса | **Нет.** Просмотрены shared key, process-local semaphore/runs, single-process readiness; нет actor/project ACL model и durable API task queue. | E03/E04: identity/resource authorization, bounded durable scheduling и per-principal quotas. |
| Chat полностью теряет историю | **Нет.** Bounded предыдущие turns включаются в model query; retrieval намеренно получает last user turn. Anaphora retrieval может пострадать. | E02: bounded conversation-aware query resolution с explicit references и никаким inheritance authority. |
| Каждый запрос всегда делает 10 calls | **Не подтверждено.** Есть simple/medium/hard, conditional retry/expansion и early exit; simple без critic. | Trace actual physical calls, cheapest sufficient path, critic/enrichment по evidence и budget. |
| Размеры модулей и package sprawl | **Подтверждено направление.** Probe: 133 package Python files, 1,011,831 bytes; 244 не является текущим числом package Python files. | E00: dependency map/ports, maintenance limits, measured import/startup и независимые adapters. |
| LSP — встроенный server | **Фактическая ошибка.** lsp.py — explicit trusted-host LSP client. | Не вырезать принятую интеграцию; вывести в adapter boundary с contract tests. |
| Jobs/tasks/workers/remote — четыре одинаковых движка | Есть пересечение primitives, но **разные обязанности**: workflow, process execution, advisory analysis, transport. Выигрыш 40–60 KB не измерен. | E00: общие state/event/lease contracts и hierarchy task→attempt→job; разные authority/effect semantics сохранить. |
| locking/source_lock/file_paths дублируют один механизм | **Неверное упрощение:** physical lock primitive, root-wide mutation lease и source-path admission — разные уровни. history/context_state/instructions также имеют разные роли. | Consolidate конкретный повтор кода; boundaries не объединять по похожему имени. |
| Ручная validation всегда хуже Pydantic | Стилей несколько; выгода переноса **не измерена**. Safety-sensitive exact types/finite values/unknown fields могут быть намеренными. | E00: strict versioned wire/persistence schemas, preserve bool≠int и bounds, compatibility/malformed-state corpus. |
| Fixture kernels едут в wheel | **Подтверждено package-data config.** Inventory: 13 fixture .py, 57,261 bytes. | E00/P11: вынести development/evaluation fixtures в optional distribution, contract migration и installed consumer checks. |
| Evidence files — мусор, можно удалить | **Не принимается.** Artifacts нужны для exact-source provenance; процессы хранения можно улучшить. | Content-addressed archives/manifests/retention для новых runs; исторические доказательства сохранять. |
| Kaggle model benchmarks = product quality | **Неверный claim.** Различие уже отражено в PROJECT_CONTEXT; live pilot отрицательный, 30-task comparison открыт. | Разделить model/retrieval/harness/end-to-end benchmarks, P07/E01/E02. |

## Дополнительные пробелы, обнаруженные в самом плане

1. **Isolation dependency:** P07 запускает исходники внешних проектов. Принятый
   execution boundary нужен до такого запуска; завершающий deployment/route gate
   P10 нельзя читать как разрешение делать P07 untrusted tests на host.
2. **Хранилище:** SQLite WAL годится для local single-host профиля. Корпоративный
   distributed профиль требует server-owned storage, fencing и transaction tests;
   SQLite файл нельзя раздавать нескольким хостам через shared filesystem.
   Live DB также не следует помещать в синхронизируемую папку source checkout.
3. **Authority domains:** actor/workspace/project и policy version должны входить
   в identity tasks/caches/artifacts/credentials с самого начала schema design,
   даже пока default профиль остаётся single-owner.
4. **Source hash contract:** source digest сейчас SHA-1. Для adversarial content
   перейти на algorithm-tagged SHA-256 c versioned migration; существующие evidence
   не переписывать. Это hardening requirement, не воспроизведённый collision exploit.
5. **Corporate ядро отдельно от UI:** E00–E08 принимаются через SDK/API/headless
   consumer. Dashboard, IDE, browser и дополнительные языки не блокируют этот release.

## Решение

Сохранить UA/delta/DAG/autonomy направление. Дополнить его честной evidence model,
core/adapters separation, retrieval uncertainty, identity/policy/data boundaries,
durable scheduling и корпоративной эксплуатационной приёмкой. Не делать dense
retrieval обязательным без сравнения и не удалять safety runtime ради сокращения LOC.
Подробные implementation packages и exit gates:
[ENTERPRISE_CORE_PLAN.md](ENTERPRISE_CORE_PLAN.md).
