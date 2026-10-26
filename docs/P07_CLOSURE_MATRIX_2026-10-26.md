# P07: точная матрица закрытия и аудит отрицательных контролей

Снимок 26 октября 2026. **P07 OPEN**. Документ фиксирует исходные требования,
а не меняет их под доступный бюджет или имеющиеся результаты.
Основание: `PRODUCTION_ROADMAP.md`, раздел P07; `ENTERPRISE_CORE_PLAN.md`, E01;
`COMPETITIVE_ACCEPTANCE.md`, D/E. Новых model calls/remote runs этим аудитом нет.

## Что именно требуется закрыть

| ID | Обязательство | Достаточное evidence | Текущее состояние |
| --- | --- | --- | --- |
| P07-01 | E01 honest verification prerequisite | Typed public VerificationReport; identifier/citation/syntax/tests/requirements/critic раздельно; false completion regressions не допускают behavioral PASS | Реализация и отдельные tests присутствуют; финальное E01 acceptance на runtime кампании требуется связать явно |
| P07-02 | Accepted isolated execution до candidate/setup/tests | Exact-source Linux/Docker acceptance, scope/secret/cancel/cleanup evidence; каждый target test в принятом isolated worker | Есть scoped acceptance runtime9645972b; первоначальный cancellation FAIL и успешный повтор сохранены. Это не root-cause fix intermittent отмены |
| P07-03 | Публичный supported journey | CLI/SDK/API: understand → plan/subtasks → authorized autonomous edits → checks → diff/publish, без monkeypatch consumer | P00–P06 host journey принят отдельно; source-only paid diagnostic не заменяет live full journey |
| P07-04 | Все 30 сред | Для каждого pinned task base requirement FAIL, gold PASS, plausible wrong requirement FAIL, сохранённые regression requirements, исходный target runner | 30 архивов round7 скачаны/checksum verified; **6 ACCEPTED / 24 OPEN** |
| P07-05 | Разделять broken environment и требуемый negative failure | Setup/collection/runner errors отдельно от host-verifiable failures внутри requirement testcase; никакого общего «любая ошибка — полезный FAIL» | Нужен ремонт классификации/runner на фактических логах, особенно SymPy17139 и pytest7168 |
| P07-06 | Замена раскрытого SymPy holdout | Новая неизученная task/repository выборка с frozen split/SHAs до model inspection, independent oracle; текущий SymPy21614 остаётся development-only | OPEN; R1/R3/R4 раскрытый кейс не превращаются в независимые holdout samples |
| P07-07 | Capable plain-agent baseline | Действующий iterative read/edit/check baseline, одинаковые authority/model/provider/caps/retry rules, достаточный preflight; failure сохраняется | Подготовлен новый text-JSON source baseline; старый Gemini baseline получил provider message:null. Live Sonnet baseline пока не оценён |
| P07-08 | Full Atomic, а не упрощённая arm | Реальные in-loop checks, bounded autonomous repair/replan/task DAG и public supported journey при одинаковом envelope | Новый p07_campaign.py — **source-scoped development**: один заранее известный writable path, syntax-only, target tests после generation, max_replans=0. Не full Atomic P07 acceptance |
| P07-09 | 30 matched pairs | 30 pinned независимых tasks × 2 arms, одна frozen model/provider/config, одинаковые max caps, порядок arms чередуется; source/config/code SHA и все physical attempts | OPEN; три Gemini диагностические пары одного SymPy-кейса не являются 3 независимыми задачами или matched Sonnet comparison |
| P07-10 | Independent candidate grading | Original candidate bytes/hash, новый isolated workspace из pinned base, hidden requirement + regression tests; candidate не меняет oracle | Механизм grading подготовлен; в R1/R3/R4 кандидатов нет, candidate oracle PASS отсутствует |
| P07-11 | Complete utility report | Verified end-to-end success, false completion, interventions, complete cost/success включая failures, tokens/calls, wall p50/p95, stop reasons, inference + semantic-index costs | OPEN; есть per-attempt accounting диагностик, нет отчёта 30 пар. Unknown cost не превращать в ноль |
| P07-12 | Ablations после работающей основной пары | Отдельно graph retrieval, semantic layer, task DAG, critic; одна изменённая переменная, те же hidden budgets/permissions/oracle | OPEN; graph/semantic отсутствие в baseline не заменяет четыре controlled ablations |
| P07-13 | Provenance и воспроизводимость | Все manifests, task/base/runtime hashes, model IDs, dependencies, images/digests, prompts/tool outputs, original ZIPs + checksums; каждый attempted/blocked/not-run case в denominator | Есть round7 и R1–R4 archives. Новому campaign runtime нужны свои remote evidence, точный overlay SHA и installed dependency receipt |
| P07-14 | Независимый итоговый review | Review кода/evidence того же SHA с перечислением незакрытых требований, без self-review как independent | OPEN для финального P07 experiment/runtime |

