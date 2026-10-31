//! Deterministic semantic slice (port of the P03 deterministic core from
//! `graph/semantic.py`): layer assignment per file path and per-symbol
//! deterministic summaries. No LLM here — these are extracted-fact
//! summaries; model-generated semantics remain the P03 milestone above.

use serde_json::{json, Value};

/// Layer assignment by path, mirroring the draft's deterministic rules:
/// entry points (main/cli), tests, docs, config; everything else is core.
pub fn layer_for(path: &str) -> &'static str {
    let lowered = path.to_lowercase();
    let file_name = path.rsplit('/').next().unwrap_or(path);
    if lowered.ends_with(".md") || lowered.starts_with("docs/") {
        return "doc";
    }
    if lowered.ends_with(".toml")
        || lowered.ends_with(".json")
        || lowered.ends_with(".yaml")
        || lowered.ends_with(".yml")
    {
        return "config";
    }
    if lowered.contains("test") || lowered.contains("fixture") {
        return "test";
    }
    if file_name == "main.py" || file_name == "__main__.py" || file_name == "cli.py" {
        return "entry";
    }
    "core"
}

/// Deterministic file summary from extracted facts: symbols and imports
/// counts, plus the layer. No model text is invented.
pub fn file_summary(file_node: &Value) -> Value {
    let facts = file_node.get("facts").cloned().unwrap_or_else(|| json!({}));
    let imports = facts
        .get("imports")
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or(0);
    let calls = facts
        .get("raw_calls")
        .and_then(Value::as_array)
        .map(|items| items.len())
        .unwrap_or(0);
    let path = file_node["path"].as_str().unwrap_or_default();
    json!({
        "path": path,
        "layer": layer_for(path),
        "parse_ok": facts.get("parse_ok").cloned().unwrap_or(json!(null)),
        "imports_count": imports,
        "raw_calls_count": calls,
        "summary": format!(
            "{} file; {} imports, {} raw calls",
            layer_for(path),
            imports,
            calls
        ),
        "summary_kind": "deterministic-facts",
    })
}

/// Assign layers to every file node of a graph document (in a copy) and
/// attach deterministic summaries as facts. Symbol nodes are untouched.
pub fn annotate(document: &Value) -> Value {
    let mut result = document.clone();
    if let Some(nodes) = result["nodes"].as_array_mut() {
        for node in nodes.iter_mut() {
            if node["kind"] == json!("file") {
                let summary = file_summary(node);
                if let Some(facts) = node.get_mut("facts").and_then(Value::as_object_mut) {
                    facts.insert("layer".into(), summary["layer"].clone());
                    facts.insert("summary".into(), summary["summary"].clone());
                    facts.insert("summary_kind".into(), json!("deterministic-facts"));
                }
            }
        }
    }
    result
}

/// Entry points of the graph: files whose raw_calls mention `main` or whose
/// path marks them as entry points.
pub fn entry_points(document: &Value) -> Vec<String> {
    let mut entries: Vec<String> = Vec::new();
    for node in document["nodes"].as_array().cloned().unwrap_or_default() {
        if node["kind"] != json!("file") {
            continue;
        }
        let path = node["path"].as_str().unwrap_or_default();
        if layer_for(path) == "entry" {
            entries.push(path.to_string());
            continue;
        }
        let calls_main = node
            .pointer("/facts/raw_calls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .any(|call| call == "main");
        if calls_main {
            entries.push(path.to_string());
        }
    }
    entries.sort();
    entries.dedup();
    entries
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn layers_follow_path_rules() {
        assert_eq!(layer_for("src/main.py"), "entry");
        assert_eq!(layer_for("src/cli.py"), "entry");
        assert_eq!(layer_for("app/__main__.py"), "entry");
        assert_eq!(layer_for("tests/test_scheduler.py"), "test");
        assert_eq!(layer_for("docs/guide.md"), "doc");
        assert_eq!(layer_for("config.toml"), "config");
        assert_eq!(layer_for("src/scheduler.py"), "core");
    }

    #[test]
    fn file_summary_is_deterministic_facts() {
        let node = json!({
            "kind": "file", "path": "src/scheduler.py",
            "facts": {"parse_ok": true, "imports": ["a", "b"], "raw_calls": ["run"]},
        });
        let summary = file_summary(&node);
        assert_eq!(summary["layer"], json!("core"));
        assert_eq!(summary["imports_count"], json!(2));
        assert!(summary["summary"].as_str().unwrap().contains("2 imports"));
        assert_eq!(summary["summary_kind"], json!("deterministic-facts"));
    }

    #[test]
    fn annotate_touches_file_nodes_only() {
        let document = json!({"nodes": [
            {"id": "file:a.py", "kind": "file", "path": "a.py",
             "facts": {"parse_ok": true}, "fact_status": {}},
            {"id": "sym:a.py::f", "kind": "function", "path": "a.py", "symbol": "f",
             "facts": {"signature": "def f()"}, "fact_status": {}},
        ], "edges": []});
        let annotated = annotate(&document);
        let file_node = &annotated["nodes"][0];
        assert_eq!(file_node["facts"]["layer"], json!("core"));
        assert_eq!(
            file_node["facts"]["summary_kind"],
            json!("deterministic-facts")
        );
        // Symbol node untouched.
        assert!(annotated["nodes"][1]["facts"].get("layer").is_none());
    }

    #[test]
    fn entry_points_from_paths_and_main_calls() {
        let document = json!({"nodes": [
            {"id": "file:src/main.py", "kind": "file", "path": "src/main.py",
             "facts": {"raw_calls": ["run"]}},
            {"id": "file:src/service.py", "kind": "file", "path": "src/service.py",
             "facts": {"raw_calls": ["main"]}},
            {"id": "file:src/lib.py", "kind": "file", "path": "src/lib.py",
             "facts": {"raw_calls": ["helper"]}},
        ], "edges": []});
        assert_eq!(
            entry_points(&document),
            vec!["src/main.py", "src/service.py"]
        );
    }
}
