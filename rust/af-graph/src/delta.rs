//! File-level structural delta (port of `graph/delta.py`, roadmap P02).
//! A delta replaces one file's owned nodes and edges wholesale. The
//! transaction refuses to apply when the stored file digest moved (content
//! CAS) or the store generation moved (generation CAS); on any refusal or
//! crash the previous consistent snapshot stays visible. A fallback full
//! rebuild is an explicit operation with a recorded reason.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use serde_json::{json, Value};
use crate::adapter_resource::adapt_source;
use crate::project::clean_build;
use crate::resolve::{exports_by_file, infer_file_edges, module_map};
use crate::schema::{file_id, GraphContractError};
use crate::store::GraphStore;

/// Ops for one file; optionally bind planning to a specific generation.
/// `None`/empty old digest means the file must be absent — never an
/// unconditional overwrite.
pub fn file_delta(
    path: &str,
    new_bytes: Option<&[u8]>,
    expected_old_digest: Option<&str>,
    base_generation: Option<i64>,
) -> Result<Value, GraphContractError> {
    if let Some(value) = base_generation {
        if value < 0 {
            return Err(GraphContractError("base generation must be a non-negative integer".into()));
        }
    }
    let owned = file_id(path);
    let Some(bytes) = new_bytes else {
        return Ok(json!({
            "path": path, "put_nodes": [], "put_edges": [],
            "drop_node_ids": [owned], "parsed_files": 0, "deleted": true,
            "new_digest": Value::Null,
            "expected_old_digest": expected_old_digest,
            "base_generation": base_generation,
        }));
    };
    let payload = adapt_source(path, bytes)?;
    let new_digest = payload["nodes"][0]["source"]["digest"].as_str().unwrap_or_default().to_string();
    Ok(json!({
        "path": path,
        "put_nodes": payload["nodes"],
        "put_edges": payload["edges"],
        "drop_node_ids": [owned], "parsed_files": 1, "deleted": false,
        "new_digest": new_digest,
        "expected_old_digest": expected_old_digest,
        "base_generation": base_generation,
    }))
}

fn id_owner_file(id: &str) -> String {
    if let Some(rest) = id.strip_prefix("file:") {
        rest.to_string()
    } else if let Some(rest) = id.strip_prefix("sym:") {
        rest.split("::").next().unwrap_or(rest).to_string()
    } else {
        id.to_string()
    }
}

