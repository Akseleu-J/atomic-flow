# Продолжение P07 — 29 октября 2026

## Актуальный checkpoint — 4 ноября

Необходимая инженерная граница **P07-E / Rust+TypeScript TaskRunner принята**
на `f76a59e`: CI41655855886, artifact13239936458 SHA256
`fbb8c466f62eb844ff13a1cb0dbc4940535087ae3094a03d4bbc4839b05a2cb4`.
Независимо перепроверены135rawhashes, шесть complete native checks,
FAIL → один replan → двухфайловый repair → fresh PASS → publication,
cleanup шести owned containers и supported graph projection.
Общий CI41655869867 SUCCESS; source-equal Python3.11/3.12 по3594PASS/26SKIP,
по485sourcehashes. Это scripted engineering acceptance, provider_calls=0.
Исследовательские пары/holdout/ablations и прежние paid UNKNOWN НЕ закрыты.

Текущая разработка по поручению владельца ведётся в обновлённом **zcode**,
base09d06c5, ветка `codex/studio-safe-zcode-20261104`.
[Studio checkpoint](STUDIO_ACCEPTANCE_2026-11-04.md): локально104RustPASS,
8JS PASS, actual native HTTP smoke PASS. UI поддерживает реальные project/source,
history, journal snapshot и host-only providers/budget. Generic checks не дают
Verified; поддерживаемый task/publication UI ещё требует admitted check contract.
Browser visual QA заблокирована URL guard; fresh native CI проверяется отдельно.
Нижние записи описывают исторический ход и не заменяют этот checkpoint.

Новое поручение владельца, 3 ноября: сначала ограниченный инженерный выход
TaskRunner с Rust + TypeScript пилотом, затем реконструкция по модулям.
Контракт и текущий gate: [polyglot TaskRunner](P07_POLYGLOT_TASKRUNNER_2026-11-03.md).
Приёмка Linux Docker ещё не установлена; исследовательские пары/holdout/ablations
сохраняются отдельным незавершённым scope, UNKNOWN не снимается.

Текущий пакет 3 ноября: [непрерывный pytest6116 diagnostic и обзор Rust](P07_CONTINUATION_AND_RUST_REVIEW_2026-11-03.md).
На `e167e92` принят captured one-container diagnostic FAIL → host repair →
PASS → fresh final: CI 41641265138 SUCCESS, 2620 completed nodes на каждой
стадии, точные 8 SKIP/11 XFAIL, 474 public blobs и raw streams сверены,
cleanup receipt=true. Artifact 13233906247 SHA256
`f79340e71d72984705033d89443354f8703c7e747a0bea5e233d4142cb5e081b`.
Source — отдельный root-owned tmpfs; одноразовый `/tmp` очищается между checks
при прежнем cap 128 MiB. Два исходных отказа сохранены. Это не TaskRunner
admission: следующий шаг — host-owned obligation/replan/publication/graph sync,
затем frozen matched pilot на принятых средах. P07 OPEN, provider calls=0.
Критерии 30 matched pairs/holdout/ablations сохранены. Rust reviewed workspace:
225 PASS/1 IGNORED локально, пять contract gaps — в отчёте; parity не заявлена.

Работать в `C:\Users\omirb\OneDrive\Документы\ChatGPT\atomic-flow 2\atomic-flow`, ветка `experimental`, draft PR №2. Начать с `docs/PROJECT_CONTEXT.md`; Git-команды выполнять только в этом клоне. Перед продолжением сверить HEAD, remote SHA и рабочий diff. P07 остаётся **OPEN**.

## pytest6116: six-failure focused reproduction

CI 40998043559 on `6e2981a355159496b1da06c9e68da0661f58bfd6` reproduced all six failures with full captured tracebacks; the previously unclear pdb failure is the old xdist plugin requiring the absent `usepdb` option. Two forked failures use removed pytest internals; progress failures include missing progress output and an execnet ExitCode serialization error. The Dockerfile, wheel and wrapper inventories match the previous diagnostic, but image ID and layers differ: this is rebuilt-image evidence, not an identical-image replay. Artifact 12951948601 SHA256 `c3dd53d6d494e23119ff1391501876abdcf116b018e34c8ec4fc994f1f4bf383`. Next qualify a separately versioned compatible dependency candidate and rerun unchanged tests; full ordinary admission and P07 remain OPEN. [Analysis](P07_PYTEST6116_COMBINED_FAILURE_ANALYSIS_2026-10-30.md).
## Реестр оставшихся ordinary-проходов

`benchmarks/stage4/p07-ordinary-coverage-v1.json` связывает 30 калибровок и шесть принятых Requests ordinary journeys с конкретными evidence hashes. Для 24 остальных полная public ordinary приёмка в этом реестре не установлена; отдельные более узкие executor и synthetic journey evidence не обесценены. Ближайший профиль — pytest6116, где native `tox.ini` задаёт `testpaths=testing`, исключает example_scripts и использует дополнительные шаблоны файлов. Bounded native collection CI 40865187820 сохранила совпадение 474 public Git blobs и 2414 collected nodes, но обнаружила два collection errors: отсутствуют xmlschema и hypothesis. Подготовлен и локально проверен prospective wheelhouse из трёх фиксированных publisher wheels, с активным closure и исходным attrs 23.1.0. Derived collection CI 40868874514: 2617 nodes в 61 public файле, ошибок коллекции и SKIP нет; сверены 474 source blobs и 314 файлов wheel, оба контейнера удалены. Это только collection, не запуск test bodies и не допуск TaskRunner. Полное execution CI 40869630592 NOT QUALIFIED: stdout усечён, native tail сообщает многочисленные ошибки, в том числе из-за повторной загрузки typeguard вложенными pytest. Подготовлена узкая фиксированная inherited policy; её влияние и новый bounded run надо проверить до typed admission. Подробный список условий закрытия: `docs/P07_REMAINING_AND_ORDINARY_COVERAGE_2026-10-29.md`.

## Requests863 public ordinary TaskRunner принят

CI 40841965525 на `3feda8c33b058768f4f1c2348d8b233fdcaf8d37`: пять публичных pytest файлов и synthetic probe, 107 PASS после одного replanning; проверены 982 raw hashes и восемь resource receipts. 167 public Git blobs сверены; известные исключения source filter и неформальный скрипт без pytest tests записаны в `docs/P07_REQUESTS863_TASK_ACCEPTANCE_2026-10-29.md`. Общий CI 40841952407 SUCCESS. Это scripted ordinary execution, без модели и hidden oracle. Далее определить и закрыть оставшиеся ordinary profiles, затем frozen matched study gates.

## Requests863 ограниченный health

