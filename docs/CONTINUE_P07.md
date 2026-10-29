# Продолжение P07 — 29 октября 2026

Работать в `C:\Users\omirb\OneDrive\Документы\ChatGPT\atomic-flow 2\atomic-flow`, ветка `experimental`, draft PR №2. Начать с `docs/PROJECT_CONTEXT.md`; Git-команды выполнять только в этом клоне. Перед продолжением сверить HEAD, remote SHA и рабочий diff. P07 остаётся **OPEN**.

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
