# Runtime production audit — 23 октября 2026

Проверена рабочая копия `8a18fbe837b5c82597f782b6455e96b708607f36`. Runtime source не изменялся. Прочитаны AGENTS.md, PROJECT_CONTEXT.md и действующий код; исторические R01–R07 не объявлялись повторно открытыми. Этот аудит покрывает публикацию исходников, восстановление, session storage и композицию TaskRunner. Он не является аудитом всего репозитория или внешним pentest.

Доказательства: `runtime_probes.py`, `runtime-probes.json`, synthetic state в указанном JSON каталоге `runtime-evidence-a1e8sfba/`. Команда: `.venv/Scripts/python.exe docs/audit-2026-10-23/runtime_probes.py`. Сеть, модели, Docker и полные test suites не запускались. У probe две предварительные setup-ошибки: неверный формат synthetic task ID, затем Windows restricted-runner ACL для созданного с mode 0700 каталога sessions. Исправлена только подготовка probe: валидные task IDs и предварительное создание sessions с наследуемыми workspace ACL. Это не runtime findings.

## RT-01 — P1: две публикации одного проекта могут успешно завершиться с потерей одной правки

**Воспроизведено детерминированным interleaving.** `agents/source.py:52–66` проверяет hash, затем отдельно вызывает `Path.replace`. Между последним check и replace другой writer может заменить файл. `agents/control.py:21–27` сериализует только один task ID; разные задачи одного проекта получают разные leases. `agents/changeset.py:349–394` не имеет project-wide publication lease. Удаление и recovery имеют аналогичные окна (`changeset.py:334–340`, `396–420`), но эти ветки отдельно не воспроизводились.

Probe создал A и B из одного baseline, одобрил изменения одного `sample.py`, взял lease A. Точно на вызове A `Path.replace`, после реальных prechecks, выполнил полную `B.publish()` под lease B. Затем A выполнил настоящий replace и оставшиеся final manifests checks. Результат: **A=published, B=published, final source=A**, изменений B нет. Ни одна из публикаций не выдала conflict. Это не monkeypatch проверки или симуляция success: подменён только scheduling boundary реального replace. Это доказывает допустимый порядок операций; частота естественного возникновения в multi-process stress не измерена.

Влияние: потеря уже опубликованной правки при нескольких задачах/процессах/SDK clients на один root. Последующий `verify_published()` B сможет обнаружить расхождение, но не предотвращает потерю записи в этот момент. Postcheck сам по себе не восстанавливает затёртые bytes внешнего редактора.

Исправление: canonical-root project mutation lease для publication, recovery, rewind и прямых cooperating source mutations; захватывать до freshness check и удерживать до durable terminal status. Проверить aliases/case normalization и отдельные process handles на Windows. Для editors/сторонних процессов advisory lease не является CAS; нужно явно ограничить контракт, сохранять конфликтующие поколения и выбирать managed branch/worktree или другой publication mechanism, если требуется более сильная гарантия. Дополнительный hash-check сам по себе окно не закрывает.

Acceptance: два процесса публикуют один baseline, барьер расположен после последнего check — ровно один succeeds, второй busy/conflict; обе версии сохраняются; такие же проверки для direct edit/create/delete/rollback/rewind; disjoint edits имеют заранее определённую serializable политику; Windows/Linux. Отдельно negative test подтверждает отсутствие обещания защиты от некооперирующего editor; для более сильной гарантии нужен явно exclusive publication contract или подходящий OS/storage mechanism.

## RT-02 — P1 для durable service: uncertainty безопасно останавливает работу, но нет проверяемого перехода к reconciled

**Подтверждено кодом и малым journal probe.** `sessions.py:86–116` добавляет unknown call/effect в set, но никогда не удаляет после последующих подтверждений. `sessions.py:27–33` не содержит reconciliation event. `agents/task_budget.py:24–34` блокирует любое состояние события кроме completed. `agents/task_loop.py:88–97,143–159` блокирует resume; `dialogue.py:37–44` — continuation/fork. `SpendLedger.record()` является низкоуровневой append-функцией, а не host reconciliation workflow, который атомарно согласует task, ledger, session и разрешённое продолжение. Reconcile jobs/remote существует для других state machines и не снимает эту блокировку session/task accounting.

Probe: model_request → model_failed(unknown) → model_response(success,cost=0.01) оставляет `call_1` unresolved; tool_effect_unknown → tool_result(success, side_effects=false) оставляет `effect_1` unresolved. **Обычный success-event и не должен самовольно снимать неопределённость.** Дефект готовности состоит в отсутствии отдельного уполномоченного recovery transition даже после независимого подтверждения владельцем.

Сценарий: timeout после отправки запроса, потерянный response или interrupted tool. Host впоследствии установил цену/фактический результат по provider request ID или состоянию целевой системы. Поддерживаемого пути закрыть hold с сохранением evidence и продолжить ту же работу нет; ручное редактирование journals/task JSON не является приемлемой production операцией. Это availability/операционная проблема, а не разрешение автоматически retry unknown requests.

Исправление: отдельная host-only reconciliation operation с immutable call/effect ID, исходной uncertainty записью, внешним evidence reference/hash, actor, reason, resolved charge/effect, временем и явно ограниченным scope. Два независимых аспекта: charge settled и effect resolved. Запись должна быть идемпотентной и crash-recoverable между task/ledger/session; не выдавать model tool, не считать обычный model text или approval доказательством. Не копировать новый budget/account ради обхода hold.

Acceptance: timeout → refusal of resume → host evidence-backed resolve → ровно одно settlement → продолжение без повторного provider/tool вызова. Повторный reconcile идемпотентен; несовпадающий call ID/evidence/revision отклоняется; сбой между любыми двумя persist boundaries восстанавливается; unresolved effect не очищается reconciliation стоимости и наоборот.

