# Package 41 — P06 goal lineage и acceptance matrix

25 октября 2026. Base 99259a581e85dc4a4f607bb3c050b0913c21d04b.
Initial runtime **84ce7c0ccd3a921cb766dcd8e17d71cd2efeffa0**, tree
eafdc6546e1fe6f56c4b788eb4f008be0333d0b8. Локальный checkpoint
8a74b351ed20b5f5d3434306219502aeca6dbd0c имеет тот же tree.
Corrected runtime **da67d89a86c917249216747f44e82b9239b72ca2**, tree
1fb53c4a96ad6bb69ad6f766a26fb606a23b0e0e; local checkpoint a4a0ddeef3c9520b3ca0624c127029f1675b799d
has the same tree. Tested merge 5275a7bbf43f3be0fafcde8fac742cf404384000.
Experimental / draft PR №2. Evidence: validation/p06-matrix-20261025/.

## Проблема и исправление

Review матрицы нашёл P1: замена плана создавала goal IDs новой generation, а
физические call/USD limits считали события только по текущим goal IDs. Flat repair
мог потерять исходный parent_goal, hierarchical repair — получить свежую allowance.
Прежний aggregate lineage predicate сохранял тексты criteria, но не связывал
потраченные средства, scopes и linked oracle obligations с новой namespace.

Новый host inherit_constraints сохраняет исходные goal IDs и ancestor chain,
если local IDs и вся goal metadata строго эквивалентны (parent, description,
criteria, scope, call/USD caps). Новые names/metadata требуют fresh review. Flat
replacement наследует единственный однозначный оставшийся parent; неоднозначное
сопоставление требует review. Никакой similarity inference по тексту модели.

Step scopes наследуют консервативное пересечение ограничений оставшихся шагов
того же parent. Расширение/пустое пересечение и потеря linked code/test obligations
не допускают автоматического продолжения. Повторные поколения не добавляют новые
эквивалентные goals: прежние IDs остаются в событиях accounting и после resume.

Replan context теперь передаёт scopes/caps/links, чтобы модель могла явно сохранить
контракт. Никакая новая metadata не предоставляет права. Envelope, source/runtime
binding, repair cap, independent tool policy и final publication gates остаются
отдельными проверками. Legacy exact-plan default не меняется.

## Полная публичная journey

prepare → review → approve (opt-in) → resume: два source files, root/child goals,
первая реальная edit_node mutation ломает существующий matching pytest и
откатывается. Host сохраняет typed failing-check evidence. Scripted replan
сохраняет goals/scopes/caps/criteria/test links; replacement редактирует оба файла,
matching checks и полный pytest проходят. Changeset публикуется; delta graph
сравнивается с clean build, включая все node/edge facts.

Вторая plan approval не вызывается. Независимая tool policy продолжает работать:
trusted fixture callback заранее автоматически разрешает три edit attempts. Это
не тест отключения policy prompts/grants. Oracle files вне write scope сохранены
побайтно. Повторный resume terminal task не делает новых provider attempts.
Все provider responses scripted; pytest, dispatcher, rollback, accounting,
changeset publication и graph sync реальные. Новых live calls/расходов = 0.

## Найденная ошибка прежнего теста

Malicious-repository fixture добавлял второй шаг в памяти, но review перезагружал
ранее сохранённую задачу. Атакующий replan не исполнялся; assertion допускал done.
Теперь fixture сохраняет оба шага до review, проверяет только paused, отсутствие
ready_to_publish и неизменность AGENTS.md. Этот тест больше не может пройти
просто из-за отсутствия атакующей ветки.

## Проверки и воспроизводимость

Новые final cases: **14 PASS**, дедупликация final-new.xml + public-final.xml
по classname/name (поздняя исправленная public case заменяет ранний failed case).
Scope: original goal call/USD cap + flat/hierarchical replacement + restart;
cap/scope/identity/test-link/step-scope relaxation; policy/source/capabilities/
executor-profile drift; real public multi-file repair. Ruff/compile/diff PASS.

Ранний combined selection: 109 PASS / 1 FAIL (ошибочное ожидание отсутствия
отдельных tool-policy callbacks); subsequent new selection: 13 PASS / 1 FAIL
(сортировка edge dict через str вместо stable ID). После correction public-final
1 PASS. Ещё раньше public fixture использовал неверное поле receipt; JUnit
сохранён. Counts разных selections не суммируются.

repro_unbound_lineage.py отключает только новый binding в собственном процессе
для эмуляции прежнего namespace path. Expected RED: следующий scripted physical call
разрешён после исчерпанного исходного goal cap, test падает с
replacement received a fresh goal call allowance. Это differential diagnostic,
а не независимый запуск старого Git checkout. unbound-lineage-red.xml/txt сохранены.

## Acceptance matrix

Machine-readable acceptance-matrix.json содержит 9 строк и точные test identities:

