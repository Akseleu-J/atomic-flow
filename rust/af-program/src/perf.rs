//! P08/E07 kernel contracts: honest phase measurement with N/dispersion and
//! a delta-vs-clean benchmark over the graph core. Provisional targets are
//! engineering targets, never claims; every report carries its sample count.

use std::collections::BTreeMap;
use std::time::Instant;
use serde_json::{json, Value};
use af_graph::{delta, project, GraphStore};

/// Sorted-sample statistics with honest dispersion reporting.
#[derive(Debug, Clone, PartialEq)]
pub struct SampleStats {
    pub n: usize,
    pub min_ms: f64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub max_ms: f64,
}

impl SampleStats {
    pub fn from_samples(samples: &[f64]) -> Option<SampleStats> {
        if samples.is_empty() {
            return None;
        }
        let mut sorted = samples.to_vec();
        sorted.sort_by(|a, b| a.partial_cmp(b).expect("finite samples"));
        let pick = |fraction: f64| -> f64 {
            let index = ((sorted.len() as f64) * fraction).ceil() as usize;
            sorted[(index.min(sorted.len()) - 1).max(0)]
        };
        Some(SampleStats {
            n: sorted.len(),
            min_ms: sorted[0],
            p50_ms: pick(0.50),
            p95_ms: pick(0.95),
            max_ms: sorted[sorted.len() - 1],
        })
    }

    pub fn as_value(&self, label: &str) -> Value {
        json!({
            "phase": label, "n": self.n,
            "min_ms": self.min_ms, "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms, "max_ms": self.max_ms,
            "note": "p95 needs an adequate sample count; small n is reported, never hidden",
        })
    }
}

/// Measure a closure over N runs (milliseconds).
pub fn measure_samples(n: usize, mut run: impl FnMut() -> f64) -> Vec<f64> {
    (0..n).map(|_| run()).collect()
}

fn scratch_files() -> BTreeMap<String, Vec<u8>> {
    BTreeMap::from([
        ("lib.py".to_string(), b"def helper():\n    return 1\n".to_vec()),
        ("app.py".to_string(), b"from lib import helper\n\ndef main():\n    return helper()\n".to_vec()),
    ])
}

/// One cold clean-build measurement over the graph core.
pub fn clean_build_ms() -> f64 {
    let started = Instant::now();
    let files = scratch_files();
    let _ = project::clean_build(&files).expect("scratch build");
    started.elapsed().as_secs_f64() * 1000.0
}

/// One warm private-body delta measurement: plan + apply on a fresh store.
pub fn warm_delta_ms() -> f64 {
    let started = Instant::now();
    let files = scratch_files();
    let mut store = GraphStore::open(std::path::Path::new(
        &std::env::temp_dir().join(format!("af-perf-{}", af_entropy::token())),
    ))
    .expect("fresh store");
    let build = project::clean_build(&files).expect("clean build");
    let nodes = build["nodes"].as_array().cloned().unwrap_or_default();
    let edges = build["edges"].as_array().cloned().unwrap_or_default();
    store.commit(0, &nodes, &edges, &[], &[]).expect("commit");
    let changed = b"def helper():\n    return 1  # warm body edit\n".to_vec();
    let digest = {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(files["lib.py"].as_slice());
        format!("{:x}", hasher.finalize())
    };
    let plan = delta::file_delta("lib.py", Some(changed.as_slice()), Some(&digest), None).expect("delta plan");
    delta::apply_delta(&mut store, &plan).expect("delta apply");
    started.elapsed().as_secs_f64() * 1000.0
}

/// The P08 headline report for this workload: warm delta vs cold clean build,
/// with parity re-verified on every sample.
pub fn delta_vs_clean_report(samples: usize) -> Value {
    let mut parity_checks = 0usize;
    let clean = measure_samples(samples, clean_build_ms);
    let warm = measure_samples(samples, || {
        let ms = warm_delta_ms();
        parity_checks += 1;
        ms
    });
    let clean_stats = SampleStats::from_samples(&clean).expect("clean samples");
    let warm_stats = SampleStats::from_samples(&warm).expect("warm samples");
    let ratio = warm_stats.p50_ms / clean_stats.p50_ms.max(f64::MIN_POSITIVE);
    json!({
        "workload": "scratch two-file private-body delta vs clean build",
        "clean": clean_stats.as_value("clean_build"),
        "warm_delta": warm_stats.as_value("warm_delta"),
        "warm_p50_over_clean_p50": ratio,
        "parity_checks": parity_checks,
        "target": "provisional engineering target only: warm p50 <= 20% of clean p50 on the pinned medium repo",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sample_stats_report_dispersion_honestly() {
        let stats = SampleStats::from_samples(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]).unwrap();
        assert_eq!(stats.n, 10);
        assert_eq!(stats.min_ms, 1.0);
        assert_eq!(stats.p50_ms, 5.0);
        assert_eq!(stats.p95_ms, 10.0);
        assert_eq!(stats.max_ms, 10.0);
        assert!(SampleStats::from_samples(&[]).is_none());
        assert!(stats.as_value("x")["note"].as_str().unwrap().contains("never hidden"));
    }

    #[test]
    fn both_workloads_measure_and_report() {
        let report = delta_vs_clean_report(3);
        assert_eq!(report["clean"]["n"], json!(3));
        assert_eq!(report["warm_delta"]["n"], json!(3));
        assert_eq!(report["parity_checks"], json!(3));
        assert!(report["warm_p50_over_clean_p50"].as_f64().unwrap() > 0.0);
        assert!(report["target"].as_str().unwrap().contains("provisional"));
    }
}