Исход P07 может быть отрицательным по качеству: проведённый воспроизводимый
эксперимент не обязан демонстрировать преимущество. Но отсутствие model patch,
незавершённые pairs, syntax-only или неподходящий baseline нельзя переименовать
в «полный P07 пройден». Для последующих **efficiency/competitive claims** требуется
заранее выбрать одну гипотезу (например quality margin 5 п.п. и cost/success −20%),
paired interval и отдельный powered confirmation holdout. Неопределённый interval
не означает PASS; эти proposed engineering targets не являются отраслевой нормой.

## Проверенная калибровочная исходная точка

Round7 workflow `40294478109`, runtime `b0f87b676b1e8152c5bdfad7545ab96dde3e2a99`,
tested merge `2f4fdf93741e84ad21f73eaf95ba7a1037e54928`.
`validation/p07-20261026/calibration-round7/summary.json` содержит все 30 уникальных
task IDs ровно один раз. Все 30 ZIP проверены против GitHub digest.
Шесть принятых: pylint6506/7228, pytest6116, SymPy16988/20212/21614.

Ниже — именно 24 OPEN строки; сокращения RF=requirement_failed, VS=verified_success,
IE=infrastructure_error. Нормализованные статусы сохранены как историческое
наблюдение, а не окончательная причинная классификация. Уточнения сверяются с raw log.

| Case | Base / gold / wrong round7 | Конкретный следующий предмет проверки |
| --- | --- | --- |
| requests1963 | RF / RF / RF | Gold имеет 50 FAILED; broad HTTP-dependent baseline failures, не валидная environment calibration |
| requests2148 | RF / RF / RF | Gold имеет 55 FAILED; prerequisite HTTP/test environment до model assessment |
| requests2317 | RF / RF / RF | Gold имеет 62 FAILED; проверить HTTP service/fixtures и историческую dependency compatibility |
| requests2674 | RF / RF / RF | Все три имеют 66 FAILED; required failure не отделён от broken environment |
| requests3362 | IE / IE / IE | 108 ERROR в каждой arm; raw setup log подтверждает recursive httpbin fixture при отсутствии нужного plugin |
| requests863 | RF / RF / RF | Gold64 FAILED; сетевые environment dependencies сохраняются, gold не принят |
| pylint5859 | RF / VS / RF | Wrong ломает пять лишних existing tests; контроль слишком широк, см. ниже |
| pylint7080 | RF / RF / RF | Gold3 FAILED; сначала разобрать unrelated existing regressions, не менять oracle assertions |
| pylint7114 | RF / RF / RF | Gold2 FAILED и XFAIL; затем wrong8 FAILED; среда/контроль требуют отдельного разбора |
| pylint7993 | RF / VS / RF | Wrong проходит F2P header testcase и ломает другой existing testcase; это неправильный отрицательный контроль |
| pytest11143 | IE / RF / RF | Gold24 FAILED; base содержит ERROR; runner/plugin/self-test environment до quality denominator |
| pytest11148 | IE / IE / IE | Gold6 FAILED/7 ERROR и parser values ERROR:; определить actual setup/test statuses из raw output |
| pytest5227 | RF / IE / IE | В v1 PASS_TO_PASS попали `[100%]`, `[` и `[100%]------------------------------` из nested progress. Final outer gold summary имеет 34 настоящих PASS; исправление labels только в новом v2 manifest, свежая калибровка обязательна |
| pytest7168 | IE / VS / RF | Base parsed tests={} при gold11PASS; base import/collection crash не доказывает требуемые assertion failures |
| pytest7432 | IE / IE / IE | Gold6 FAILED/6 ERROR; проверить required plugin fixtures и self-test runner |
| sphinx7738 | IE / IE / IE | Gold parsed tests={}; PASS parsing/verbosity mismatch — потенциальная причина, нужен raw log |
| sphinx8474 | IE / IE / IE | Parser выдаёт только FAILED, gold76; missing required entries и actual regressions разделить |
| sphinx8506 | IE / IE / IE | Gold parsed только один FAILED; необходимые PASSED entries отсутствуют |
| sphinx8595 | RF / IE / RF | Base/wrong целевой FAIL, gold tests={}; проверить реальный gold PASS output и parser |
| sphinx8713 | IE / IE / IE | Parsed только failures; required PASS entries отсутствуют, gold имеет дополнительный FAIL |
| sphinx8721 | IE / IE / IE | Parsed только failures; нужно восстановить реальные PASSED и отдельно исправить environment FAIL |
| sympy13971 | IE / IE / IE | Gold9 FAILED/9 ERROR; file:test aliases не должны порождать дубли, реальные testcase failures не скрывать |
| sympy17139 | IE / VS / IE | Required test-body TypeError — проявление исходного бага, не обязательно setup failure; wrong также ломает existing tests |
| sympy18057 | RF / VS / RF | Added protected test_non_string_equality не включён в официальный F2P; ошибочно посчитан «регрессией», см. ниже |

