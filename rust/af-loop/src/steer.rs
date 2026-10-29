//! HITL steering (LangGraph-style interrupts on our journal): pause a run at
//! a bounded point, resume it with the operator's input, and fork it into a
//! new session. Pauses are journaled; a resumed run appends, never replays;
//! forks reference the parent and start a fresh journal.

use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SteerError(pub String);

pub type SteerResult<T> = Result<T, SteerError>;

/// Steering state derived from the journal's committed events.
pub struct Steering;

impl Steering {
    /// Pause: record the interrupt with the pending question. The run must
    /// treat this as a terminal state for the current turn.
    pub fn pause(
        journal: &mut af_journal::SessionJournal,
        reason: &str,
        question: &str,
    ) -> SteerResult<Value> {
        if reason.trim().is_empty() || question.trim().is_empty() {
            return Err(SteerError("pause requires a reason and a question".into()));
        }
        let event = journal
            .append(
                "question",
                &json!({"reason": reason, "question": question, "awaiting": "host"}),
                None,
                None,
                None,
            )
            .map_err(|error| SteerError(error.0))?;
        Ok(event)
    }

    /// Resume is admissible only after a pause and only with operator input;
    /// it appends a steering event, it never replays pending calls.
    pub fn resume(
        journal: &mut af_journal::SessionJournal,
        operator: &str,
        answer: &str,
    ) -> SteerResult<Value> {
        if operator.trim().is_empty() || answer.trim().is_empty() {
            return Err(SteerError("resume requires operator and answer".into()));
        }
        let snapshot = journal.read().map_err(|error| SteerError(error.0))?;
        if !snapshot
            .events
            .iter()
            .any(|event| event["event_type"] == json!("question"))
        {
            return Err(SteerError("resume requires a journaled pause".into()));
        }
        if !snapshot.unresolved_call_ids().is_empty() {
            return Err(SteerError(
                "unresolved calls hold the run; reconcile before steering".into(),
            ));
        }
        journal
            .append(
                "steering_applied",
                &json!({"operator": operator, "answer": answer}),
                None,
                None,
                None,
            )
            .map_err(|error| SteerError(error.0))
    }

    /// Fork: a new session that references the parent; history is copied by
    /// reference (the parent digest), never by replaying its events.
    pub fn fork(
        parent: &mut af_journal::SessionJournal,
        child_id: &str,
        forked_by: &str,
    ) -> SteerResult<af_journal::SessionJournal> {
        let snapshot = parent.read().map_err(|error| SteerError(error.0))?;
        if snapshot.incomplete_tail {
            return Err(SteerError("fork refuses a session with an interrupted tail".into()));
        }
        let parent_digest = {
            use sha2::{Digest, Sha256};
            let mut hasher = Sha256::new();
            for event in &snapshot.events {
                hasher.update(serde_json::to_string(event).unwrap_or_default().as_bytes());
            }
            format!("{:x}", hasher.finalize())
        };
        let project = std::path::Path::new(".");
        let mut child = af_journal::SessionJournal::create(project, Some(child_id), None)
            .map_err(|error| SteerError(error.0))?;
        child
            .append(
                "fork_created",
                &json!({
                    "parent_session_id": parent.session_id,
                    "parent_events": snapshot.events.len(),
                    "parent_history_sha256": parent_digest,
                    "forked_by": forked_by,
                    "replay": false,
                }),
                None,
                None,
                None,
            )
            .map_err(|error| SteerError(error.0))?;
        Ok(child)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp_journal(id: &str) -> (std::path::PathBuf, af_journal::SessionJournal) {
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let home = std::env::temp_dir().join(format!("af-steer-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", &home);
        let project = home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        let journal = af_journal::SessionJournal::create(&project, Some(id), None).unwrap();
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        drop(guard);
        (home, journal)
    }

    #[test]
    fn pause_resume_cycle_is_journaled() {
        let (_home, mut journal) = temp_journal("cycle");
        Steering::pause(&mut journal, "ambiguous scope", "extend to tests/?").unwrap();
        // Resume before any pause would refuse; with the pause journaled it works.
        let resumed = Steering::resume(&mut journal, "owner", "yes, include tests/").unwrap();
        assert_eq!(resumed["event_type"], json!("steering_applied"));
        let snapshot = journal.read().unwrap();
        assert!(snapshot.events.iter().any(|event| event["event_type"] == json!("question")));
        assert!(snapshot.unresolved_call_ids().is_empty());
    }

    #[test]
    fn resume_requires_a_journaled_pause_and_clean_holds() {
        let (_home, mut journal) = temp_journal("nopause");
        let error = Steering::resume(&mut journal, "owner", "go").unwrap_err();
        assert!(error.0.contains("requires a journaled pause"));
        // A paused run with an unknown charge cannot be steered.
        Steering::pause(&mut journal, "hold", "ok?").unwrap();
        journal
            .append("model_request", &json!({}), Some("call-x"), None, None)
            .unwrap();
        journal
            .append("model_failed", &json!({}), Some("call-x"), Some("error"), None)
            .unwrap();
        let error = Steering::resume(&mut journal, "owner", "go").unwrap_err();
        assert!(error.0.contains("unresolved calls hold the run"));
    }

    #[test]
    fn fork_copies_by_reference_not_replay() {
        // The child session resolves its path at creation: keep the isolated
        // home active for the whole fork.
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let home = std::env::temp_dir().join(format!("af-steer-fork-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", &home);
        let project = home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        let mut parent =
            af_journal::SessionJournal::create(&project, Some("parent"), None).unwrap();
        parent
            .append("user_input", &json!({"text": "hi"}), None, None, None)
            .unwrap();
        let child = Steering::fork(&mut parent, "child-fork", "owner");
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        drop(guard);
        let mut child = child.unwrap();
        let snapshot = child.read().unwrap();
        assert_eq!(snapshot.events.len(), 2, "creation + fork event only");
        assert_eq!(snapshot.events[1]["event_type"], json!("fork_created"));
        assert_eq!(snapshot.events[1]["payload"]["replay"], json!(false));
        assert!(snapshot.events[1]["payload"]["parent_history_sha256"]
            .as_str()
            .is_some_and(|digest| digest.len() == 64));
        let _ = std::fs::remove_dir_all(&home);
    }
}
