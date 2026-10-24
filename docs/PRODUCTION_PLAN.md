# Production plan: критерии принятия и оставшаяся работа

24 октября 2026. Заменяет рабочие статусы первоначального
[`audit-2026-10-23/PRODUCTION_PLAN.md`](audit-2026-10-23/PRODUCTION_PLAN.md),
который сохраняется как исторический аудит. Исходная точка review — `445f1b9`.
Наличие реализации и зелёный CI — отдельные факты от выполнения всех exit gates.
UI/UX, marketplace, writable parallel workers и multi-tenant service вне этого этапа.

## Поддерживаемый первый scope

Single-owner Python coding framework: CLI/SDK, authenticated single-worker API,
проверяемый staged edit одного корня, serializable cooperating writers, durable
учёт запросов и unknown effects, несколько явно разрешённых provider routes.
Core/client устанавливаются без Docker. Непроверенное выполнение требует принятого
изолированного executor; trusted local не является такой изоляцией.
Гарантии не распространяются на произвольного внешнего редактора/host administrator,
нечестный remote worker и неизвестные provider billing semantics.

## W1–W9 после проверки

| Направление | Подтверждено | Что необходимо до полного закрытия |
|---|---|---|
| W1: запись/API/usage | Leases, source conflict gates, index generation CAS, уникальные tmp, API 4xx, parsing-independent usage; regressions в CI. | Матрица OS/multiprocess и заявленный crash model должны быть явно перечислены. Установка wheel на Windows не доказывает Windows concurrency semantics. |
| W2: recovery/accounting | Host-only immutable reconciliation, original call/day identity, unknown holds, idempotent crash repair. | Матрица отказов между persistence boundaries, диск full/permission failure и consumer replay refusal. Не заявлять power-loss durability по обычному pytest. |
| W3: routing | Fixed/free_only/capped_paid, per-physical-attempt ledger, quota scope/cooldown, отказ от retry unknown. | Отдельный bounded live adapter smoke на официальных разрешённых routes; подтверждённый pre-inference отказ и отсутствие повторного списания. Не истощать квоту искусственно. |
| W4: Docker-free | Core/CLI/SDK/API wheel matrix без Docker; trusted local lifecycle, optional remote worker/OCI adapter, fail-closed execution. | Принять один настоящий worker deployment с environment identity и повторяемой consumer-проверкой. Native filesystem/network sandbox не реализован и не заявляется. |
| W5: release foundation | Wheel/sdist, installed-package isolation, 12 core/API OS/Python combinations, real interoperability job. | Reproducible constraints/transitive dependency hashes, SBOM/licenses, source→wheel provenance и offline consumer rehearsal. Публикация на PyPI/подпись релиза — отдельный шаг. |
| W6: composition | Три реальных LSP/MCP сценария на Windows локально и Linux CI `0dbd593`: public prepare/review/approve/resume, Python+notebook edit→pytest→publish; cancel и source conflict; published-journal recovery без новых calls. Общий budget, staged bindings и expected terminal states проверены. Scoped composition gate **PASS**. | Durable-state injection не доказывает OS process-kill/power-loss recovery или remote deployment; эти требования переходят в production acceptance. |
| W7: operations/scale | File/byte/disk preflight, bounded snapshots, streaming session read/recovery, cancellation hooks; synthetic measurements. | Cold/warm pinned real repos; RSS/disk/queue/cancel, p50/p95 по повторным запускам; long-session soak. Journal append всё ещё имеет quadratic cumulative prefix reads. |
| W8: utility | 30 pinned issues/5 repos, независимый patch grader, одна real calibration и отрицательный live development pilot. | Остальные 29 environment calibrations; self-hosted pytest-source evaluator; одинаковые frozen caps; replacement blinded holdout; все 60 arms, failures/costs/interventions. No superiority claim до результатов. |
| W9: release candidate | Draft PR, обязательные CI workflows и сохраняемые artifacts. | Закрытые scope-specific gates W1–W8, exact-source/wheel/config manifest, независимый review и consumer run. Пока **OPEN**. |

## Порядок исполнения и условия перехода

1. **Достоверная исходная точка.** Исправить pilot totals, quota coverage,
   scalar/matrix описание, source excerpt и устаревший PROJECT_CONTEXT. Сохранить
   исходные отчёты, failed attempts и логи. Автоматизировать их сверку. В новых
   sidecars обязательно записывать framework source hashes, dirty state,
   interpreter/dependencies, tariffs и frozen caps. Старый provenance не выдумывать.
2. **Композиция и runtime performance.** Закрыть перечисленные W6 сценарии.
   Для индекса переиспользовать AST только при новом content hash, сохранять CAS,
   cancellation и cold/warm graph parity. Индексы staged и published source оба
   необходимы. Добавить проверяемую границу длинных путей без общего обещания
   совместимости Python/Git/analyzers. Только затем повторять live atomic pilot.
3. **Операционный envelope.** Измерить реальные репозитории минимум пятью повторениями
   cold/warm, отдельно startup/AST/link/hash/copy/publish, RSS и диск. Зафиксировать
   supported max workload из измерений. Long session должен включать corruption,
   truncated tail, interrupted append, cancellation и concurrent admission.
   Переход журнала на новый store требует отдельной миграции и доказательства
   прежнего integrity contract; stat-only shortcut не принимается.
