//! Single-writer structural graph store with generation CAS (port of
//! `graph/store.py`, roadmap P01). Storage is an atomically replaced canonical
//! JSON file with fsync. Atomic replacement alone is not CAS: cooperating
//! writers compare the persisted generation while holding a persistent OS
//! lease on a marker lock file that is never unlinked.

use std::collections::BTreeMap;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use serde_json::{json, Value};
use crate::canonical::canonical_bytes;
use crate::schema::{validate_edge, validate_node, GraphContractError, GRAPH_SCHEMA_VERSION};

/// A persistently marked lock file lease (blocking with a deadline, like the
/// Python `exclusive_file(blocking=True)` wrapper: on timeout the refusal is a
/// conflict, not a silent wait forever).
pub struct StoreLease {
    _inner: af_entropy::FileLease,
}

impl StoreLease {
    pub fn acquire(lock_path: &Path, busy_message: &str) -> Result<Self, GraphContractError> {
        af_entropy::FileLease::acquire(lock_path, true, busy_message)
            .map(|lease| StoreLease { _inner: lease })
            .map_err(GraphContractError)
    }
}

pub struct GraphStore {
    path: PathBuf,
    nodes: BTreeMap<String, Value>,
    edges: BTreeMap<String, Value>,
    generation: i64,
}

impl GraphStore {
    pub fn open(path: &Path) -> Result<Self, GraphContractError> {
        if path.exists() {
            let text = std::fs::read_to_string(path)
                .map_err(|error| GraphContractError(format!("graph store read failed: {error}")))?;
            let document: Value = serde_json::from_str(&text)
                .map_err(|error| GraphContractError(format!("graph store is not valid JSON: {error}")))?;
            if document.get("schema_version").and_then(Value::as_i64) != Some(GRAPH_SCHEMA_VERSION) {
                return Err(GraphContractError("graph store schema mismatch; migrate explicitly".into()));
            }
            let mut nodes = BTreeMap::new();
            for node in document["nodes"].as_array().cloned().unwrap_or_default() {
                let id = node["id"].as_str().unwrap_or_default().to_string();
                nodes.insert(id, node);
            }
            let mut edges = BTreeMap::new();
            for edge in document["edges"].as_array().cloned().unwrap_or_default() {
                let id = edge["id"].as_str().unwrap_or_default().to_string();
                edges.insert(id, edge);
            }
            let generation = document.get("generation").and_then(Value::as_i64).unwrap_or(0);
            let store = GraphStore { path: path.to_path_buf(), nodes, edges, generation };
            store.validate()?;
            Ok(store)
        } else {
            Ok(GraphStore { path: path.to_path_buf(), nodes: BTreeMap::new(), edges: BTreeMap::new(), generation: 0 })
        }
    }

    pub fn generation(&self) -> i64 {
        self.generation
    }

    pub fn nodes(&self) -> Vec<Value> {
        self.nodes.values().cloned().collect()
    }

