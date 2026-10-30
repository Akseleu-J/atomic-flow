# Package 40 — P06 typed failures и durable repair ceiling

25 октября 2026. Base a756447 → runtime a61c6dc1b8247d6b0686aeace3533df932171517,
локальный checkpoint fe9380109627982d645084b5f5d2e51bf3975c4c имеет тот же tree
3b63cb00866b0e81add31bfea832c44034827a6b. CI-corrected runtime
**f9fd48d5bacce8b3c93bf9b15677df3f7edbc4b3**, tree
9067a4044c56e04a49070fd8a2b01d3ee042627a. Experimental / draft PR №2.

## Изменение поведения

Раньше любой StepFailure попадал в deterministic_test_failure; 25% repair allowance
существовал только в decision metadata. Теперь failure kind задаёт trusted host,
а automatic repair требует typed failing-check receipt. Текст модели, timeout,
ошибка сбора/запуска pytest, отказ policy и неизвестное исключение не заменяют receipt.

- pytest exit 1 → deterministic_test_failure; syntax/ruff/mypy failure →
  deterministic_check_failure. Только эти виды допускают bounded repair.
- pytest exit 2/5 и другие неподдерживаемые exit codes → check_unavailable;
  timeout → check_timeout; model critique → model_critique;
  отказ policy → scope_violation_detected; missing/malformed protocol → stop.
- При ошибке сохранён Step.failure: typed kind, check/exit/outcome, step index,
  attempt started_at, checked-source revision, current-source revision и rollback
  revision. Classifier и state decoder валидируют поля. Проверка другого attempt,
  stale source или более поздняя mutation не разрешают repair. Перед replan повторно
  проверяется rollback revision; legacy failed steps без receipt требуют review.
- run_command и post-edit pytest аудируют admitted source до/после процесса.
  Изменение исходных inputs или добавление indexed code/config становится source_conflict.
  Новые data outputs в одноразовой проверочной копии разрешены; publication scope
  постоянного TaskRunner workspace проверяется полностью перед commit. Timeout/exit/source metadata проходят
  через ToolResult.summary и rollback artifacts, а не теряются как error string.
- Синтаксическая проверка связывает compiled bytes с source hash; pytest source
  snapshot и откатившийся workspace имеют разные явно сохранённые revisions.

## Durable spending

Первый допустимый repair после завершения независимо runnable work фиксирует
Task.repair_budget: id, cap=25% оставшегося task budget, baseline spend/event и
failure step. Этот единственный episode сохраняется до terminal task state.
Replan/resume/review/new TaskBudgetBackend не обновляют cap. После активации
он охватывает ВСЕ оставшиеся calls этой задачи, включая planning и execution.
Это консервативный общий ceiling; отдельные новые allowances на каждую ошибку
не выдаются. Пока независимые шаги дренируются перед первым replan, действует
прежний task budget; repair episode ещё не активирован.

Каждая физическая reservation проверяется по выбранным model/prices и тегируется
repair_id. Shared parent backend/call lock сохраняет ту же границу для native,
routed и worker calls. Ceiling действует независимо от global task budget switch.
Unknown call сохраняет hold; missing attribution/invalid costs/moved accounting
останавливают продолжение. Known response cost сначала сохраняется в task/daily
ledger, затем проверяется ceiling/reservation: overspend не теряется и блокирует
следующие calls/steps/publication. Final host gate тоже проверяет repair state.

Это admission на основе declared prices, не гарантия provider invoice. Физический
ответ может сообщить стоимость выше reservation; такой факт учитывается и задача
останавливается. Кооперативная отмена не доказывает отмену remote inference.

## Local checks

Final acceptance.xml: **226 PASS / 3 SKIP**, 229 unique case IDs, Python 3.12.14,
Windows. Один объединённый selection: repair_budget/failure_policy/failure_wiring/
internal_replan/lineage/goal/dependencies/task_budget + execution/edit/file operations/
completion/capability/composition/tool-policy/routed-accounting/workers.
Ruff/compile PASS. Evidence: validation/p06-repair-20261025/.
Промежуточные 2 FAIL ожидали прежний failed status/repair по preflight error;
fixtures обновлены под stop/pause и настоящий host-failed-check outcome, исходные
JUnit/logs сохранены. Counts разных selections не складываются.

Поведенческие regressions: cap до send; no reset на restart и новый replan;
planner/execution shared allowance; native admission; unknown usage без replay;
reported overspend сохранён; invalid persisted state; stale attempt/source;
timeout/collection/zero-tests отдельно; later mutation; actual snapshot mutation
в run_command и verify_edit. Соседние suites проверяют прежние scope/rollback/
accounting/worker safety contracts.

