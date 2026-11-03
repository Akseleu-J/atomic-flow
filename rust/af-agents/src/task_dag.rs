//! Hierarchical task DAG (P05 core port from `agents/goals.py`/`planner.py`
//! contracts): goals with scopes/caps, steps with dependencies and explicit
//! states, cycle detection, frontier computation and invalidation cascade.
//! States mirror the P05 set; transitions are explicit and reasoned — no
//! implicit state changes, and re-running a succeeded step requires an
//! invalidation.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct DagError(pub String);

pub type DagResult<T> = Result<T, DagError>;

pub const STATES: [&str; 8] = [
    "pending",
    "ready",
    "running",
    "blocked",
    "succeeded",
    "failed",
    "cancelled",
    "superseded",
];

#[derive(Debug, Clone, PartialEq)]
pub struct Step {
    pub id: String,
    pub title: String,
    pub depends_on: Vec<String>,
    pub state: &'static str,
    pub scope_paths: Vec<String>,
    /// Evidence trail: every transition appends a record with a reason.
    pub history: Vec<Value>,
}

impl Step {
    fn new(id: String, title: String, depends_on: Vec<String>, scope_paths: Vec<String>) -> Step {
        Step {
            history: vec![json!({"event": "created", "state": "pending"})],
            id,
            title,
            depends_on,
            state: "pending",
            scope_paths,
        }
    }

    fn transition(&mut self, state: &'static str, reason: &str) {
        self.history.push(json!({
            "event": "transition", "from": self.state, "to": state, "reason": reason,
        }));
        self.state = state;
    }
}

/// One task: a goal plus its step DAG.
#[derive(Debug, Clone, Default)]
pub struct TaskDag {
    pub task_id: String,
    pub goal: String,
    pub scope_paths: Vec<String>,
    pub max_budget_usd: Option<f64>,
    pub steps: BTreeMap<String, Step>,
}

impl TaskDag {
    pub fn new(task_id: &str, goal: &str, scope_paths: &[String]) -> DagResult<TaskDag> {
        if task_id.trim().is_empty() || task_id.len() > 128 {
            return Err(DagError("task id must be bounded non-empty text".into()));
        }
        if goal.trim().is_empty() {
            return Err(DagError("task goal must be non-empty".into()));
        }
        Ok(TaskDag {
            task_id: task_id.to_string(),
            goal: goal.to_string(),
            scope_paths: scope_paths.to_vec(),
            max_budget_usd: None,
            steps: BTreeMap::new(),
        })
    }

    /// Add a step; ids must be unique, dependencies must exist, and the
    /// resulting graph must stay acyclic (the new edge set is checked).
    pub fn add_step(
        &mut self,
        id: &str,
        title: &str,
        depends_on: &[String],
        scope_paths: &[String],
    ) -> DagResult<()> {
        if self.steps.contains_key(id) {
            return Err(DagError(format!("duplicate step id {id}")));
        }
        if self.steps.len() >= 64 {
            return Err(DagError("task step limit (64) reached".into()));
        }
        for dep in depends_on {
            if !self.steps.contains_key(dep) {
                return Err(DagError(format!("step {id} depends on unknown step {dep}")));
            }
            if dep == id {
                return Err(DagError(format!("step {id} depends on itself")));
            }
        }
        self.steps.insert(
            id.to_string(),
            Step::new(
                id.to_string(),
                title.to_string(),
                depends_on.to_vec(),
                scope_paths.to_vec(),
            ),
        );
        Self::assert_acyclic(&self.steps)?;
        // Freshly added steps with satisfied dependencies become ready.
        self.refresh_ready();
        Ok(())
    }

    fn assert_acyclic(steps: &BTreeMap<String, Step>) -> DagResult<()> {
        fn visit(
            id: &str,
            steps: &BTreeMap<String, Step>,
            visiting: &mut BTreeSet<String>,
            done: &mut BTreeSet<String>,
        ) -> Result<(), String> {
            if done.contains(id) {
                return Ok(());
            }
            if !visiting.insert(id.to_string()) {
                return Err(format!("cycle through {id}"));
            }
            if let Some(step) = steps.get(id) {
                for dep in &step.depends_on {
                    visit(dep, steps, visiting, done)
                        .map_err(|error| format!("{error}; via {id}"))?;
                }
            }
            visiting.remove(id);
            done.insert(id.to_string());
            Ok(())
        }
        let mut done = BTreeSet::new();
        for id in steps.keys() {
            visit(id, steps, &mut BTreeSet::new(), &mut done).map_err(DagError)?;
        }
        Ok(())
    }