## Отрицательные контроли: корректировки с основанием

Это **предложения**, не выполненные/принятые калибровки. Замена controls требует
нового version/hash/rationale и свежего base/gold/wrong grade. Старые ZIP и
controls сохраняются. Нельзя подбирать критерий после модельного результата.

### pylint5859: ограничить ошибку punctuation boundary

Требование: `test_non_alphanumeric_codetag` с `notes=["???"]`. Текущий wrong
заменяет конец строки `\Z` на `[a-z]`, ломая todo-without-message,
xxx-without-space, fixme, other codetag и todoist checks. Он подтверждает
несколько регрессий вместо узкой правдоподобной ошибки.

Предложенный wrong для no-notes-regex branch:
`regex_string = rf"#\s*({notes})\b(?=(:|\s|\Z))"`.
Ошибочно сохранён word boundary, поэтому `???` по-прежнему не распознаётся,
а исправленный trailing lookahead остаётся. Гипотеза: F2P FAIL, existing word
codetags PASS. Это необходимо реально проверить; не считать гипотезу результатом.

### pylint7993: контроль обязан провалить header requirement

Требование: `test_template_option_with_header` с escaped JSON-like braces.
Текущий wrong `r"\{(\w)(:.*)?\}"` распознаёт только односимвольные fields;
target header testcase проходит, но `test_template_option_non_existing` падает.
Значит wrong не проверяет исходную ошибку, несмотря на красный статус job.

Предложенный wrong: `r"\{([^}]+?)(:.*)?\}"`: попытка ограничить закрывающую
скобку вместо корректного ограничения имени `\w+?`. Он остаётся уязвимым к
escaped/opening braces, сохраняя многосимвольные обычные field names.
Гипотеза: required header FAIL, existing unknown-name handling PASS. Fresh-grade обязателен.

### sympy18057: new requirement не является existing regression

Protected `test_patch` добавляет `test_non_string_equality` и меняет `test_var`.
Официальный `FAIL_TO_PASS` перечисляет только `test_var`; добавленный тест
выпадает из requirements и strict extra-failure rule считает его регрессией.
Base/wrong проваливают оба, gold проходит оба. Правильная процедура:

1. Перед model run проверить manifest/official test IDs и содержимое test_patch.
2. Выпустить явную новую версию oracle requirement manifest, включив добавленный
   testcase в requirement obligations (с protected source digest и rationale).
3. Все прежние existing regression obligations сохранить; не объявлять любой
   новый observed failure разрешённым. Решение принимается по patched test source,
   а не автоматически по тому, что base упал.
4. Повторить base/gold/wrong в новой среде, сохранить оба manifest versions.

### sympy17139: ошибка внутри требуемого testcase отличается от infrastructure

Тесты требуют, чтобы complex exponent не вызывал invalid relational comparison.
Base выбрасывает TypeError в testcase — это сам bug; gold проходит. Правило
«любой ERROR — infrastructure» неверно на этом кейсе. Но обратная замена
«любой ERROR — правильный requirement FAIL» тоже неверна: requests3362 setup
fixture ERROR нельзя принять как полезный отрицательный результат.

