# P06 chapter status — актуализация 25 октября 2026

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
