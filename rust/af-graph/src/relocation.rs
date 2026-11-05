//! Explicit revision-to-revision relocation reports (port of
//! `graph/relocation.py`); never rename authority. Mappings are revision
//! observations, not read or write grants.

use crate::schema::{file_id, GraphContractError};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// Compose observed, successful moves; never infer them from model arguments.
pub fn owned_move_hints(
    previous: &BTreeMap<String, String>,
    calls: &[Value],
    before: &BTreeMap<String, String>,
    after: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, GraphContractError> {
    let mut hints = previous.clone();
    for call in calls {
        let tool = call.get("tool").and_then(Value::as_str).unwrap_or_default();
        let summary = call.get("result_summary").cloned().unwrap_or(Value::Null);
        if tool != "move_file" || summary.get("status").and_then(Value::as_str) != Some("applied") {
            continue;
        }
        let changes = summary
            .get("changes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let removed: Vec<&Value> = changes
            .iter()
            .filter(|row| {
                row.get("before_hash").and_then(Value::as_str).is_some()
                    && row.get("after_hash").is_some_and(Value::is_null)
            })
            .collect();
        let added: Vec<&Value> = changes
            .iter()
            .filter(|row| {
                row.get("before_hash").is_some_and(Value::is_null)
                    && row.get("after_hash").and_then(Value::as_str).is_some()
            })
            .collect();
        if changes.len() != 2 || removed.len() != 1 || added.len() != 1 {
            continue;
        }
        let left = removed[0]["path"].as_str().unwrap_or_default().to_string();
        let right = added[0]["path"].as_str().unwrap_or_default().to_string();
        let _ = file_id(&left);
        let _ = file_id(&right);
        let before_hash = removed[0]["before_hash"].as_str().unwrap_or_default();
        if before.get(&left).map(String::as_str) != Some(before_hash)
            || after.contains_key(&left)
            || !after.contains_key(&right)
            || before.contains_key(&right)
            || added[0].get("after_hash").and_then(Value::as_str) != Some(before_hash)
        {
            continue;
        }
        let origins: Vec<String> = hints
            .iter()
            .filter(|(_, destination)| destination.as_str() == left.as_str())
            .map(|(origin, _)| origin.clone())
            .collect();
        if !origins.is_empty() {
            for origin in origins {
                hints.insert(origin, right.clone());
            }
        } else {
            hints.insert(left, right);
        }
    }
    if hints.len() > 4096 {
        return Err(GraphContractError(
            "owned move hints exceed observation cap".into(),
        ));
    }
    Ok(hints)
}

fn file_nodes(document: &Value) -> BTreeMap<String, &Value> {
    document["nodes"]
        .as_array()
        .map(|nodes| {
            nodes
                .iter()
                .filter(|node| node["kind"] == json!("file"))
                .filter_map(|node| {
                    let path = node["path"].as_str()?.to_string();
                    Some((path, node))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Build a revision-to-revision relocation report from two canonical graph
/// documents (plus optional host-provided move pairs).
pub fn relocation_report(
    before: &Value,
    after: &Value,
    moves: Option<&BTreeMap<String, String>>,
) -> Result<Value, GraphContractError> {
    let old = file_nodes(before);
    let new = file_nodes(after);
    let removed: BTreeSet<String> = old
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>()
        .difference(&new.keys().cloned().collect::<BTreeSet<_>>())
        .cloned()
        .collect();
    let added: BTreeSet<String> = new
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>()
        .difference(&old.keys().cloned().collect::<BTreeSet<_>>())
        .cloned()
        .collect();
    if let Some(moves) = moves {
        for (left, right) in moves {
            if left.is_empty() || right.is_empty() {
                return Err(GraphContractError(
                    "move mapping requires exact old/new path pairs".into(),
                ));
            }
        }
    }
    let mut pairs: Vec<Value> = Vec::new();
    let mut used_old: BTreeSet<String> = BTreeSet::new();
    let mut used_new: BTreeSet<String> = BTreeSet::new();
    if let Some(moves) = moves {
        for (left, right) in moves {
            let _ = file_id(left);
            let _ = file_id(right);
            if !removed.contains(left) || !added.contains(right) || used_new.contains(right) {
                return Err(GraphContractError(
                    "move mapping does not match removed/added source identities".into(),
                ));
            }
            pairs.push(json!({"old_path": left, "new_path": right, "method": "provided_hint"}));
            used_old.insert(left.clone());
            used_new.insert(right.clone());
        }
    }
    let mut groups_old: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    let mut groups_new: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    for path in removed.difference(&used_old) {
        let node = old[path];
        groups_old
            .entry((
                node.pointer("/source/algorithm")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                node.pointer("/source/digest")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            ))
            .or_default()
            .push(path.clone());
    }
    for path in added.difference(&used_new) {
        let node = new[path];
        groups_new
            .entry((
                node.pointer("/source/algorithm")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                node.pointer("/source/digest")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            ))
            .or_default()
            .push(path.clone());
    }
    let mut ambiguous: Vec<Value> = Vec::new();
    for key in groups_old
        .keys()
        .collect::<BTreeSet<_>>()
        .intersection(&groups_new.keys().collect::<BTreeSet<_>>())
    {
        let lefts = &groups_old[key];
        let rights = &groups_new[key];
        if key.0 == "sha256" && lefts.len() == 1 && rights.len() == 1 {
            let (left, right) = (lefts[0].clone(), rights[0].clone());
            pairs.push(
                json!({"old_path": left, "new_path": right, "method": "unique_content_relocation"}),
            );
            used_old.insert(left);
            used_new.insert(right);
        } else {
            ambiguous.push(json!({
                "old_paths": lefts, "new_paths": rights,
                "reason": "ambiguous_content_identity",
            }));
        }
    }
    let mut mapping: BTreeMap<String, String> = BTreeMap::new();
    for pair in &pairs {
        let left = pair["old_path"].as_str().unwrap_or_default();
        let right = pair["new_path"].as_str().unwrap_or_default();
        mapping.insert(file_id(left), file_id(right));
        let mut old_symbols: BTreeMap<(String, Option<String>), Vec<String>> = BTreeMap::new();
        let mut new_symbols: BTreeMap<(String, Option<String>), Vec<String>> = BTreeMap::new();
        for node in before["nodes"].as_array().cloned().unwrap_or_default() {
            if node["path"].as_str() == Some(left) && node["kind"] != json!("file") {
                let key = (
                    node["kind"].as_str().unwrap_or_default().to_string(),
                    node.get("symbol")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                );
                old_symbols
                    .entry(key)
                    .or_default()
                    .push(node["id"].as_str().unwrap_or_default().to_string());
            }
        }
        for node in after["nodes"].as_array().cloned().unwrap_or_default() {
            if node["path"].as_str() == Some(right) && node["kind"] != json!("file") {
                let key = (
                    node["kind"].as_str().unwrap_or_default().to_string(),
                    node.get("symbol")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                );
                new_symbols
                    .entry(key)
                    .or_default()
                    .push(node["id"].as_str().unwrap_or_default().to_string());
            }
        }
        for key in old_symbols
            .keys()
            .collect::<BTreeSet<_>>()
            .intersection(&new_symbols.keys().collect::<BTreeSet<_>>())
        {
            let old_ids = &old_symbols[key];
            let new_ids = &new_symbols[key];
            if old_ids.len() == 1 && new_ids.len() == 1 {
                mapping.insert(old_ids[0].clone(), new_ids[0].clone());
            } else {
                ambiguous.push(json!({
                    "old_ids": old_ids, "new_ids": new_ids,
                    "reason": "ambiguous_symbol_identity",
                }));
            }
        }
    }
    Ok(json!({
        "path_moves": pairs,
        "identity_mapping": mapping,
        "ambiguous_moves": ambiguous,
        "unmapped_removed": removed.difference(&used_old).cloned().collect::<Vec<_>>(),
        "unmapped_added": added.difference(&used_new).cloned().collect::<Vec<_>>(),
        "authority": "none; mappings are revision observations, not read or write grants",
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn file_doc(path: &str, digest: &str) -> Value {
        json!({"id": format!("file:{path}"), "kind": "file", "path": path,
               "source": {"digest": digest, "algorithm": "sha256"},
               "facts": {}, "fact_status": {}, "facts_class": "extracted"})
    }

    fn symbol_doc(path: &str, symbol: &str, digest: &str) -> Value {
        json!({"id": format!("sym:{path}::{symbol}"), "kind": "function", "path": path,
               "symbol": symbol, "source": {"digest": digest, "algorithm": "sha256"},
               "start_line": 1, "end_line": 2, "facts": {}, "fact_status": {},
               "facts_class": "extracted"})
    }

    fn document(nodes: Vec<Value>) -> Value {
        json!({"nodes": nodes, "edges": []})
    }

    #[test]
    fn unique_content_relocation_maps_file_and_symbols() {
        let digest = "a".repeat(64);
        let before = document(vec![
            file_doc("old.py", &digest),
            symbol_doc("old.py", "run", &digest),
        ]);
        let after = document(vec![
            file_doc("new.py", &digest),
            symbol_doc("new.py", "run", &digest),
        ]);
        let report = relocation_report(&before, &after, None).unwrap();
        let moves = report["path_moves"].as_array().unwrap();
        assert_eq!(moves.len(), 1);
        assert_eq!(moves[0]["method"], json!("unique_content_relocation"));
        assert_eq!(
            report["identity_mapping"]["file:old.py"],
            json!("file:new.py")
        );
        assert_eq!(
            report["identity_mapping"]["sym:old.py::run"],
            json!("sym:new.py::run")
        );
        assert!(report["ambiguous_moves"].as_array().unwrap().is_empty());
        assert_eq!(
            report["authority"].as_str().unwrap(),
            "none; mappings are revision observations, not read or write grants"
        );
    }

    #[test]
    fn ambiguous_identity_is_reported_never_merged() {
        let digest = "a".repeat(64);
        let before = document(vec![
            file_doc("old1.py", &digest),
            file_doc("old2.py", &digest),
        ]);
        let after = document(vec![
            file_doc("new1.py", &digest),
            file_doc("new2.py", &digest),
        ]);
        let report = relocation_report(&before, &after, None).unwrap();
        let ambiguous = report["ambiguous_moves"].as_array().unwrap();
        assert_eq!(ambiguous.len(), 1);
        assert_eq!(ambiguous[0]["reason"], json!("ambiguous_content_identity"));
        assert!(report["identity_mapping"].as_object().unwrap().is_empty());
    }

    #[test]
    fn provided_hint_must_match_identities() {
        let before = document(vec![file_doc("old.py", &"a".repeat(64))]);
        let after = document(vec![file_doc("new.py", &"b".repeat(64))]);
        let mut moves = BTreeMap::new();
        moves.insert("ghost.py".to_string(), "new.py".to_string());
        let error = relocation_report(&before, &after, Some(&moves)).unwrap_err();
        assert!(error.0.contains("does not match removed/added"));
        moves.clear();
        moves.insert("old.py".to_string(), "new.py".to_string());
        let report = relocation_report(&before, &after, Some(&moves)).unwrap();
        assert_eq!(report["path_moves"][0]["method"], json!("provided_hint"));
    }

    #[test]
    fn owned_move_hints_compose_observed_moves_only() {
        let mut previous = BTreeMap::new();
        let before = BTreeMap::from([("a.py".to_string(), "h1".to_string())]);
        let mut after = BTreeMap::new();
        after.insert("b.py".to_string(), "h1".to_string());
        let calls = vec![json!({
            "tool": "move_file",
            "result_summary": {"status": "applied", "changes": [
                {"path": "a.py", "before_hash": "h1", "after_hash": null},
                {"path": "b.py", "before_hash": null, "after_hash": "h1"},
            ]},
        })];
        let hints = owned_move_hints(&previous, &calls, &before, &after).unwrap();
        assert_eq!(hints.get("a.py").map(String::as_str), Some("b.py"));
        // Failed moves and wrong shapes are never hints.
        let rejected = vec![json!({"tool": "move_file", "result_summary": {"status": "failed"}})];
        assert!(owned_move_hints(&previous, &rejected, &before, &after)
            .unwrap()
            .is_empty());
        previous.insert("z.py".to_string(), "a.py".to_string());
        let hints = owned_move_hints(&previous, &calls, &before, &after).unwrap();
        assert_eq!(
            hints.get("z.py").map(String::as_str),
            Some("b.py"),
            "chains follow the moved identity"
        );
    }
}
