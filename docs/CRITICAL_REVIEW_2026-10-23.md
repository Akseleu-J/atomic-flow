# Независимый критический разбор atomic-flow

Дата: 23 октября 2026. Проверенный HEAD:
`00f77b592234ebca98ae5d6716a0ea6902d70b43`; runtime:
`58b3dd494ee038f5303386d3e527012150444e34`.

Последующее исправление R01–R07: `IMPLEMENTATION_STEP_17.md`. Этот документ
сохраняет исходный аудит и воспроизведения на старом commit; строки «открыто» ниже
относятся к моменту аудита. Текущие regression/CI результаты и ограничения fixes
записаны в PROJECT_CONTEXT. G01/G02/G03 остаются отдельными открытыми gates.

## Вердикт

Проект имеет обширные проверенные контракты, но **готовность к конкурентному
production-применению не доказана**. Аудит обнаружил дефекты, которые прошли
существующий CI: обход редактирования секретов через результаты исполнения,
подмену quality checker, повтор неидемпотентного действия после неопределённого
исхода, ошибки workflow/scheduler и недостоверные оценки качества/расходов.
Это конкретные препятствия перед эксплуатацией, а не просьба добавить ещё функции.

Предыдущие «62/62 implemented_scoped» обозначают покрытие каталога ограниченными
реализациями. Они не означают, что 62 подсистемы прошли эксплуатационную приёмку.
735 CPU tests и Docker acceptance подтверждают проверенные сценарии, но не
отсутствие дефектов и не качество решения новых задач живой моделью.

Прошли три независимых AI-reviewer с отдельным контекстом: безопасность;
runtime/архитектура; evaluation/release. Основной агент прочитал выводы и повторил
отобранные offline reproductions. Это внутренний adversarial review, **не внешний
человеческий pentest, не formal verification и не сертификат**. Runtime не изменялся;
перечисленные дефекты остаются открытыми на указанном commit.

## Доказательства и приоритеты

`validation/critic-20261023/`: исходники воспроизведений как `.py.txt`, raw outputs
и hash manifest. Использованы фиктивный credential, собственные временные проекты,
scripted model responses. Vault, настоящие ключи и внешние model API не использовались.
Локальный pytest выполнял только созданный для аудита тест. Реального Docker-прогона
этих новых reproductions на ноутбуке не было; аналогичный путь в Docker отмечен
как вывод из кода, а не измеренный результат.

**P1:** закрыть до пилота с приватным кодом/внешними эффектами или до использования
затронутых метрик для заявлений о преимуществе. **P2:** закрыть до поддерживаемого
пилотного релиза/масштабирования. Это внутренние приоритеты, не CVSS и не
универсальная сертификационная шкала. P0 в этом проходе не доказан.

| ID | Приоритет | Находка | Основание |
|---|---|---|---|
| R01 | P1 | Source confidentiality обходится через execution output | Local repro, реальный dispatcher |
| R02 | P1 | Неопределённое неидемпотентное действие допускает повтор | Реальный orchestrator/tool loop со scripted ответами |
| R03 | P1 для доверия к проверкам | Project module подменяет pytest и final completion | Local repro, реальный assess_completion |
| R04 | P2 | Workflow теряет факт предыдущих успешных эффектов | Реальный workflow/dispatcher, эффект моделируется |
| R05 | P2 | Независимый шаг обходится без последующего replan | Реальный scheduler, внешние зависимости замоканы |
| R06 | P1 для quality claims | QA grader принимает неверную семантику и ложное abstention | Offline repro на текущих fixtures |
| R07 | P1 для efficiency claims | Legacy benchmark теряет usage неуспешных попыток | Синтетический accounting repro |
| G01 | P1 production gate | Нет независимого live edit benchmark | Проверка опубликованных reports и runner code |
| G02 | P2 release gate | Нет текущего isolated wheel acceptance и воспроизводимого release resolution | Статический аудит CI/packaging |
| G03 | P1 для больших repo / P2 integration | Нет enforceable snapshot admission и единого durable capability path | Статический аудит, documented scope gaps |

### R01 — execution output обходит ограничения на source

Места: `agents/changeset.py:93-115,134-150`,
`agents/tools/run_command.py:37-49`, `llm/orchestrator.py:523-531`.
Пути относительно `src/atomic_flow/`.

Прямое чтение `credentials.json` отклоняется source admission; тот же файл,
исключённый `.gitignore`, попадает в ChangeSet snapshot по другой политике.
Падающий тест печатает его содержимое. `run_command` возвращает raw stdout/stderr,
которые orchestrator сохраняет и добавляет в model history. Фиктивный token
распознаётся существующим redactor, но этот канал его не вызывает.

