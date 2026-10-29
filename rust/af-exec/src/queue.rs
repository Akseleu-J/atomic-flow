//! P10/E04 kernel contract: bounded admission queue with fencing tokens.
//! Claims carry an epoch-scoped fence; bumping the epoch (after an unknown
//! claim outcome) invalidates every outstanding fence, so no orphaned worker
//! can start or resume a job twice. Backpressure refuses new submissions at
//! the cap instead of silently queueing forever.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, PartialEq)]
pub enum EntryState {
    Queued,
    Claimed { fence: String },
    Cancelled,
    Resolved { fence: String, outcome: Value },
}

#[derive(Debug, Clone)]
struct Entry {
    spec: Value,
    state: EntryState,
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct QueueError(pub String);

pub type QueueResult<T> = Result<T, QueueError>;

#[derive(Debug, Clone)]
pub struct ClaimTicket {
    pub seq: u64,
    pub fence: String,
    pub spec: Value,
}

#[derive(Default)]
pub struct AdmissionQueue {
    max_queued: usize,
    epoch: u64,
    next_seq: u64,
    entries: BTreeMap<u64, Entry>,
}

impl AdmissionQueue {
    pub fn new(max_queued: usize) -> QueueResult<AdmissionQueue> {
        if max_queued == 0 {
            return Err(QueueError("admission capacity must be positive".into()));
        }
        Ok(AdmissionQueue {
            max_queued,
            epoch: 1,
            next_seq: 0,
            entries: BTreeMap::new(),
        })
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    /// Durable submission; backpressure refuses at the cap.
    pub fn submit(&mut self, spec: Value) -> QueueResult<u64> {
        let queued = self
            .entries
            .values()
            .filter(|entry| matches!(entry.state, EntryState::Queued | EntryState::Claimed { .. }))
            .count();
        if queued >= self.max_queued {
            return Err(QueueError(
                "admission queue is at capacity; backpressure applied".into(),
            ));
        }
        if !spec.is_object() {
            return Err(QueueError("job spec must be an object".into()));
        }
        let seq = self.next_seq;
        self.next_seq += 1;
        self.entries.insert(
            seq,
            Entry {
                spec,
                state: EntryState::Queued,
            },
        );
        Ok(seq)
    }

    /// Claim the oldest queued entry with the current epoch's fence.
    pub fn claim(&mut self) -> QueueResult<Option<ClaimTicket>> {
        let Some((seq, _entry)) = self
            .entries
            .iter()
            .find(|(_, entry)| matches!(entry.state, EntryState::Queued))
            .map(|(seq, entry)| (*seq, entry.clone()))
        else {
            return Ok(None);
        };
        let fence = format!("fence_{}:{}", self.epoch, seq);
        self.entries.get_mut(&seq).unwrap().state = EntryState::Claimed {
            fence: fence.clone(),
        };
        Ok(Some(ClaimTicket {
            seq,
            fence,
            spec: _entry.spec,
        }))
    }

    /// After an unknown claim outcome: bump the epoch; all outstanding fences
    /// become stale, so no orphan can act with old authority. Claimed entries
    /// return to the queue — re-running them is a new claim, never a resume
    /// of the old one.
    pub fn bump_epoch(&mut self, reason: &str) -> u64 {
        if reason.trim().is_empty() {
            // Still recorded by the caller; the epoch moves regardless.
        }
        self.epoch += 1;
        for entry in self.entries.values_mut() {
            if let EntryState::Claimed { fence } = &entry.state {
                if !fence_is_live(fence, self.epoch) {
                    entry.state = EntryState::Queued;
                }
            }
        }
        self.epoch
    }

    /// Resolve a claimed entry; only the live fence is accepted.
    pub fn resolve(&mut self, seq: u64, fence: &str, outcome: Value) -> QueueResult<()> {
        let Some(entry) = self.entries.get_mut(&seq) else {
            return Err(QueueError(format!("unknown seq {seq}")));
        };
        match &entry.state {
            EntryState::Claimed { fence: held }
                if held == fence && fence_is_live(fence, self.epoch) =>
            {
                entry.state = EntryState::Resolved {
                    fence: fence.to_string(),
                    outcome,
                };
                Ok(())
            }
            _ => Err(QueueError(
                "stale or foreign fence; reconcile through a new epoch".into(),
            )),
        }
    }

    /// Cancel before a claim; a claimed entry is not cancellable here —
    /// it goes through the reconciliation path.
    pub fn cancel(&mut self, seq: u64) -> QueueResult<bool> {
        let Some(entry) = self.entries.get_mut(&seq) else {
            return Err(QueueError(format!("unknown seq {seq}")));
        };
        if matches!(entry.state, EntryState::Queued) {
            entry.state = EntryState::Cancelled;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    pub fn snapshot(&self) -> Value {
        let states: BTreeSet<&str> = self
            .entries
            .values()
            .map(|entry| match &entry.state {
                EntryState::Queued => "queued",
                EntryState::Claimed { .. } => "claimed",
                EntryState::Cancelled => "cancelled",
                EntryState::Resolved { .. } => "resolved",
            })
            .collect();
        json!({
            "epoch": self.epoch,
            "entries": self.entries.iter().map(|(seq, entry)| json!({
                "seq": seq,
                "state": match &entry.state {
                    EntryState::Queued => json!("queued"),
                    EntryState::Claimed { fence } => json!(format!("claimed:{fence}")),
                    EntryState::Cancelled => json!("cancelled"),
                    EntryState::Resolved { outcome, .. } => json!(format!("resolved:{}", outcome)),
                },
            })).collect::<Vec<_>>(),
            "distinct_states": states.into_iter().collect::<Vec<_>>(),
        })
    }
}

fn fence_is_live(fence: &str, epoch: u64) -> bool {
    fence
        .strip_prefix("fence_")
        .and_then(|rest| rest.split_once(':'))
        .map(|(fence_epoch, _)| fence_epoch.parse::<u64>().unwrap_or(0) == epoch)
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(name: &str) -> Value {
        json!({"job": name, "argv": ["true"]})
    }

    #[test]
    fn fifo_claims_in_submission_order() {
        let mut queue = AdmissionQueue::new(4).unwrap();
        let first = queue.submit(spec("a")).unwrap();
        let _second = queue.submit(spec("b")).unwrap();
        let ticket = queue.claim().unwrap().expect("claim");
        assert_eq!(ticket.seq, first);
        assert!(ticket.fence.starts_with("fence_1:"));
        assert_eq!(
            queue.snapshot()["entries"][0]["state"],
            json!(format!("claimed:{}", ticket.fence))
        );
    }

    #[test]
    fn backpressure_refuses_at_capacity() {
        let mut queue = AdmissionQueue::new(1).unwrap();
        queue.submit(spec("a")).unwrap();
        let error = queue.submit(spec("b")).unwrap_err();
        assert!(error.0.contains("at capacity"));
    }

    #[test]
    fn stale_fences_cannot_resolve_after_epoch_bump() {
        let mut queue = AdmissionQueue::new(2).unwrap();
        let seq = queue.submit(spec("a")).unwrap();
        let ticket = queue.claim().unwrap().unwrap();
        // Unknown outcome: bump the epoch; the old fence must die.
        queue.bump_epoch("claim outcome unknown after worker disconnect");
        let error = queue
            .resolve(seq, &ticket.fence, json!("succeeded"))
            .unwrap_err();
        assert!(error.0.contains("stale or foreign fence"));
        // A claim under the new epoch gets a fresh, live fence.
        let new_ticket = queue.claim().unwrap().expect("re-claim under new epoch");
        assert_eq!(
            new_ticket.seq, seq,
            "the same job is offered, never silently duplicated elsewhere"
        );
        assert!(new_ticket.fence.starts_with("fence_2:"));
        queue
            .resolve(seq, &new_ticket.fence, json!({"status": "succeeded"}))
            .unwrap();
        assert_eq!(
            queue.snapshot()["entries"][0]["state"],
            json!(format!("resolved:{}", json!({"status": "succeeded"})))
        );
    }

    #[test]
    fn cancel_works_only_before_claim() {
        let mut queue = AdmissionQueue::new(2).unwrap();
        let seq = queue.submit(spec("a")).unwrap();
        assert!(queue.cancel(seq).unwrap());
        assert!(
            queue.claim().unwrap().is_none(),
            "cancelled entries are not claimable"
        );
        let other = queue.submit(spec("b")).unwrap();
        let _ticket = queue.claim().unwrap().unwrap();
        assert!(
            !queue.cancel(other).unwrap(),
            "claimed entries need reconciliation, not cancel"
        );
        assert!(queue.cancel(999).is_err());
    }

    #[test]
    fn double_claim_is_impossible() {
        let mut queue = AdmissionQueue::new(1).unwrap();
        queue.submit(spec("a")).unwrap();
        let _first = queue.claim().unwrap().unwrap();
        assert!(queue.claim().unwrap().is_none());
    }
}
