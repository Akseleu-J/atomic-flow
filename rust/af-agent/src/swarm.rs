//! Wide-research swarm (Manus-style breadth mode) on our admission queue:
//! one task decomposes into N subtask specs, each dispatched through the
//! fenced queue, executed by an injected runner (the real runner binds the
//! SandboxDriver), and aggregated into a sortable result table. Isolation is
//! per-subtask: no shared mutable state, every row keeps its own outcome and
//! cost; unknown outcomes hold the aggregation instead of becoming zeros.

use std::collections::BTreeMap;
use af_exec::queue::AdmissionQueue;
use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SwarmError(pub String);

pub type SwarmResult<T> = Result<T, SwarmError>;

/// One planned row of the wide-research sweep.
#[derive(Debug, Clone)]
pub struct SweepItem {
    pub seq: u64,
    pub description: String,
}

/// Plan the sweep: bounded item count, honest limits, queue admission.
pub fn plan_sweep(
    queue: &mut AdmissionQueue,
    goal: &str,
    item_descriptions: &[String],
    max_items: usize,
) -> SwarmResult<Vec<SweepItem>> {
    if item_descriptions.is_empty() {
        return Err(SwarmError("a sweep needs at least one item".into()));
    }
    if item_descriptions.len() > max_items {
        return Err(SwarmError(format!(
            "sweep items exceed the admitted bound ({max_items})"
        )));
    }
    let mut items = Vec::new();
    for description in item_descriptions {
        let seq = queue
            .submit(json!({
                "kind": "wide_research",
                "goal": goal,
                "item": description,
            }))
            .map_err(|error| SwarmError(error.0))?;
        items.push(SweepItem {
            seq,
            description: description.clone(),
        });
    }
    Ok(items)
}

/// One executed row. `None` cost stays unknown — the aggregation says so.
#[derive(Debug, Clone)]
pub struct SweepRow {
    pub seq: u64,
    pub description: String,
    pub status: String,
    pub answer: Option<String>,
    pub cost_usd: Option<f64>,
}

/// Aggregate resolved rows into the sortable table. Rows still queued or
/// claimed are listed as pending; the sweep is not declared complete over
/// them.
pub fn aggregate(_queue: &AdmissionQueue, items: &[SweepItem], rows: &[SweepRow]) -> Value {
    let resolved: std::collections::BTreeMap<u64, &SweepRow> =
        rows.iter().map(|row| (row.seq, row)).collect();
    let mut table: Vec<Value> = Vec::new();
    let mut pending = 0usize;
    let mut unknown_cost = 0usize;
    for item in items {
        match resolved.get(&item.seq) {
            Some(row) => {
                if row.cost_usd.is_none() {
                    unknown_cost += 1;
                }
                table.push(json!({
                    "seq": item.seq, "item": item.description,
                    "status": row.status, "answer": row.answer,
                    "cost_usd": row.cost_usd,
                }));
            }
            None => {
                pending += 1;
                table.push(json!({
                    "seq": item.seq, "item": item.description,
                    "status": "pending", "answer": Value::Null,
                    "cost_usd": Value::Null,
                }));
            }
        }
    }
    let known_cost: f64 = rows.iter().filter_map(|row| row.cost_usd).sum();
    json!({
        "table": table,
        "summary": {
            "items": items.len(), "resolved": items.len() - pending,
            "pending": pending, "unknown_cost_rows": unknown_cost,
            "known_cost_usd": known_cost,
            "complete": pending == 0,
        },
        "note": "unknown costs are counted, never zeroed; pending rows keep the sweep incomplete",
    })
}

