//! Versioned structural graph contract (roadmap P01) — port of `graph/schema.py`.
//!
//! Facts carry provenance and freshness; line numbers are revision coordinates
//! only. Identity is project path + qualified symbol; ambiguous matches are
//! never merged silently. Nodes/edges are validated as untyped JSON objects to
//! keep the persisted shape identical to the Python contract.

use serde_json::{Map, Value};
use std::collections::BTreeSet;

pub const GRAPH_SCHEMA_VERSION: i64 = 1;

pub const NODE_KINDS: [&str; 7] = [
    "file", "module", "class", "function", "config", "document", "pipeline",
];
pub const EDGE_KINDS: [&str; 4] = ["contains", "imports", "calls", "references"];
pub const FACT_CLASSES: [&str; 3] = ["extracted", "inferred", "runtime_observed"];
pub const STATUS: [&str; 2] = ["fresh", "stale"];
pub const EDGE_STATUS: [&str; 2] = ["active", "tombstone"];

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct GraphContractError(pub String);

impl GraphContractError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

fn posix(path: &str) -> String {
    path.replace('\\', "/")
}

/// `file:<posix path>`
pub fn file_id(path: &str) -> String {
    format!("file:{}", posix(path))
}

/// `sym:<posix path>::<qualified>[#N]` — the disambiguator is omitted for 1.
pub fn symbol_id(path: &str, qualified: &str, disambiguator: u32) -> String {
    let suffix = if disambiguator == 1 {
        String::new()
    } else {
        format!("#{disambiguator}")
    };
    format!("sym:{}::{}{}", posix(path), qualified, suffix)
}

/// `<src>-<kind>-><dst>`
pub fn edge_id(src: &str, kind: &str, dst: &str) -> String {
    format!("{src}-{kind}->{dst}")
}

/// `^[A-Za-z_][A-Za-z0-9_.]*$` (hand-rolled: no regex dependency in the core).
fn is_qualified(value: &str) -> bool {
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.')
}

