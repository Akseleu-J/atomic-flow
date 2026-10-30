# ADR-0002: graph storage for the local P01 contract

Date: 26 October 2026. Decision owner: integrating maintainer agent, within the
authorized P00–P06 implementation. Status: accepted for the local single-root
profile; replaces the initial SQLite selection in the roadmap, not its integrity
or transaction requirements.

Use the implemented bounded canonical JSON GraphStore behind the versioned read
port. A commit checks generation and persisted content identity, validates the
complete candidate snapshot, and atomically replaces it under cooperating-writer
locking. Defensive copies, strict decoding, explicit migration, source freshness,
and namespace-bound readers remain required. P01 tests exercise stale concurrent
writers, same-generation tampering, corruption, migration and identity boundaries.

The existing single-host code already uses this mechanism. Adding a second
backend before measuring its workload would add a migration and recovery surface
without evidence that it improves this profile. P01's numbered storage criterion
requires the transaction/API properties; SQLite is an implementation choice,
superseded here explicitly. No SQLite/WAL implementation or benchmark is claimed.

This decision does not establish throughput, power-loss durability, network-volume
support or multi-host corporate storage. The separate production scale gate must
measure p95, RSS, long runs and append costs. A backend change needs comparative
workload and crash evidence through the same port; performance acceptance cannot
be inferred from correctness tests. Session journal migration remains P09 scope.
