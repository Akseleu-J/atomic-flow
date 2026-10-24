# Пакет 19 — W5: installed wheel acceptance

24 октября 2026. Основание: `audit-2026-10-23/PRODUCTION_PLAN.md` (W5) и
`docker-release-audit.md`/`quality-scale-audit.md` gate G02: CI проверял source
checkout, изолированной wheel-проверки не было.

## Что добавлено

- `.github/workflows/checks.yml`: job `wheel` (Ubuntu 24.04, Python 3.11/3.12) —
  build wheel+sdist из checkout (`python -m build`), SHA256SUMS, clean venv вне
  checkout, установка `dist/*.whl[api]` + httpx, `pip freeze` как tested
  environment evidence, затем smoke ИЗ ДИРЕКТОРИИ checkout (намеренно): пакет
  обязан резолвиться из site-packages, а не из исходников.
- `tools/wheel_smoke.py`: запускается интерпретатором clean venv; проверяет
  import location (site-packages побеждает checkout), packaged fixtures через
  `importlib.resources`, core journey `build_tree/save_tree/load_tree` без модели,
  SDK import, API `/health`+`/ready` через TestClient (если установлен extra),
  CLI `--help`. Без сети и model calls.

## Локальная проверка (Windows, этот ноутбук)

- Wheel собран `pip wheel . --no-deps`: `atomic_flow-0.2.0-py3-none-any.whl`,
  sha256 `5ff6529b3034def9cdf56b78bbd2db0edf76c014eb0438948e23b4946abd5447`
  (327 628 bytes).
- Clean venv (`--system-site-packages` от bundled runtime + зависимости
  установлены в него), wheel установлен `--no-deps --force-reinstall`.
- `tools/wheel_smoke.py --project-root .` изнутри checkout: **PASS**
  (import location site-packages; cases.json 7137 bytes; tree build/save/load ok;
  sdk ok; api health/ready ok на fastapi 0.162/starlette 1.9; cli ok).
- Локальная проверка не заменяет CI job: зависимости ставились в один venv
  поверх system-site, а не в полностью изолированное окружение; Linux-матрица
  и artifacts — на CI при пуше.

## Границы

- Это W5 acceptance pipeline, не release: нет SBOM, подписей, SLSA-заявлений,
  pinned base image digest и support matrix — они остаются в W9.
- Зависимости в CI ставятся из текущего PyPI (fresh resolution); freeze
  фиксирует фактический результат прогона как evidence, но не делает следующую
  сборку бит-в-бит воспроизводимой. Tested constraints/lock — отдельный шаг W9.
- Smoke покрывает CLI/SDK/API/fixtures/migration-независимые поверхности;
  packaged fixtures migration (исторические схемы) не прогоняется отдельным
  сценарием — сессии миграций покрываются CPU-набором в checkout.
- G02 закрыт этим пакетом только как «wheel acceptance pipeline существует и
  локально зелёный»; обязательный CI-прогон exact-source — на пуше.