/// Recompute inferred cross-file edges of the closure hit by this delta.
/// The closure is the changed file plus files importing/depending on changed
/// module or export bindings, plus inferred-edge neighbours. Returns
/// (new_edges, dropped_edge_ids, relinked_count, unresolved).
fn relink(
    store: &GraphStore,
    delta: &Value,
    drop_ids: &BTreeSet<String>,
) -> (Vec<Value>, Vec<String>, usize, Vec<String>) {
    let path = delta["path"].as_str().unwrap_or_default();
    let nodes_pre = store.nodes();
    let mut post_nodes: Vec<Value> = nodes_pre
        .iter()
        .filter(|node| {
            let id = node["id"].as_str().unwrap_or_default();
            let node_path = node["path"].as_str().unwrap_or_default();
            !drop_ids.contains(id) && node_path != path
        })
        .cloned()
        .collect();
    post_nodes.extend(delta["put_nodes"].as_array().cloned().unwrap_or_default());
    let file_nodes: BTreeMap<String, Value> = post_nodes
        .iter()
        .filter(|node| node["kind"] == "file")
        .map(|node| (node["path"].as_str().unwrap_or_default().to_string(), node.clone()))
        .collect();

    let old_edges = store.edges();
    let mut affected: BTreeSet<String> = BTreeSet::from([path.to_string()]);
    let pre_files: BTreeMap<String, Value> = nodes_pre
        .iter()
        .filter(|node| node["kind"] == "file")
        .map(|node| (node["path"].as_str().unwrap_or_default().to_string(), node.clone()))
        .collect();
    let pre_paths: Vec<String> = pre_files.keys().cloned().collect();
    let post_paths: Vec<String> = file_nodes.keys().cloned().collect();
    let pre_modules = module_map(&pre_paths);
    let post_modules = module_map(&post_paths);
    let pre_exports = exports_by_file(&nodes_pre);
    let post_exports = exports_by_file(&post_nodes);
    let mut changed_modules: BTreeSet<String> = BTreeSet::new();
    for name in pre_modules.keys().chain(post_modules.keys()) {
        if pre_modules.get(name) != post_modules.get(name) {
            changed_modules.insert(name.clone());
        }
    }
    let mut changed_exports: BTreeSet<String> = BTreeSet::new();
    for candidate in pre_exports.keys().chain(post_exports.keys()) {
        if pre_exports.get(candidate) != post_exports.get(candidate) {
            changed_exports.insert(candidate.clone());
        }
    }
    for (candidate_path, node) in &file_nodes {
        if !pre_files.contains_key(candidate_path) {
            affected.insert(candidate_path.clone());
            continue;
        }
        let imports: Vec<String> = node
            .pointer("/facts/imports")
            .and_then(Value::as_array)
            .map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .unwrap_or_default();
        let mut hit = false;
        for name in &imports {
            let parts: Vec<&str> = name.split('.').collect();
            for size in (1..=parts.len()).rev() {
                let prefix = parts[..size].join(".");
                if changed_modules.contains(&prefix) {
                    hit = true;
                    break;
                }
                if let Some(target) = post_modules.get(&prefix) {
                    if target != candidate_path && changed_exports.contains(target) {
                        hit = true;
                        break;
                    }
                }
            }
            if hit {
                break;
            }
        }
        if hit {
            affected.insert(candidate_path.clone());
        }
    }
    for edge in &old_edges {
        if edge.get("facts_class").and_then(Value::as_str) != Some("inferred")
            || edge["status"].as_str() != Some("active")
        {
            continue;
        }
        let src_file = id_owner_file(edge["src"].as_str().unwrap_or_default());
        let dst_file = id_owner_file(edge["dst"].as_str().unwrap_or_default());
        if path == src_file || path == dst_file {
            affected.insert(src_file);
            affected.insert(dst_file);
        }
    }

    let mut dropped: BTreeSet<String> = BTreeSet::new();
    for edge in &old_edges {
        if edge.get("facts_class").and_then(Value::as_str) != Some("inferred") {
            continue;
        }
        let src_file = id_owner_file(edge["src"].as_str().unwrap_or_default());
        if affected.contains(&src_file) {
            dropped.insert(edge["id"].as_str().unwrap_or_default().to_string());
        }
    }
    let mut new_edges: Vec<Value> = Vec::new();
    let mut unresolved: Vec<String> = Vec::new();
    for affected_path in &affected {
        let Some(node) = file_nodes.get(affected_path) else { continue };
        let (inferred, items) = infer_file_edges(node, &post_modules, &post_exports);
        new_edges.extend(inferred);
        unresolved.extend(items.into_iter().map(|item| format!("{affected_path}: {item}")));
    }
    unresolved.sort();
    (new_edges, dropped.into_iter().collect(), affected.len(), unresolved)
}

