# REVIEW v3: atomic_flow 0.2 + atomic_ops (GDN2)

Статус проверки: ✅ = запускал в sandbox (числа/тесты), 🔎 = найдено чтением кода, патч не прогонялся на вашем репо.
Приоритеты: P0 ломает результат/безопасность · P1 качество · P2 эксплуатация · P3 гигиена.

## A. atomic_flow

### P0
| # | Где | Проблема | Исправление |
|---|---|---|---|
| A1 | `benchmarking.py::run_benchmark` | 🔎 `case["mode"]` **нигде не читается**: `answer(..., mode=mode)` берёт глобальный `--mode` (medium). Кейсы `multi-*` ("hard") и `atomic-ops-kernel-config` ("simple") идут в medium. «Multi-step 4/4» в METRICS.md не про hard-режим. | `mode=case.get("mode", mode)`; у CLI `--mode` default `None` (None = «как в кейсе», значение = принудительно). |
| A2 | `pyproject.toml` package-data | 🔎 `benchmark_data/fixtures/*.py` не захватывает подпапку `fixtures/atomic_ops/` → в установленном (не `-e`) пакете `fixture_project()` без atomic_ops, 5 кейсов падают `ValueError: points to missing node(s)`. | `"benchmark_data/fixtures/**/*.py"` (setuptools>=68 ок). Добавить в CI шаг «build wheel → install → pytest». |
| A3 | `tests/test_config_backends_api.py::test_placeholder_server_key_refused` | 🔎 `import atomic_flow.api.server` выполняет `app = create_app()` на импорте. В изоляции (`pytest -k placeholder`) импорт падает **вне** `pytest.raises`; тест зелёный только из-за порядка (раньше модуль импортировал другой тест). | `uvicorn.run(create_app, factory=True)`, убрать `app = create_app()` с уровня модуля (или `__getattr__` ленивый). |
| A4 | `trust` (config.py) | 🔎 доверие привязано к **пути**, не к содержимому. После `atomic trust` любой `git pull` с изменённым `.atomic_flow/config.yaml`/`prompts/*` снова может перенаправить ключ. | В `trusted_projects.txt` хранить `path<TAB>sha256(config.yaml + prompts/*)`; несовпадение хэша = не доверен. |
| A5 | `backends/http.py` | 🔎 `urllib` при редиректе пересылает заголовки (в т.ч. `Authorization`/`x-api-key`) на новый хост (насколько мне известно). | Свой `HTTPRedirectHandler`, запрещающий редиректы (или кросс-хост). |

### P1 — качество поиска
| # | Где | Проблема | Статус / исправление |
|---|---|---|---|
| A6 | `activation/text.py::stem` | ✅ Несогласованный стемминг: `process/processes → proces/process`, `class/classes`, `file/files → file/fil`, `rule/rules`, `node/nodes`, `status/statuses`. Документация обещает consistency. | **Исправлено** в `text.py` (plural → derivational → trailing e), 10 пар в тесте. |
| A7 | `text.py::SYNONYMS` | ✅ Ключи ищутся **после** стемминга: `state→stat`, `combine→combin`, `inverse→invers`, `score→scor`, `layer→lay` — записи мёртвые (не срабатывали никогда). | **Исправлено**: ключи стеммятся при импорте. |
| A8 | `text.py::terms` | 🔎 Синонимы вшиты и в **индекс** (раздувают `dl`, дают ложные совпадения: узел со словом `state` матчится на `carry`), и в запрос с весом 1.0. | `terms(expand=False)` для индекса, `expand=True` только для запроса; вес синонимов 0.5 (патч `activate.py` ниже). |
| A9 | `SYNONYMS["gradient"]` | 🔎 содержит `dhu`, `dav` — это имена функций из **вашего же** benchmark-кейса. Это подгонка под тест; метрика retrieval завышена. | Generic-аббревиатуры остались; доменные — через `register_synonyms()` (project glossary). |
| A10 | `activate.py::search` | 🔎 `matched/len(q_terms)` считает и синонимы → `weak` ложно срабатывает. | Считать по primary-терминам (патч ниже). |
| A11 | `activate.py` exact-name prior | 🔎 `symbol_key in q_raw` ×1.35 даёт буст всем `step`/`body`/`run`/`get` при запросе «next step». | Только если `len(name) >= 4` и имя не из набора generic (`step, body, run, get, main`). |
| A12 | RU-запросы | ✅ 9 из 27 кейсов — `skip_retrieval` (русские), т.е. ранжирование не измеряется и зависит от LLM-Parser. | **Добавлен** детерминированный RU→EN glossary (generic ПО-лексика): «скидка/округляется/итог/счёта» → `discount, round, total, invoic`. Теперь их можно включить в free-метрику. |
| A13 | `tree/builder.py::_link` | 🔎 Рёбра только из `ast.Call`. Не видны: `scan(step,…)`, `pallas_call(kernel,…)`, `_gdn2_core.defvjp(fwd, bwd)` (module-level), декораторы, `import … as _r2c`. Поэтому `_gdn2_core_bwd` имеет fan_in=0 — это прямая причина «омитит dav_backward_pallas / forward_trainable» в METRICS. | **Добавлен** `tree/references.py` (✅ тест): reference-рёбра + alias-таблица. Подключить в `_link` с весом 0.3 (меньше, чем call). |
| A14 | `builder._link` | 🔎 Атрибутные вызовы `x.get()` = голое имя `get`; при совпадении в том же файле даёт ложное ребро. Вложенные `step` (6 штук в atomic_ops) — неоднозначны, берётся до 3. | Для `ast.Attribute` резолвить только если имя уникально в проекте; nested-функции резолвить в своём родителе. |
| A15 | builder | 🔎 Не индексируются: константы уровня модуля (`FREE_SHIPPING_THRESHOLD`, `VAT_RATES`, `KAGGLE_*`, `BACKEND_DEFAULTS`) и содержимое `.md` (в `terms` попадает только первая строка!). Вопрос по `docs/` без шансов. | Новые `kind`: `variable` (UPPER_CASE присваивания) и `section` (заголовки markdown). |
| A16 | `choose_mode` | ✅ `"review" in "preview the invoice"` → True → hard (6+ вызовов). Также `trace`/`audit` подстрокой. | `re.search(r"\b(review|refactor|migrat\w*|trace|audit)\b")`. |
| A17 | `orchestrator` medium | 🔎 `needs_more_context` в medium → повтор с тем же контекстом (попытка впустую). | В medium при `needs_more_context` сразу выходить или делать один шаг expansion по графу (`index.neighbors`). |
| A18 | `_answer` | 🔎 `load_tree` → затем `refresh_tree` снова `load_tree`: JSON парсится дважды на запрос; `TreeNode.model_validate` для всех узлов каждый раз. | Грузить 1 раз; кэшировать валидированные узлы по `fingerprint`. |

