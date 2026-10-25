# P06 chapter status — актуализация 25 октября 2026

Актуализация package 40 (25 октября): [P06 typed failures и durable repair ceiling](IMPLEMENTATION_STEP_40_P06_REPAIR.md). Runtime f9fd48d5bacce8b3c93bf9b15677df3f7edbc4b3: host-typed source/attempt-bound failure receipts и persisted repair allowance на physical admission/settlement/resume/publication. Предыдущий metadata-only cap заменён реальным guard. Полный P06 и четыре production gates остаются OPEN; CI 40219160156 / 40219160140 SUCCESS; по 1280 PASS / 14 SKIP на Python 3.11/3.12, 273 source hashes verified, real Docker/composition PASS. Границы evidence — в package 40. Новых provider calls=0.

Полная сдача по §13: [P00–P06 marathon report](P00_P06_MARATHON_2026-10-25.md#p06--bounded-autonomy).

Lineage predicate и failure table встроены в runtime. StepFailure → bounded repair
с persisted last_failure_decision; неизвестное исключение → paused/stop. Dedicated
malicious-instruction и sync-crash journeys реализованы. Runtime 395b8ac;
P06 wiring опубликован как 0551924 с tree локального fc3bd2a.

Code scope локально проверен; full P06 acceptance OPEN: независимого review
в этом продолжении не было, self-review явно отделён от него. Scripted evidence
не подтверждает качество live repair и не закрывает production worker gate.

CI/source evidence и оставшиеся matrix cases — в общем отчёте и
validation/marathon-20261025/manifest.json.
