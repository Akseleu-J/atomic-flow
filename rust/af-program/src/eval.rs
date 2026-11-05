//! P07/E01 kernel contracts: honest verification verdicts, calibration
//! triples and paired comparison records. Scripted only — no live model
//! calls happen here; this is the machinery a live campaign must satisfy.

use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq)]
pub enum Verdict {
    /// Passed with the evidence that establishes it.
    Passed { evidence_sha256: String },
    /// Failed with the exact failing observation.
    Failed { reason: String },
    /// Not assessed: no admissible evidence exists. Omissions are explicit.
    NotAssessed { omissions: Vec<String> },
}

impl Verdict {
    pub fn as_value(&self) -> Value {
        match self {
            Verdict::Passed { evidence_sha256 } => {
                json!({"verdict": "passed", "evidence_sha256": evidence_sha256})
            }
            Verdict::Failed { reason } => json!({"verdict": "failed", "reason": reason}),
            Verdict::NotAssessed { omissions } => {
                json!({"verdict": "not_assessed", "omissions": omissions})
            }
        }
    }
}

/// One calibration environment: the triple that must hold before any task is
/// usable — base FAIL, gold PASS, plausible-wrong FAIL. Installation errors
/// are not requirement failures; they mark the environment not_assessed.
#[derive(Debug, Clone)]
pub struct CalibrationEnvironment {
    pub task_id: String,
    pub base_result: Verdict,
    pub gold_result: Verdict,
    pub plausible_wrong_result: Verdict,
}

impl CalibrationEnvironment {
    /// The triple gate. Unknown infrastructure outcomes are never PASS/FAIL.
    pub fn calibration_status(&self) -> Value {
        let graded = |verdict: &Verdict| match verdict {
            Verdict::Failed { .. } => "failed",
            Verdict::Passed { .. } => "passed",
            Verdict::NotAssessed { .. } => "not_assessed",
        };
        let triple_ok = matches!(self.base_result, Verdict::Failed { .. })
            && matches!(self.gold_result, Verdict::Passed { .. })
            && matches!(self.plausible_wrong_result, Verdict::Failed { .. });
        json!({
            "task_id": self.task_id,
            "base": graded(&self.base_result),
            "gold": graded(&self.gold_result),
            "plausible_wrong": graded(&self.plausible_wrong_result),
            "calibrated": triple_ok,
        })
    }
}

/// One arm of a paired comparison on a frozen task.
#[derive(Debug, Clone)]
pub struct PairedArm {
    pub arm: String,
    pub verdict: Verdict,
    /// None means the cost is unknown; unknown is never summed as zero.
    pub cost_usd: Option<f64>,
    pub wall_seconds: Option<f64>,
    pub interventions: u32,
}

#[derive(Debug, Clone)]
pub struct PairedTask {
    pub task_id: String,
    pub baseline: PairedArm,
    pub atomic: PairedArm,
}

/// Honest paired report: unknown costs stay unknown; nothing is dropped.
pub fn paired_report(tasks: &[PairedTask]) -> Value {
    let mut known_costs = (0.0, 0.0);
    let mut unknown_cost_tasks: Vec<String> = Vec::new();
    for task in tasks {
        for (arm, slot) in [
            (&task.baseline, &mut known_costs.0),
            (&task.atomic, &mut known_costs.1),
        ] {
            match arm.cost_usd {
                Some(cost) if cost.is_finite() && cost >= 0.0 && (*slot + cost).is_finite() => {
                    *slot += cost
                }
                Some(_) => unknown_cost_tasks
                    .push(format!("{}:{}:invalid_or_overflow", task.task_id, arm.arm)),
                None => unknown_cost_tasks.push(format!("{}:{}", task.task_id, arm.arm)),
            }
        }
    }
    let atomic_wins = tasks
        .iter()
        .filter(|task| {
            matches!(task.atomic.verdict, Verdict::Passed { .. })
                && matches!(task.baseline.verdict, Verdict::Failed { .. })
        })
        .count();
    let baseline_wins = tasks
        .iter()
        .filter(|task| {
            matches!(task.baseline.verdict, Verdict::Passed { .. })
                && matches!(task.atomic.verdict, Verdict::Failed { .. })
        })
        .count();
    json!({
        "tasks": tasks.iter().map(|task| json!({
            "task_id": task.task_id,
            "baseline": arm_value(&task.baseline),
            "atomic": arm_value(&task.atomic),
        })).collect::<Vec<_>>(),
        "summary": {
            "n_tasks": tasks.len(),
            "atomic_only_pass": atomic_wins,
            "baseline_only_pass": baseline_wins,
            "unassessed_pairs": tasks.iter().filter(|task| matches!(task.atomic.verdict, Verdict::NotAssessed { .. })
                || matches!(task.baseline.verdict, Verdict::NotAssessed { .. })).map(|task| task.task_id.clone()).collect::<Vec<_>>(),
            "known_cost_usd": {"baseline": known_costs.0, "atomic": known_costs.1},
            "unknown_cost_arms": unknown_cost_tasks,
            "claim": "exploratory; no superiority claim without a frozen hypothesis and a fresh holdout",
        },
    })
}