4. **Калибровка и качество.** Для каждого из 30 cases сначала environment identity,
   base requirement FAIL / gold PASS / plausible wrong FAIL и все исходные oracle IDs.
   Infrastructure error не является requirement failure. Задача SymPy уже раскрыта;
   она development-only, для confirmation нужен новый repo holdout. Старые pytest
   исходники проверяются их собственным runner, а не установленной другой версией.
   Затем frozen model, matched caps и заранее заданный порядок baseline/atomic.
   Публиковать стоимость всех attempts, не только последнего успешного поколения.
5. **Release rehearsal.** Из exact Git SHA собрать wheel/sdist, provenance/SBOM,
   hashes/dependency lock; clean installs и tests на заявленной matrix. Consumer
   выполняет supported journey с тем же wheel, без source checkout/PYTHONPATH и
   внутренних подмен. Только после evidence review принимать release candidate.

В этом проходе выполнены исправления отчёта/контекста, offline evidence auditor,
future-run provenance metadata, расширенный W6 и hash-validated index reuse с
regressions. Их итоговый tested SHA фиксируется отдельно после CI. Это не
подменяет измерения и release gates 3–5.
Нельзя закрыть W8/W9 сменой статуса в Markdown. Если новый pilot снова отрицательный,
сначала изучить failure class/ablation, затем менять модель или механизм с новым
experiment ID; не увеличивать caps задним числом внутри matched comparison.

## Провайдеры и деньги

Сохранённый public pilot: 23 calls, USD 0.0364695 quota cost; это расход бесплатного
кредита, а не подтверждённый денежный платёж. Семь quota snapshots, три ранних
платных запуска без отдельных snapshots. Подробности — `IMPLEMENTATION_STEP_24_EVALUATION.md`.
Участники разрешили credentials 3/4; 4 неактивен, 3 был доступен при проверке,
но этот сохранённый pilot использовал только owner 2. Авторизация и квоты каждого
владельца учитываются отдельно, сведения о доступности могут устареть.

Официальный Model Proxy quota отдельный от TPU/GPU hours:
[Kaggle CLI benchmarks reference](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/benchmarks.md).
Free-tier каталоги полезны для discovery, но не удостоверяют тариф/совместимость,
бесконечный лимит или право обходить ограничения. Host проверяет capabilities,
egress, действующий тариф и quota scope. Unknown outcome/charge не переключается
на другой аккаунт/провайдер. Confirmed quota refusal может выбрать другой явно
разрешённый route с тем же accounting owner и общим deadline.

Reported-cost stop после ответа и configured estimate не гарантируют жёсткий
счёт провайдера. При неизвестном тарифе это явно маркируется; production admission
с positive budget должен отказывать. Credits не используются для обхода блокировок
или антибот-систем. Все ключи только в памяти процесса, без отправки в GitHub CI.

## Trust boundary оценщика и источники архитектуры

Oracle находится вне candidate patch и проверяет оригинальные требования; это
независимость данных теста, а не защита от враждебного Python в том же процессе.
Disposable worker/изоляция защищают host; они сами по себе не доказывают, что
импортированный candidate не вмешивается в Python evaluator. Для adversarial
acceptance нужен отдельный execution/observation contract либо review исходного
candidate; не выдавать нынешний pytest harness за такой механизм.

Зеркало Claude Code остаётся архитектурным reference с неподтверждённой provenance
и закрытой лицензией; код/prompts не импортируются. Собственные implementation
choices проверяются regression/composition/quality evidence, а не сходством с зеркалом.

## Закрытие текущей главы и сколько осталось

Текущая глава **завершена**: независимая сверка стороннего отчёта и копии,
исправленные totals/provenance claims, воспроизводимый offline auditor, новая
run provenance, expanded W6 acceptance, исправления warm index/references,
resource/path admission и cancellation. Всё проверено на runtime `0dbd593`.
Два push workflow и соответствующие PR workflows PASS; source-specific evidence:
`validation/ci-production-review-20261024/evidence-manifest.json`.
Полный Python job: 1021 passed / 14 skipped; clean wheel matrix: 12 PASS;
actual external composition: 3 PASS. Пересекающиеся наборы не суммируются.

До общего production acceptance остаются **четыре рабочих блока**, а не
«ещё четыре теста». Реализации W1–W5 подтверждены в указанном scope, W6 scope
закрыт; это не означает, что все их первоначальные exit criteria выполнены.

| Следующая глава | Измеримый результат завершения |
|---|---|
| 1. Полезность / W8 | Ещё 29 environment calibrations, корректный pytest-source runner, замена раскрытого holdout; 30 задач × baseline/atomic при frozen caps. Все ошибки, вмешательства, время и расходы включены. |
| 2. Масштаб / W7 | Реальные pinned repo cold/warm measurements, repeated p95/RSS/disk/cancel, long-session soak; решение journal append с integrity-preserving migration. Supported envelope опубликован. |
| 3. Release foundation / W5+W9 | Полные dependency constraints/hashes, SBOM/licenses/source-wheel provenance; clean consumer rehearsal без checkout. Проверяемые wheel/sdist/config manifest, отсутствие скрытых ручных подмен. |
| 4. Production acceptance / W1–W4+W9 | Заявленная OS/multiprocess/crash matrix, failure injection между persistence boundaries, принятый remote/isolated worker и bounded live route acceptance; независимый финальный review. |

Время этих блоков пока не оценено: исторические окружения и стоимость 60 arms
ещё не измерены. Не назначать дату release из количества Markdown-пунктов.
Каждый блок заканчивается отдельным evidence-bound решением; отрицательный
quality result допустим как результат эксперимента, но не как доказательство пользы.
