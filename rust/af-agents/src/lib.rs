//! af-agents: opt-in, bounded internal replans; summaries never grant
//! execution authority (port of `agents/envelope.py`). The predicate checks a
//! host-created envelope and fails closed for legacy approvals, tool widening
//! or weaker verification. The runner must still enforce the approved
//! source/scope, current runtime binding, accounting and effect holds.

use std::collections::BTreeSet;
use serde_json::{json, Value};

pub const MUTATING_TOOLS: [&str; 6] = ["edit_node", "patch_file", "notebook_edit", "create_file", "move_file", "delete_file"];
pub const SOURCE_TOOLS: [&str; 8] = ["read_node", "find_callers", "search_tree", "search_text", "read_result", "read_file", "find_files", "notebook_read"];
pub const SUPPORTED_TOOLS: [&str; 16] = [
    "read_node", "find_callers", "search_tree", "search_text", "read_result",
    "read_file", "find_files", "notebook_read",
    "edit_node", "patch_file", "notebook_edit", "create_file", "move_file", "delete_file",
    "run_command", "ask_user",
];

/// verify floor ranking: none < py_compile < pytest.
pub fn verify_rank(kind: &str) -> Option<u8> {
    match kind {
        "none" => Some(0),
        "py_compile" => Some(1),
        "pytest" => Some(2),
        _ => None,
    }
}

pub const ENVELOPE_FIELDS: [&str; 8] = [
    "version", "allowed_tools", "required_verify", "max_budget_usd", "max_model_calls",
    "max_replans", "runtime_digest", "replan_failures",
];

fn is_hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Strict persisted shape; malformed, nonfinite or unbounded data is refused.
pub fn validate_envelope(value: &Value) -> Result<(), String> {
    let Some(object) = value.as_object() else {
        return Err("invalid internal replan envelope fields".into());
    };
    if object.len() != ENVELOPE_FIELDS.len() || ENVELOPE_FIELDS.iter().any(|field| !object.contains_key(*field)) {
        return Err("invalid internal replan envelope fields".into());
    }
    if value["version"].as_i64() != Some(1) || value["version"].is_f64() {
        return Err("invalid internal replan envelope version".into());
    }
    let Some(tools) = value["allowed_tools"].as_array() else {
        return Err("invalid internal replan allowed tools".into());
    };
    let mut names: Vec<&str> = Vec::new();
    for tool in tools {
        let Some(name) = tool.as_str() else {
            return Err("invalid internal replan allowed tools".into());
        };
        names.push(name);
    }
    let sorted_and_unique = names
        .windows(2)
        .all(|pair| pair[0] < pair[1]);
    if !sorted_and_unique || names.iter().any(|name| !SUPPORTED_TOOLS.contains(name)) {
        return Err("invalid internal replan allowed tools".into());
    }
    let required_verify = value["required_verify"].as_str().unwrap_or_default();
    if verify_rank(required_verify).is_none() {
        return Err("invalid internal replan verification floor".into());
    }
    let budget = value["max_budget_usd"].as_f64();
    // Integers are valid budgets too; reject bools and non-finite values.
    let budget_ok = match &value["max_budget_usd"] {
        Value::Number(number) => number.as_f64().is_some_and(|v| v.is_finite() && v > 0.0),
        _ => false,
    };
    let _ = budget;
    if !budget_ok {
        return Err("internal replans require a finite positive enforced budget".into());
    }
    for (name, maximum) in [("max_model_calls", 1000), ("max_replans", 5)] {
        let valid = value[name].as_u64().is_some_and(|v| (1..=maximum).contains(&v));
        if !valid {
            return Err(format!("invalid internal replan {name}"));
        }
    }
    let digest = value["runtime_digest"].as_str().unwrap_or_default();
    if !is_hex64(digest) {
        return Err("invalid internal replan runtime digest".into());
    }
    let Some(history) = value["replan_failures"].as_array() else {
        return Err("invalid internal replan failure history".into());
    };
    if history.len() > 5 {
        return Err("invalid internal replan failure history".into());
    }
    for item in history {
        let Some(text) = item.as_str() else {
            return Err("invalid internal replan failure history".into());
        };
        if text.trim().is_empty() || text.len() > 500 {
            return Err("invalid internal replan failure history".into());
        }
    }
    Ok(())
}

/// Exact recurring first-line failure signature (bounded to 500 chars).
pub fn failure_signature(failure: &str) -> String {
    let first = failure.trim().lines().next().unwrap_or("");
    first.chars().take(500).collect()
}

fn envelope_of(approval: &Value) -> Option<&Value> {
    if approval.is_object() && approval.get("internal_replan").is_some() {
        approval.get("internal_replan")
    } else {
        Some(approval)
    }
}