    /// Recompute ready/blocked for pending steps: ready when every
    /// dependency is succeeded; blocked when any dependency failed or was
    /// cancelled without invalidation.
    fn refresh_ready(&mut self) {
        let states: BTreeMap<String, &'static str> = self
            .steps
            .iter()
            .map(|(id, step)| (id.clone(), step.state))
            .collect();
        for (_id, step) in self.steps.iter_mut() {
            if step.state != "pending" {
                continue;
            }
            let mut deps = step.depends_on.clone();
            deps.sort();
            deps.dedup();
            if deps.is_empty() {
                step.transition("ready", "no dependencies");
                continue;
            }
            if deps
                .iter()
                .any(|dep| matches!(states.get(dep), Some(&"failed") | Some(&"cancelled")))
            {
                step.transition("blocked", "a dependency failed or was cancelled");
            } else if deps.iter().all(|dep| states.get(dep) == Some(&"succeeded")) {
                step.transition("ready", "all dependencies succeeded");
            }
        }
    }

    /// The frontier: steps ready for execution right now, in id order.
    pub fn frontier(&self) -> Vec<String> {
        self.steps
            .values()
            .filter(|step| step.state == "ready")
            .map(|step| step.id.clone())
            .collect()
    }

    /// Claim a ready step for execution (pending -> running with reason).
    pub fn claim(&mut self, id: &str, reason: &str) -> DagResult<()> {
        let step = self
            .steps
            .get_mut(id)
            .ok_or_else(|| DagError(format!("unknown step {id}")))?;
        if step.state != "ready" {
            return Err(DagError(format!("step {id} is {}, not ready", step.state)));
        }
        step.transition("running", reason);
        Ok(())
    }

    /// Terminal result of a running step.
    pub fn finish(&mut self, id: &str, succeeded: bool, reason: &str) -> DagResult<()> {
        let step = self
            .steps
            .get_mut(id)
            .ok_or_else(|| DagError(format!("unknown step {id}")))?;
        if step.state != "running" {
            return Err(DagError(format!(
                "step {id} is {}, not running",
                step.state
            )));
        }
        step.transition(if succeeded { "succeeded" } else { "failed" }, reason);
        self.refresh_ready();
        Ok(())
    }

    /// Explicit invalidation: the only way a succeeded step re-enters the
    /// queue. Its result is superseded (never silently rerun), and every
    /// transitive dependent is invalidated too — their old results were
    /// built on the superseded one.
    pub fn invalidate(&mut self, id: &str, reason: &str) -> DagResult<Value> {
        let Some(step) = self.steps.get(id) else {
            return Err(DagError(format!("unknown step {id}")));
        };
        if step.state != "succeeded" && step.state != "failed" {
            return Err(DagError(format!(
                "step {id} is {}; only succeeded/failed steps can be invalidated",
                step.state
            )));
        }
        let mut superseded: Vec<String> = vec![id.to_string()];
        loop {
            let mut grew = false;
            for (other_id, other) in self.steps.iter() {
                if superseded.contains(other_id) {
                    continue;
                }
                if other.depends_on.iter().any(|dep| superseded.contains(dep)) {
                    superseded.push(other_id.clone());
                    grew = true;
                }
            }
            if !grew {
                break;
            }
        }
        for step_id in &superseded {
            let step = self.steps.get_mut(step_id).expect("checked above");
            // The old RESULT is superseded; every affected step re-enters as
            // pending (the frontier recomputes readiness after).
            step.transition("pending", &format!("invalidated: {reason}"));
        }
        self.refresh_ready();
        Ok(json!({
            "invalidated": id,
            "cascade": superseded.iter().skip(1).cloned().collect::<Vec<_>>(),
            "reason": reason,
        }))
    }

