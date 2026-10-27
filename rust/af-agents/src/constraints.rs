//! Bind provably equivalent replacement goals to their original accounting
//! IDs (port of `agents/replan_constraints.py`). A new generation is not a
//! fresh allowance. Exact local goal identity and metadata permit reuse of
//! the old goal, including its ancestor chain. A flat replacement may inherit
//! one unambiguous remaining parent. Other mappings require fresh review; no
//! semantic equivalence is inferred from model text.
//!
//! Goals/steps are JSON objects: goal fields include `id`/`parent_id` plus
//! reviewed metadata (requirements, scope, caps); steps carry `parent_goal`,
//! `scope_paths`, `linked_code_nodes`, `linked_test_nodes`, `status`.

use std::collections::{BTreeMap, BTreeSet};
use serde_json::Value;

pub struct Inheritance {
    pub refusals: Vec<String>,
    pub goal_mapping: BTreeMap<String, String>,
    pub scoped_steps: Vec<(String, String, Vec<String>)>,
}

fn local_part(id: &str) -> &str {
    id.split_once(':').map(|(_, local)| local).unwrap_or(id)
}

fn goal_metadata(goal: &Value) -> Value {
    let mut metadata = goal.clone();
    if let Some(object) = metadata.as_object_mut() {
        object.remove("id");
        if let Some(parent_value) = object.get_mut("parent_id") {
            if let Some(parent_str) = parent_value.as_str().map(str::to_string) {
                *parent_value = Value::String(local_part(&parent_str).to_string());
            }
        }
    }
    metadata
}

fn active_steps<'a>(steps: &'a [Value]) -> Vec<&'a Value> {
    steps
        .iter()
        .filter(|step| {
            let status = step.get("status").and_then(Value::as_str).unwrap_or("pending");
            status != "done" && status != "superseded"
        })
        .collect()
}