Наблюдалось: `source_read_denied=true`, `ignored_credential_copied=true`,
`raw_fake_credential_in_tool_result=true`, `redactor_would_have_masked=true`.
Container filesystem/network restrictions сами по себе не устраняют утечку через
разрешённый output channel; аналогичный snapshot mount виден в `execution.py:92-99`.
Это не утверждение, что все `.gitignore` файлы всегда должны исключаться из сборки:
execution inputs требуют отдельной явной политики и разрешений.

**Приёмка:** отдельная host-owned admission policy для execution inputs; private
paths исключаются без специального разрешения; единая обработка stdout/stderr,
diagnostics, final-check output, artifacts и provider-bound messages. Тест с
распознаваемым фиктивным секретом не должен обнаружить его ни в одном из этих
выходов. Redaction — дополнительная защита, а не гарантия распознавания любого секрета.

### R02 — unknown effect не останавливает дальнейшие effects

Места: `llm/orchestrator.py:509-529`, `llm/history.py:70-76`,
`agents/base.py:21-44`.

Tool вернул error и `side_effects_may_have_occurred=True` при
`idempotent=False`. Loop отправил результат модели и выполнил такой же tool снова.
Повторный-call guard допускает несколько идентичных вызовов и сбрасывается между
role invocations; idempotence не превращается в запрет повтора после uncertainty.

Воспроизведение вызвало реальный `_Run._invoke_with_tools` и dispatcher:
`tool_invocations=2`, `recorded_uncertain_calls=2`. Внешний эффект моделировался;
настоящая операция не выполнялась. Новая генерация модели не делает безопасным
повтор уже отправленного действия с неизвестным результатом.

**Приёмка:** host-level unresolved-effect latch на run/task/operation scope.
После uncertainty запрещены новые effects до явного reconciliation/нового host
решения. Состояние не теряется при retry, steering, смене роли, compaction и resume.
Протокол должен различать transport replay, новый запрос модели и разрешённую новую
операцию; нельзя полагаться только на совпадение текстовых args.

### R03 — checker identity определяется содержимым проекта

Места: `agents/tools/run_command.py:37-44`, `agents/execution.py:196-200`,
`agents/completion.py:60-67`.

`python -m pytest` запускается из project root. Локальный `pytest.py` подменяет
установленный package. В synthetic project оставался настоящий падающий тест,
но marker-only module завершился с exit 0. Реальный `assess_completion` вернул
`ready_to_publish=true`, `tests=passed`, `test_exit_code=0`.

**Приёмка:** host-controlled launcher доверенного installed checker вне repository
import resolution; explicit workspace target, checker identity и collection/result
evidence. `pytest.py`, `ruff.py`, `mypy.py` в проекте не подменяют выбранный checker;
zero collected tests не превращается в подтверждённый test success.
Это устраняет показанную подмену, но само по себе не делает произвольные
злонамеренные tests независимым доказательством correctness.

### R04 — workflow сообщает false после завершённого write

Места: `workflows.py:103-114,120-124`.

Cumulative `effects` обновляется только из возвращённого uncertainty flag.
Успешные write/execute tools обычно его не выставляют. После успешного simulated
write и отменённого read report содержит completed applied write и одновременно
`side_effects_may_have_occurred=false`.

**Приёмка:** сохранять наличие завершённых write/execute effects отдельно от
uncertainty текущей операции. Cancel/error report учитывает всё, что произошло
раньше; completed effect не исчезает из общей сводки при read-only pending step.

### R05 — независимая работа теряет обещанный replan

Места: `agents/task_loop.py:248-255,303-309`.

Один failed step без независимой работы вызывает replanner один раз и требует
fresh review. Добавление независимого шага приводит к его выполнению, после чего
no-runnable branch останавливает task с `failed/done`, не вызывая replanner вообще.
Это противоречит намерению «finish independent work before replanning» в коде.

**Приёмка:** после независимой работы eligible failed dependencies попадают в
bounded replanning; сохраняются completed steps, max_replans/budget/cancel gates,
а прежнее approval инвалидируется до исполнения нового плана.

### R06 — правдоподобная форма засчитывается как правильность

Места: `benchmarking.py:70-90`, `llm/verify.py:16-38`.

Текущий QA grader проверяет regex, citation IDs и наличие identifiers. На реальных
fixtures неверное «The subtotal subtracts price from quantity.» с правильным ID
получило `passed=true, grounded=true`. Для отсутствующих payout данных утверждение
«Payments are processed instantly.» с `confidence=low` получило
`abstained=true, passed=true`. Backtick identifier check не проверил ни одного имени.

**Приёмка:** замороженный rubric отдельно оценивает factual entailment, relevance
citations и реальный отказ утверждать неизвестное. Gold, semantic inversions,
irrelevant citations и false abstentions должны проходить calibration до model
сравнения. Ноль пропущенных заранее сконструированных ложных примеров в regression
наборе — gate этого набора, не гарантия универсальной semantic verification.
Identifier/regex checks оставить диагностикой, не называть их factual grounding.