    pub fn edges(&self) -> Vec<Value> {
        self.edges.values().cloned().collect()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    pub fn validate(&self) -> Result<(), GraphContractError> {
        for node in self.nodes.values() {
            validate_node(node)?;
        }
        for edge in self.edges.values() {
            validate_edge(edge)?;
            if edge["status"] == "active" {
                for endpoint in ["src", "dst"] {
                    let id = edge[endpoint].as_str().unwrap_or_default();
                    if !self.nodes.contains_key(id) {
                        return Err(GraphContractError(format!("dangling edge endpoint {id:?}")));
                    }
                }
            }
        }
        Ok(())
    }

    /// CAS one persisted snapshot under the writer lease. Returns the new generation.
    pub fn commit(
        &mut self,
        base_generation: i64,
        put_nodes: &[Value],
        put_edges: &[Value],
        drop_node_ids: &[String],
        drop_edge_ids: &[String],
    ) -> Result<i64, GraphContractError> {
        if base_generation < 0 {
            return Err(GraphContractError("base generation must be a non-negative integer".into()));
        }
        let lock_path = self.path.with_file_name(format!(
            "{}.lock",
            self.path.file_name().and_then(|n| n.to_str()).unwrap_or("graph.json")
        ));
        let _lease = StoreLease::acquire(&lock_path, "graph writer lease is held")?;
        self.commit_locked(base_generation, put_nodes, put_edges, drop_node_ids, drop_edge_ids)
    }

    fn commit_locked(
        &mut self,
        base_generation: i64,
        put_nodes: &[Value],
        put_edges: &[Value],
        drop_node_ids: &[String],
        drop_edge_ids: &[String],
    ) -> Result<i64, GraphContractError> {
        let persisted_generation = match std::fs::read_to_string(&self.path) {
            Ok(text) => {
                let persisted: Value = serde_json::from_str(&text)
                    .map_err(|_| GraphContractError("graph store is not valid JSON".into()))?;
                if persisted.get("schema_version").and_then(Value::as_i64) != Some(GRAPH_SCHEMA_VERSION) {
                    return Err(GraphContractError("graph store schema mismatch; migrate explicitly".into()));
                }
                match persisted.get("generation").and_then(Value::as_i64) {
                    Some(value) if value >= 0 => value,
                    _ => return Err(GraphContractError("persisted graph generation is malformed".into())),
                }
            }
            Err(_) => 0,
        };
        if persisted_generation != base_generation {
            return Err(GraphContractError::conflict(format!(
                "persisted store generation moved to {persisted_generation}; re-read and retry"
            )));
        }
        if base_generation != self.generation {
            return Err(GraphContractError::conflict(format!(
                "store generation moved to {}; re-read and retry", self.generation
            )));
        }
        let mut nodes = self.nodes.clone();
        let mut edges = self.edges.clone();
        for node_id in drop_node_ids {
            nodes.remove(node_id);
            edges.retain(|_, edge| {
                edge["src"].as_str() != Some(node_id.as_str()) && edge["dst"].as_str() != Some(node_id.as_str())
            });
        }
        for edge_id_value in drop_edge_ids {
            edges.remove(edge_id_value);
        }
        for node in put_nodes {
            validate_node(node)?;
            nodes.insert(node["id"].as_str().unwrap_or_default().to_string(), node.clone());
        }
        for edge in put_edges {
            validate_edge(edge)?;
            edges.insert(edge["id"].as_str().unwrap_or_default().to_string(), edge.clone());
        }
        let candidate = GraphStore {
            path: self.path.clone(),
            nodes,
            edges,
            generation: base_generation + 1,
        };
        candidate.validate()?;
        let payload = canonical_bytes(&candidate.canonical_document());
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let temp_path = self.path.with_file_name(format!(
            "graph-{}.tmp",
            std::process::id() as u64 ^ std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos() as u64
        ));
        {
            let mut file = File::create(&temp_path)
                .map_err(|error| GraphContractError(format!("graph temp write failed: {error}")))?;
            file.write_all(&payload)
                .and_then(|_| file.flush())
                .and_then(|_| file.sync_all())
                .map_err(|error| {
                    let _ = std::fs::remove_file(&temp_path);
                    GraphContractError(format!("graph fsync write failed: {error}"))
                })?;
        }
        std::fs::rename(&temp_path, &self.path).map_err(|error| {
            let _ = std::fs::remove_file(&temp_path);
            GraphContractError(format!("graph replace failed: {error}"))
        })?;
        self.nodes = candidate.nodes;
        self.edges = candidate.edges;
        self.generation = candidate.generation;
        Ok(self.generation)
    }

    fn canonical_document(&self) -> Value {
        json!({
            "schema_version": GRAPH_SCHEMA_VERSION,
            "generation": self.generation,
            "nodes": self.nodes.values().collect::<Vec<_>>(),
            "edges": self.edges.values().collect::<Vec<_>>(),
        })
    }

    /// Deterministic canonical bytes for parity checks and inspection.
    pub fn export_canonical(&self) -> Vec<u8> {
        canonical_bytes(&self.canonical_document())
    }

    pub fn document(&self) -> Value {
        serde_json::from_slice(&self.export_canonical()).expect("canonical bytes are valid JSON")
    }
}

impl GraphContractError {
    pub fn conflict(message: impl Into<String>) -> Self {
        GraphContractError(message.into())
    }

