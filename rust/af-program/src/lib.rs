//! af-program: kernel contracts for the remaining roadmap chapters, one
//! module per chapter. These are the machine-checkable cores — the live
//! model, container and UI surfaces of each chapter sit above them.
//!
//! - [`eval`]: P07/E01 — honest verdicts, calibration triples, paired reports
//! - [`perf`]: P08 — sample statistics, delta-vs-clean benchmark
//! - [`release`]: P11 — artifact manifests, consumer verification, rollback
//! - [`rc`]: P12 — evidence-bound acceptance registry and RC decision
//! - [`publication`]: P14/E02 — verifiable publication report
//! - [`corporate`]: E03/E05/E06/E08 — principal scoping, egress policy,
//!   backend conformance, headless multi-principal acceptance

pub mod corporate;
pub mod eval;
pub mod perf;
pub mod publication;
pub mod rc;
pub mod release;