/// Execute the sweep through the fenced admission queue: for each queued
/// item — claim with the live fence, run the injected executor (the real one
/// binds a SandboxDriver/router), resolve with the fence. The executor
/// returns `Err` for an UNKNOWN outcome (timeout after dispatch, worker
/// loss): the epoch then moves, the dead fence dies, and the item returns to
/// the queue as a NEW claim — never a resume. Unknown rows are not recorded
/// as results.
pub fn execute_sweep(
    queue: &mut AdmissionQueue,
    items: &[SweepItem],
    mut execute: impl FnMut(&SweepItem) -> Result<(String, Option<String>, Option<f64>), String>,
    max_reclaims: usize,
) -> SwarmResult<Vec<SweepRow>> {
    let mut rows: Vec<SweepRow> = Vec::new();
    let mut reclaims: BTreeMap<u64, usize> = BTreeMap::new();
    let mut pending: std::collections::VecDeque<u64> = items.iter().map(|item| item.seq).collect();
    while let Some(seq) = pending.pop_front() {
        let Some(ticket) = queue.claim().map_err(|error| SwarmError(error.0))? else {
            return Err(SwarmError(format!("sweep item {seq} is not claimable; queue state diverged")));
        };
        if ticket.seq != seq {
            return Err(SwarmError(format!(
                "sweep queue offered seq {} while executing seq {seq}; state diverged",
                ticket.seq
            )));
        }
        let Some(item) = items.iter().find(|item| item.seq == seq) else {
            return Err(SwarmError(format!("sweep item {seq} vanished")));
        };
        match execute(item) {
            Ok((status, answer, cost_usd)) => {
                queue
                    .resolve(seq, &ticket.fence, json!({"status": status}))
                    .map_err(|error| SwarmError(error.0))?;
                rows.push(SweepRow {
                    seq,
                    description: item.description.clone(),
                    status,
                    answer,
                    cost_usd,
                });
            }
            Err(detail) => {
                let seen = reclaims.entry(seq).or_insert(0);
                *seen += 1;
                if *seen > max_reclaims {
                    return Err(SwarmError(format!(
                        "sweep item {seq} exceeded reclaim bound ({max_reclaims}): {detail}"
                    )));
                }
                queue.bump_epoch(&format!("sweep item {seq} unknown: {detail}"));
                pending.push_front(seq);
            }
        }
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn queue_with_capacity(capacity: usize) -> AdmissionQueue {
        AdmissionQueue::new(capacity).unwrap()
    }

    #[test]
    fn sweep_admits_bounded_items_through_the_fenced_queue() {
        let mut queue = queue_with_capacity(8);
        let items = plan_sweep(
            &mut queue,
            "scan ten landing pages",
            &["page 1".into(), "page 2".into(), "page 3".into()],
            10,
        )
        .unwrap();
        assert_eq!(items.len(), 3);
        assert_eq!(items[0].seq, 0);
        // Bound enforcement.
        let too_many: Vec<String> = (0..5).map(|i| format!("page {i}")).collect();
        assert!(plan_sweep(&mut queue, "g", &too_many, 2).is_err());
        assert!(plan_sweep(&mut queue, "g", &[], 2).is_err());
    }

    #[test]
    fn aggregation_holds_pending_and_unknown_costs() {
        let mut queue = queue_with_capacity(8);
        let items = plan_sweep(&mut queue, "g", &["a".into(), "b".into(), "c".into()], 4).unwrap();
        // Claim one item (its outcome is in flight) and resolve two.
        let _ticket = queue.claim().unwrap().unwrap();
        let rows = vec![
            SweepRow {
                seq: items[1].seq,
                description: items[1].description.clone(),
                status: "succeeded".into(),
                answer: Some("b answer".into()),
                cost_usd: Some(0.02),
            },
            SweepRow {
                seq: items[2].seq,
                description: items[2].description.clone(),
                status: "failed".into(),
                answer: None,
                cost_usd: None,
            },
        ];
        let report = aggregate(&queue, &items, &rows);
        assert_eq!(report["summary"]["pending"], json!(1));
        assert_eq!(report["summary"]["complete"], json!(false));
        assert_eq!(report["summary"]["unknown_cost_rows"], json!(1));
        assert_eq!(report["summary"]["known_cost_usd"], json!(0.02));
        let table = report["table"].as_array().unwrap();
        assert_eq!(table[0]["status"], json!("pending"));
        assert_eq!(table[1]["answer"], json!("b answer"));
        assert_eq!(table[2]["status"], json!("failed"));
        assert_eq!(table[2]["cost_usd"], Value::Null, "unknown is never zeroed");
    }

    #[test]
    fn fully_resolved_sweep_is_complete() {
        let mut queue = queue_with_capacity(4);
        let items = plan_sweep(&mut queue, "g", &["a".into()], 2).unwrap();
        let rows = vec![SweepRow {
            seq: items[0].seq,
            description: items[0].description.clone(),
            status: "succeeded".into(),
            answer: Some("done".into()),
            cost_usd: Some(0.01),
        }];
        let report = aggregate(&queue, &items, &rows);
        assert_eq!(report["summary"]["complete"], json!(true));
    }

    #[test]
    fn execute_sweep_claims_resolves_and_aggregates() {
        let mut queue = queue_with_capacity(8);
        let items = plan_sweep(
            &mut queue,
            "scan",
            &["one".into(), "two".into(), "three".into()],
            8,
        )
        .unwrap();
        let mut executed: Vec<String> = Vec::new();
        let rows = execute_sweep(
            &mut queue,
            &items,
            |item| {
                executed.push(item.description.clone());
                Ok(("succeeded".into(), Some(format!("answer for {}", item.description)), Some(0.01)))
            },
            2,
        )
        .unwrap();
        assert_eq!(executed, vec!["one", "two", "three"], "FIFO, each item exactly once");
        assert_eq!(rows.len(), 3);
        let report = aggregate(&queue, &items, &rows);
        assert_eq!(report["summary"]["complete"], json!(true));
        assert_eq!(report["summary"]["known_cost_usd"], json!(0.03));
    }

    #[test]
    fn unknown_outcome_reclaims_as_new_claim_under_new_epoch() {
        let mut queue = queue_with_capacity(4);
        let items = plan_sweep(&mut queue, "g", &["a".into()], 2).unwrap();
        let mut attempts = 0usize;
        let rows = execute_sweep(
            &mut queue,
            &items,
            |_item| {
                attempts += 1;
                if attempts == 1 {
                    // First execution is lost: outcome unknown, must NOT be
                    // recorded as a result — the item re-runs from scratch.
                    Err("worker lost after dispatch".into())
                } else {
                    Ok(("succeeded".into(), Some("fresh run".into()), Some(0.01)))
                }
            },
            2,
        )
        .unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].status, "succeeded");
        assert_eq!(rows[0].answer.as_deref(), Some("fresh run"));
        assert_eq!(attempts, 2, "the item was re-run exactly once as a new claim");
        let snapshot = queue.snapshot();
        assert!(snapshot["epoch"].as_u64().unwrap() >= 2);
        // The reclaim bound eventually holds the sweep instead of looping.
        let fresh = plan_sweep(&mut queue, "g2", &["b".into()], 2).unwrap();
        let bounded = execute_sweep(
            &mut queue,
            &fresh,
            |_item| Err("always lost".into()),
            1,
        );
        assert!(bounded.is_err());
    }
}
