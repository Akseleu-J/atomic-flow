//! Resolver layer (port of `graph/resolve.py`, roadmap P02): inferred cross-file
//! edges from stored facts, without re-parsing sources. `imports` edges bind a
//! file to the file owning the imported module; `calls` edges bind a caller
//! file to a symbol node when the imported module exports that exact leaf
//! uniquely. Ambiguous or unmatched names go to the unresolved report instead
//! of a guessed edge. Edges carry `facts_class: "inferred"`.

use crate::schema::edge_id;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const RESOLVER: &str = "name-resolver/1";

/// Dotted module name -> file path (packages map to `__init__.py` when present).
pub fn module_map(paths: &[String]) -> BTreeMap<String, String> {
    let mut mapping: BTreeMap<String, String> = BTreeMap::new();
    let mut inits: BTreeMap<String, String> = BTreeMap::new();
    for path in paths {
        if let Some(base) = path.strip_suffix("/__init__.py") {
            inits.insert(base.replace('/', "."), path.clone());
        }
    }
    for path in paths {
        if !path.ends_with(".py") {
            continue;
        }
        let dotted = path.strip_suffix(".py").unwrap_or(path).replace('/', ".");
        if path.ends_with("__init__.py") {
            let package = dotted
                .strip_suffix(".__init__")
                .unwrap_or(&dotted)
                .to_string();
            mapping.entry(package).or_insert_with(|| path.clone());
            continue;
        }
        mapping.insert(dotted, path.clone());
    }
    for (name, path) in inits {
        mapping.insert(name, path);
    }
    mapping
}

/// path -> {leaf symbol name -> [symbol node ids]} for class/function nodes.
pub fn exports_by_file(nodes: &[Value]) -> BTreeMap<String, BTreeMap<String, Vec<String>>> {
    let mut result: BTreeMap<String, BTreeMap<String, Vec<String>>> = BTreeMap::new();
    for node in nodes {
        let kind = node.get("kind").and_then(Value::as_str).unwrap_or_default();
        if kind != "class" && kind != "function" {
            continue;
        }
        let path = node
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let symbol = node
            .get("symbol")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let leaf = symbol.rsplit('.').next().unwrap_or(symbol).to_string();
        let id = node
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        result
            .entry(path)
            .or_default()
            .entry(leaf)
            .or_default()
            .push(id);
    }
    result
}

fn resolver_edge(src: &str, kind: &str, dst: &str) -> Value {
    let mut facts = Map::new();
    facts.insert("resolver".into(), json!(RESOLVER));
    let mut fact_status = Map::new();
    fact_status.insert("resolver".into(), json!("fresh"));
    json!({
        "id": edge_id(src, kind, dst), "kind": kind, "src": src, "dst": dst,
        "status": "active", "facts": facts, "fact_status": fact_status,
        "facts_class": "inferred",
    })
}