### R07 — legacy benchmark показывает неполный расход как полный

Место: `benchmarking.py:120-140`.

Exception row не сохраняет usage; summary суммирует только успешно возвращённые
ответы. Synthetic model: два ответа по $0.10/10 input tokens, второй затем падает
на parsing. Отчёт показывает $0.10/10 вместо modeled $0.20/20. Это не фактический
provider bill; воспроизведение показывает потерю данных в accounting aggregation.
Новый QA CallMeter имеет другой путь учёта, но старый CLI benchmark остаётся.

**Приёмка:** общий durable attempt accounting для всех evaluator entry points.
Parsing failure, cancellation и rejected final сохраняют known usage; unknown
делает total unknown, а не правдоподобным partial total. Cost per success включает
затраты неуспешных попыток. Quota equivalent и provider billing различаются явно.

## Пробелы уровня продукта и доказательств

**G01 — качество и преимущество.** `docs/EVALUATION_PILOT.md:3-5` и
`evaluation.py:108-130`: calibration проверяет шесть известных own-repo changes,
а не решение новых задач моделью. Live QA — восемь пар, одна модель, один repeat;
7/8 против 6/8 имеет только одну discordant пару. Atomic использовал 3.25× chat calls,
2.31× суммарное wall time и 0.627× reported quota cost baseline. Разные prompts,
context и calls не изолируют вклад graph/critic. Это полезный exploratory result,
но не доказанное превосходство и не live edit success.

**G02 — поставляемый release.** `.github/workflows/checks.yml:19-37,41-62`
проверяет source checkout на Ubuntu/Python 3.11–3.12. `pyproject.toml:30-32`
добавляет source в pytest import path. Нет текущего обязательного wheel build →
install outside checkout → smoke CLI/SDK/API/fixtures/migrations. Dependencies
имеют открытые нижние границы, Docker base tag mutable. `pip freeze` фиксирует
полученный environment, но не задаёт воспроизводимое разрешение следующей сборки.
Предлагаемый gate: tested constraints/lock для application/CI, base image digest,
clean wheel/sdist acceptance, checksums/SBOM и проверяемая build provenance.
Тестировать минимум поддерживаемых и выбранных актуальных Python/OS, явно
документировать support matrix. SLSA level автоматически не присваивается.

**G03 — масштаб и композиция.** `agents/changeset.py:93-111,127-155` целиком читает
каждый nonignored file, повторяет scans и копирует snapshot без file-count/total-byte/
per-file admission и cancellation callback в этих loops. Это source-confirmed gap;
OOM или disk exhaustion в аудите не вызывались. Нужны preflight scope/size/disk bounds,
streaming hashes/copy, cancel и cleanup, доказанный отказ до копирования oversize input.
`tools/measure_startup.py:51-68` измеряет imports по трём samples, не indexing/task p95.

`agents/task_loop.py:30-39,380-385` также не принимает/не передаёт capabilities,
которые доступны `sdk.start_answer`: custom registry/role tools, hooks, LSP/docs/root
clients. Workers — отдельный host API. Это документированные границы, не скрытые баги,
но потребителю нужен поддерживаемый end-to-end durable path с корректными staged
project bindings, общими budget/cancel и recovery, а не ручная сборка подсистем.

## Внешние ориентиры и цель превосходства

Единой сертификации «мировой coding agent» нет. Для этого проекта нужны раздельные
критерии безопасности, доставки и полезности. OWASP связывает excessive agency
с избыточными возможностями/правами/автономностью; это основание проверять host
authority, а не доверять тексту critic. [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/).

SWE-bench harness разделяет применение patch, исправление failing tests и сохранение
passing tests. Это полезный образец independent result grading, не доказательство
качества на всех задачах. [SWE-bench harness](https://www.swebench.com/SWE-bench/api/harness/).
В качестве воспроизводимого open baseline рассмотреть pinned mini-SWE-agent с тем
же backend и caps, не сравнивать непарные проценты разных моделей.
[mini-SWE-agent evaluation](https://mini-swe-agent.com/latest/usage/swebench/).

SLSA разделяет наличие provenance, подписанную provenance hosted build и hardened
build platform. Наши сохранённые log hashes полезны, но не заменяют защищённую
release provenance. [SLSA build track](https://slsa.dev/spec/v1.2/build-track-basics).

Рациональная гипотеза atomic-flow: **дешевле решать ограниченные задачи при
сохранении независимо проверенного качества и более надёжных границах effects**.
До matched эксперимента это гипотеза. Превосходство следует заявлять по заранее
выбранной метрике и workload, а не по числу функций или внутреннему self-score.

Порядок работы и предложенные измеримые gates:
`COMPETITIVE_ACCEPTANCE.md`. Источники сверены 23 октября 2026; citations не
означают соответствие всему стандарту или сравнение с закрытыми продуктами.