## CI / source evidence

Финальные workflow IDs, tested merge, JUnit, archive/source hashes и job statuses
сохраняются в ci-verified.json и ci-runs.json этого evidence package после окончания
CI. Runtime публикуется отдельно от documentation follow-up. Нельзя переносить
зелёный статус другого SHA; сравниваются Git tree и normalized src/tests hashes.

## Review, ограничения и решение

Self-review тем же исполнителем; субагенты не запускались. Findings закрыты:
1. Blanket StepFailure classification → typed kinds и validated receipts.
2. Metadata-only repair cap → physical admission/settlement/resume/publication gates.
3. Потеря timeout/exit metadata после rollback → сохранение через ToolResult summary.
4. Проверка устаревшего source → source audits, later-mutation и attempt binding.
5. Неизвестные/ошибочные costs и silent cap reset → persisted allowance/attribution.

Scope ACCEPTED для f9fd48d по verified artifacts и CI; локальные interrupted
прогоны не объявлены успешными.
Полный P06 всё ещё OPEN: независимый review и оставшиеся acceptance matrix cases
требуют отдельной сдачи. Evidence hash — integrity check, не attestation dishonest
host. Check exit 1 сам по себе не доказывает истинность тестового oracle; dishonest
project tests остаются отдельным worker/oracle trust gate. Source audit полнее,
но дороже event-only hints; latency/p95/RSS/soak здесь не измерялись. Несвязанный
source drift после failed check консервативно требует fresh review.

Provider/model/live calls=0, реальные новые расходы=0. Scripted fixtures не
подтверждают качество LLM repair. Четыре production-блока (quality, scale, release
provenance, isolated worker/crash/OS review) остаются OPEN. Docker-free client/core
не отменяет изоляцию недоверенного выполнения.

Следующее: независимый P06 review / оставшаяся journey matrix; закрыть explicit
P01–P03 integration gaps из marathon report, затем P07 frozen quality evaluation
на подходящем host. Не повторять исторический unknown SymPy request вслепую.

## CI correction history

Первый runtime a61c6dc: checks 40215708331 RED (5 FAIL / 1275 PASS / 14 SKIP
на Python 3.11); integrations 40215708324 SUCCESS. Три fixtures сохраняли старую
семантику failed/replan без proof, два confidentiality tests выявили слишком
строгий запрет новых disposable data outputs. Исправлены expectations и boundary
аудита, исходный CI failure сохранён в first-ci-failure.txt.

Локальный recovery прогон final-fix.xml: 31 PASS / 3 FAIL / 1 SKIP при нехватке
commit memory Windows; два subprocess exit 3221225773 (0xC000012D), запуск Node
отказал с os error 1455. Это не успешная acceptance. Повтор одного task fixture
после освобождения памяти: single-fix.xml, 1 PASS. Итог повторного selection
и exact-source CI записываются отдельно.

Повтор recovered.xml остановлен исполнителем: 7728 KiB RAM / 274144 KiB virtual
free, resource-stop.json. Незавершённый selection не считается acceptance.
Ruff и git diff --check после CI fix PASS. Исправленный runtime направлен на
checks 40219160156 и interoperability 40219160140; результат фиксируется ниже.

## Финальная приёмка исправленного runtime

[Checks 40219160156](https://github.com/Akseleu-J/atomic-flow/actions/runs/40219160156)
и [interoperability 40219160140](https://github.com/Akseleu-J/atomic-flow/actions/runs/40219160140)
SUCCESS. Python 3.11.17 / 3.12.15: **1280 PASS / 14 SKIP каждый**, 1294 unique
cases в каждом наборе. Два installed-wheel checks, матрица 12 wheels на трёх OS,
реальный Docker и composition publish/cancel/source_conflict SUCCESS.

Скачаны четыре ZIP (host 3.11/3.12, Docker, composition); SHA256 сверены с GitHub
artifact digests. Offline verify.py проверяет workflow/job conclusions, tree equality
head f9fd48d ↔ tested merge a30b26b2fcfbeacc777225c434d9d4be832b86f4, normalized
SHA256 всех **273 src/tests Python files**, JUnit checksum/counts и runner results.
ci-verified.json содержит machine-readable отчёт. 12 отдельных wheel archives не
скачивались: для них подтверждены job conclusions. Documentation follow-up не
меняет runtime; его собственные workflow IDs доступны по опубликованному SHA.