    pub fn is_conflict(&self) -> bool {
        self.0.contains("re-read and retry") || self.0.contains("lease is held")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn file_node(path: &str, digest: &str) -> Value {
        json!({
            "id": format!("file:{path}"), "kind": "file", "path": path,
            "source": {"digest": digest, "algorithm": "sha256"},
            "facts": {"extractor": "t"}, "fact_status": {"extractor": "fresh"},
            "facts_class": "extracted",
        })
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "af-graph-test-{}-{}",
            std::process::id(),
            name
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn commit_advances_generation_and_persists() {
        let dir = temp_dir("commit");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        let generation = store.commit(0, &[file_node("a.py", "d1")], &[], &[], &[]).unwrap();
        assert_eq!(generation, 1);
        let reopened = GraphStore::open(&path).unwrap();
        assert_eq!(reopened.generation(), 1);
        assert_eq!(reopened.node_count(), 1);
    }

    #[test]
    fn stale_base_generation_is_refused() {
        let dir = temp_dir("stale");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        store.commit(0, &[file_node("a.py", "d1")], &[], &[], &[]).unwrap();
        let error = store.commit(0, &[file_node("b.py", "d2")], &[], &[], &[]).unwrap_err();
        assert!(error.is_conflict());
        assert_eq!(store.generation(), 1);
    }

    #[test]
    fn dropping_node_drops_its_edges() {
        let dir = temp_dir("drop");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        let edge = json!({"id": "file:a.py-contains->file:b.py", "kind": "contains",
                          "src": "file:a.py", "dst": "file:b.py", "status": "active"});
        store.commit(0, &[file_node("a.py", "d1"), file_node("b.py", "d2")], &[edge], &[], &[]).unwrap();
        assert_eq!(store.edge_count(), 1);
        store.commit(1, &[], &[], &["file:a.py".to_string()], &[]).unwrap();
        assert_eq!(store.node_count(), 1);
        assert_eq!(store.edge_count(), 0);
    }

    #[test]
    fn dangling_active_edge_refused() {
        let dir = temp_dir("dangling");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        let edge = json!({"id": "file:a.py-contains->file:ghost.py", "kind": "contains",
                          "src": "file:a.py", "dst": "file:ghost.py", "status": "active"});
        let error = store.commit(0, &[file_node("a.py", "d1")], &[edge], &[], &[]).unwrap_err();
        assert!(error.0.contains("dangling"));
    }

    #[test]
    fn canonical_export_is_stable() {
        let dir = temp_dir("canonical");
        let path = dir.join("graph.json");
        let mut store = GraphStore::open(&path).unwrap();
        store.commit(0, &[file_node("b.py", "d2"), file_node("a.py", "d1")], &[], &[], &[]).unwrap();
        let first = store.export_canonical();
        let reopened = GraphStore::open(&path).unwrap();
        assert_eq!(first, reopened.export_canonical());
        let text = String::from_utf8(first).unwrap();
        assert!(text.starts_with("{\n \"edges\": [],\n \"generation\": 1,\n"));
    }

    #[test]
    fn concurrent_writer_hits_conflict_not_corruption() {
        let dir = temp_dir("cas");
        let path = dir.join("graph.json");
        let mut a = GraphStore::open(&path).unwrap();
        let mut b = GraphStore::open(&path).unwrap();
        a.commit(0, &[file_node("a.py", "d1")], &[], &[], &[]).unwrap();
        let error = b.commit(0, &[file_node("b.py", "d2")], &[], &[], &[]).unwrap_err();
        assert!(error.is_conflict());
        // b re-reads and can now commit on top.
        let mut b = GraphStore::open(&path).unwrap();
        b.commit(1, &[file_node("b.py", "d2")], &[], &[], &[]).unwrap();
    }
}
