//! Durable repair ceiling (port of `agents/repair_budget.py`), shared by every
//! subsequent physical model attempt. One repair episode per task: replans and
//! resume never refresh its allowance. The ceiling remains until task
//! completion; reservations are estimates and reported overspend is retained
//! and blocks progress.

use serde_json::{json, Value};

fn is_finite_nonnegative(value: &Value) -> bool {
    value.as_f64().is_some_and(|v| v.is_finite() && v >= 0.0)
}

fn is_int_at_least(value: &Value, minimum: i64) -> bool {
    value.as_i64().is_some_and(|v| v >= minimum)
}

/// Strict persisted shape; empty state means no repair episode is active.
pub fn validate_repair(state: &Value) -> Result<(), String> {
    let Some(object) = state.as_object() else {
        return Err("repair budget state must be an object".into());
    };
    if object.is_empty() {
        return Ok(());
    }
    let fields = [
        "version",
        "id",
        "cap_usd",
        "start_spent_usd",
        "start_event",
        "failure_step",
    ];
    if object.len() != fields.len() || fields.iter().any(|field| !object.contains_key(*field)) {
        return Err("invalid repair budget fields/version".into());
    }
    if state["version"].as_i64() != Some(1) {
        return Err("invalid repair budget fields/version".into());
    }
    let id = state["id"].as_str().unwrap_or_default();
    let id_ok = id.len() == 39
        && id.starts_with("repair_")
        && id[7..].bytes().all(|b| b.is_ascii_hexdigit());
    if !id_ok {
        return Err("invalid repair budget identity".into());
    }
    for key in ["cap_usd", "start_spent_usd"] {
        if !is_finite_nonnegative(&state[key]) {
            return Err("invalid repair budget amount".into());
        }
    }
    if state["cap_usd"].as_f64().unwrap_or(0.0) <= 0.0 {
        return Err("repair ceiling must be positive".into());
    }
    for key in ["start_event", "failure_step"] {
        if !is_int_at_least(&state[key], 0) {
            return Err("invalid repair budget event/step".into());
        }
    }
    Ok(())
}

/// Task accounting view the ceiling checks operate on.
pub struct RepairTaskView<'a> {
    pub repair_budget: &'a Value,
    pub budget_events: &'a [Value],
    pub budget_spent: f64,
}

/// Fail-closed ceiling check before the next physical attempt.
/// `known_prices=false` refuses: the repair budget requires known prices.
pub fn check_repair_budget(
    task: &RepairTaskView,
    reservation: f64,
    known_prices: bool,
) -> Result<(), String> {
    if let Err(error) = validate_repair(task.repair_budget) {
        return Err(error);
    }
    if task.repair_budget.as_object().is_none_or(|o| o.is_empty()) {
        return Ok(());
    }
    if !reservation.is_finite() || reservation < 0.0 {
        return Err("invalid repair reservation".into());
    }
    if !known_prices {
        return Err("repair budget requires known prices".into());
    }
    let id = task.repair_budget["id"]
        .as_str()
        .unwrap_or_default()
        .to_string();
    let start_spent = task.repair_budget["start_spent_usd"]
        .as_f64()
        .unwrap_or(0.0);
    let start_event = task.repair_budget["start_event"]
        .as_i64()
        .unwrap_or(i64::MAX) as usize;
    if start_event > task.budget_events.len() || task.budget_spent < start_spent {
        return Err("repair budget accounting moved backwards".into());
    }
    let events = &task.budget_events[start_event..];
    if events
        .iter()
        .any(|event| event.get("repair_id").and_then(Value::as_str) != Some(id.as_str()))
    {
        return Err("repair budget attribution missing; no automatic reset".into());
    }
    if events
        .iter()
        .any(|event| event.get("status").and_then(Value::as_str) != Some("completed"))
    {
        return Err("repair has an unresolved model call; reconcile before continuation".into());
    }
    for event in events {
        for key in ["cost_usd", "reserved_usd"] {
            if !is_finite_nonnegative(&event[key]) {
                return Err("repair model-call cost/reservation is invalid".into());
            }
        }
    }
    let spent: f64 = events
        .iter()
        .map(|event| event["cost_usd"].as_f64().unwrap_or(0.0))
        .sum();
    if (task.budget_spent - start_spent - spent).abs() > 1e-9 {
        return Err("repair budget accounting inconsistent".into());
    }
    let overspent = events.iter().any(|event| {
        let not_sent = event
            .get("not_sent")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        !not_sent
            && event["cost_usd"].as_f64().unwrap_or(0.0)
                > event["reserved_usd"].as_f64().unwrap_or(0.0) + 1e-12
    });
    if overspent {
        return Err(
            "repair reported cost exceeded reservation; inspect before continuation".into(),
        );
    }
    let cap = task.repair_budget["cap_usd"].as_f64().unwrap_or(0.0);
    if spent + reservation > cap + 1e-12 {
        return Err("repair budget cannot cover next reservation or reported spend".into());
    }
    Ok(())
}