/// Exact recurring first-line failures; the independent replan cap also applies.
pub fn no_progress(approval: &Value, failure: &str, limit: usize) -> bool {
    let signature = failure_signature(failure);
    if signature.is_empty() {
        return false;
    }
    let Some(envelope) = envelope_of(approval) else { return false };
    let history = envelope
        .get("replan_failures")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    history
        .iter()
        .filter(|item| item.as_str() == Some(signature.as_str()))
        .count()
        >= limit
}

fn step_tools(step: &Value) -> Vec<&str> {
    step.get("tools_needed")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default()
}

fn is_mutating(name: &str) -> bool {
    MUTATING_TOOLS.contains(&name)
}

/// Fail closed for legacy approvals, tool widening or weaker verification.
/// This is not a source/sandbox check: the runner validates source and runtime
/// before calling it and applies its tool set to the actual dispatcher.
/// Returns (preserved, reasons).
pub fn envelope_preserved(approval: &Value, new_steps: &[Value], failure: &str, budget_spent: f64) -> (bool, Vec<String>) {
    let mut reasons: Vec<String> = Vec::new();
    if !approval.is_object() || approval.get("schema_version").and_then(Value::as_i64) != Some(2) {
        return (false, vec!["internal replan was not explicitly approved; fresh review required".to_string()]);
    }
    let Some(envelope) = approval.get("internal_replan") else {
        return (false, vec!["invalid internal replan envelope: missing".to_string()]);
    };
    if let Err(error) = validate_envelope(envelope) {
        return (false, vec![format!("invalid internal replan envelope: {error}")]);
    }
    if !budget_spent.is_finite() || budget_spent < 0.0 {
        reasons.push("invalid budget accounting".to_string());
    } else if budget_spent >= envelope["max_budget_usd"].as_f64().unwrap_or(f64::INFINITY) {
        reasons.push("budget reached the approved cap".to_string());
    }
    if new_steps.is_empty() {
        reasons.push("replan has no verifiable steps".to_string());
    }
    let allowed: BTreeSet<&str> = envelope["allowed_tools"]
        .as_array()
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let proposed: BTreeSet<&str> = new_steps.iter().flat_map(step_tools).collect();
    let allowed_mutating: BTreeSet<&str> = allowed.iter().copied().filter(|name| is_mutating(name)).collect();
    let proposed_mutating: BTreeSet<&str> = proposed.iter().copied().filter(|name| is_mutating(name)).collect();
    if !allowed_mutating.is_empty() && proposed_mutating.is_empty() {
        reasons.push("replan would drop the approved verified edit obligation".to_string());
    }
    if allowed.contains("run_command")
        && !proposed.contains("run_command")
        && !new_steps.iter().any(|step| step.get("verify_with").and_then(Value::as_str) == Some("pytest"))
    {
        reasons.push("replan would drop the approved execution/check obligation".to_string());
    }
    let required_rank = verify_rank(envelope["required_verify"].as_str().unwrap_or("none")).unwrap_or(0);
    for (position, step) in new_steps.iter().enumerate() {
        let tools = step_tools(step);
        let unknown: Vec<&str> = tools.iter().copied().filter(|name| !allowed.contains(*name)).collect();
        if !unknown.is_empty() {
            let mut sorted = unknown.clone();
            sorted.sort();
            reasons.push(format!("step {}: tools outside approved envelope: {:?}", position, sorted));
        }
        if tools.iter().copied().any(is_mutating) && allowed_mutating.is_empty() {
            reasons.push(format!("step {}: introduces a mutating tool the plan never had", position));
        }
        let verify = step.get("verify_with").and_then(Value::as_str).unwrap_or("none");
        let rank = verify_rank(verify).unwrap_or(0);
        if rank < required_rank {
            reasons.push(format!(
                "step {}: verify_with would weaken the approved verification obligation",
                position
            ));
        }
        if verify == "pytest" && envelope["required_verify"].as_str() != Some("pytest") && !allowed.contains("run_command")
        {
            reasons.push(format!("step {}: pytest execution was not approved", position));
        }
    }
    if no_progress(approval, failure, 2) {
        reasons.push("no-progress guard: recurring failure requires review".to_string());
    }
    (reasons.is_empty(), reasons)
}

/// Runtime digest shape helper (the full runtime binding lands with the
/// Rust config port; persisted envelopes must still carry a hex-64 digest).
pub fn runtime_digest_shape_ok(digest: &str) -> bool {
    is_hex64(digest)
}

