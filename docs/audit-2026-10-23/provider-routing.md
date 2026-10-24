# Провайдеры, бесплатные квоты и автоматическое переключение

Дата: 23 октября 2026. Аудит HEAD `8a18fbe837b5c82597f782b6455e96b708607f36`, runtime `78f0df5c2abf18fc4b0035030a92178335134e04`. Это проект механизма и результаты offline probes, не реализация и не live-проверка аккаунтов. Model calls=0; секреты и пользовательские аккаунты не читались. Внешние источники проверены через web/GitHub 23 октября.

## Вывод

Автоматическое переключение реализуемо и полезно. Оно должно объединять разрешённые пользователем независимые сервисы и соблюдать их квоты. Оно не делает Claude/model API бесплатными и безлимитными: меняются доступная модель, качество, размер контекста, правила обработки данных и SLA. Бесплатность клиента Claude Code или шлюза не равна бесплатности используемой модели Claude. Экономию измерять полной стоимостью успешной задачи, включая неудачные попытки и человеческое исправление.

Рекомендация: небольшой собственный coordinator в atomic-flow с per-attempt accounting; внешний gateway оставить необязательным adapter. Не добавлять Docker/Redis/Node как обязательные зависимости ядра ради routing.

## Что реально есть в коде и что сломается

| ID / приоритет | Доказательство | Следствие / исправление |
|---|---|---|
| B01 / P1 | `backends/router.py:15-30,33-56` выбирает один backend при создании; `BackendConfig` (`config.py:41-60`) не содержит route policy/quota state. | Нет runtime failover или cooldown; `auto` означает выбор при старте. Добавить явные упорядоченные маршруты и состояния провайдера. |
| B02 / P1, reproduced | `backends/openai_compatible.py:87-99` вызывает `.get()` у `usage.cost`. OpenRouter документирует число. | `cost=0.001` вызывает BackendError с AttributeError, хотя ответ корректен; `cost=0.0` теряется и без configured prices становится unknown. Нормализовать provider usage до разбора content; учитывать числовой ноль; сохранить known usage даже при malformed content. |
| B03 / P2, reproduced | `backends/http.py:130-143`: Retry-After ограничивается 10 секундами и читается только как float. | При `Retry-After: 120` повтор идёт через 10 секунд. HTTP-date игнорируется. Установить `eligible_at` не раньше server deadline; если он за пределами общего deadline, переключить разрешённый маршрут или вернуть waiting, не сокращать cooldown. TaskBudgetBackend сейчас отключает HTTP retries — дефект относится к прямому adapter/другим retry-enabled caller paths. |
| B04 / P1 для failover | `http.py:18-19,35-40`; `agents/task_budget.py:161-184`. 402 классифицируется unknown; 429 — refused без структурированного subtype. | Нельзя отличить credits/quota/RPM/concurrency/billing restriction по одному статусу. 402 сохраняет консервативный unknown hold. Добавить provider-specific typed error + evidence о submission/usage; неизвестный ответ не объявлять бесплатным отказом. |
| B05 / P1 для failover | `agents/task_budget.py:43-53,103-138,158-211` фиксирует model/config/prices и одно событие на один backend call; `ChatResult` (`backends/base.py:28-38`) не несёт resolved provider/model/attempt id. | Wrapper, который незаметно переключает backend под budget wrapper, неверно атрибутирует цену, модель, quota и reservation. Route selection/admission должны предшествовать резервированию каждой физической попытки. |
| B06 / P2 | `http.py:60-72` возвращает только parsed body, теряя success headers; error detail обрезается до 1000 chars. | Невозможно надёжно обновлять remaining/reset или читать structured quota metadata. Добавить bounded allowlisted metadata, request ID и безопасный structured error parser; не хранить Authorization/raw headers. |
| B07 / P1 для совместимости | `backends/base.py:41-50`, `llm/request_budget.py:43-100`. Context limits явные/могут быть unknown; native tools opt-in только для compatible transport, streaming false. | Смена модели не гарантирует поддержанные tool schemas и размер истории. Перед каждой попыткой повторять admission и capabilities/quality policy. Нельзя переносить произвольный native transcript в Anthropic text adapter. |

Offline reproduction: `provider_probes.py` и `provider-probes.json` в этой папке. Скрипт заменяет HTTP вызов fixture; Retry-After использует fake clock. Он не подтверждает работоспособность живого OpenRouter/account. B04 — воспроизведение текущей классификации, а не доказательство некорректности любого 402 на любом сервисе.

## Какие проекты полезны