    pub fn as_value(&self) -> Value {
        json!({
            "task_id": self.task_id, "goal": self.goal,
            "scope_paths": self.scope_paths,
            "max_budget_usd": self.max_budget_usd,
            "frontier": self.frontier(),
            "steps": self.steps.values().map(|step| json!({
                "id": step.id, "title": step.title,
                "depends_on": step.depends_on, "state": step.state,
                "scope_paths": step.scope_paths,
                "history": step.history,
            })).collect::<Vec<_>>(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dag() -> TaskDag {
        let mut dag = TaskDag::new("t1", "add feature", &["src/".to_string()]).unwrap();
        dag.add_step("s1", "read code", &[], &["src/lib.py".to_string()])
            .unwrap();
        dag.add_step("s2", "edit", &["s1".into()], &["src/lib.py".to_string()])
            .unwrap();
        dag.add_step("s3", "check", &["s2".into()], &["src/lib.py".to_string()])
            .unwrap();
        dag
    }

    #[test]
    fn frontier_and_lifecycle() {
        let mut dag = dag();
        assert_eq!(dag.frontier(), vec!["s1".to_string()]);
        dag.claim("s1", "operator started").unwrap();
        assert!(
            dag.frontier().is_empty(),
            "running step leaves the frontier"
        );
        dag.finish("s1", true, "read complete").unwrap();
        assert_eq!(dag.frontier(), vec!["s2".to_string()]);
        dag.claim("s2", "started").unwrap();
        dag.finish("s2", true, "edited").unwrap();
        dag.claim("s3", "started").unwrap();
        dag.finish("s3", true, "check ok").unwrap();
        assert!(dag.frontier().is_empty());
        let value = dag.as_value();
        assert!(value["steps"]
            .as_array()
            .unwrap()
            .iter()
            .all(|s| s["state"] == json!("succeeded")));
    }

    #[test]
    fn failure_blocks_descendants_until_invalidation() {
        let mut dag = dag();
        dag.claim("s1", "started").unwrap();
        dag.finish("s1", false, "test failed").unwrap();
        assert!(dag.frontier().is_empty());
        let blocked = dag.steps.get("s2").unwrap();
        assert_eq!(blocked.state, "blocked");
        // Invalidation re-opens the chain: the failed step becomes
        // superseded, dependents return to pending and go ready.
        let report = dag.invalidate("s1", "repair approved").unwrap();
        assert_eq!(report["cascade"], json!(["s2", "s3"]));
        assert_eq!(
            dag.frontier(),
            vec!["s1".to_string()],
            "superseded step re-enters as pending->ready"
        );
    }

    #[test]
    fn cycles_and_unknown_dependencies_are_refused() {
        let mut dag = TaskDag::new("t", "g", &[]).unwrap();
        dag.add_step("a", "", &[], &[]).unwrap();
        dag.add_step("b", "", &["a".into()], &[]).unwrap();
        assert!(
            dag.add_step("a", "", &["b".into()], &[]).is_err(),
            "duplicate id"
        );
        let mut dag2 = TaskDag::new("t2", "g", &[]).unwrap();
        dag2.add_step("x", "", &["ghost".into()], &[]).unwrap_err();
        // Direct cycle: c -> d -> c is impossible via add (deps must exist),
        // so the cycle check guards future edge additions; unknown deps are
        // the entry guard here.
        assert!(dag2.add_step("c", "", &["ghost".into()], &[]).is_err());
    }

    #[test]
    fn invalidation_requires_terminal_state() {
        let mut dag = dag();
        assert!(
            dag.invalidate("s1", "too early").is_err(),
            "pending step cannot be invalidated"
        );
        dag.claim("s1", "started").unwrap();
        assert!(dag.invalidate("s1", "still running").is_err());
    }

    #[test]
    fn step_limit_enforced() {
        let mut dag = TaskDag::new("big", "g", &[]).unwrap();
        for index in 0..64 {
            dag.add_step(&format!("s{index}"), "", &[], &[]).unwrap();
        }
        assert!(dag.add_step("overflow", "", &[], &[]).is_err());
    }
}
