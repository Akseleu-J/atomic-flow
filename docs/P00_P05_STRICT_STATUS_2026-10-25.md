# P00–P05: строгая сверка статусов 25 октября 2026

Проверенный опубликованный snapshot: 5c8de452fa4b6e3c962c84469e2369ae3c257e3b.
Метод: исходные критерии PRODUCTION_ROADMAP.md + chapter reports + accepted
evidence. Graph/watching/activation source между 395b8ac и 5c8de45 не менялся;
поэтому перечисленные там integration gaps не закрылись пакетами P06.
Это аудит статусов, не новый независимый code review всех P00–P05 модулей.

**Все P00–P05 полностью закрытыми считать нельзя.** Accepted implementation
slice и functional host core не означают закрытие всех gates/prerequisites.

| Глава | Что действительно принято | Что мешает утверждать полное закрытие |
|---|---|---|
| P00 | Frozen baseline card, coverage map, acceptance registry и preregistered hypothesis | Отдельная полная сдача расширенного P00/E00/E01 contract/design gate по §13 не подтверждена: typed identities/VerificationReport/omissions design и claims inventory нельзя подменить одной baseline card. Исторический coverage map — inventory/spot checks, не доказательство покрытия каждого требования. |
| P01 | Python structural adapter и source-only config/document/pipeline metadata | Полное выбранное UA mapping, versioned read/API/export/migration compatibility и canonical fixtures; root/principal boundaries по enterprise plan. |
| P02 | Python Git reconciliation API и owned-publication delta slice | Watcher и non-Git reconciliation, re-export/wildcard/fixpoint semantics, retrieval invalidation propagation, полный normal-edit delta hot path/overlay и требуемые parity journeys. Git ingestion сейчас выполняет полный source/AST audit и не является доказательством быстрого incremental path. |
| P03 | Scripted semantic callback/cache и SDK understand_project | Общий provider budget adapter; CLI/MCP inspect/search/explain/chat/impact/tour; dependent layer/tour invalidation; domain links, walkthrough pinned external repo и semantic acceptance. NOT RUN/not_assessed не считаются выполненным gate. |
| P04 | Context-pack и hash-bound range edit с read-before-edit/rollback | Полная связка impact→test selection→final checks, несколько multi-step context/omission/stale-source journeys и coverage/unknown-impact contract. |
| P05 | Functional host core ACCEPTED в package39 и самом roadmap: DAG/frontier/receipts/bounds/invalidation/migration/cancel | Это принятый functional core, а не вся P00–P05 dependency chain: P04 prerequisite остаётся открыт. Automatic authority expansion и semantic evaluator по исходному scope относятся к другим gates, не следует задним числом требовать их как фиктивный дефект P05. При новых review findings функциональный verdict требуется пересмотреть. |

P95/RSS/soak и dependency locks/release/worker deployment относятся к последующим
P07–P12/production gates. Их отсутствие не превращает baseline P00 в performance
сертификат и не даёт закрыть предыдущие integration gaps переносом в будущую главу.

## Skips: фактический остаток доказательств

В source-bound Linux host JUnit runtime da67d89 (архив
validation/p06-matrix-20261025/12609626928.zip) **14 SKIP**:
10 требуют explicit Docker integration, 4 требуют Windows (taskkill helper —
три variants; lowercase-drive LSP URI — один). Эти случаи не являются PASS
host suite. Другой job/platform может их покрыть, но это нужно явно сопоставить
с тем же source; installed-wheel smoke не заменяет Windows runtime suite.

На final-doc HEAD5c8de45 host Python3.11/3.12 и interoperability SUCCESS; Docker
checks40222460255 FAIL, artifact12610221339 (SHA256
7203a1f3aa4c0598dc5d99e2fe4b92df9d08aab3a1167263b25e52b8e264a020):
container-boundaries 25 PASS / 1 FAIL, remote slow-job did not reach running.
Он не объявлен безусловным flake и не скрыт за зелёным предыдущим runtime.
Root расследует worker/reconcile lease contention; исходный ZIP сохранён.

## Порядок реального закрытия

1. Независимый P06 review/fixes/полный CI (делегирован отдельному субагенту).
2. Зафиксировать для P00–P04 checklist каждого исходного exit criterion со
   source-bound evidence и отдельным решением; закрывать пробелы последовательно.
3. Прогнать P05 regressions после backfill интеграций и принять полную dependency
   chain. P07 quality не использовать как замену незавершённым P01–P04 operations.

Новых provider/model calls в этом аудите нет. Полное production closure не заявлено.
