//! Source-only adapters for UA config/document/pipeline resources (port of
//! `graph/adapter_resource.py`). No YAML constructors, templates, workflow
//! steps or repository code are executed. Classification is path-based.

use crate::adapter_ast;
use crate::schema::{all_fresh, edge_id, file_id, symbol_id, GraphContractError};
use serde_json::{json, Map, Value};

pub const RESOURCE_EXTRACTOR: &str = "resource-metadata/1";

pub fn resource_kind(path: &str) -> Option<&'static str> {
    let suffix: String = path.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    let dot = path.rfind('.').map(|i| &path[i..]).unwrap_or("");
    if path.starts_with(".github/workflows/") && (dot == ".yaml" || dot == ".yml") {
        return Some("pipeline");
    }
    if matches!(
        dot.to_ascii_lowercase().as_str(),
        ".toml" | ".json" | ".yaml" | ".yml"
    ) {
        return Some("config");
    }
    if dot.to_ascii_lowercase() == ".md" {
        return Some("document");
    }
    let _ = suffix;
    None
}

/// Dispatch: Python sources go to the AST adapter, resources to the metadata
/// adapter. Unsupported paths are refused — never silently file-noded.
pub fn adapt_source(path: &str, source: &[u8]) -> Result<Value, GraphContractError> {
    if path.ends_with(".rs") {
        return crate::adapter_rust::adapt_file(path, source);
    }
    if path.ends_with(".py") || path.ends_with(".pyi") {
        return Ok(adapter_ast::adapt_file(path, source));
    }
    let Some(kind) = resource_kind(path) else {
        return Err(GraphContractError(format!(
            "unsupported source adapter: {path}"
        )));
    };
    let text = std::str::from_utf8(source)
        .map_err(|_| GraphContractError("resource must be bounded UTF-8 source".into()))?;
    if source.contains(&0u8) || source.len() > 1_000_000 {
        return Err(GraphContractError(
            "resource must be bounded UTF-8 source".into(),
        ));
    }
    let digest = {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(source);
        format!("{:x}", hasher.finalize())
    };
    let suffix = path
        .rfind('.')
        .map(|i| path[i + 1..].to_ascii_lowercase())
        .unwrap_or_default();
    let mut facts = Map::new();
    facts.insert("extractor".into(), json!(RESOURCE_EXTRACTOR));
    facts.insert("imports".into(), json!([]));
    facts.insert("raw_calls".into(), json!([]));
    facts.insert("parse_ok".into(), json!(null));
    facts.insert("format".into(), json!(suffix));
    facts.insert("classification".into(), json!("path-based"));
    facts.insert("semantic_status".into(), json!("not_assessed"));
    if path.ends_with(".json") {
        let parse_ok = serde_json::from_str::<serde_json::Value>(text).is_ok();
        facts.insert("parse_ok".into(), json!(parse_ok));
    }
    let end_line = text.lines().count().max(1);
    let owner = file_id(path);
    let identifier = symbol_id(path, kind, 1);
    let file_node = json!({
        "id": owner, "kind": "file", "path": path,
        "source": {"digest": digest, "algorithm": "sha256"},
        "facts": Value::Object(facts.clone()),
        "fact_status": all_fresh(&facts), "facts_class": "extracted",
    });
    let mut resource_facts = Map::new();
    resource_facts.insert("extractor".into(), json!(RESOURCE_EXTRACTOR));
    resource_facts.insert("semantic_status".into(), json!("not_assessed"));
    let mut resource_status = all_fresh(&resource_facts);
    resource_status.insert("semantic_status".into(), json!("fresh"));
    let resource = json!({
        "id": identifier, "kind": kind, "symbol": kind, "path": path,
        "source": {"digest": digest, "algorithm": "sha256"},
        "start_line": 1, "end_line": end_line,
        "facts": Value::Object(resource_facts),
        "fact_status": resource_status, "facts_class": "extracted",
    });
    let edge = json!({
        "id": edge_id(&owner, "contains", &identifier), "kind": "contains",
        "src": owner, "dst": identifier, "status": "active",
        "facts": {"extractor": RESOURCE_EXTRACTOR}, "fact_status": {"extractor": "fresh"},
        "facts_class": "extracted",
    });
    Ok(json!({"nodes": [file_node, resource], "edges": [edge]}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::validate_node;

    #[test]
    fn classification_is_path_based() {
        assert_eq!(resource_kind(".github/workflows/ci.yml"), Some("pipeline"));
        assert_eq!(resource_kind("config.toml"), Some("config"));
        assert_eq!(resource_kind("data.JSON"), Some("config"));
        assert_eq!(resource_kind("README.md"), Some("document"));
        assert_eq!(resource_kind("script.py"), None);
        assert_eq!(resource_kind("blob.bin"), None);
    }

    #[test]
    fn resource_emits_file_and_kind_symbol() {
        let payload = adapt_source("cfg/app.toml", b"[a]\nb = 1\n").expect("toml adapted");
        let nodes = payload["nodes"].as_array().unwrap();
        validate_node(&nodes[0]).expect("file node valid");
        validate_node(&nodes[1]).expect("resource node valid");
        assert_eq!(nodes[1]["kind"], "config");
        assert_eq!(nodes[0]["facts"]["parse_ok"], Value::Null);
        let edges = payload["edges"].as_array().unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0]["kind"], "contains");
    }

    #[test]
    fn json_parse_ok_is_a_fact_not_an_error() {
        let good = adapt_source("x.json", b"{\"a\": 1}").unwrap();
        assert_eq!(good["nodes"][0]["facts"]["parse_ok"], json!(true));
        let bad = adapt_source("x.json", b"{oops").unwrap();
        assert_eq!(bad["nodes"][0]["facts"]["parse_ok"], json!(false));
    }

    #[test]
    fn python_dispatches_to_ast_and_unknown_refused() {
        let py = adapt_source("m.py", b"def f(): pass\n").unwrap();
        assert_eq!(py["nodes"][0]["facts"]["extractor"], "python-ast/1");
        assert!(adapt_source("m.txt", b"hi").is_err());
    }

    #[test]
    fn nul_bytes_and_oversize_refused() {
        assert!(adapt_source("x.md", b"a\0b").is_err());
        assert!(adapt_source("x.md", &[b'a'; 1_000_001]).is_err());
    }
}