fn is_hex32(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

pub fn validate_node(node: &Value) -> Result<(), GraphContractError> {
    let Some(object) = node.as_object() else {
        return Err(GraphContractError::new("node must be an object"));
    };
    let required = ["id", "kind", "path", "source", "facts", "fact_status"];
    let missing: Vec<&str> = required
        .iter()
        .filter(|field| !object.contains_key(**field))
        .copied()
        .collect();
    if !missing.is_empty() {
        let mut names: Vec<String> = missing.iter().map(|m| format!("{m:?}")).collect();
        names.sort();
        return Err(GraphContractError::new(format!(
            "node missing fields: {}",
            names.join(", ")
        )));
    }
    let kind = object["kind"].as_str().unwrap_or_default();
    if !NODE_KINDS.contains(&kind) {
        return Err(GraphContractError::new(format!(
            "unsupported node kind {kind:?}"
        )));
    }
    let path = object["path"].as_str().unwrap_or_default();
    if path.is_empty() || path.contains('\\') {
        return Err(GraphContractError::new(
            "node path must be non-empty posix-relative",
        ));
    }
    let source = object["source"].as_object();
    let source_ok = source.is_some_and(|s| s.contains_key("digest") && s.contains_key("algorithm"));
    if !source_ok {
        return Err(GraphContractError::new(
            "node.source needs digest+algorithm",
        ));
    }
    let id = object["id"].as_str().unwrap_or_default();
    if kind == "file" {
        if id != file_id(path) {
            return Err(GraphContractError::new("file id mismatch"));
        }
    } else {
        let symbol = object["symbol"].as_str().unwrap_or_default();
        if symbol.is_empty() || !is_qualified(symbol) {
            return Err(GraphContractError::new(
                "symbol nodes need a qualified symbol",
            ));
        }
        let prefix = format!("sym:{path}::{symbol}");
        let suffix = &id[prefix.len().min(id.len())..];
        let id_derives = id == prefix
            || (id.len() > prefix.len()
                && id.starts_with(&prefix)
                && id.as_bytes()[prefix.len()] == b'#'
                && suffix[1..].bytes().all(|b| b.is_ascii_digit())
                && suffix.len() > 1);
        if !id_derives {
            return Err(GraphContractError::new(
                "symbol id must derive from path+symbol",
            ));
        }
        if !object.contains_key("start_line") || !object.contains_key("end_line") {
            return Err(GraphContractError::new(
                "symbol nodes carry revision line coordinates",
            ));
        }
    }
    let facts = object["facts"].as_object();
    let fact_status = object["fact_status"].as_object();
    if facts.is_none() || fact_status.is_none() {
        return Err(GraphContractError::new("facts/fact_status must be objects"));
    }
    let fact_status = fact_status.unwrap();
    let unknown: BTreeSet<&str> = fact_status
        .values()
        .filter_map(|v| v.as_str())
        .filter(|status| !STATUS.contains(status))
        .collect();
    if !unknown.is_empty() {
        let names: Vec<String> = unknown.iter().map(|s| format!("{s:?}")).collect();
        return Err(GraphContractError::new(format!(
            "unknown fact status {}",
            names.join(", ")
        )));
    }
    let facts = facts.unwrap();
    for name in fact_status.keys() {
        if !facts.contains_key(name) {
            return Err(GraphContractError::new(format!(
                "status for unknown fact {name:?}"
            )));
        }
    }
    let facts_class = object
        .get("facts_class")
        .and_then(|v| v.as_str())
        .unwrap_or("extracted");
    if !FACT_CLASSES.contains(&facts_class) {
        return Err(GraphContractError::new("unknown facts class"));
    }
    Ok(())
}

pub fn validate_edge(edge: &Value) -> Result<(), GraphContractError> {
    let Some(object) = edge.as_object() else {
        return Err(GraphContractError::new("edge must be an object"));
    };
    for field in ["id", "kind", "src", "dst", "status"] {
        if !object.contains_key(field) {
            return Err(GraphContractError::new("edge missing fields"));
        }
    }
    let kind = object["kind"].as_str().unwrap_or_default();
    if !EDGE_KINDS.contains(&kind) {
        return Err(GraphContractError::new(format!(
            "unsupported edge kind {kind:?}"
        )));
    }
    let status = object["status"].as_str().unwrap_or_default();
    if !EDGE_STATUS.contains(&status) {
        return Err(GraphContractError::new(format!(
            "unknown edge status {status:?}"
        )));
    }
    let (src, dst) = (
        object["src"].as_str().unwrap_or_default(),
        object["dst"].as_str().unwrap_or_default(),
    );
    if src == dst {
        return Err(GraphContractError::new("self edge refused"));
    }
    if object["id"].as_str() != Some(&edge_id(src, kind, dst)) {
        return Err(GraphContractError::new(
            "edge id must be derived from src/kind/dst",
        ));
    }
    Ok(())
}

/// Dangling-endpoint check shared by the store (active edges only).
pub fn validate_hex_digest(value: &Value) -> bool {
    value.as_str().is_some_and(is_hex32)
}

/// Fact-map helper mirroring Python's `{k: "fresh" for k in facts}`.
pub fn all_fresh(facts: &Map<String, Value>) -> Map<String, Value> {
    facts
        .keys()
        .map(|k| (k.clone(), Value::String("fresh".into())))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn file_node(path: &str) -> Value {
        serde_json::json!({
            "id": file_id(path), "kind": "file", "path": path,
            "source": {"digest": "a".repeat(64), "algorithm": "sha256"},
            "facts": {"extractor": "test"}, "fact_status": {"extractor": "fresh"},
            "facts_class": "extracted",
        })
    }

    #[test]
    fn ids_derive_from_identity() {
        assert_eq!(file_id("a\\b.py"), "file:a/b.py");
        assert_eq!(symbol_id("a.py", "C.m", 1), "sym:a.py::C.m");
        assert_eq!(symbol_id("a.py", "f", 2), "sym:a.py::f#2");
        assert_eq!(edge_id("x", "calls", "y"), "x-calls->y");
    }

    #[test]
    fn file_node_validates_and_rejects_bad_kind() {
        validate_node(&file_node("pkg/mod.py")).expect("valid file node");
        let mut bad = file_node("pkg/mod.py");
        bad["kind"] = Value::String("galaxy".into());
        assert!(validate_node(&bad).is_err());
    }

    #[test]
    fn symbol_node_requires_derived_id_and_lines() {
        let mut node = file_node("m.py");
        node["id"] = Value::String(symbol_id("m.py", "Cls.run", 1));
        node["kind"] = Value::String("class".into());
        node["symbol"] = Value::String("Cls.run".into());
        node["start_line"] = Value::from(1);
        node["end_line"] = Value::from(9);
        validate_node(&node).expect("valid symbol node");

        let mut no_lines = node.clone();
        no_lines.as_object_mut().unwrap().remove("start_line");
        assert!(validate_node(&no_lines).is_err());

        let mut bad_disambiguator = node;
        bad_disambiguator["id"] = Value::String("sym:m.py::Cls.run#abc".into());
        assert!(validate_node(&bad_disambiguator).is_err());
    }

    #[test]
    fn edge_validation_rejects_self_and_foreign_id() {
        let ok = serde_json::json!({
            "id": "a-contains->b", "kind": "contains", "src": "a", "dst": "b", "status": "active"
        });
        validate_edge(&ok).expect("valid edge");
        let self_edge = serde_json::json!({
            "id": "a-contains->a", "kind": "contains", "src": "a", "dst": "a", "status": "active"
        });
        assert!(validate_edge(&self_edge).is_err());
        let foreign = serde_json::json!({
            "id": "nope", "kind": "contains", "src": "a", "dst": "b", "status": "active"
        });
        assert!(validate_edge(&foreign).is_err());
    }

    #[test]
    fn unknown_fact_status_and_class_are_refused() {
        let mut node = file_node("m.py");
        node["fact_status"]["extractor"] = Value::String("moldy".into());
        assert!(validate_node(&node).is_err());
        let mut cls = file_node("m.py");
        cls["facts_class"] = Value::String("guessed".into());
        assert!(validate_node(&cls).is_err());
    }
}