/// Validate digests, then commit; returns instrumentation for the report.
pub fn apply_delta(store: &mut GraphStore, delta: &Value) -> Result<Value, GraphContractError> {
    let path = delta["path"].as_str().unwrap_or_default().to_string();
    let expected = delta.get("expected_old_digest").and_then(Value::as_str);
    let base_generation = match delta.get("base_generation").and_then(Value::as_i64) {
        Some(value) => value,
        None => store.generation(),
    };
    if base_generation != store.generation() {
        return Err(GraphContractError("delta superseded: store generation moved since planning".into()));
    }
    let mut drop_ids: BTreeSet<String> = delta["drop_node_ids"]
        .as_array()
        .map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default();
    // A file-level delta owns every node of that path; stale symbols of the
    // previous revision must not survive the replacement.
    for node in store.nodes() {
        if node["path"].as_str() == Some(path.as_str()) {
            drop_ids.insert(node["id"].as_str().unwrap_or_default().to_string());
        }
    }
    let stored_digest = store
        .nodes()
        .iter()
        .find(|node| node["id"].as_str() == Some(file_id(&path).as_str()))
        .and_then(|node| node.pointer("/source/digest"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let expected_value = expected.unwrap_or("");
    if stored_digest != expected_value {
    fn short(value: &str) -> &str {
        if value.len() > 12 { &value[..12] } else { value }
    }
    let expected_label = if expected_value.is_empty() { "no file" } else { short(expected_value) };
    let stored_label = if stored_digest.is_empty() { "no file" } else { short(&stored_digest) };
        return Err(GraphContractError(format!(
            "source changed since delta was built: {path} (expected {expected_label}, store has {stored_label})"
        )));
    }
    let (relink_edges, dropped_edges, relinked, unresolved) = relink(store, delta, &drop_ids);
    let mut put_edges: Vec<Value> = delta["put_edges"].as_array().cloned().unwrap_or_default();
    let put_edge_ids: BTreeSet<String> = put_edges
        .iter()
        .filter_map(|edge| edge["id"].as_str())
        .map(str::to_string)
        .collect();
    for edge in relink_edges {
        if !put_edge_ids.contains(edge["id"].as_str().unwrap_or_default()) {
            put_edges.push(edge);
        }
    }
    let put_nodes = delta["put_nodes"].as_array().cloned().unwrap_or_default();
    let drop_node_ids: Vec<String> = drop_ids.iter().cloned().collect();
    let generation = store
        .commit(base_generation, &put_nodes, &put_edges, &drop_node_ids, &dropped_edges)
        .map_err(|error| {
            if error.is_conflict() {
                GraphContractError(format!("delta superseded: {error}"))
            } else {
                error
            }
        })?;
    Ok(json!({
        "path": path, "generation": generation,
        "deleted": delta["deleted"],
        "put_nodes": put_nodes.len(), "put_edges": put_edges.len(),
        "dropped": drop_ids.len(), "parsed_files": delta["parsed_files"],
        "scanned_files": 1, "relinked_files": relinked, "unresolved": unresolved,
    }))
}

/// Full rebuild inside one transaction; reason is recorded, never silent.
pub fn fallback_rebuild(
    store: &mut GraphStore,
    files: &BTreeMap<String, Vec<u8>>,
    reason: &str,
) -> Result<Value, GraphContractError> {
    if reason.is_empty() {
        return Err(GraphContractError("fallback rebuild requires an explicit reason".into()));
    }
    let clean = clean_build(files)?;
    let old_ids: Vec<String> = store.nodes().iter().map(|n| n["id"].as_str().unwrap_or_default().to_string()).collect();
    let nodes = clean["nodes"].as_array().cloned().unwrap_or_default();
    let edges = clean["edges"].as_array().cloned().unwrap_or_default();
    let generation = store.commit(store.generation(), &nodes, &edges, &old_ids, &[])?;
    Ok(json!({
        "generation": generation, "reason": reason,
        "parsed_files": clean["parsed_files"], "scanned_files": files.len(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::GraphStore;

    fn temp_root(name: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static COUNTER: AtomicU32 = AtomicU32::new(0);
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("af-delta-{}-{name}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn setup(files: &[(&str, &[u8])]) -> (std::path::PathBuf, GraphStore) {
        let dir = temp_root("setup");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        let map: BTreeMap<String, Vec<u8>> = files
            .iter()
            .map(|(p, b)| (p.to_string(), b.to_vec()))
            .collect();
        fallback_rebuild(&mut store, &map, "initial clean build").unwrap();
        (dir, store)
    }

    #[test]
    fn body_edit_deltas_to_equal_clean_build() {
        let (_dir, mut store) = setup(&[
            ("lib.py", b"def helper(): pass\ndef extra(): pass\n"),
            ("app.py", b"from lib import helper\n\ndef main():\n    helper()\n"),
        ]);
        let generation_before = store.generation();
        let delta = file_delta("lib.py", Some(b"def helper(): pass  # changed\ndef extra(): pass\n"),
            Some(digest_of(b"def helper(): pass\ndef extra(): pass\n").as_str()), None).unwrap();
        let report = apply_delta(&mut store, &delta).unwrap();
        assert_eq!(report["generation"], json!(generation_before + 1));
        assert_eq!(report["parsed_files"], json!(1));
        // Parity: fresh clean build over the new bytes equals the delta graph.
        let files = BTreeMap::from([
            ("lib.py".to_string(), b"def helper(): pass  # changed\ndef extra(): pass\n".to_vec()),
            ("app.py".to_string(), b"from lib import helper\n\ndef main():\n    helper()\n".to_vec()),
        ]);
        let clean = clean_build(&files).unwrap();
        assert_eq!(
            sorted_ids(&clean["edges"]), sorted_edges(&store),
            "delta result must equal clean build"
        );
    }

    #[test]
    fn content_cas_refuses_stale_digest() {
        let (_dir, mut store) = setup(&[("lib.py", b"def helper(): pass\n")]);
        let delta = file_delta("lib.py", Some(b"def helper(): pass\n"),
            Some(digest_of(b"OLD").as_str()), None).unwrap();
        let error = apply_delta(&mut store, &delta).unwrap_err();
        assert!(error.0.contains("source changed since delta was built"));
    }

    #[test]
    fn delete_and_readd_updates_unresolved_consumer() {
        let (_dir, mut store) = setup(&[
            ("lib.py", b"def helper(): pass\n"),
            ("app.py", b"from lib import helper\n\ndef main():\n    helper()\n"),
        ]);
        // Delete lib.py: app.py's calls edge must be dropped, import unresolved.
        let delta = file_delta("lib.py", None, Some(digest_of(b"def helper(): pass\n").as_str()), None).unwrap();
        let report = apply_delta(&mut store, &delta).unwrap();
        assert_eq!(report["deleted"], json!(true));
        let edges = store.edges();
        assert!(!edges.iter().any(|e| e["kind"] == "calls" && e["src"] == "file:app.py"));
        assert!(!edges.iter().any(|e| e["kind"] == "imports" && e["dst"] == "file:lib.py"));
        // Re-add lib.py with helper: consumer resolves again via relink closure.
        let delta = file_delta("lib.py", Some(b"def helper(): pass\n"),
            None, None).unwrap();
        apply_delta(&mut store, &delta).unwrap();
        let edges = store.edges();
        assert!(edges.iter().any(|e| e["kind"] == "calls" && e["dst"] == "sym:lib.py::helper"));
    }

    #[test]
    fn generation_binding_rejects_moved_store() {
        let (_dir, mut store) = setup(&[("lib.py", b"def a(): pass\n")]);
        let bound = file_delta("lib.py", Some(b"def a(): pass\n"),
            Some(digest_of(b"def a(): pass\n").as_str()), Some(store.generation())).unwrap();
        // Someone else commits in between.
        let other = file_delta("lib.py", Some(b"def a(): pass\ndef b(): pass\n"),
            Some(digest_of(b"def a(): pass\n").as_str()), None).unwrap();
        apply_delta(&mut store, &other).unwrap();
        let error = apply_delta(&mut store, &bound).unwrap_err();
        assert!(error.0.contains("delta superseded"));
    }

    fn digest_of(bytes: &[u8]) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(bytes);
        format!("{:x}", hasher.finalize())
    }

    fn sorted_ids(edges: &Value) -> Vec<String> {
        let mut ids: Vec<String> = edges.as_array().unwrap()
            .iter().map(|e| e["id"].as_str().unwrap_or_default().to_string()).collect();
        ids.sort();
        ids
    }

    fn sorted_edges(store: &GraphStore) -> Vec<String> {
        let mut ids: Vec<String> = store.edges().iter()
            .map(|e| e["id"].as_str().unwrap_or_default().to_string()).collect();
        ids.sort();
        ids
    }
}