- [Claude Code Router](https://github.com/musistudio/claude-code-router): проверен README; описывает protocol adapters, ordered fallbacks, routing и наблюдаемость. MIT указан в README. Это архитектурный ориентир и optional gateway, не доказательство нашей совместимости. Текущий CLI добавляет Node/runtime/service; для малого ядра обязательная зависимость не нужна. Ни исходники, ни пакеты не запускались.
- [LiteLLM Router](https://docs.litellm.ai/docs/routing): зрелый набор cooldown/retry/route механизмов. Нужна отдельная contract-проверка: автоматический повтор generic errors/timeout может противоречить нашим unknown-charge gates; вложенные retries умножают попытки. Только один слой должен владеть retry policy. Базовая схема не требует внешнего gateway; Redis имеет смысл лишь для будущего распределённого сервиса.
- [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis): проверен README, полезен для discovery. Обещания каталога и перечисленные quotas не являются контрактом провайдера; модель/тариф/данные проверяются по первоисточнику.
- `cheahjs/free-llm-api-resources` обнаруживается в поиске, но прямой GitHub connector запрос к repo и README дал 404 в этой сессии. Не делаем вывод, что проект удалён, и не считаем cached/fork quotas актуальными. Поэтому используем независимо проверенные provider docs.

## Начальная матрица сервисов

Точный model ID выбирается после account-specific discovery и contract smoke. Числа из сторонних списков не зашиваются в код. «Подходит» ниже — кандидат для пилота, не measured coding quality.

| Сервис | Подтверждено первоисточником | Роль в пилоте / ограничения |
|---|---|---|
| Groq | [Rate limits](https://console.groq.com/docs/rate-limits): лимиты по запросам/токенам, 429, Retry-After и remaining/reset headers. | Прямой compatible adapter, сначала text-JSON. Использовать дешёвые/бесплатные разрешённые модели только после проверки tool quality. Квоты разделять по реальному scope, не по числу ключей. |
| Gemini API | [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits): RPM/TPM/RPD, scope project, не API key; account/model limits показываются в AI Studio. [Pricing](https://ai.google.dev/gemini-api/docs/pricing): free/paid различаются, есть условия использования данных для улучшения продуктов. | Кандидат второго независимого провайдера. Privacy policy должна допускать именно выбранный tier; для приватного кода нельзя автоматически выбрать сервис только по цене. Native Gemini adapter либо отдельно проверенный compatible endpoint. |
| OpenRouter | [Limits](https://openrouter.ai/docs/api_reference/limits): free models ограничены, есть platform и upstream quota, key quota API; upstream fallback не отменяет platform cap. [Usage](https://openrouter.ai/docs/cookbook/administration/usage-accounting): числовой cost и token usage. | Optional gateway; сначала B02. Для воспроизводимого пилота pinned model + provider restrictions. Не выбирать случайную free model на каждом шаге; неизвестная model capability исключает маршрут. В free_only upstream paid fallback также запрещён. |
| Mistral | [Usage and limits](https://docs.mistral.ai/admin/billing-usage/usage-limits), [free-mode guidance](https://help.mistral.ai/en/articles/698531-why-am-i-hitting-api-rate-limits-and-how-do-i-increase-them): free mode ограничен и предназначен для evaluation/prototyping. | Дополнительный прямой сервис после основных contract tests; не закладывать production SLA на бесплатный тариф. |
| Cloudflare Workers AI | [Pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/): free allocation и платное потребление имеют отдельные условия. | Второй этап исследования; проверить endpoint/model tool support, account quotas и hard stop до платного использования. Не называть его бесконечным бесплатным резервом. |
| Собственный inference | Доступные ресурсы и model license требуют отдельной проверки. | Убирает внешнюю API-квоту, но сохраняет hardware/energy/ops стоимость и ограниченную пропускную способность. На ноутбуке 3.4 GiB не планировать сильную локальную coding model; возможен явно выбранный удалённый host. |

Схема не включает создание аккаунтов, ротацию владельцев или обход блокировок. Несколько ключей одного project/org обычно делят лимиты. Restricted/account-policy errors требуют исправления доступа; это не сигнал обходить ограничение через другой ключ.

## Предлагаемый контракт routing v1

**1. Host-owned policy.** `allowed_routes`, `free_only | capped_paid`, data-egress allowlist, max total cost/calls/time, максимум физических attempts, minimum model capability/quality tier. Конфиг проекта не может расширить список egress/credential grants. Секреты остаются process-only references. Политика выбирается до отправки исходников.

**2. Resolved route.** Immutable snapshot: provider/model/revision if available, account/quota scope ID без секретов, protocol, context/tool limits, price source/as-of, pricing tier, data policy. Для неизвестных цен free_only отказывает; нулевые configured prices без подтверждённого бесплатного маршрута не считаются доказательством нулевого счёта.

**3. Single attempt coordinator.** Logical turn ID объединяет попытки, но у каждой собственный attempt ID. Последовательность: выбрать route → проверить policy/capabilities/context → атомарно зарезервировать task/daily spend и quota → durable not-sent/in-flight record → один transport send → нормализовать usage/outcome → settle → решить следующий шаг. Сохранить один shared task account для parent/workers, не заводить отдельный бюджет на provider. Полностью полученный billable ответ учитывается до content parsing/cancel. После неуспеха reservation не исчезает без evidence.

**4. QuotaStore.** Счётчики по provider/account/project/model/quota-group/window. RPM/TPM, дневной/месячный лимит и max concurrency — разные измерения. Durable cooldown/disabled states, Retry-After delta/date, clock skew, provider reset timezone, jitter и один half-open probe. Unknown remaining не считать infinity. Однопроцессного dict недостаточно для параллельных CLI; начать с локального transactional storage/lease. Распределённое хранилище — отдельный backend, не зависимость первого релиза.

**5. Outcome policy.** В v1 безопасное переключение только после доказанного not_sent или документированного отказа до inference. 429 transient: cooldown и следующий разрешённый route. Daily quota exhausted: ждать reset либо next independent allowed provider. 401/config/model error: disable route до исправления; не retry storm. 403 account/policy restriction: остановка с actionable status; provider-specific обычный quota denial можно обрабатывать отдельно по metadata. 402: классифицировать по контракту провайдера. Timeout/disconnect/5xx после возможной отправки: unknown hold, без автоматического replay. Explicit reconciliation может разрешить продолжение с сохранением фактической/неизвестной стоимости. Не склеивать частичные ответы разных моделей и не повторять уже выполненные tool effects.

**6. Sticky execution.** По возможности удерживать модель для шага. На разрешённом переключении повторно материализовать provider-neutral transcript, проверить source revision и tool pairing. Сохранять immutable tool result IDs; сжатие/смена модели не создаёт permissions/read proof. Состояния unknown tool effect и unknown billing независимо блокируют дальнейшие effects.

**7. Auditability.** Events: route selected/rejected, reason, cooldown/reset, attempt outcome, requested и resolved model/provider, token/cost source, logical/physical call counts, unknown holds. Экспорт метрик без prompt/secret по умолчанию; diagnostic payload bounded/redacted. Config version и routing decision записываются для воспроизводимости.

Это изменение архитектуры учёта, а не `except 429: next_backend()`. Сначала выделить общий accounting coordinator для task и ordinary ask, затем добавить маршруты поверх него.

## Gates перед включением по умолчанию

1. Offline contracts: numeric zero/nonzero/missing/malformed usage, NaN/negative/bool, malformed content with valid cost; 429 rate/day/token/concurrency; 402/403 structured errors; Retry-After seconds/date; quota reset/clock shift.
2. Accounting: crash before reserve, after reserve, after send, after response, during settlement; restart не повторяет неизвестную попытку; сумма физических расходов равна ledger, включая failures; free_only не вызывает paid fake transport ни по одному пути.
3. Concurrency: несколько CLI/processes, shared provider quota и parent/worker budget; нет stampede при reset; общий deadline/attempt cap ограничивает весь маршрут, а не каждый вложенный retry отдельно.
4. Protocol: меньшее окно fallback не отрезает mandatory evidence; unsupported native tools исключают route; история с tool results сохраняет pairing; смена модели не выполняет tool дважды.
5. Privacy: запрещённый provider не получает ни payload, ни probe с исходниками. Catalog/endpoint change не расширяет permission. Credentials не попадают в artifact.
6. Opt-in live smoke на разрешённых реальных аккаунтах: frozen provider/model/tier, minimal public fixture, успешный ответ/native contract и usage. Не истощать quota специально; quota fault injection локальный. Account availability и pricing сверяются непосредственно перед запуском.
7. Quality experiment: один фиксированный backend vs routing на одинаковых holdout tasks и budgets; success, false success, p95, cost per verified success, intervention/unknown rate. Режим free_only имеет честный `waiting_for_quota`, если все допустимые ресурсы исчерпаны.

## Ссылка на зеркало Claude Code

Найдена исходная ссылка из проекта: [777genius/claude-code-source-code-full](https://github.com/777genius/claude-code-source-code-full). Повторно прочитаны LICENSE и architecture documentation; provenance не аутентифицирован. [LICENSE](https://github.com/777genius/claude-code-source-code-full/blob/main/LICENSE) помечает материал `UNLICENSED — NOT FOR REDISTRIBUTION`. Согласно действующему AGENTS.md код/prompts зеркала не импортируем. Изученные общие темы — tool contracts, контекст, recovery, concurrency metadata, lazy loading — уже частично реализованы в собственных пакетах 6–17. Следующий шаг: проверить их совместную работу и независимые acceptance journeys. Копирование продуктовой структуры или увеличение каталога функций не закрывает наши production gates. Подробная прежняя карта: `docs/ARCHITECTURE_REFERENCE_REVIEW.md`, `docs/REFERENCE_OPPORTUNITIES.md`.
