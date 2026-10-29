# Продолжение P07 — 29 октября 2026

Работать в `C:\Users\omirb\OneDrive\Документы\ChatGPT\atomic-flow 2\atomic-flow`, ветка `experimental`, draft PR №2. Начать с `docs/PROJECT_CONTEXT.md`; Git-команды выполнять только в этом клоне. Перед продолжением сверить HEAD, remote SHA и рабочий diff. P07 остаётся **OPEN**.

## Текущий checkpoint

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

Полный Requests3362 ordinary путь принят в указанном scope. Продолжить function36 fixture/controller/resource receipt integration для Requests1963/2148/2317; затем Python2.7 Requests2674 и SSLv3/wire Requests863. Generic ordinary dispatch допускает только 3362-cn через отдельный owned root-Linux controller; остальные private Requests cohorts отклоняются. Не включать их по metadata/unit tests.

Далее нужны все 30 ordinary profiles, полный исторический Atomic/flat driver с равными лимитами, замороженный study profile/cohort/order/owner caps, 30 пар с независимым oracle, свежий holdout, ablations и final review. 30/30 environment calibrations уже приняты, но это не 30 решённых моделью задач.

## Бюджет и разрешения

Пользователь разрешил автономное продолжение P07, осторожные push и CI. Merge/release не разрешены. Paid ceiling $20; записанный расход $0.035931375, новых provider calls в этой работе нет. Модель: `anthropic/claude-sonnet-5@default`. Owner2 `akseley` и participant3 `umirbayulgaisha` — отдельные авторизованные контуры; не менять аккаунты для обхода квоты. Старая quota не является текущим балансом.

Перед paid send: frozen cohort/owner/model/profile/caps, durable reserve → remote checkpoint ACK → единственный send → actual usage settlement. UNKNOWN блокирует новые physical calls до сверки. Не откатывать и не удалять data/checkpoint branch `codex/p07-model-queue-paid-20261028-01`. Не смешивать scripted callbacks, live model calls, ordinary checks и hidden grading.

## Доступ и инструменты

Vault: `C:\Users\omirb\OneDrive\Документы\ChatGPT\computer_control\shared-access\.env.enc`; README и `shared-secrets.ps1` рядом. PowerShell 7.2+. Пароль владелец передаёт отдельно; его, tokens, headers и расшифрованный vault не писать в команды, файлы, artifacts или логи. Предпочитать узкий `.local-tools/with-github.ps1` с SecureString вместо импорта всех переменных.

Локальный `.local-tools/p07-operator.mjs` предоставляет `github`, `git`, `artifact`; credentials остаются в процессе. `artifact` сверяет GitHub SHA256. `.local-tools/p07-zip.mjs` проверяет ZIP members/CRC; при извлечении отдельно проверять confinement и свежую папку. Helpers не входят в clone; старые Node REPL bindings не считать существующими. Kaggle-контур operator закреплён за owner2, не использовать его как переключатель аккаунтов.

Python/Ruff: `.venv/Scripts/python.exe`, `.venv/Scripts/ruff.exe`. Node: `C:\Users\omirb\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`. Тесты выполнять последовательно с `-p no:cacheprovider`; Windows испытывала MemoryError. Перед commit обновлять `tools/update_project_index.py` и **дожидаться завершения**. Не запускать зависимый push, пока commit ещё работает в фоновой exec session. После push проверить полный remote SHA, после dispatch — actual head_sha и tested-commit в архиве.

Не делать `git add .`: есть исторические untracked evidence и `.atomic_flow/tree.lock`. Ничего не удалять. Core/client Docker-free; недоверенное исполнение требует принятого isolated worker. Протоколы: `GITHUB_WORKFLOW.md`, `REAL_API_BENCHMARK_PROTOCOL.md`, `P07_MATCHED_QUEUE_EXECUTION_DESIGN_2026-10-28.md`, `P07_PAID_QUEUE_PREPARATION_2026-10-28.md`.