| ID | Проверяемая граница |
|---|---|
| M01 | Hierarchical multi-file repair → checks → publish → graph parity |
| M02 | Scope/tools/commands expansion и real dispatcher refusal |
| M03 | Verify floor, criteria, linked test oracle и current-source evidence |
| M04 | Original goal USD/call caps, repair ceiling, no-progress, overspend accounting |
| M05 | Runtime/backend/policy/source/capability/executor-profile drift |
| M06 | Unknown charge/effect/response: durable hold без replay |
| M07 | Cancel до send и после known response: точный accounting |
| M08 | Persisted interruption/resume/legacy: нет новых прав или replay |
| M09 | Malicious repository instruction: атакующая ветка действительно достигнута |

Verifier требует присутствие и PASS всех параметризованных variants каждого
required test на обеих CI Python versions. Нет зачёта строки только по наличию
имени файла или зелёному общему числу. Артефакты связываются с Git merge/tree,
полными normalized source hashes и JUnit digests. CI IDs/results фиксируются
ниже и в ci-verified.json после completion.

## Review и границы сдачи

Self-review тем же исполнителем; независимый reviewer в этом продолжении не
привлекался, субагенты не запускались. Исправлены lost goal accounting lineage и
ложноположительный malicious fixture; дополнительные проверенные границы —
scopes/linked oracle obligations, legacy/uncertainty/cancel/accounting regressions.

Матрица относится к trusted-host mechanics. Unknown external effects остаются
hold; inject/crash checkpoints не доказывают OS kill/power-loss recovery. Opt-in
не поддерживает injected capabilities или arbitrary egress grants: они требуют
review. Hash integrity не является attestation dishonest host. Fixture oracle
не доказывает общее качество LLM repair или защиту от нечестных project tests.

Full P06 chapter acceptance остаётся OPEN до независимого review этого runtime
и evidence; self-review его не заменяет. Четыре production-блока quality/scale/
release/isolated-worker+crash+OS acceptance остаются OPEN. Docker не нужен
клиентскому core; untrusted execution по-прежнему требует isolation.

Следующее: независимый P06 review и correction findings, затем P07 frozen
matched-budget quality protocol. Явные P01–P03 integration gaps из marathon
report не исчезают после проверки P06.

## CI finding: stale project bytecode

Initial 84ce7c0 checks 40221128811 RED: Python 3.11 — 1293 PASS / 1 FAIL /
14 SKIP; Python 3.12, Docker и integrations 40221128839 SUCCESS. Public journey
на быстром runner выявил stale timestamp/size-valid project .pyc после edits.
Это не просто test flake: отдельный deterministic regression с одинаковым
size/mtime подтвердил и ложный FAIL, и ложный PASS (bytecode-red.xml: 2 FAIL).

Trusted pytest bootstrap после импорта installed pytest и перед project imports
перенаправляет bytecode cache в новый private temp prefix и отключает writes.
Один -B не устранил бы чтение прежнего cache. Project .pyc не удаляется и не
изменяется; в локальном runner temporary HOME очищается после call, в Docker
private /tmp принадлежит удаляемому container. Это не защищает от dishonest test
code, переписывающего свой interpreter. Startup/latency impact не измерялся.

checker-final.xml: 27 PASS / 3 SKIP, включая bytecode regressions обоих направлений
и полную public journey после исправления. Goal cap regression усилен: physical
scripted step И planner consume прежний goal limit, repair call блокируется до
send; restart сохраняет расход. Rerun ожидаемо-красного unbound-lineage diagnostic
сохранён отдельно. Это последующий corrected runtime, его SHA/CI — ниже.

## Финальная exact-source приёмка

Corrected runtime da67d89: [checks 40221853531](https://github.com/Akseleu-J/atomic-flow/actions/runs/40221853531)
и [interoperability 40221853379](https://github.com/Akseleu-J/atomic-flow/actions/runs/40221853379)
SUCCESS. Python 3.11.17 и 3.12.15: **1296 PASS / 14 SKIP каждый**, 1310 cases.
Все **9/9 host acceptance rows** имеют PASS всех указанных variants на обеих
версиях; presence/counts проверены offline verifier, не вручную по job badge.

Скачаны host 3.11/3.12, real Docker и real composition ZIP archives. SHA256
совпали с GitHub digests; JUnit digest/status и normalized hashes всех **277
src/tests Python files** совпали с runtime. Tested merge 5275a7b и runtime da67d89
имеют tree 1fb53c4a96ad6bb69ad6f766a26fb606a23b0e0e. Docker compile/tests/extra
checks PASS; composition publish/cancel/source_conflict PASS. Два core wheel
checks и 12 installed-wheel OS/Python/core-api jobs SUCCESS; 12 individual
wheel archives отдельно не скачивались. ci-verified.json / ci-runs.json /
ci-*-jobs.json и MANIFEST.sha256 сохраняют результаты и integrity evidence.

Решение: code scope и host acceptance matrix ACCEPTED для da67d89. Полная
глава P06 остаётся OPEN только до независимого review заявленного scope и
исправления его findings. Self-review не является независимым. Production и
качество автономного LLM repair этим результатом не объявлены. Documentation
follow-up не меняет runtime, его workflows проверяются отдельно по final HEAD.