fn arm_value(arm: &PairedArm) -> Value {
    json!({
        "arm": arm.arm,
        "verdict": arm.verdict.as_value(),
        "cost_usd": arm.cost_usd,
        "wall_seconds": arm.wall_seconds,
        "interventions": arm.interventions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn passed() -> Verdict {
        Verdict::Passed {
            evidence_sha256: "a".repeat(64),
        }
    }

    fn failed(reason: &str) -> Verdict {
        Verdict::Failed {
            reason: reason.into(),
        }
    }

    #[test]
    fn calibration_requires_the_exact_triple() {
        let good = CalibrationEnvironment {
            task_id: "t1".into(),
            base_result: failed("assertion error"),
            gold_result: passed(),
            plausible_wrong_result: failed("wrong kind split"),
        };
        assert_eq!(good.calibration_status()["calibrated"], json!(true));
        // Installation error is not a requirement failure.
        let install = CalibrationEnvironment {
            task_id: "t2".into(),
            base_result: Verdict::NotAssessed {
                omissions: vec!["pip resolution failed".into()],
            },
            gold_result: Verdict::NotAssessed { omissions: vec![] },
            plausible_wrong_result: Verdict::NotAssessed { omissions: vec![] },
        };
        assert_eq!(install.calibration_status()["calibrated"], json!(false));
        assert_eq!(install.calibration_status()["base"], json!("not_assessed"));
        // Gold must pass.
        let bad = CalibrationEnvironment {
            task_id: "t3".into(),
            base_result: failed("x"),
            gold_result: failed("gold itself broken"),
            plausible_wrong_result: failed("y"),
        };
        assert_eq!(bad.calibration_status()["calibrated"], json!(false));
    }

    #[test]
    fn paired_report_keeps_unknown_costs_unknown() {
        let tasks = vec![
            PairedTask {
                task_id: "t1".into(),
                baseline: PairedArm {
                    arm: "baseline".into(),
                    verdict: failed("no patch"),
                    cost_usd: Some(0.01),
                    wall_seconds: Some(10.0),
                    interventions: 1,
                },
                atomic: PairedArm {
                    arm: "atomic".into(),
                    verdict: passed(),
                    cost_usd: Some(0.02),
                    wall_seconds: Some(12.0),
                    interventions: 0,
                },
            },
            PairedTask {
                task_id: "t2".into(),
                baseline: PairedArm {
                    arm: "baseline".into(),
                    verdict: failed("timeout"),
                    cost_usd: None,
                    wall_seconds: None,
                    interventions: 2,
                },
                atomic: PairedArm {
                    arm: "atomic".into(),
                    verdict: failed("wrong edit"),
                    cost_usd: Some(0.03),
                    wall_seconds: Some(9.0),
                    interventions: 0,
                },
            },
        ];
        let report = paired_report(&tasks);
        assert_eq!(report["summary"]["n_tasks"], json!(2));
        assert_eq!(report["summary"]["atomic_only_pass"], json!(1));
        assert_eq!(report["summary"]["known_cost_usd"]["baseline"], json!(0.01));
        assert_eq!(
            report["summary"]["unknown_cost_arms"],
            json!(["t2:baseline"])
        );
        assert!(report["summary"]["claim"]
            .as_str()
            .unwrap()
            .contains("exploratory"));
    }

    #[test]
    fn unassessed_arm_never_counts_as_an_opponents_win() {
        let arm = |name: &str, verdict| PairedArm {
            arm: name.into(),
            verdict,
            cost_usd: None,
            wall_seconds: None,
            interventions: 0,
        };
        let tasks = [
            PairedTask {
                task_id: "baseline-missing".into(),
                baseline: arm(
                    "baseline",
                    Verdict::NotAssessed {
                        omissions: vec!["no oracle".into()],
                    },
                ),
                atomic: arm("atomic", passed()),
            },
            PairedTask {
                task_id: "atomic-missing".into(),
                baseline: arm("baseline", passed()),
                atomic: arm(
                    "atomic",
                    Verdict::NotAssessed {
                        omissions: vec!["runner unavailable".into()],
                    },
                ),
            },
        ];
        let report = paired_report(&tasks);
        assert_eq!(report["summary"]["atomic_only_pass"], json!(0));
        assert_eq!(report["summary"]["baseline_only_pass"], json!(0));
        assert_eq!(
            report["summary"]["unassessed_pairs"],
            json!(["baseline-missing", "atomic-missing"])
        );
    }

    #[test]
    fn invalid_cost_is_explicitly_unknown_and_never_reduces_known_spend() {
        for cost in [f64::NAN, f64::INFINITY, -0.1] {
            let arm = |name: &str| PairedArm {
                arm: name.into(),
                verdict: failed("test"),
                cost_usd: Some(cost),
                wall_seconds: None,
                interventions: 0,
            };
            let report = paired_report(&[PairedTask {
                task_id: "invalid".into(),
                baseline: arm("baseline"),
                atomic: arm("atomic"),
            }]);
            assert_eq!(
                report["summary"]["unknown_cost_arms"]
                    .as_array()
                    .unwrap()
                    .len(),
                2
            );
            assert_eq!(report["summary"]["known_cost_usd"]["baseline"], json!(0.0));
        }
    }
}
