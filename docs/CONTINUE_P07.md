# Продолжение P07 — 29 октября 2026

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
