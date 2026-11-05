//! Host failing-check evidence and the repair gate (port of the codex
//! session's `agents/failure_policy.py` v2). A deterministic-test-failure
//! repair is admissible only on completed, source-bound ordinary-check
//! evidence: the check must have COMPLETED, FAILED while completed, and the
//! checked source revision must equal the current one. Anything else stops.

use serde_json::{json, Value};

pub const REPAIR_BUDGET_FRACTION: f64 = 0.25;

const V1_FIELDS: [&str; 8] = [
    "version",
    "check",
    "exit_code",
    "step_index",
    "started_at",
    "source_revision",
    "checked_source_revision",
    "outcome",
];
const V2_FIELDS: [&str; 14] = [
    "version",
    "check",
    "exit_code",
    "step_index",
    "started_at",
    "source_revision",
    "checked_source_revision",
    "outcome",
    "runner",
    "check_completed",
    "check_failed_completed",
    "check_obligation_digest",
    "check_spec_digest",
    "check_receipt_digest",
];

fn is_hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn finite_nonnegative(value: &Value) -> bool {
    value.as_f64().is_some_and(|v| v.is_finite() && v >= 0.0)
}

/// Strict evidence validation, v1 (syntax/pytest/ruff/mypy) and v2
/// (ordinary_test with completed-check digests).
pub fn validate_failure_evidence(evidence: &Value) -> Result<(), String> {
    let Some(object) = evidence.as_object() else {
        return Err("missing or invalid host failing-check evidence".into());
    };
    let Some(version) = evidence.get("version").and_then(Value::as_i64) else {
        return Err("missing or invalid host failing-check evidence".into());
    };
    let fields: &[&str] = if version == 1 {
        &V1_FIELDS
    } else if version == 2 {
        &V2_FIELDS
    } else {
        return Err("missing or invalid host failing-check evidence".into());
    };
    if object.len() != fields.len() || fields.iter().any(|field| !object.contains_key(*field)) {
        return Err("missing or invalid host failing-check evidence".into());
    }
    let check = evidence["check"].as_str().unwrap_or_default();
    let check_ok = if version == 1 {
        matches!(check, "syntax" | "pytest" | "ruff" | "mypy")
    } else {
        check == "ordinary_test"
    };
    if !check_ok {
        return Err("missing or invalid host failing-check evidence".into());
    }
    if evidence["exit_code"].as_i64() != Some(1) {
        return Err("missing or invalid host failing-check evidence".into());
    }
    if evidence["step_index"].as_i64().is_none_or(|v| v < 0) {
        return Err("missing or invalid host failing-check evidence".into());
    }
    if !finite_nonnegative(&evidence["started_at"]) {
        return Err("missing or invalid host failing-check evidence".into());
    }
    for key in ["source_revision", "checked_source_revision"] {
        if !evidence
            .get(key)
            .and_then(Value::as_str)
            .is_some_and(is_hex64)
        {
            return Err("missing or invalid host failing-check evidence".into());
        }
    }
    let outcome = evidence["outcome"].as_str().unwrap_or_default();
    if !matches!(outcome, "success" | "error" | "unknown") {
        return Err("missing or invalid host failing-check evidence".into());
    }
    if version == 2 {
        if evidence["runner"].as_str().unwrap_or_default().is_empty()
            || !matches!(
                evidence["runner"].as_str(),
                Some("pytest") | Some("sympy-native") | Some("pytest-self")
            )
        {
            return Err("missing or invalid completed ordinary-check evidence".into());
        }
        if evidence["check_completed"] != json!(true)
            || evidence["check_failed_completed"] != json!(true)
        {
            return Err("missing or invalid completed ordinary-check evidence".into());
        }
        if evidence["source_revision"] != evidence["checked_source_revision"] {
            return Err("missing or invalid completed ordinary-check evidence".into());
        }
        for key in [
            "check_obligation_digest",
            "check_spec_digest",
            "check_receipt_digest",
        ] {
            if !evidence
                .get(key)
                .and_then(Value::as_str)
                .is_some_and(is_hex64)
            {
                return Err("missing or invalid completed ordinary-check evidence".into());
            }
        }
    }
    Ok(())
}

pub const FAILURE_KINDS: [&str; 5] = [
    "deterministic_test_failure",
    "source_conflict",
    "unknown_effect",
    "authority_expansion",
    "ambiguous_requirement",
];