/// Begin the one repair episode; existing state is never refreshed.
/// Returns the possibly-new state for the caller to persist.
pub fn begin_repair(task_state: &Value, cap: f64, step_index: usize) -> Result<Value, String> {
    let mut state = task_state.clone();
    if state.as_object().is_none_or(|o| o.is_empty()) {
        if !cap.is_finite() || cap <= 0.0 {
            return Err("repair ceiling must be positive".into());
        }
        state = json!({
            "version": 1,
            "id": format!("repair_{}", af_entropy::token()),
            "cap_usd": cap,
            "start_spent_usd": 0.0,
            "start_event": 0,
            "failure_step": step_index,
        });
        validate_repair(&state)?;
    }
    Ok(state)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repair_state(cap: f64, start_spent: f64, start_event: usize) -> Value {
        json!({"version": 1, "id": format!("repair_{}", "a".repeat(32)), "cap_usd": cap,
               "start_spent_usd": start_spent, "start_event": start_event, "failure_step": 0})
    }

    fn completed_event(cost: f64, reserved: f64) -> Value {
        json!({"repair_id": format!("repair_{}", "a".repeat(32)), "status": "completed",
               "cost_usd": cost, "reserved_usd": reserved})
    }

    #[test]
    fn validation_is_strict() {
        assert!(validate_repair(&json!({})).is_ok());
        assert!(validate_repair(&repair_state(1.0, 0.0, 0)).is_ok());
        let mut bad = repair_state(1.0, 0.0, 0);
        bad["id"] = json!("repair_zz");
        assert!(validate_repair(&bad).is_err());
        bad = repair_state(0.0, 0.0, 0);
        assert!(validate_repair(&bad).is_err(), "ceiling must be positive");
        bad = repair_state(1.0, 0.0, 0);
        bad["extra"] = json!(1);
        assert!(validate_repair(&bad).is_err());
    }

    #[test]
    fn spend_up_to_cap_passes_then_refuses() {
        let state = repair_state(0.05, 0.10, 0);
        let events = vec![completed_event(0.03, 0.03)];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &events,
            budget_spent: 0.13,
        };
        assert!(check_repair_budget(&task, 0.02, true).is_ok());
        assert!(check_repair_budget(&task, 0.021, true)
            .unwrap_err()
            .contains("cannot cover next reservation"));
    }

    #[test]
    fn attribution_and_settlement_are_enforced() {
        let state = repair_state(1.0, 0.0, 0);
        let foreign = vec![
            json!({"repair_id": format!("repair_{}", "b".repeat(32)), "status": "completed", "cost_usd": 0.0, "reserved_usd": 0.0}),
        ];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &foreign,
            budget_spent: 0.0,
        };
        assert!(check_repair_budget(&task, 0.0, true)
            .unwrap_err()
            .contains("attribution missing"));
        let unresolved = vec![
            json!({"repair_id": format!("repair_{}", "a".repeat(32)), "status": "in_flight", "cost_usd": 0.0, "reserved_usd": 0.0}),
        ];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &unresolved,
            budget_spent: 0.0,
        };
        assert!(check_repair_budget(&task, 0.0, true)
            .unwrap_err()
            .contains("unresolved model call"));
        let overspent = vec![completed_event(0.05, 0.04)];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &overspent,
            budget_spent: 0.05,
        };
        assert!(check_repair_budget(&task, 0.0, true)
            .unwrap_err()
            .contains("exceeded reservation"));
        // not_sent attempts are exempt from the overspend check.
        let not_sent = vec![
            json!({"repair_id": format!("repair_{}", "a".repeat(32)), "status": "completed", "cost_usd": 0.0, "reserved_usd": 0.05, "not_sent": true}),
        ];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &not_sent,
            budget_spent: 0.0,
        };
        assert!(check_repair_budget(&task, 0.01, true).is_ok());
    }

    #[test]
    fn moved_backwards_accounting_and_unknown_prices_refuse() {
        let state = repair_state(1.0, 0.50, 1);
        let events = vec![completed_event(0.0, 0.0)];
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &events,
            budget_spent: 0.40,
        };
        assert!(check_repair_budget(&task, 0.0, true)
            .unwrap_err()
            .contains("moved backwards"));
        let task = RepairTaskView {
            repair_budget: &state,
            budget_events: &events,
            budget_spent: 0.50,
        };
        assert!(check_repair_budget(&task, 0.0, false)
            .unwrap_err()
            .contains("known prices"));
    }

    #[test]
    fn begin_repair_never_refreshes_existing_episode() {
        let existing = repair_state(0.10, 0.0, 0);
        let refreshed = begin_repair(&existing, 99.0, 5).unwrap();
        assert_eq!(
            refreshed["cap_usd"],
            json!(0.10),
            "existing episode is kept"
        );
        assert_eq!(refreshed["failure_step"], json!(0));
        let fresh = begin_repair(&json!({}), 0.25, 3).unwrap();
        assert!(fresh["id"].as_str().unwrap().starts_with("repair_"));
        assert_eq!(fresh["cap_usd"], json!(0.25));
        assert_eq!(fresh["failure_step"], json!(3));
        assert!(begin_repair(&json!({}), 0.0, 3).is_err());
    }
}