### P2 / P3
* `openai_compatible.py`: reasoning-модели OpenAI требуют `max_completion_tokens`; `max_tokens` в списке «droppable» отсутствует → HTTP 400. Добавить swap.
* Anthropic backend: нет prompt caching. Критик и Answerer шлют один и тот же `project_context` (METRICS: 746k in / 230k out за 54 прогона) — `cache_control` на блок контекста + в критик передавать только цитируемые узлы ≈ −30..40% input.
* `redact._LITERAL` требует кавычек: `password: hunter2hunter2` в YAML/.env не маскируется. Для `.yaml/.toml/.env` разрешить значения без кавычек.
* `kaggle/credentials._expiry`: epoch в миллисекундах молча даёт «никогда не истекает» (`if numeric > 1e11: numeric /= 1000`).
* `config.load_config`: опечатка в `type:` → голый `KeyError` из `BACKEND_DEFAULTS[kind]`, а не `ConfigurationError`.
* `.atomic_flow/focus.yaml` создаётся `atomic init`, но **нигде не читается** — мёртвая фича: реализовать (буст путей) или убрать.
* Версия в 3 местах (`__init__`, `pyproject`, `FastAPI(version=…)`) → `importlib.metadata.version`.
* `AnswerRequest.project="."` резолвится от cwd, а не от `ATOMIC_FLOW_PROJECT_ROOT` → лишние 403.
* Нет CI (`.github/workflows`): ruff + mypy + pytest + wheel-install.
* Нет тестов: `redact`, `http.post_json` (retry/Retry-After), `anthropic.py`, `kaggle/credentials.parse_env_file`, drop-param в openai backend.

### Методология бенчмарка
1. Утверждения регэкспов правились **после** просмотра падений (`checkout-composition`, `sourcing-*`). Нужен held-out набор, который не трогают после первого прогона.
2. Заголовочная цифра = **strict** (51/54 = 94.4%), а не lenient (100%).
3. Критик = та же модель; `verdict` — самооценка. Реальная метрика качества — `strict_passed` + `grounding`.
4. 2 повтора на кейс не отличают flaky от шума; для флаки-кейсов нужно ≥5.

## B. atomic_ops (GDN2)

Математику **forward и backward я сверил вручную** с token-serial: WY-решение `A=(I+T)^-1`, `w_pseudo/u`, `v_new=u−w_pseudo·h_pre`, обновление состояния, B1 (reverse-scan), B2, B3 (включая `dT=−AᵀdA·Aᵀ` и множитель `(1−eps)`), B4 и B5 — согласованы, расхождений формул не нашёл. Слабые места не в формулах, а в нумерике, памяти и инженерии:

| # | Где | Проблема | Исправление |
|---|---|---|---|
| B1 | `configs.sanitize` везде | 🔎 clip ±1e4 + `nan_to_num` на каждой границе **маскирует** Inf/NaN и вносит смещение градиента без сигнала; вне custom_vjp-маски (clip не учтён в backward). | Режим `strict` (по умолчанию в тестах): счётчик сработавших clip через `jax.debug` под флагом, assert «0 clip» в grad-тестах. |
| B2 | Kernel A / B4 | 🔎 `(bc,bc,D)` тензор `exp(gc_i−gc_j)` на VPU: 8 MiB на временную при bc=D=128, ×4 временных; B4 просит `vmem_limit=150 MiB` — больше VMEM известных мне поколений TPU. | ✅ **`aqk_factorized.py`**: при `g≤0` `exp(gc_i−gc_j)=exp(gc_i−c)·exp(c−gc_j)`, `c=gc[i0−1]`, оба множителя ≤1 → одна MXU-матрица на строку-блок; elementwise остаётся только на диагональных 16×16. Ошибка vs elementwise: 1e-15 (float64) при max\|gc\|=388. Нужно `g≤0` (задокументировать/`jnp.minimum(g,0)`), и тот же приём в B4. |
| B3 | Kernel B | 🔎 Альтернатива forward-substitution: `(I+T)^-1=(I−T)(I+T²)(I+T⁴)…` (T нильпотентна) — 7 матмулов для C=256. ✅ в float64 точна (rel err ≤4e-16, cond ≤7). **Не проверено в f32 на плохо обусловленных T** — это эксперимент, не замена; сравнить с `_wy_inverse` на ваших реальных Akk. |
| B4 | pipeline | 🔎 Остаточные тензоры ≈ 5.75 KB/токен/голову (Aqk, Akk, A = 3 KB; w_pseudo,u,kg,qg = 2 KB; v_new 0.5; h_pre 0.25). Для B=4,H=8,L=32k ≈ 6 GB. `Akk` передаётся в B3, но **не используется** (мёртвый DMA + residual); `kg/qg` пересчитываются в B3 всё равно. | Убрать `Akk` из residuals и `in_specs` B3 (−1 KB/токен); `kg`,`qg` не хранить (−1 KB). ≈ −35%. |
| B5 | `pl.pallas_call` | 🔎 Нет `dimension_semantics` → на megacore (v4/v5p) grid не делится по ядрам; нет `name=`/`cost_estimate`. | `pltpu.CompilerParams(dimension_semantics=("parallel",)*3, vmem_limit_bytes=…)`. |
| B6 | `L % bt == 0`, `D==128` | 🔎 Любая длина не кратная 256 — ValueError. | ✅ **`padding.py`**: pad `k=b=v=w=0, g=0` ⇒ `alpha=1`, запись 0 ⇒ состояние не меняется. Проверено на token-serial: err = 0.0 по выходу и `h_final`. |
| B7 | `gdn2_fwd.py` | 🔎 `gdn2_pallas_forward` и `…_with_residuals` (и `inter_chunk_combine` ×2) — копипаста. Любая правка в одной ветке расходится с primal-путём `_gdn2_core`. | Одна функция с `return_residuals: bool`. |
| B8 | docs vs code | 🔎 `reference.py` говорит о «fallback on CPU/GPU» в `gdn2_pallas_forward_trainable` — в коде fallback'а нет; `interpret=` есть только у A и B4 (B, C, B1–B3 нет) → нельзя гонять Pallas-путь на CPU в CI. `pipeline` пишет «NO recomputation», но B3 пересчитывает `kb_decayed/kg/qg`, а pipeline — `gc`. `configs` утверждает, что `bc/mb не влияют на точность` — `mb` меняет порядок суммирования. | Протянуть `interpret` во все `pallas_call`; поправить docstring'и; добавить реальный fallback на `gdn2_chunked_wy_reference(wy_eps=config.wy_eps)`. |
| B9 | dtype | 🔎 Token-serial возвращает `q.dtype`, chunked reference и Pallas — float32. Сравнения по dtype расходятся незаметно. | Единое правило: output → `q.dtype`. |
| B10 | Тесты | 🔎 Нужны: (1) chunked vs serial в float64 с h0 и случайным g; (2) `jax.grad` custom_vjp vs `jax.grad(reference)` (interpret=True); (3) почти вырожденные Akk (крупные `‖b·k‖`) c eps>0; (4) счётчик clip == 0; (5) padding; (6) bf16-вход. |

## C. Что я приложил
* `src/atomic_flow/activation/text.py` — готовая замена (✅ A6, A7, A8, A12; 17 тестов проходят).
* `src/atomic_flow/tree/references.py` — сборщик reference-рёбер (✅ тест), подключение в `_link` — на вашей стороне.
* `atomic_ops/aqk_factorized.py`, `atomic_ops/padding.py` — проверены численно.
* `tests/test_text_v3.py`, `tests/test_references_v3.py`.
* `PATCHES.md` — точечные диффы для A1, A2, A3, A10, A11, A16.