/// The failure-kind gate: deterministic test failures require pytest-family
/// evidence; anything else stops with the mismatch named.
pub fn classify(failure: &Value) -> Value {
    let kind = failure
        .get("kind")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !FAILURE_KINDS.contains(&kind) {
        return json!({"action": "stop", "kind": kind, "reason": "unknown failure kind"});
    }
    let evidence = failure.get("evidence").cloned().unwrap_or(Value::Null);
    if let Err(reason) = validate_failure_evidence(&evidence) {
        return json!({"action": "stop", "kind": kind, "reason": reason});
    }
    let check = evidence["check"].as_str().unwrap_or_default();
    if (kind == "deterministic_test_failure") != (check == "pytest" || check == "ordinary_test") {
        return json!({"action": "stop", "kind": kind, "reason": "failure kind/check mismatch"});
    }
    if kind == "deterministic_test_failure" {
        let remaining = failure.get("remaining_budget_usd").and_then(Value::as_f64);
        match remaining {
            Some(remaining) if remaining.is_finite() && remaining > 0.0 => {
                return json!({
                    "action": "bounded_repair",
                    "kind": kind,
                    "repair_allowance_usd": remaining * REPAIR_BUDGET_FRACTION,
                });
            }
            _ => {
                return json!({
                    "action": "stop", "kind": kind,
                    "reason": "no finite remaining budget for a bounded repair",
                });
            }
        }
    }
    json!({"action": "stop", "kind": kind, "reason": "failure kind requires host decision"})
}

/// Bounded repair may use a quarter of the remaining budget.
pub fn repair_allowance(remaining_budget_usd: f64) -> f64 {
    remaining_budget_usd * REPAIR_BUDGET_FRACTION
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v1_evidence() -> Value {
        json!({
            "version": 1, "check": "pytest", "exit_code": 1, "step_index": 2,
            "started_at": 12.5,
            "source_revision": "a".repeat(64), "checked_source_revision": "a".repeat(64),
            "outcome": "error",
        })
    }

    fn v2_evidence() -> Value {
        json!({
            "version": 2, "check": "ordinary_test", "exit_code": 1, "step_index": 2,
            "started_at": 12.5,
            "source_revision": "a".repeat(64), "checked_source_revision": "a".repeat(64),
            "outcome": "error", "runner": "pytest",
            "check_completed": true, "check_failed_completed": true,
            "check_obligation_digest": "b".repeat(64),
            "check_spec_digest": "c".repeat(64),
            "check_receipt_digest": "d".repeat(64),
        })
    }

    #[test]
    fn v1_and_v2_shapes_validate() {
        assert!(validate_failure_evidence(&v1_evidence()).is_ok());
        assert!(validate_failure_evidence(&v2_evidence()).is_ok());
    }

    #[test]
    fn v2_requires_completed_and_matching_revisions() {
        let mut bad = v2_evidence();
        bad["check_failed_completed"] = json!(false);
        let error = validate_failure_evidence(&bad).unwrap_err();
        assert!(error.contains("completed ordinary-check"));
        let mut bad = v2_evidence();
        bad["checked_source_revision"] = json!("e".repeat(64));
        assert!(validate_failure_evidence(&bad)
            .unwrap_err()
            .contains("completed ordinary-check"));
        let mut bad = v2_evidence();
        bad["check_spec_digest"] = json!("short");
        assert!(validate_failure_evidence(&bad).is_err());
        // A v1-shaped record cannot smuggle ordinary_test.
        let mut smuggled = v1_evidence();
        smuggled["check"] = json!("ordinary_test");
        assert!(validate_failure_evidence(&smuggled).is_err());
    }

    #[test]
    fn deterministic_failures_classify_to_bounded_repair() {
        let decision = classify(&json!({
            "kind": "deterministic_test_failure",
            "evidence": v2_evidence(),
            "remaining_budget_usd": 0.80,
        }));
        assert_eq!(decision["action"], json!("bounded_repair"));
        assert!((decision["repair_allowance_usd"].as_f64().unwrap() - 0.20).abs() < 1e-9);
    }

    #[test]
    fn mismatches_and_other_kinds_stop() {
        let mut mismatch = json!({
            "kind": "source_conflict",
            "evidence": v1_evidence(),
            "remaining_budget_usd": 1.0,
        });
        let decision = classify(&mismatch.clone());
        assert_eq!(decision["action"], json!("stop"));
        assert_eq!(decision["reason"], json!("failure kind/check mismatch"));
        mismatch["evidence"] = json!({"version": 1});
        assert_eq!(
            classify(&mismatch)["action"],
            json!("stop"),
            "invalid evidence stops"
        );
        let unknown = classify(&json!({
            "kind": "unknown_effect",
            "evidence": v1_evidence(),
        }));
        assert_eq!(unknown["action"], json!("stop"));
        let no_budget = classify(&json!({
            "kind": "deterministic_test_failure",
            "evidence": v2_evidence(),
            "remaining_budget_usd": 0.0,
        }));
        assert_eq!(no_budget["action"], json!("stop"));
        assert_eq!(repair_allowance(1.0), 0.25);
    }
}