/// Full entry point: original goals + steps + proposed goals/steps. Steps are
/// mutated only after proof, so the caller receives a proposed mapping plus
/// per-step (step, original parent, inherited scope) targets instead of
/// mutation.
pub fn inherit(
    original_goals: &[Value],
    original_steps: &[Value],
    new_goals: &[Value],
    new_steps: &[Value],
) -> Inheritance {
    let mut refusals: Vec<String> = Vec::new();
    let active = active_steps(original_steps);
    let mut by_local: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for goal in original_goals {
        by_local
            .entry(local_part(goal.get("id").and_then(Value::as_str).unwrap_or_default()).to_string())
            .or_default()
            .push(goal);
    }
    let mut mapping: BTreeMap<String, String> = BTreeMap::new();
    for goal in new_goals {
        let new_id = goal.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
        let matches = by_local.get(local_part(&new_id)).cloned().unwrap_or_default();
        if matches.len() != 1 {
            refusals.push("replacement goal identity is new or ambiguous".to_string());
            continue;
        }
        let old = matches[0];
        if goal_metadata(goal) != goal_metadata(old) {
            refusals.push("replacement goal changes reviewed requirements, scope or caps".to_string());
            continue;
        }
        mapping.insert(new_id.clone(), old.get("id").and_then(Value::as_str).unwrap_or_default().to_string());
    }
    let parents: BTreeSet<String> = active
        .iter()
        .filter_map(|step| step.get("parent_goal").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    let mut targets: Vec<(String, String, Option<Vec<String>>)> = Vec::new();
    for step in new_steps {
        let step_parent = step.get("parent_goal").and_then(Value::as_str).unwrap_or_default();
        let parent = if !step_parent.is_empty() {
            mapping.get(step_parent).cloned()
        } else if parents.len() == 1 {
            parents.iter().next().cloned()
        } else {
            None
        };
        let Some(parent) = parent.filter(|parent| parents.contains(parent)) else {
            refusals.push("replacement step has no unambiguous original goal lineage".to_string());
            continue;
        };
        let previous_scopes: Vec<BTreeSet<String>> = active
            .iter()
            .filter(|previous| previous.get("parent_goal").and_then(Value::as_str) == Some(parent.as_str()))
            .filter_map(|previous| previous.get("scope_paths").and_then(Value::as_array))
            .map(|paths| paths.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .collect();
        let scope: Option<BTreeSet<String>> = if previous_scopes.is_empty() {
            None
        } else {
            let mut intersection = previous_scopes[0].clone();
            for set in &previous_scopes[1..] {
                intersection = intersection.intersection(set).cloned().collect();
            }
            Some(intersection)
        };
        if let Some(scope) = &scope {
            if scope.is_empty() {
                refusals.push("replacement step widens or ambiguously merges original step scopes".to_string());
                continue;
            }
            let proposed: BTreeSet<String> = step
                .get("scope_paths")
                .and_then(Value::as_array)
                .map(|paths| paths.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default();
            if !proposed.is_empty() && !proposed.is_subset(scope) {
                refusals.push("replacement step widens or ambiguously merges original step scopes".to_string());
                continue;
            }
        }
        targets.push((
            step.get("id").and_then(Value::as_str).unwrap_or_default().to_string(),
            parent,
            scope.map(|set| set.into_iter().collect()),
        ));
    }
    for field in ["linked_code_nodes", "linked_test_nodes"] {
        for parent in &parents {
            let mut required: BTreeSet<String> = BTreeSet::new();
            for previous in &active {
                if previous.get("parent_goal").and_then(Value::as_str) != Some(parent.as_str()) {
                    continue;
                }
                if let Some(nodes) = previous.get(field).and_then(Value::as_array) {
                    for node in nodes {
                        if let Some(name) = node.as_str() {
                            required.insert(name.to_string());
                        }
                    }
                }
            }
            let mut covered: BTreeSet<String> = BTreeSet::new();
            for (step_id, target, _) in &targets {
                if target != parent {
                    continue;
                }
                let Some(step) = new_steps
                    .iter()
                    .find(|step| step.get("id").and_then(Value::as_str) == Some(step_id.as_str()))
                else {
                    continue;
                };
                if let Some(nodes) = step.get(field).and_then(Value::as_array) {
                    for node in nodes {
                        if let Some(name) = node.as_str() {
                            covered.insert(name.to_string());
                        }
                    }
                }
            }
            if required.difference(&covered).next().is_some() {
                refusals.push(format!("replacement loses original {field} obligations"));
            }
        }
    }
    let scoped_steps = targets
        .into_iter()
        .map(|(step, parent, scope)| (step, parent, scope.unwrap_or_default()))
        .collect();
    Inheritance { refusals: dedup_sorted(refusals), goal_mapping: mapping, scoped_steps }
}

fn dedup_sorted(mut reasons: Vec<String>) -> Vec<String> {
    reasons.sort();
    reasons.dedup();
    reasons
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn goal(id: &str, parent: &str) -> Value {
        json!({"id": id, "parent_id": parent, "requirements": "same", "scope_paths": ["src/"], "max_budget_usd": 1.0})
    }

    fn step(id: &str, parent: &str, scopes: &[&str], code: &[&str]) -> Value {
        json!({"id": id, "parent_goal": parent, "scope_paths": scopes,
               "linked_code_nodes": code, "linked_test_nodes": [], "status": "pending"})
    }

    #[test]
    fn exact_goal_identity_rebinds_lineage() {
        let original = vec![goal("g:alpha", "g:root")];
        let original_steps = vec![step("s:1", "g:alpha", &["src/a.py"], &["file:src/a.py"])];
        let new_goals = vec![goal("g:alpha", "g:root")];
        let new_steps = vec![step("s:new", "g:alpha", &[], &["file:src/a.py"])];
        let result = inherit(&original, &original_steps, &new_goals, &new_steps);
        assert!(result.refusals.is_empty(), "{:?}", result.refusals);
        assert_eq!(result.goal_mapping.get("g:alpha").map(String::as_str), Some("g:alpha"));
        // Empty proposed scope inherits the original intersection.
        assert_eq!(result.scoped_steps[0].2, vec!["src/a.py".to_string()]);
    }

    #[test]
    fn changed_metadata_or_new_identity_refuses() {
        let original = vec![goal("g:alpha", "g:root")];
        let original_steps = vec![step("s:1", "g:alpha", &["src/a.py"], &[])];
        let mut changed = goal("g:alpha", "g:root");
        changed["max_budget_usd"] = json!(5.0);
        let result = inherit(&original, &original_steps, &[changed], &[]);
        assert!(result.refusals.iter().any(|r| r.contains("changes reviewed requirements")));
        let result = inherit(&original, &original_steps, &[goal("g:beta", "g:root")], &[]);
        assert!(result.refusals.iter().any(|r| r.contains("new or ambiguous")));
    }

    #[test]
    fn scope_widening_refuses() {
        let original = vec![goal("g:alpha", "g:root")];
        let original_steps = vec![step("s:1", "g:alpha", &["src/a.py"], &[])];
        let new_steps = vec![step("s:new", "g:alpha", &["src/b.py"], &[])];
        let result = inherit(&original, &original_steps, &[goal("g:alpha", "g:root")], &new_steps);
        assert!(result.refusals.iter().any(|r| r.contains("widens or ambiguously merges")));
    }

    #[test]
    fn lost_linked_obligations_refuse() {
        let original = vec![goal("g:alpha", "g:root")];
        let original_steps = vec![step("s:1", "g:alpha", &["src/a.py"], &["file:src/a.py", "file:src/b.py"])];
        let new_steps = vec![step("s:new", "g:alpha", &[], &["file:src/a.py"])];
        let result = inherit(&original, &original_steps, &[goal("g:alpha", "g:root")], &new_steps);
        assert!(result.refusals.iter().any(|r| r.contains("loses original linked_code_nodes")));
    }

    #[test]
    fn flat_replacement_needs_unambiguous_parent() {
        let original = vec![goal("g:alpha", "g:root"), goal("g:beta", "g:root")];
        let original_steps = vec![
            step("s:1", "g:alpha", &["src/a.py"], &[]),
            step("s:2", "g:beta", &["src/b.py"], &[]),
        ];
        // No parent_goal and two active parents: ambiguous.
        let result = inherit(&original, &original_steps, &[goal("g:alpha", "g:root"), goal("g:beta", "g:root")], &[step("s:new", "", &[], &[])]);
        assert!(result.refusals.iter().any(|r| r.contains("no unambiguous original goal lineage")));
        // One active parent: flat step inherits it.
        let original_steps = vec![step("s:1", "g:alpha", &["src/a.py"], &[])];
        let result = inherit(&original, &original_steps, &[goal("g:alpha", "g:root")], &[step("s:new", "", &[], &[])]);
        assert!(result.refusals.is_empty(), "{:?}", result.refusals);
        assert_eq!(result.scoped_steps[0].1, "g:alpha");
    }
}