Нужна версия classification contract, различающая collection/setup/teardown,
runner identity и failure/test-body exception по raw phase/trace evidence.
Текущий wrong инвертирует guard и ломает TR5/TR6/fu/TR15/simplify_expr.
Более узкий правдоподобный wrong-кандидат — проверять `rv.exp.is_real is None`
вместо `not rv.exp.is_real`: он ошибочно продолжит обработку заведомо non-real
exponents, сохранив часть real/unknown поведения. Гипотезу надо fresh-grade;
не принимать existing regressions ради достижения 30/30.

## Бюджет и реальная граница завершения

Исторический budget receipt до нового поручения: всего было разрешено $6,
booked today $0.035611375 на owner2, остаток прежнего разрешения $5.964388625.
Затем пользователь **явно увеличил суммарный бюджет до $20** для owner2 и
participant3, ориентировочно по $10 каждому. Это отдельная новая авторизация;
старый $6 потолок не должен использоваться как текущий общий cap.
Известный предшествующий расход учитывается внутри нового total, поэтому без
других расходов nominal combined remainder = **$19.964388625**. Это арифметика,
а не свежее свидетельство quota: перед запуском отдельно сверяются status,
суточная/месячная квота, owner authorization и все uncertain attempts каждого
участника. Credentials/accounts нельзя автоматически менять при отказе или
исчерпании квоты; распределение заранее фиксируется в campaign manifest.

Полные 30×2 при прежних $0.80/arm = $48 **до ablations/confirmation** по-прежнему
превышают новый total$20. Более низкие равные caps — новый заранее объявленный
профиль эксперимента с риском insufficient budget, а не эквивалент старого.
Нужно заранее распределить $20 между primary pairs, pilot, holdout и ablations;
все суммы, попытки и unknown charges входят в общий и per-owner ledgers.
Подготовка скрипта не является model execution. Нельзя молча повышать total,
скрывать исчерпанные arm, объявлять source-only pilot полным P07 или автоматически
считать дополнительные аккаунты способом обхода ограничений платформы.

## Версионированная migration oracle manifest

Исходный `benchmarks/stage4/corpus.json` сохранён побайтно:
`01e282e7f2b25c49dfc6ce6ed83f6bc3d253c88c0864d2d02431dd1174e2c12b`.
Новый `benchmarks/stage4/corpus-p07-v2.json`:
`ac32cd32aa0305821b440b9de277f0b3e3d5c1c55c9b485091f96b19773713dd`.
`p07-manifest-migrations.json` содержит каждую операцию, before/after counts,
archive/log/test_patch SHA256, tested merge, rationale и статус
`proposed_requires_fresh_calibration_and_review`.

Изменены только две label collections: удалены три несуществующих progress IDs
pytest5227 (все 31 genuine P2P остаются) и добавлен protected new requirement
SymPy18057 (test_var и все P2P остаются). В manifest по-прежнему 30 задач,
ни source patch, ни protected test patch, ни task ID/base/repo не изменены.
Raw log data разобрано как данные; target code этим аудитом не выполнялся.
Migration сама не присваивает ни calibration PASS, ни качество модели.

## Детерминированный paired utility report

`tools/p07_paired_report.py` принимает только metadata JSON и не вызывает модель,
setup, target imports или tests. Команда:

```bash
python tools/p07_paired_report.py --manifest experiment-manifest.json --records arm-records.json --obligations evidence-obligations.json --output paired-report-new.json
```

Host manifest имеет `schema_version=1`, ровно 30 tasks с `instance_id`,
`base_commit`, общим для пары `source_sha256` и predeclared `arm_order`.
`frozen` фиксирует `model`, `provider`, `runtime_sha256`, `limits`,
`permissions_sha256` и `prompt_profile_sha256`. Это hash всего source manifest,
а не хэш одной произвольно выбранной функции.

Каждая arm record содержит matching identity, `order_index`, terminal `status`,
`attempts`/`physical_calls`, явно известные `semantic_index_cost_usd`,
`wall_seconds`, `human_interventions`, boolean `completion_claimed`, `stop_reason`.
Платный attempt должен иметь числовой provider cost и известный request outcome;
configured estimate не становится invoice и останавливает aggregate admission.
Отклонённая до send/явно refused попытка может иметь подтверждённую нулевую стоимость.
Atomic `study_scope` должен быть `full_atomic_public_journey`; source-only запись
не может незаметно заменить full arm.

