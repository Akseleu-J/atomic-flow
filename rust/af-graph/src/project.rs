//! Clean structural build over project files plus name-level reverse indexes
//! (port of `graph/project.py`). The reverse indexes are name matches,
//! deliberately NOT resolution; true call/import resolution lives in the
//! resolver and is what deltas recompute.

use crate::adapter_resource::adapt_source;
use crate::resolve::{exports_by_file, infer_file_edges, module_map};
use serde_json::{json, Value};
use std::collections::BTreeMap;

/// Full clean build: per-file adapters + resolver over all files.
pub fn clean_build(
    files: &BTreeMap<String, Vec<u8>>,
) -> Result<Value, crate::schema::GraphContractError> {
    let mut nodes: Vec<Value> = Vec::new();
    let mut edges: Vec<Value> = Vec::new();
    let mut reverse_imports: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut reverse_calls: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (path, bytes) in files {
        let payload = adapt_source(path, bytes)?;
        nodes.extend(payload["nodes"].as_array().cloned().unwrap_or_default());
        edges.extend(payload["edges"].as_array().cloned().unwrap_or_default());
        let file_node = &payload["nodes"].as_array().unwrap()[0];
        for name in file_node
            .pointer("/facts/imports")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(name) = name.as_str() {
                reverse_imports
                    .entry(name.to_string())
                    .or_default()
                    .push(path.clone());
            }
        }
        for name in file_node
            .pointer("/facts/raw_calls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let Some(name) = name.as_str() {
                reverse_calls
                    .entry(name.to_string())
                    .or_default()
                    .push(path.clone());
            }
        }
    }
    let mut exports: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for node in &nodes {
        let kind = node["kind"].as_str().unwrap_or_default();
        if kind == "class" || kind == "function" {
            let symbol = node["symbol"].as_str().unwrap_or_default();
            let leaf = symbol.rsplit('.').next().unwrap_or(symbol).to_string();
            exports
                .entry(leaf)
                .or_default()
                .push(node["id"].as_str().unwrap_or_default().to_string());
        }
    }
    let paths: Vec<String> = files.keys().cloned().collect();
    let modules = module_map(&paths);
    let by_file_exports = exports_by_file(&nodes);
    let mut unresolved: Vec<String> = Vec::new();
    for node in &nodes {
        if node["kind"].as_str() != Some("file") {
            continue;
        }
        let (inferred, items) = infer_file_edges(node, &modules, &by_file_exports);
        edges.extend(inferred);
        let path = node["path"].as_str().unwrap_or_default();
        unresolved.extend(items.into_iter().map(|item| format!("{path}: {item}")));
    }
    unresolved.sort();
    Ok(json!({
        "nodes": nodes, "edges": edges,
        "reverse": {"imports": reverse_imports, "calls": reverse_calls},
        "exports": exports,
        "parsed_files": files.len(),
        "unresolved": unresolved,
    }))
}

/// Advisory name-level blast radius; never treats a name match as a resolved edge.
pub fn affected_by_name(reverse: &Value, names: &[&str]) -> Vec<String> {
    let mut mentioned: BTreeMap<String, ()> = BTreeMap::new();
    for name in names {
        for kind in ["imports", "calls"] {
            for path in reverse[kind][*name].as_array().into_iter().flatten() {
                if let Some(path) = path.as_str() {
                    mentioned.insert(path.to_string(), ());
                }
            }
        }
    }
    mentioned.into_keys().collect()
}

/// Ignore patterns from `tree/defaults.py::DEFAULT_IGNORE` (subset in use).
pub const DEFAULT_IGNORE: [&str; 13] = [
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".atomic_flow",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.egg-info",
    "package-lock.json",
];

/// Extensions adapted by the structural graph: Python sources and UA
/// resources (config/document/pipeline).
pub const GRAPH_SUFFIXES: [&str; 7] = [".py", ".pyi", ".toml", ".json", ".yaml", ".yml", ".md"];

/// Bounded source collection for a clean build: regular files only, ignore
/// patterns and hidden entries skipped like the sandbox scan, byte cap per
/// file. Any read failure refuses the whole snapshot (same contract as the
/// Python `project_files`).
pub fn collect_source_files(root: &std::path::Path) -> Result<BTreeMap<String, Vec<u8>>, String> {
    if !root.is_absolute() {
        return Err("graph root must be an absolute path".into());
    }
    let mut files = BTreeMap::new();
    let mut stack = vec![root.to_path_buf()];
    let mut visited = 0usize;
    while let Some(folder) = stack.pop() {
        let entries =
            std::fs::read_dir(&folder).map_err(|error| format!("scan failed: {error}"))?;
        for entry in entries.filter_map(|e| e.ok()) {
            visited += 1;
            if visited > 200_000 {
                return Err("graph scan entry limit exceeded".into());
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') && name != ".github" {
                continue;
            }
            if DEFAULT_IGNORE
                .iter()
                .any(|pattern| project_glob(pattern, &name))
            {
                continue;
            }
            let path = entry.path();
            let file_type = entry
                .file_type()
                .map_err(|error| format!("scan failed: {error}"))?;
            if file_type.is_dir() {
                stack.push(path);
                continue;
            }
            if file_type.is_symlink() {
                return Err("graph snapshot requires regular source files".into());
            }
            let dot = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| format!(".{}", e.to_ascii_lowercase()));
            let Some(dot) = dot else { continue };
            if !GRAPH_SUFFIXES.contains(&dot.as_str()) || name.ends_with(".pyc") {
                continue;
            }
            let bytes = std::fs::read(&path)
                .map_err(|error| format!("read failed for {name:?}: {error}"))?;
            if bytes.len() > 1_000_000 {
                return Err(format!("source file {name:?} exceeds its byte cap"));
            }
            let rel = path
                .strip_prefix(root)
                .map_err(|_| "path escaped root".to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            files.insert(rel, bytes);
        }
    }
    Ok(files)
}

fn project_glob(pattern: &str, name: &str) -> bool {
    fn inner(p: &[u8], n: &[u8]) -> bool {
        match (p.first(), n.first()) {
            (None, None) => true,
            (Some(b'*'), _) => inner(&p[1..], n) || (!n.is_empty() && inner(p, &n[1..])),
            (Some(a), Some(b)) if a.eq_ignore_ascii_case(b) => inner(&p[1..], &n[1..]),
            _ => false,
        }
    }
    inner(pattern.as_bytes(), name.as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn files() -> BTreeMap<String, Vec<u8>> {
        BTreeMap::from([
            (
                "app.py".to_string(),
                b"from lib import helper\n\ndef main():\n    helper()\n".to_vec(),
            ),
            ("lib.py".to_string(), b"def helper(): pass\n".to_vec()),
        ])
    }

    #[test]
    fn clean_build_resolves_import_and_unique_call() {
        let build = clean_build(&files()).unwrap();
        assert_eq!(build["parsed_files"], json!(2));
        let edges = build["edges"].as_array().unwrap();
        assert!(edges
            .iter()
            .any(|e| e["kind"] == "imports" && e["dst"] == "file:lib.py"));
        assert!(edges
            .iter()
            .any(|e| e["kind"] == "calls" && e["dst"] == "sym:lib.py::helper"));
        assert!(build["unresolved"].as_array().unwrap().is_empty());
    }

    #[test]
    fn reverse_index_names_mentioning_files() {
        let build = clean_build(&files()).unwrap();
        let affected = affected_by_name(&build["reverse"], &["helper"]);
        assert_eq!(affected, vec!["app.py".to_string()]);
    }
}