/// Convenience: a minimal schema-2 approval envelope for tests and hosts.
/// Allowed tools are normalized to the sorted-unique shape the contract
/// requires.
pub fn example_envelope(allowed_tools: &[&str], required_verify: &str) -> Value {
    let mut tools: Vec<&str> = allowed_tools.to_vec();
    tools.sort();
    tools.dedup();
    json!({
        "schema_version": 2,
        "internal_replan": {
            "version": 1,
            "allowed_tools": tools,
            "required_verify": required_verify,
            "max_budget_usd": 1.0,
            "max_model_calls": 10,
            "max_replans": 3,
            "runtime_digest": "a".repeat(64),
            "replan_failures": [],
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn step(index: usize, tools: &[&str], verify: &str) -> Value {
        json!({"index": index, "tools_needed": tools, "verify_with": verify})
    }

    fn envelope() -> Value {
        example_envelope(&["read_file", "edit_node", "run_command"], "pytest")
    }

    #[test]
    fn valid_in_envelope_repair_is_preserved() {
        let approval = envelope();
        let steps = vec![step(0, &["edit_node", "run_command"], "pytest")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "new failure", 0.1);
        assert!(preserved, "{reasons:?}");
        assert!(reasons.is_empty());
    }

    #[test]
    fn legacy_approval_fails_closed() {
        let mut legacy = envelope();
        legacy["schema_version"] = json!(1);
        let steps = vec![step(0, &["read_file"], "none")];
        let (preserved, reasons) = envelope_preserved(&legacy, &steps, "f", 0.0);
        assert!(!preserved);
        assert!(reasons[0].contains("fresh review required"));
    }

    #[test]
    fn tool_widening_is_refused() {
        let approval = envelope();
        let steps = vec![step(0, &["edit_node", "run_command", "delete_file"], "pytest")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "f", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("tools outside approved envelope")));
        // The "never had" reason only fires when the approved plan contained
        // no mutating tool at all (parity with the Python predicate).
        let read_only = example_envelope(&["read_file", "run_command"], "pytest");
        let steps = vec![step(0, &["edit_node", "run_command"], "pytest")];
        let (preserved, reasons) = envelope_preserved(&read_only, &steps, "f", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("mutating tool the plan never had")));
    }

    #[test]
    fn verify_weakening_is_refused() {
        let approval = envelope();
        let steps = vec![step(0, &["edit_node", "run_command"], "py_compile")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "f", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("weaken the approved verification")));
    }

    #[test]
    fn budget_cap_stops_replan() {
        let approval = envelope();
        let steps = vec![step(0, &["edit_node", "run_command"], "pytest")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "f", 1.0);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("budget reached the approved cap")));
        let (preserved, _) = envelope_preserved(&approval, &steps, "f", f64::NAN);
        assert!(!preserved, "non-finite accounting must fail closed");
    }

    #[test]
    fn dropping_obligations_is_refused() {
        let approval = envelope();
        // Drops both the mutating edit and the execution/check obligation:
        // a non-pytest step without edit_node loses both floors at once.
        let steps = vec![step(0, &["read_file"], "none")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "f", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("verified edit obligation")));
        assert!(reasons.iter().any(|r| r.contains("execution/check obligation")));
        assert!(reasons.iter().any(|r| r.contains("weaken the approved verification")));
    }

    #[test]
    fn empty_replan_refused() {
        let approval = envelope();
        let (preserved, reasons) = envelope_preserved(&approval, &[], "f", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("no verifiable steps")));
    }

    #[test]
    fn no_progress_guard_requires_review() {
        let mut approval = envelope();
        approval["internal_replan"]["replan_failures"] = json!(["syntax error at line 3", "syntax error at line 3"]);
        let steps = vec![step(0, &["edit_node", "run_command"], "pytest")];
        let (preserved, reasons) = envelope_preserved(&approval, &steps, "syntax error at line 3", 0.1);
        assert!(!preserved);
        assert!(reasons.iter().any(|r| r.contains("no-progress guard")));
        // A different failure does not trigger the guard.
        let (preserved, _) = envelope_preserved(&approval, &steps, "different failure", 0.1);
        assert!(preserved);
    }

    #[test]
    fn envelope_validation_is_strict() {
        let mut value = envelope()["internal_replan"].clone();
        assert!(validate_envelope(&value).is_ok());
        value["max_budget_usd"] = json!(-1);
        assert!(validate_envelope(&value).is_err());
        let mut value = envelope()["internal_replan"].clone();
        value["max_budget_usd"] = json!(true);
        assert!(validate_envelope(&value).is_err(), "bool must not pass as a number");
        let mut value = envelope()["internal_replan"].clone();
        value["runtime_digest"] = json!("short");
        assert!(validate_envelope(&value).is_err());
        let mut value = envelope()["internal_replan"].clone();
        value["allowed_tools"] = json!(["run_command", "read_file"]);
        assert!(validate_envelope(&value).is_err(), "tools must stay sorted");
        let mut value = envelope()["internal_replan"].clone();
        value["replan_failures"] = json!(["  "]);
        assert!(validate_envelope(&value).is_err());
        let mut value = envelope()["internal_replan"].clone();
        value["extra"] = json!(1);
        assert!(validate_envelope(&value).is_err(), "unknown fields refused");
    }

    #[test]
    fn failure_signature_first_line_bounded() {
        assert_eq!(failure_signature("one\ntwo"), "one");
        assert_eq!(failure_signature("   \n  x"), "x");
        assert_eq!(failure_signature("").len(), 0);
        assert_eq!(failure_signature(&"y".repeat(600)).len(), 500);
    }
}