## RT-03 — P2: session append повторно читает и валидирует всю историю, суммарно O(N²)

**Подтверждено счётчиком реальных validations.** `sessions.py:175–190` каждый append вызывает read под lease; `193–208` читает все bytes и разбирает все строки; `211–227` заново валидирует каждый event, включая `_clean`/encoding. Для 20/40/80 appends probe измерил **210/820/3240** проверок старых events — точно N(N+1)/2. Это алгоритмическое доказательство, не latency benchmark. Hard cap 64 MiB ограничивает размер одной истории, но не устраняет квадратичный рост; большие payload усиливают эффект. `fsync` на каждый append является дополнительной стоимостью, не причиной вычисленного O(N²).

Влияние: более длинные agent sessions, tool-heavy tasks и большой response history постепенно тормозят storage critical section и увеличивают CPU/allocation pressure на небольших машинах. Нельзя экстраполировать секунды или RSS из этих probes.

Исправление: transactional journal store (например SQLite WAL с schema/migrations и bounded records) либо segmented append-only journal с подтверждённым sequence/offset/hash checkpoint и incremental validation. Полная integrity validation при explicit verify/recovery; нельзя просто доверять cached sequence после чужой записи или corruption. Добавить rotation/retention и disk-full режим с гарантированной фиксацией safety observations.

Acceptance: при фиксированном payload добавление N событий делает O(N) total record validation в обычном single-writer пути; 1k/10k/100k event load с p50/p95 append latency и peak RSS на целевых ОС. Дополнительно tail corruption, mid-file tampering, concurrent writer, interrupted checkpoint и disk-full. Ухудшать fail-closed проверки ради скорости нельзя.

## RT-04 — P2: новые host capabilities не компонуются с публичным TaskRunner API

**Подтверждено signature probe и чтением call site.** `agents/task_loop.py:31–39` принимает лишь config/project/backend/approval/question. `_execute_step` в `task_loop.py:414–425` создаёт Orchestrator без capability arguments. Между тем `llm/orchestrator.py:604–615` принимает workspace_session, lsp_client, documentation_client, hooks, tool_registry, role_tools, а SDK RunHandle передаёт их (`sdk.py:36–58`). Probe подтвердил отсутствие всех шести параметров у TaskRunner при наличии у Orchestrator.

Влияние: individually implemented SDK integrations нельзя штатно включить в durable reviewed multi-step task pipeline. Для этого приходится обходить TaskRunner или наследовать/подменять внутренний `_execute_step`; в результате легко потерять общий TaskBudgetBackend, lease, cancellation, scope и final host checks. Это integration gap, а не утверждение, что LSP/MCP вообще не работают.

Исправление: типизированный host capability provider/factory, создающий scope-bound capabilities для task workspace и конкретного step. Нельзя просто передать исходный root-bound LSP/workspace client в staging: capability должна перепривязываться и закрываться корректно. Сделать task context, budget, cancellation, hooks, role allowlist и effect recording общими инвариантами. Не давать модели конструировать executables/capabilities.

Acceptance: documented SDK example двухшаговой задачи с injected read-only documentation/custom tool/hook под тем же бюджетом; source-bound LSP работает только на staged workspace; hook/unknown effect pauses and prevents publication; arbitrary original-root mutation denied. После fresh process recovery capabilities восстанавливаются только trusted host, разрешения не импортируются из session summary.

## RT-05 — P2, static durability gap: publication «durable before write» не подкреплено fsync

**Только статический вывод; power-loss/VM-kill experiment не запускался.** `agents/changeset.py:243–247` сохраняет journal через write_text + replace без fsync; `365–374` записывает backups без fsync, затем комментирует all intents/backups durable before first target write. `agents/source.py:61–66` заменяет target без fsync staging bytes/parent directory. Закрытие файла и atomic rename не эквивалентны гарантии persistence после OS crash/power loss. Уже реализованная logical exception/process recovery не доказывает этот более сильный failure model.

Сценарий: source replace попал на устойчивый носитель, а backup/intents ещё только в cache; после потери питания recovery не располагает обещанным before image. Реальный порядок зависит от ОС/filesystem/storage и здесь не измерен.

Исправление: явно определить supported crash model. Если нужен power-loss recovery — durable writes/flush file handles, fsync directories на поддерживаемых платформах, последовательность write-ahead журналирования и проверенный Windows equivalent; либо transaction store + recoverable content blobs. Уточнить документацию, если гарантируется только обычный process failure.

Acceptance: fault injection каждого persistence boundary плюс controlled kill/restart на Linux/Windows VM, verification exact source/journal/backup hashes; tests для disk full и inaccessible backup. Нельзя считать обычный exception test доказательством power-loss durability.

## Порядок runtime работ

1. RT-01: сериализация source publication/recovery — предотвратить потерю пользовательских правок до расширения параллелизма.
2. RT-02 и crash consistency: определить call/effect/reconciliation state machines и единый durable transaction contract. Это prerequisite для безопасного provider failover после неопределённых запросов: смена provider не делает предыдущий request «не отправленным».
3. RT-04: capability composition при неизменных budget/effect/scope invariants. Только затем расширять интеграционные claim и multi-agent возможности.
4. RT-03: storage/scale budget и измеряемые latency/RSS SLO, затем workload/chaos acceptance. На текущем ноутбуке ограничиться малыми probes; нагрузки и crash tests выполнять на подходящих CI/VM workers.

Docker не исправляет RT-01–RT-04: это host runtime correctness/availability. Удаление контейнера без замены execution isolation к ним не относится. Никакой finding здесь не требует от пользователя установить Docker Desktop.