[CI 40837223989](https://github.com/Akseleu-J/atomic-flow/actions/runs/40837223989) на `a841f55baaa50e3240b3b574fddff6f204cfe22b`: source-free реальный SSLv3/TLS/wire/Digest fixture принят под 1 ГиБ, 2 CPU, 128 PID, non-root и network=none. Контейнер удалён, raw stdout/stderr и archive SHA256 сверены. Отчёт: `docs/P07_REQUESTS863_BOUNDED_HEALTH_2026-10-29.md`. На этом предыдущем checkpoint был принят только health; последующую приёмку полного ordinary TaskRunner и generic dispatch см. выше.

## Requests2674 завершён в ordinary scripted scope

CI 40835105105 на `520158504af00ab1f08899553458916943ad52b1`: 163 collected, 162 required PASS плюс один source-declared XPASS после исправления synthetic probe, один replan и fresh final gate. Сверены 841 raw hashes, восемь ресурсных квитанций и все 132 Git blob публичной базы. Общий exact-source CI 40835100470 SUCCESS. Отчёт: `docs/P07_REQUESTS2674_TASK_ACCEPTANCE_2026-10-29.md`. Это не model/oracle grade. Следующий профиль — Requests863 SSLv3/wire. Текст ранее отражает исторические этапы.

## Начало интеграции Requests2674

Подготовлен bootstrap для Python2: JSON-имена модулей и argv преобразуются в ограниченные ASCII native str. Для 2674 добавлена отдельная точная политика публичного `test_requests.py::RequestsTestCase::test_response_iter_lines_reentrant` (source-declared xfail; calibration наблюдала XPASS с raw ID без `::()::`). Разрешены только xfailed/xpassed этого узла, новые SKIP/XPASS по-прежнему не проходят. Это только локальные контракты: 110 профильных тестов PASS; реальный synthetic bootstrap на Python2.7.18 принят в узком scope: CI 40832039669 на e3ae5fc908bbced6ff2afc97e85675e5c4ef87f9 SUCCESS, artifact 12879460458 SHA256 64a3fb73f4292d61d119140fd987989de28e2bcfae3ee7dd203500bce7733b35; шесть raw hashes, PASS/FAIL/required-SKIP refusal и точный XPASS проверены. Fixture/controller/TaskRunner integration ещё не приняты. Никакой generic dispatch 2674 не открыт. Продолжать с owned fixture/CA controller; не считать калибровку модели или качеством.

## Новый checkpoint: function36 TaskRunner

Runtime `875fd01b1f3f4b4521b887688d27a82678f47df1`: три полных public ordinary journey Requests1963/2148/2317 приняты в scripted synthetic scope; CI 40824311185 SUCCESS, соответственно 122/134/144 PASS после probe repair, required SKIP отклонён. По 823 raw hashes и восемь resource receipts на задачу независимо сверены. Публичные базовые деревья сверены полностью: единственное ресурсное исключение — CA вне candidate; добавлены два synthetic файла. General CI 40824307411 SUCCESS, Python 3.11/3.12 по 3267 PASS / 14 SKIP, Windows 4 PASS. Отчёт: `docs/P07_FUNCTION36_TASK_JOURNEYS_2026-10-29.md`; локальные архивы и assessments: `validation/p07-budget20/function36-task-v1/`. Model calls 0, hidden oracle не запускался, P07 OPEN.

Следующий приоритет: оставшиеся ordinary profiles и study gates. Function36 generic dispatch теперь включён только для трёх указанных принятых контуров; прежний текст checkpoint ниже исторический.

## Предыдущий checkpoint

Последний принятый runtime: `4d3c08e3eedcc47509ba700a8b8ec9029f335b13`. Полный public ordinary Requests3362 принят: CI 40818355491 SUCCESS, artifact 12872864520, SHA256 `4ce5d17e20198a0f3e2f31b40d288a2e623785d01d0f6b70e2af68b73ca8f4a1`. Сверены 810 raw hashes и 80 условий приёмки. Один replan, пять полных checks: 349 required PASS + один ожидаемый probe FAIL, затем четыре раза 350 required PASS. Три source-declared optional outcomes сохранены отдельно; все 353 collected items завершены. Fresh final gate, публикация только probe, graph sync и cleanup подтверждены. Evidence: `validation/p07-budget20/requests3362-full-public-v5/`. Это обычные public tests и synthetic repair probe, без модели и hidden grade.

Подтверждено:

- Synthetic Requests3362 controller: CI 40813084358 на `72bb7ef`, FAIL → edited PASS → required-SKIP refusal; 779 raw hashes проверены.
- Полный synthetic TaskRunner: CI 40814165859 на `500a0fd`, один replan, пять raw checks, публикация только probe, fresh final PASS и graph sync; 810 hashes проверены. Историческое качество модели этим не измерено.
- General CI 40818353076 на `4d3c08e`: по 3229 PASS / 14 SKIP на Python 3.11/3.12, Windows 4 PASS, wheel/Docker PASS; шесть ZIP digests, JUnit и 395 source hashes сверены с Git. Evidence: requests-full-public-core-ci-v2.
- Pure function36 CA overlay исправлен: core revision отдельно от resource manifest «core + единственный requests/cacert.pem». Копия кандидата не меняется, глобальный PEM-фильтр не расширен. Docker/obligation/resource receipt integration этого пути ещё нет.

Подробный отчёт и границы всех результатов: `docs/P07_REQUESTS_BOUNDARY_ACCEPTANCE_2026-10-29.md`.

## Полный ordinary inventory Requests3362

Все шесть `tests/**/test_*.py` независимо сверены с публичным base commit `36453b95b13079296776d11b09cab2567ea3e703`; hidden patch и gold не использовались. Дополнительно включён фиксированный synthetic repair probe. Всего 353 collected items.

V1 выявил URL-callable wrappers над plugin-owned HTTPBin servers и неподдержанную отчётность исторических xfail/skip. V2 ошибочно ушёл на прежний SHA и отменён; запись сохранена. V3 подтвердил устранение 115 teardown errors, но потребовал точного отображения raw pytest2.8 `::()::` IDs. V4 принял полный negative receipt и replan, затем получил 350 required PASS после исправления probe, но host validator ошибочно ожидал native exit 1 из-за non-strict XPASS. Правка откатилась; V4 NOT QUALIFIED.

В 4d3c08e исправлено последнее предположение: native exit остаётся неизменным и должен соответствовать required failures. Три source-declared optional nodes закреплены точной неизменяемой политикой: два отмеченных xfail-теста сохраняют XFAIL/XPASS отдельно, Python3 cStringIO setup skip сохраняется отдельно. Для них проверяются raw и canonical IDs и точная фаза. Required-SKIP вне этих трёх nodes остаётся запрещённым. Новые skips, неизвестные nodes, setup/teardown errors и неполные inventories не принимаются.

## Следующая работа

Полный Requests3362 ordinary путь и function36 journeys Requests1963/2148/2317 приняты в указанном synthetic scope. Продолжить остальные ordinary profiles и study gates. Остальные private Requests cohorts отклоняются до evidence-gated приёмки; не включать их по metadata/unit tests.

Далее нужны все 30 ordinary profiles, полный исторический Atomic/flat driver с равными лимитами, замороженный study profile/cohort/order/owner caps, 30 пар с независимым oracle, свежий holdout, ablations и final review. 30/30 environment calibrations уже приняты, но это не 30 решённых моделью задач.

## Бюджет и разрешения

Пользователь разрешил автономное продолжение P07, осторожные push и CI. Merge/release не разрешены. Paid ceiling $20; записанный расход $0.035931375, новых provider calls в этой работе нет. Модель: `anthropic/claude-sonnet-5@default`. Owner2 `akseley` и participant3 `umirbayulgaisha` — отдельные авторизованные контуры; не менять аккаунты для обхода квоты. Старая quota не является текущим балансом.

Перед paid send: frozen cohort/owner/model/profile/caps, durable reserve → remote checkpoint ACK → единственный send → actual usage settlement. UNKNOWN блокирует новые physical calls до сверки. Не откатывать и не удалять data/checkpoint branch `codex/p07-model-queue-paid-20261028-01`. Не смешивать scripted callbacks, live model calls, ordinary checks и hidden grading.

## Доступ и инструменты

Vault: `C:\Users\omirb\OneDrive\Документы\ChatGPT\computer_control\shared-access\.env.enc`; README и `shared-secrets.ps1` рядом. PowerShell 7.2+. Пароль владелец передаёт отдельно; его, tokens, headers и расшифрованный vault не писать в команды, файлы, artifacts или логи. Предпочитать узкий `.local-tools/with-github.ps1` с SecureString вместо импорта всех переменных.

Локальный `.local-tools/p07-operator.mjs` предоставляет `github`, `git`, `artifact`; credentials остаются в процессе. `artifact` сверяет GitHub SHA256. `.local-tools/p07-zip.mjs` проверяет ZIP members/CRC; при извлечении отдельно проверять confinement и свежую папку. Helpers не входят в clone; старые Node REPL bindings не считать существующими. Kaggle-контур operator закреплён за owner2, не использовать его как переключатель аккаунтов.

Python/Ruff: `.venv/Scripts/python.exe`, `.venv/Scripts/ruff.exe`. Node: `C:\Users\omirb\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`. Тесты выполнять последовательно с `-p no:cacheprovider`; Windows испытывала MemoryError. Перед commit обновлять `tools/update_project_index.py` и **дожидаться завершения**. Не запускать зависимый push, пока commit ещё работает в фоновой exec session. После push проверить полный remote SHA, после dispatch — actual head_sha и tested-commit в архиве.

Не делать `git add .`: есть исторические untracked evidence и `.atomic_flow/tree.lock`. Ничего не удалять. Core/client Docker-free; недоверенное исполнение требует принятого isolated worker. Протоколы: `GITHUB_WORKFLOW.md`, `REAL_API_BENCHMARK_PROTOCOL.md`, `P07_MATCHED_QUEUE_EXECUTION_DESIGN_2026-10-28.md`, `P07_PAID_QUEUE_PREPARATION_2026-10-28.md`.

## pytest6116: актуальная граница

Публичный сбор 2617 nodes в 61 файле подтверждён CI 40868874514, но два полных исполнения пока NOT QUALIFIED. Последний [CI 40870725655](https://github.com/Akseleu-J/atomic-flow/actions/runs/40870725655), artifact 12896565434 SHA256 `f1cb697e5237e7005c5f42711c01f00fbf42e92b2a8cbbe099a040e391d05e80`: вывод усечён; в хвосте 1439 failed, 1060 passed, 103 skipped, 9 xfailed, 6 errors и маркер 2617 завершённых nodes. Nested pytest по-прежнему загружает несовместимый vendored typeguard; `PYTEST_ADDOPTS` проблему не решил и меняет видимое тестам окружение. Источник (474 blobs) и cleanup подтверждены, provider calls = 0. Подробности и следующий gate: `docs/P07_REMAINING_AND_ORDINARY_COVERAGE_2026-10-29.md`. Не считать это ordinary acceptance.

Причина подтверждена исходным `src/_pytest/pytester.py`: `Testdir.__init__` удаляет `PYTEST_ADDOPTS` перед вложенными запусками (строка 538); Git blob `6b45e077b487e9b9166604e02e4c7fd4a01f9f79` совпадает с публичным деревом.

CI 40882353088 (`5d356c6`) дал полный диагностический pytest6116 отчёт: 2617/2617 nodes, 48 failed, 2439 passed, 114 skipped, 10 xfailed, 6 errors; выход не усечён, оба контейнера удалены, provider calls 0. Точечное отключение vendored typeguard устранило его tracebacks, но меняет metadata и оставляет прежний RECORD несогласованным: это не приёмка. Следующий кандидат закрепляет setuptools 44.1.1, проверяет publisher wheel и установленный inventory; другие ошибки и skips остаются отдельными gates.

CI 40886145374 (`4add856`) — полный diagnostic с setuptools 44.1.1: 2617/2617, 8 failed, 2484 passed, 115 skipped, 10 xfailed; 103 publisher-wheel файла и 474 source blobs проверены, cleanup подтверждён, provider calls 0. Пять ошибок содержат stderr от оставшегося после замены setuptools `distutils-precedence.pth`; исправление удаляет только сверенный по hash orphan в отдельном образе и требует чистый stderr startup. Ещё две ошибки parser deprecation и одно assertion, skips и TaskRunner остаются OPEN.

После удаления hash-проверенного orphan hook CI 40886945385 (`78fbf64`): полный pytest6116 diagnostic 2617/2617, 3 failed, 2489 passed, 115 skipped, 10 xfailed; stderr пуст, оба контейнера удалены, source и wheel bytes проверены, provider calls 0. Две ошибки — parser deprecation Python 3.9, третья — `test_getfslineno` assertion; обязательные skips, среда, TaskRunner и приёмка остаются OPEN. Подробности в P07_REMAINING_AND_ORDINARY_COVERAGE_2026-10-29.md.

Пропуски из полного отчёта 40886945385 сверены с SHA256 и native summary: 107 по отсутствующим Python-зависимостям, 2 lsof, 3 platform, 3 source-disabled. Инструмент `tools/p07_pytest6116_skip_audit.py` и детальный `validation/p07-budget20/pytest6116-public-execution-v5/skip-audit.json` не дают разрешения на пропуски. Публичный tox.ini включает py38; связь трёх ошибок с Python 3.9 остаётся гипотезой до отдельного воспроизводимого запуска на 3.8.

CI 40888913341 (`fb8b1ad`) на Python 3.8.20: три ранее падавших публичных теста PASS, 474 source blobs и cleanup проверены; artifact 12902724745 SHA256 `be2dcbb33f0dc2ce9678e6c6618a18f5de84dd9abacbc088691f2c93e9d8d8d5`. Среда отличается не только Python, поэтому это совместимость-кандидат, а не причинное доказательство и не ordinary acceptance. Следующий шаг — полный публичный diagnostic 3.8; затем разбор skips, installed-byte provenance и TaskRunner. Provider calls 0.

Полный Python 3.8 diagnostic CI 40889437163 (`6aaa7a9`): 2617/2617, 4 failed, 2488 passed, 115 skipped, 10 xfailed; source 474 blobs и cleanup подтверждены, stdout полный, provider calls 0. В двух ошибках nested processes видят pytest 5.2.2, а не версию публичного исходника; кандидат добавляет явный site hook `/testbed/src` и отдельную проверку путей импорта. Это изменение среды требует проверки и не означает приёмку.

Полный Python 3.8 diagnostic [CI 40890624670](https://github.com/Akseleu-J/atomic-flow/actions/runs/40890624670) (`60fb335`): 2617/2617 завершены, 2492 passed, 115 skipped, 10 xfailed, exit 0. Все node IDs совпали с предыдущим запуском; 474 source blobs, импорт публичного исходника дочерним Python и cleanup проверены. Artifact 12905520444 SHA256 `abbd3bff8a99a94af9057f7f2647824d24f73af71c657a440709429a8c75210a`. Остались политика 115 skips, независимая installed-byte provenance, failure/repair/fresh-pass, TaskRunner и graph integration. P07 OPEN; provider calls 0.

Python 3.8 skips привязаны к хешу отчёта: 107 отсутствующих Python dependencies, 2 lsof, 3 platform, 3 source-disabled; это только диагностика. Попытка installed-byte audit CI 40892134352 (`e660d6b`) остановилась при pull до build/container; какой image и причина неизвестны, stdout/stderr pull тогда не сохранялись. Новый runner сохраняет их для следующей попытки. Не считать байтовую provenance принятой.

CI 40892426034 (`9cc4b11`) подтвердил 504 файла 11 publisher wheels независимой сверкой со stopped-container inventory, плюс 44 разрешённых pip metadata; это не аудит всего base image или console scripts. Повторно выполнены те же 2617 nodes: 2492 passed, 115 skipped, 10 xfailed; 474 source blobs и cleanup подтверждены, provider calls 0. Ordinary admission всё ещё OPEN. Следующий diagnostic собирает индивидуальные node IDs, фазы и причины для skip/xfail; агрегатные строки не являются разрешением на пропуски.

CI 40893566658 (`4fe8c09`) повторил 2617 nodes: 2492 passed, 115 skipped, 10 xfailed, provider calls 0. Сохранены 125 индивидуальных записей node/phase: 82 call skips, 33 setup skips и 10 call xfails; все принадлежат inventory, все node/phase уникальны. 548 installed inventory entries и все node IDs совпадают с предыдущим CI; source 474 blobs и cleanup подтверждены. Artifact 12905113601 SHA256 `af20ec3755c405f96b35991111e5d6c810fdc7163deae217c14eb5c632c02a6e`. Детальный `validation/p07-budget20/pytest6116-py38-node-reports-v1/nonpassing-observations.json` не является allowlist.

Параллельный аудит: `docs/P07_PYTEST6116_SKIP_REVIEW_2026-10-29.md` привязывает 125 skip/xfail к публичным source conditions; xdist 1.18.2 содержит `xdist.boxed` только в sdist, более поздние исследованные версии его не содержат, совместимость не принята. `tools/p07_matched_arm_contract.py` проверяет snapshot общих ограничений, очереди и CheckSpec двух arms, но не выполняет host admission и не является полным driver. Proxy adapter теперь переводит противоречивые дубли стоимости/tokens в UNKNOWN с удержанием резерва; fake-fetch тесты не доказывают provider compatibility. Платных вызовов не было.

Профиль pexpect 4.7.0 / ptyprocess 0.6.0: CI 40895530344 (`8c03a91`), artifact 12906114592 SHA256 `d40900aa8071a249a1c3b355835bb2ca84ded8513975fd024d3933a16448d165`: 2617/2617, 2532 passed, 75 skipped, 10 xfailed; все 40 прежних skips из-за pexpect теперь passed. 531 файл 13 publisher wheels независимо сверены, 52 pip metadata разрешены, 474 source blobs/cleanup и идентичность node IDs проверены. Ordinary admission, причины 75 skips и TaskRunner остаются OPEN.

Общие CI 40895865419 и 40895865423 на `26821e998af04db4c9291d234fc3095f2d34156e` SUCCESS. В JUnit host Python 3.11/3.12 по 3317 cases, 0 failures, 14 skipped; по 411 source hashes сверены с Git, JUnit и три ZIP SHA проверены. External composition scripted, без model quality claim; paid calls 0.

Параллельно подготовлены: `tools/p07_matched_runner_probe.py` — проверка snapshot фактических runner/backend/obligation без запуска и без допуска, не связывает callbacks/resource_limits и не устраняет TOCTOU; `tests/p07_unknown_restart_checkpoint_test.mjs` — synthetic in-memory fault/restart проверки UNKNOWN, без реальных GitHub/proxy вызовов или ручной reconciliation. Совместный pexpect+optional wheel candidate: 25 закреплённых архивов / 16 активных dependency edges, `tools/p07_pytest6116_optional_prepare.py` и receipt `validation/p07-budget20/pytest6116-py38-optional-pexpect-wheelhouse-v1/preparation.json`; target imports 0. Перемещаемые .data файлы/console scripts, нативный MarkupSafe ABI, image и полный тест ещё не квалифицированы.

`docs/P07_UNKNOWN_RECONCILIATION_REVIEW_2026-10-29.md`: адаптер не передаёт provider idempotency key, attempt_id локальный, remote checkpoint хранит только хеш; ручное разрешение UNKNOWN не реализовано. `tools/p07_unknown_resolution_contract.mjs` лишь сверяет форму и возвращает can_resolve/can_settle/can_send=false; provider/actor/checkpoint authenticity не доказана. `docs/P07_MATCHED_DRIVER_GAPS_2026-10-29.md` фиксирует доказанную неизменность snapshot digest при замене ProjectLimits/callback/journal root; это не доказательство обхода других gates. Нужны host-owned bindings и effect-boundary enforcement, не только snapshot.

Следующий блок подготовлен: combined optional+pexpect Docker diagnostic проверяет 796 publisher-derived files в stopped container, схему pip/sysconfig и MarkupSafe native ABI внутри ограниченного контейнера; 5 generated wrappers сохраняются как content-unverified. Пока реальный Docker diagnostic не завершён, это лишь проверяемый кандидат. `p07_matched_host_envelope.py` фиксирует только декларации общих параметров без аттестации, а `p07_unknown_intent_simulation.mjs` моделирует сравнение снимков без CAS/ledger I/O/полномочий; никакой gate P07 этим не закрыт.

Optional+pexpect CI 40930969344 (`9fc88fe`) завершился: 2617/2617 nodes, 2560 passed, 47 skipped, 10 xfailed; по сравнению с pexpect-кандидатом ещё 28 ранее nonpassing nodes прошли, все node IDs совпадают. 796 publisher-derived файлов независимо сверены с остановленным контейнером, MarkupSafe native импорт/function/path в изоляции проверены, 474 source blobs и cleanup подтверждены; 5 generated wrappers content-unverified. Artifact 12923017273 SHA256 `c19b1aa71ef44d7e09edd1fc9cd212c590bbe9a3fcca318701539ba4f79b3ab0`. Осталось 47 skips: numpy 13, xdist 18, twisted 8, lsof 2, platform 3, source-disabled 3. Это diagnostic, НЕ ordinary admission; provider calls 0.

xdist 1.18.2: выполнен bytes-only аудит исходного архива и четырёх build/runtime wheel; `docs/P07_PYTEST6116_XDIST_BUILD_DESIGN_2026-10-29.md` описывает будущую offline двухкратную сборку. Пока wheel не собран, plugin compatibility и isolated runtime не проверены. `p07_matched_object_watch.py` — in-process наблюдение за выбранными ссылками и параметрами, без lock/effect guard/admission; provider evidence inspection только связывает недостоверные декларации и всегда запрещает resolution/send.

Для шести platform/source-disabled skips из CI 40930969344 добавлен source-bound review `tools/p07_pytest6116_source_skip_review.py`: точные IDs, setup phase, причины и Git blob identities сохранены в `validation/p07-budget20/pytest6116-py38-optional-runtime-v1/source-skip-review.json`. Публичные четыре файла независимо проверены по Git blob и размерам; отдельное доказательство реальных runtime predicates и решение skip policy ещё отсутствуют. Это не admission.

Подготовлены отдельные CI: `p07-pytest6116-xdist-build.yml` выполняет две offline сборки xdist 1.18.2 с проверкой полного wheel и cleanup; `p07-pytest6116-lsof-probe.yml` проверяет Debian lsof own-process capability, а не исходные тесты. NumPy wheel 1.21.6 проверен только как publisher bytes (709 файлов), установка/ABI ещё OPEN. Ни одно из этих условий не является ordinary admission.

CI 40933846341 (`04c3e69`), artifact 12924300891 SHA256 `d1f83290bcd0c15a7ff0b0651e3eae9f17be9de12682724d2db1d68a3341041e`: две независимые offline сборки xdist1.18.2 совпали побайтно, wheel SHA256 `5fa22b9449f0afbcec12430a550ef9242a2307725b7409fba7e80b8de48db347`, RECORD/boxed/plugin metadata и cleanup проверены; runtime compatibility OPEN. CI 40933849918, artifact 12923039096 SHA256 `afb9faa4df7df077407bd547f427075c2f205b1390a29afa121211c19c89d024`: lsof package и installed binary сверены, внутри изоляции lsof видит открытый файл собственного процесса; подпись Debian metadata и два исходных теста не проверены. Provider calls 0, ordinary acceptance false.

Готовятся отдельные isolated smoke: NumPy 1.21.6 (installed publisher bytes и native matmul) и два точных lsof nodes в полном optional+pexpect окружении. Оба — diagnostics, не заменяют полный public suite и ordinary admission.

Три focused CI на `c517aad`: lsof 40934640652 — оба исходных nodes PASS, 796 dependency files/474 source blobs/lsof binary и cleanup проверены; numpy 40934644253 — 709 publisher files независимо сверены, Python3.8 native import/matmul PASS; xdist 40934648043 — загрузка трёх plugin entries и два forked nodes PASS, 833 files/source/cleanup подтверждены. ZIP digests и tested-commit сверены. Generated wrappers остаются content-unverified; данные CI не заменяют объединённый полный прогон, отрицательную/repair проверку и TaskRunner admission. Provider calls 0.

Подготовлен полный combined public diagnostic: объединяет проверенные optional+pexpect, NumPy, lsof и собранный xdist; проверяет установленные bytes и пять wrapper bytes, но NumPy-generated wrappers и Debian signature остаются непроверенными. Требует Docker CI, не закрывает admission. Twisted/trial candidate 32 wheel / 26 edges подготовлен bytes-only; замена setuptools и native zope ABI остаются отдельными gates.

CI 40993869379 (`f24a59c`) trial candidate: 1925 installed publisher files независимо сверены, native zope.interface и импорт trial PASS, 15 generated wrappers content-unverified; исходные 8 trial tests и combined environment не проверены. CI 40993871572 — пять raw wrappers извлечены из stopped container и независимо совпали с шаблоном, installed pip source не аттестован; оба cleanup подтверждены. Provider calls 0, ordinary admission false.

Combined CI 40993867055 (`f24a59c`) NOT QUALIFIED: 2617/2617, 6 failed, 2584 passed, 16 skipped, 11 xfailed; 474 source, installed candidates, пять wrappers и cleanup подтверждены. Сохранять как полный диагностический отрицательный результат; xdist и pdb причины исследуются. Trial focused CI 40994444905 (`455e876`): 8 nodes, 7 failed, 1 passed, внутри вложенных pytest Hypothesis сообщает недоступный /testbed/.hypothesis/examples; исправление storage candidate готовится, предупреждения не подавлять. Оба artifact/stream hashes сверены; provider calls 0, admission false.

CI 40994729172 (`cf7090b`) после writable Hypothesis storage: 8/8 точных trial nodes PASS, все фазы завершены; 1925 publisher files независимо сверены, 474 source blobs/cleanup подтверждены. Artifact 12949909301 SHA256 `967a94c4923c8b6e70f06fe4c71bb5463454eb8a713b0851b66fd59f50fe97da`; это focused, совместимость с combined full ещё не доказана, admission false, provider calls 0.

## Diagnostic work in progress — 30 October

A separately versioned xdist 1.28.0 / pytest-forked 1.0.2 publisher-wheel candidate has been prepared without target imports. The 28-wheel bytes, RECORD and active dependency closure have been audited locally; `tools/p07_pytest6116_xdist128_prepare.py` and its fixed profile retain this as a prospective byte-level result only. Neither `xdist.boxed` nor a runtime compatibility adapter is present. Static review indicates that simply re-exporting pytest-forked's runner would mishandle `pytest.exit`; the complete fork, plugin and progress semantics must be tested before any admission.

`tools/p07_unknown_snapshot_classifier.mjs` provides a read-only structural classification of three UNKNOWN ledger snapshots: no recorded reservation, reserved but unsettled, and recorded settlement. Local synthetic fault/restart tests cover all three. Every authority flag remains false: snapshot consistency does not authenticate provider evidence or a remote checkpoint, prove a request was unsent, resolve UNKNOWN, clear a hold or authorize another send. Remote CI and real recovery remain separate gates.

## Ordinary profile reconnaissance — 30 October

Public-source collection diagnostics are prepared for pylint6506 and Sphinx7738 on their fixed base commits and immutable images. The former binds 2563 Git blobs (including two symlinks); the latter binds 1416. No hidden patches, model calls or test bodies are used. Nonpublic collected paths, missing nodes, collection errors/skips or incomplete bounded output prevent a clean collection observation. Additional files in the image have not been fully audited; neither a clean collection observation nor the green diagnostic is ordinary acceptance or proof of tox-environment equivalence.

A separately disclosed pytest6116 `xdist.boxed` adapter has only host-side simulated fork tests. Actual target fork, crash/exit semantics, plugin registration, installed-byte inventory and full unchanged suite remain unverified.

Pylint6506 collection CI 41001191083 (`8a7131cb64c84ea241dd7255c01ec418f13ddc99`) was NOT QUALIFIED before collection: 2555 of 2563 source entries were reported as unexpected type or mode, and cleanup was confirmed. Artifact 12952889545 SHA256 `9fb2d606352dbbadb06394118dea27eaa55e2d174eab675de13a9174f9d3608a`. The verifier's combined error did not distinguish file permission mismatch from missing files or differing bytes. The next diagnostic records content/type and executable mode separately; mode differences remain disclosed and never establish source-mode equivalence or ordinary admission. Sphinx CI 41001193385 similarly stopped before collection on a source-byte difference; retain that attempt.

Sphinx7738 CI 41001193385 at `8a7131cb64c84ea241dd7255c01ec418f13ddc99` was NOT QUALIFIED: its image's `setup.py` differs from the frozen public base; no collection was run and cleanup was confirmed. Artifact 12952908375 SHA256 `5ae7aea83f9928ba66d0534885899326b35a2fcc7f9a9b3d518b9937796de412`. A prospective separate derived image overlays only the 1416 source files from a fixed archive after checking each public Git blob, then repeats the source check inside the restricted worker. Extra image files and tox equivalence remain unverified. This changes the image and requires a fresh diagnostic; it is not a repair of the historical task.

Pylint6506 [CI 41001497225](https://github.com/Akseleu-J/atomic-flow/actions/runs/41001497225), commit `6120f849e75825fa2a6cf6f1b62d8c3e653f7358`, artifact 12953079593 SHA256 `c7bea09559e76a07d5fc581b03e10b989c6a90b62eb94b08dcbc30cc6afe9035`: all 2563 public source contents/types matched; 2555 executable modes differ (image mode 0777 for public 100644 files). Native collection reached 1941 nodes in 74 public files, exit 2, two errors for missing GitPython, no collection skips or nonpublic files; no tests executed. Raw stdout/stderr hashes matched, no truncation or timeout, cleanup receipt confirmed. This is NOT ordinary acceptance; full image extras and dependencies remain unaudited. A separately pinned dependency candidate is being prepared.

Sphinx7738 [CI 41001700454](https://github.com/Akseleu-J/atomic-flow/actions/runs/41001700454), commit `c0de21ff9b576b866ed53d287976e408f997cb56`, completed collection of 1502 distinct nodes across 94 public files with zero collection errors/skips/nonpublic files. Artifact 12953362920 SHA256 `0fa24c3f7af3dca472d7cacb453dd8ca9856d45619205084965a90087eb23108`; all 1416 source archive files independently matched Git blob hashes, base/derived lineage and stream hashes checked. Image source verification and cleanup are worker receipts. This is collection only; extra image files, tox equivalence and full ordinary execution/TaskRunner remain open.

pytest6116 xdist128 [CI 41001862614](https://github.com/Akseleu-J/atomic-flow/actions/runs/41001862614), commit `fb269f74b73f6b9ac51601858d01ba9418a9bdf0`, completed 19 exact focused nodes: 18 passed, one failed. Artifact 12954011514 SHA256 `d7e5f68bd3ecb84779d58f7fd4b9961d7be45550eb0595d22578bd196e02238d`; captured teardown progress failure includes execnet failing to serialize pytest `ExitCode`, while the five other prior failures passed in this separate candidate environment. No full suite, image equivalence or ordinary admission is claimed.

SymPy20212 has a prospective deterministic three-batch reconciliation contract (256/256/17): exact file and node identities, native selector collision checks and nonpassing outcomes are preserved. It does not authenticate input evidence or authorize ordinary acceptance or publication.

## Public executions and focused pytest — 30 October

Sphinx7738 [CI 41002365611](https://github.com/Akseleu-J/atomic-flow/actions/runs/41002365611), commit `9f7fb6f9429e32b6dffba8839125ac6a32b530c5`, artifact 12954086578 SHA256 `4fb298ebeac5551bee942a5c7a64eeea02bee92de6143bf7cfa3011150916c8d`, executed the collected public suite: 1502 setup/teardown reports, 1355 call PASS, 126 call FAIL, 20 setup SKIP and one call SKIP, no collection error or timeout/truncation. Stream hashes, source audit receipt and container cleanup checked. The image ID differs from collection's earlier rebuild. Failures include output/assertion differences and three external linkcheck tests; skips include missing LaTeX/Graphviz/rendering and related tools. No assertion was weakened; tox equivalence, image extras and ordinary admission remain unverified.

pytest6116 [CI 41002475865](https://github.com/Akseleu-J/atomic-flow/actions/runs/41002475865), commit `4c72468f03b8f33241d0f7f87e6d94404f95a919`, artifact 12953493799 SHA256 `1c5c9209f07319a3163e9ddd451750802d58864ebebe29e3f0fc6346f4888816`: all 19 focused public nodes passed all phases with xdist1.34 and disclosed adapter. Streams and adapter matched, 844 installed publisher-file hashes reconciled to independently checked wheel plans, 474 source blobs and cleanup recorded. This image lacks NumPy/lsof/Twisted; full suite and generated wrappers remain separate. The combined full public execution CI 41029891304 is in progress as of this note, not an acceptance.

Pylint6506 [CI 41002231450](https://github.com/Akseleu-J/atomic-flow/actions/runs/41002231450), commit `993500a888131e1409e134f5c023a5682bb2e6d4`, artifact 12954036994 SHA256 `bc84750ca9587772413d0ebd0236c7158ed06827a74c817409b796263793ea42`, collected 1954 unique nodes in 76 public files, no errors/skips/nonpublic paths. 88 publisher wheel file hashes independently reconciled, source audit reports 2563 matching contents with 2555 executable-mode differences; inspection and both cleanup receipts checked. Extra files, generated metadata and tox equivalence remain unverified. The separate full execution CI 41029509433 failed and requires artifact analysis.

## Continued ordinary diagnostics — 1 November

Pylint6506 [CI 41029509433](https://github.com/Akseleu-J/atomic-flow/actions/runs/41029509433), commit `c8a9b0b05fa0a391e5037c14fdf240b13d48c178`, artifact 12964803919 SHA256 `997649055c16353d4ed7c84338056db6c626a4e1a67a5de42d248fb5e6ced7db`: 1954 nodes completed setup/teardown, 10 call failures, 14 setup failures, 4 teardown failures, 285 skipped phases including 6 xfails. Many failures attempt to write in read-only `/testbed`; unrelated TOML and functional discrepancies remain. Output was not truncated, both container cleanup receipts were confirmed; ordinary acceptance false. A separate prospective writable-container-layer diagnostic will preserve no network/no host mounts and audit source before and after; it is explicitly different isolation, not acceptance.

Sphinx7738 [CI 41321839212](https://github.com/Akseleu-J/atomic-flow/actions/runs/41321839212), commit `6c9b2fb600e4ad25192106a97c8205718d26a06b`, artifact 13089662919 SHA256 `5230f8be5f867fdd0cec7ebe5bf482cc2e6a2993a0685c9eae3483788fa0d6dd`: all 53 previously failing selected figure tests passed every phase on separate docutils0.16/Pygments2.7.4 overlay; 432 installed publisher file hashes, source archive, output and cleanup receipts checked. Thirteen generated scripts and pip source remain unverified, and full suite has not been repeated in this environment.

SymPy20212 [CI 41321750781](https://github.com/Akseleu-J/atomic-flow/actions/runs/41321750781), commit `99cec3c677c044dca7fb7e9cab83151d74481c7c`, artifact 13088868789 SHA256 `7e94f57a2e244f6e0e746b9873e7998078277c59001992d5c35bfbe165dcc0bd`: internal runner discovery selected the exact groups 256/256/17, all 529 files, no extras or omissions. Source/cleanup are worker receipts; test bodies were not executed.

Pylint6506 [CI 41321748657](https://github.com/Akseleu-J/atomic-flow/actions/runs/41321748657), commit `99cec3c677c044dca7fb7e9cab83151d74481c7c`, artifact 13089623121 SHA256 `a9641b85e70fa1a295058bc3bab0a4244209c6ef46061d8b6705097c3265032b`: in a distinct disposable writable container layer, all 1954 nodes completed, 1664 call PASS, 5 call FAIL, 285 skipped phases including 6 XFAIL; no setup/teardown failure. Post-run source audit reports no content change but 2555 original executable-mode differences. Two TOML failures and three functional mismatches remain; generated files and image extras not fully audited. No ordinary acceptance.

pytest6116 [CI 41029891304](https://github.com/Akseleu-J/atomic-flow/actions/runs/41029891304), commit `20929818911a5387b3f5c7e039786a299aee1c1e`, artifact 12965118097 SHA256 `d1c584f1435f4a81bd25eb6be2882afd76e9288ee13b8829388db04c5fff7acd`: all 2617 nodes / 7845 phases completed, 2598 passed, 8 skipped and 11 xfailed, no XPASS. Streams and adapter matched; independent wheel-to-retained-inventory comparison covered 1973 publisher files plus 709 NumPy files. Source verification and cleanup are worker receipts; wrapper content and lsof publisher signature remain incomplete. This is full public diagnostic, not an accepted TaskRunner journey or model result. A non-authorizing exact observed-outcome comparator and separate 1200s/24MB envelope preserve skip/xfail details; generic CheckSpec remains unchanged.

Pylint [CI 41322412942](https://github.com/Akseleu-J/atomic-flow/actions/runs/41322412942), commit `19e78154ca1e4172efbd0212b2e32a878dda6af5`, independently executed the same five failing nodes in three candidate profiles. Baseline 5 fail; attrs22.2.0 1 pass/4 fail (regression_4439 passes); tomlkit0.10.1 2 pass/3 fail (both TOML tests pass). The two recursion tests failed in all three. SHA256: baseline `159f054d7d9d5f7298f031477175886b1feffcff04ddba3d1750cf47a816ef85`, attrs `c43e2a416832369efc2a9e7a274e9fabbc5b6e67516fe2bf25db95b51b9b6344`, tomlkit `1a9e0bdc67387fc8fc8f720efb904ff7a1386c33a2219afff0d1056ac970caac`. Raw streams and cleanup matched; post-execution checks show no content changes. Each was focused, with explicit inherited PYTHONPATH, not a full or historical environment acceptance.

SymPy batch [CI 41322143363](https://github.com/Akseleu-J/atomic-flow/actions/runs/41322143363), commit `99fb55011bcc3c03a671543326bd9cdbcdbe945c`, artifact 13088904356 SHA256 `909bb1c757510cbbc4b04469331c58130bdfab9c351a7229b75b0d2e8b1186dd`, executed 17 exact selected files through internal runner: 450 pass, 57 skip, 150 xfail and one xpass (658 reporter events). Per-file counts matched retained output; this does not independently establish a complete node inventory or CLI equivalence. Source and cleanup are worker receipts; no ordinary admission.

Sphinx full overlay [CI 41322203366](https://github.com/Akseleu-J/atomic-flow/actions/runs/41322203366), commit `7435197433ec156d2848ab6b4eec154a205a34aa`, artifact 13089802631 SHA256 `a5164abd86b1fdf40c864acf62912b85907b4641c69ebf4d1642c0f9610228a0`: all 1502 nodes completed; 1473 call PASS, 7 call FAIL, 22 skipped phases (20 setup, 2 call). Three failures are external linkcheck; four other nodes receive a separate detailed-trace diagnostic. Streams matched; source and cleanup are worker receipts. Different rebuilt image, no tox-equivalence or ordinary admission claim.

pytest transfer smoke [CI 41323686120](https://github.com/Akseleu-J/atomic-flow/actions/runs/41323686120), commit `65c7d1ccb6ba9e7874d541721e4ca9267292223e`, artifact 13090755841 SHA256 `60fb4b7758860777533298b2d5c22b656cbd067e5c65b03e802520dc554fdb77`: NOT QUALIFIED before the negative test. Source transfer receipt verified 476 expected files/modes; pytest import failed because generated `src/_pytest/_version.py` is absent from the public Git tree. `setup.py` configures setuptools_scm to generate this file. This attempt did not observe probe FAIL; a separate stopped-image byte extraction is being prepared, without claiming generation provenance.

Pylint setuptools-factor [CI 41323947093](https://github.com/Akseleu-J/atomic-flow/actions/runs/41323947093), commit `ee8736dea9cc2496e9982418ce47b80a247d3c05`, did not reach image build or tests: both arms raised a host-side `RecursionError` while constructing the focused source. The two small artifacts contain only tested-commit; no target test outcome can be inferred. The source builder now retains the original function before injection; regression test exercises the actual injected path. A fresh run is required.

Pylint setuptools factor [CI 41324268185](https://github.com/Akseleu-J/atomic-flow/actions/runs/41324268185), commit `54d00a26cc5473114bb8e5604387286bf6935f57`, compared the exact two remaining nodes: both failed using setuptools75.1.0 and both passed using independently pinned setuptools57.5.0, with attrs/tomlkit fixed. Baseline artifact 13089889693 SHA256 `cc546f43cf915d415c65ffd5082fe5d36dd9f2432de3d71b856121b36de12569`; candidate artifact 13090483389 SHA256 `08c1c29e31b457af5d12c987290bb7bd00ef6abc879391a0e963102efb5e19fd`. Stream hashes and cleanup receipts checked. This is focused evidence, not full-suite acceptance; a new full run is required.

pytest negative transfer [CI 41324475961](https://github.com/Akseleu-J/atomic-flow/actions/runs/41324475961), commit `6ce81dcb3db168c525e1258620f298a848c59b48`, artifact 13090124455 SHA256 `c6fefe725da776cf5cd8cb512a71a8268747595c3814ccb6847f3a578bac60b7`: observed exact probe setup PASS/call FAIL `assert 1 == 42`/teardown PASS, 474 source blobs and 477-file pre/post inventory with no differences, 1973 publisher-file and 709 NumPy hashes checked against wheels, cleanup receipt confirmed. GitHub workflow reports failure solely because the host CLI retained an obsolete exit predicate; diagnostic child exit 1 was expected. This is one isolated negative probe, not full-suite or TaskRunner acceptance. Generated version source path and generation are not proven.

Pylint full compatibility [CI 41324657230](https://github.com/Akseleu-J/atomic-flow/actions/runs/41324657230), commit `079b5938d5941899ff3d28255b93d6328872fc84`, artifact 13090419239 SHA256 `ef669e6e179611f999f169a3160f71e6caf732289e1419f26bde130f82a403de`: 1954 complete nodes, 1669 call PASS, no failures, 285 skipped phases including 6 XFAIL. Stream hashes and cleanup receipts match; no ordinary acceptance. 206 skips use source option `--primer-stdlib`; separate four-batch execution is prepared. Thirty-four spelling nodes lack enchant/dictionary, and external primer remains a network-dependent separate boundary.

pytest reconstructed focused repair [CI 41324980113](https://github.com/Akseleu-J/atomic-flow/actions/runs/41324980113), commit `7992666cd1619e570a551e4b7675e8cd1b8e5aa7`, artifact 13090567974 SHA256 `cee874b44aaacaac7e2afe4fc26985b4047956aac682d5eb0c04455ab60b56da`: setup/call/teardown PASS for one repaired probe. Raw output and 474 public blobs in transfer independently checked. Reconstructed workspace/image differ from negative; this is not a continuous TaskRunner journey or full-suite fresh final.

## 2 November ordinary diagnostics

Pylint stdlib primer [CI 41437983048](https://github.com/Akseleu-J/atomic-flow/actions/runs/41437983048), commit `a271bab35b4d9e14dac8192faafd453a716ab913`: four retained artifacts cover exactly 206 distinct nodes, 52/52/51/51; all setup/call/teardown phases passed. Archive/stream hashes and installed wheels were independently checked; source/cleanup are worker receipts. This is a separate targeted primer run, not whole-suite or TaskRunner admission.

pytest full negative [CI 41439666936](https://github.com/Akseleu-J/atomic-flow/actions/runs/41439666936), commit `d2f14f950f9752dfb4589ae01d2a083f7587add9`, artifact 13138183827 SHA256 `eb8ae421ef38ea232734d77b74ea9b37be8a706fc28620b51393e32e1e4242ce`: 2620 nodes completed (2617 public baseline, two source-discovered meta-tests, one probe). Only probe failed as intended; 8 skips and 11 xfails match fixed observed policy, no xpass; raw streams/marker checked, source and cleanup are worker receipts. This is not TaskRunner admission.

SymPy [CI 41437986385](https://github.com/Akseleu-J/atomic-flow/actions/runs/41437986385), commit `a271bab35b4d9e14dac8192faafd453a716ab913`: each of the two 256-file batches completed. Batch 0: 4318 pass, 7 exceptions, 162 skip, 109 xfail, 5 xpass; batch 1: 4327 pass, 1 exception, 178 skip, 92 xfail, 2 xpass. Seven exceptions involve deprecated setuptools compiler discovery; one attempts writing to read-only source. Preserve all outcomes; these are diagnostic reporter events, not ordinary acceptance.

pytest full repaired [CI 41440513220](https://github.com/Akseleu-J/atomic-flow/actions/runs/41440513220), commit `cf64d8b`, artifact 13140251355 SHA256 `5f6afb0a2d71f20b10514579d7627d014e3385ab6a72de20f2e49207fbf0bdc9`: 2620 nodes completed; repaired probe and 2600 public/meta calls pass, 8 skip and 11 xfail preserved, no truncation or timeout, cleanup receipt confirmed. Rebuilt separately from negative: no continuous workspace or TaskRunner claim. A further fresh final has been dispatched separately.

SymPy focused setuptools [CI 41441281803](https://github.com/Akseleu-J/atomic-flow/actions/runs/41441281803), artifact 13139557111 SHA256 `207e8e93706ed1a36dc785ffcd840ceed7f125db4d6dc37f1007ff9545b910a6`: 2 selected files completed, 8 passes, 8 skips and 1 exception; no full or ordinary acceptance. Inspect the exception before further changes.

pytest fresh final [CI 41441542432](https://github.com/Akseleu-J/atomic-flow/actions/runs/41441542432), artifact 13140852270 SHA256 `bd04c9253777f644e5f567d8e2039a2a8e22fc39ed73533c80c3f1f8a968a5cc`, independently rebuilt and completed 2620 nodes, 2601 call PASS and 8 SKIP/11 XFAIL, no timeout/truncation; cleanup receipt confirmed. No physical continuity or TaskRunner execution is claimed. A separate retained-transfer audit checks 474 frozen public blobs and exactly one fixed probe change.

## 3 November continuation

pytest6116 transfer pair audit on `d96ecfd` independently verifies 474 frozen public Git blobs in both retained transfers and exactly the fixed probe byte change. The negative, repair and fresh-final full-suite diagnostics were independently rebuilt; they do not establish a continuous TaskRunner workspace or ordinary admission. The host-owned execution boundary for that journey remains open.

SymPy20212 executable-tmpfs focus [CI 41443153512](https://github.com/Akseleu-J/atomic-flow/actions/runs/41443153512), artifact 13141088765 SHA256 `e9c0d60be30e2e2f2cd923b5f28f3e81ee8c2ce76f908c13582f0c0c169c26f8`: two public files produced 8 PASS and 9 SKIP, with no exceptions; stdout/stderr hashes and cleanup receipt checked. In the prior noexec candidate these files had 8 PASS, 8 SKIP and one PermissionError. The affected C compilation test is now SKIP, not PASS: compiler capability remains unverified. `/tmp` was a separate rw,exec,nosuid 128 MiB tmpfs, a different isolation profile. Installed 155 wheel files and 545 source files are worker receipts; this focused result is not ordinary admission.