/// Inferred outgoing edges of one file from its stored facts.
/// Returns (edges, unresolved report) — the report is sorted and deduplicated.
pub fn infer_file_edges(
    file_node: &Value,
    modules: &BTreeMap<String, String>,
    exports: &BTreeMap<String, BTreeMap<String, Vec<String>>>,
) -> (Vec<Value>, Vec<String>) {
    let path = file_node
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let node_id = file_node
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let imports: Vec<&str> = file_node
        .pointer("/facts/imports")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let raw_calls: Vec<&str> = file_node
        .pointer("/facts/raw_calls")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let mut edges: Vec<Value> = Vec::new();
    let mut unresolved: BTreeSet<String> = BTreeSet::new();
    let mut imported_files: BTreeSet<String> = BTreeSet::new();
    for name in &imports {
        let parts: Vec<&str> = name.split('.').collect();
        let mut target: Option<&String> = None;
        for size in (1..=parts.len()).rev() {
            let candidate = parts[..size].join(".");
            if let Some(found) = modules.get(&candidate) {
                if found != &path {
                    target = Some(found);
                    break;
                }
            }
        }
        match target {
            Some(found) => {
                imported_files.insert(found.clone());
                edges.push(resolver_edge(&node_id, "imports", &format!("file:{found}")));
            }
            None => {
                unresolved.insert(format!("import {name}"));
            }
        }
    }
    let imported_leaves: BTreeSet<&str> = imports
        .iter()
        .map(|name| name.rsplit('.').next().unwrap_or(name))
        .collect();
    for call in &raw_calls {
        let mut targets: Vec<&String> = Vec::new();
        for target_file in &imported_files {
            if let Some(found) = exports
                .get(target_file)
                .and_then(|leaves| leaves.get(*call))
            {
                targets.extend(found.iter());
            }
        }
        if targets.len() == 1 && imported_leaves.contains(call) {
            edges.push(resolver_edge(&node_id, "calls", targets[0]));
        } else if targets.len() > 1 {
            unresolved.insert(format!(
                "call {call} (ambiguous: {} candidates)",
                targets.len()
            ));
        } else {
            unresolved.insert(format!("call {call} (no imported exporter)"));
        }
    }
    (edges, unresolved.into_iter().collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn file_with(path: &str, imports: &[&str], calls: &[&str]) -> Value {
        json!({
            "id": format!("file:{path}"), "kind": "file", "path": path,
            "source": {"digest": "d", "algorithm": "sha256"},
            "facts": {"imports": imports, "raw_calls": calls},
            "fact_status": {}, "facts_class": "extracted",
        })
    }

    #[test]
    fn packages_map_to_init() {
        let paths: Vec<String> = ["pkg/__init__.py", "pkg/mod.py", "pkg/sub/__init__.py"]
            .iter()
            .map(|p| p.to_string())
            .collect();
        let modules = module_map(&paths);
        assert_eq!(
            modules.get("pkg").map(String::as_str),
            Some("pkg/__init__.py")
        );
        assert_eq!(
            modules.get("pkg.mod").map(String::as_str),
            Some("pkg/mod.py")
        );
        assert_eq!(
            modules.get("pkg.sub").map(String::as_str),
            Some("pkg/sub/__init__.py")
        );
    }

    #[test]
    fn unique_export_binds_call_ambiguity_reported() {
        let paths: Vec<String> = ["a.py", "b.py"].iter().map(|p| p.to_string()).collect();
        let modules = module_map(&paths);
        let a = file_with("a.py", &["b.helper"], &["helper"]);
        let exports = BTreeMap::from([(
            "b.py".to_string(),
            BTreeMap::from([("helper".to_string(), vec!["sym:b.py::helper".to_string()])]),
        )]);
        let (edges, unresolved) = infer_file_edges(&a, &modules, &exports);
        assert_eq!(edges.len(), 2);
        assert!(edges
            .iter()
            .any(|e| e["kind"] == "imports" && e["dst"] == "file:b.py"));
        assert!(edges
            .iter()
            .any(|e| e["kind"] == "calls" && e["dst"] == "sym:b.py::helper"));
        assert!(unresolved.is_empty());
        assert_eq!(edges[0]["facts_class"], "inferred");

        let doubled = BTreeMap::from([(
            "b.py".to_string(),
            BTreeMap::from([(
                "helper".to_string(),
                vec!["sym:b.py::helper".into(), "sym:b.py::helper#2".into()],
            )]),
        )]);
        let (_, unresolved) = infer_file_edges(&a, &modules, &doubled);
        assert!(unresolved.iter().any(|u| u.contains("ambiguous")));
    }

    #[test]
    fn self_import_and_unresolvable_are_reported_not_guessed() {
        let a = file_with("a.py", &["a", "nowhere.deep"], &[]);
        let modules = module_map(&["a.py".to_string()]);
        let (edges, unresolved) = infer_file_edges(&a, &modules, &BTreeMap::new());
        assert!(edges.is_empty());
        assert_eq!(
            unresolved,
            vec!["import a".to_string(), "import nowhere.deep".to_string()]
        );
    }

    #[test]
    fn call_requires_imported_leaf_name() {
        let paths: Vec<String> = ["a.py", "b.py"].iter().map(|p| p.to_string()).collect();
        let modules = module_map(&paths);
        // helper is called but never imported: no edge even though exported.
        let a = file_with("a.py", &["b"], &["helper"]);
        let exports = BTreeMap::from([(
            "b.py".to_string(),
            BTreeMap::from([("helper".to_string(), vec!["sym:b.py::helper".to_string()])]),
        )]);
        let (edges, unresolved) = infer_file_edges(&a, &modules, &exports);
        assert_eq!(edges.len(), 1); // only the imports edge
        assert!(unresolved
            .iter()
            .any(|u| u.contains("no imported exporter")));
    }
}