Independent grade связывается с task/base/candidate SHA, принятым calibration,
image digest и evidence SHA256. Для success нужны requirements+regressions PASS;
непрошедшая oracle infrastructure не превращается в model failure или success.
No-candidate terminal failure остаётся в denominator; известный false completion
отделён от неопределённого, когда grade пока отсутствует.

Report сохраняет затраты неудач и duplicates, но duplicate task-arm не добавляет
успеха. Показаны verified success/30, complete cost per success (при нуле success
или incomplete usage — null с причиной), tokens/calls, known interventions,
wall p50/p95 с явным count, stop reasons и все missing/extra/duplicate rows.
Три-task pilot может быть описан как 6 observations внутри expected30 report;
заменить expected denominator на3 и назвать это full P07 report нельзя.

E01, execution, full public journey, fresh holdout и отдельные ablations
graph/semantic/DAG/critic требуют evidence references. Ablation должна сохранять
frozen signature и иметь все predeclared observations. Даже при полном metadata
coverage output остаётся `p07=OPEN`, `closure_readiness=requires_independent_review`:
этот скрипт проверяет accounting supplied host receipts, а не аутентичность
внешних claims и не semantic entailment модели.

## Новый holdout без просмотра solution/problem этим агентом

В рамках этого аудита новая задача не выбиралась по содержимому issue/gold и
не открывалась. Ни одна task не названа blind только потому, что другой процесс
прочитал её раньше. Для воспроизводимой замены раскрытого SymPy21614:

1. Freeze upstream dataset repo+commit, файл и SHA256, список development task IDs
   и repository split, модель/runner/caps, гипотезу, seed до доступа генератора.
2. Отдельный trusted metadata-only extractor читает dataset как данные и выпускает
   только `instance_id`, `repo`, `base_commit`, version/test-environment identity;
   поля `problem_statement`, `patch`, `test_patch`, assertions и solutions в
   planning context не выводятся. Raw dataset остаётся у oracle custodian.
3. Для repository holdout отфильтровать **все пять development repositories**;
   если upstream не содержит иных repo, честно объявить within-repository holdout
   и не заявлять repo-independent confirmation. Task, issue и patch из прежних
   чатов, fixtures, тестов и логов входят в exclusion manifest.
4. Из оставшихся metadata записей сортировать по
   `SHA256(seed || dataset_sha256 || instance_id)`; взять заранее заданное N.
   Сохранить selection algorithm/version, seed, exclusion hash и selected IDs
   с SHA, не смотреть содержимое при выборе. Для независимого подтверждения
   размер рассчитывается отдельно по paired discordance, не фиксируется на N=1.
5. Oracle custodian до generation калибрует выбранные среды; result наружу —
   hashes+admitted/blocked, без solution. Недопущенный case остаётся в selection
   ledger. Правило replacement by next hash-ranked ID фиксируется до результатов.
6. Только после freeze generation worker получает issue+base source. Oracle
   получает generated patch независимо. Первое раскрытие problem означает, что
   дальнейшая отладка на этом case делает его development; нужен новый holdout
   для следующего confirmation, а не повторное заявление blind.

Это механизм отбора, **не evidence уже выполненного holdout** и не утверждение,
что oracle calibration можно сделать без доверенного отделения solution data.

## Порядок независимой работы

1. Ремонтировать environment/parser/control inputs без модели; rerun только
   затронутые cases, полный ledger всех 30 и originals сохраняется.
2. Малый Sonnet transport/agent pilot с заранее frozen caps и hashed source;
   unknown charge останавливает общий admission, не только один workspace.
3. Original candidates → отдельный calibrated isolated oracle; ни gold patch,
   ни hidden tests модель не видит. Message:null не заменяется выдуманным ответом.
4. Добавить full public journey/in-loop tests и fixed authority для полной
   Atomic arm; source-only результаты оставить development evidence.
5. Заменить раскрытый holdout, провести доступные matched pairs, собрать все
   метрики и controlled ablations. Финальный review сверяет **каждую строку**
   матрицы с exact-source evidence; remaining/blocked/not-run не становятся PASS.
