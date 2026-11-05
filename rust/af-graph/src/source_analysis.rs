//! Source-bound envelope shared with the TypeScript compiler sidecar.
//! No repository code or config is executed and no extracted fact is verification.
use crate::schema::GraphContractError;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const SCHEMA: &str = "source-analysis/1";

pub fn validate_path(path: &str) -> Result<(), GraphContractError> {
    if path.is_empty()
        || path.len() > 512
        || path.contains(['\\', ':', '\0'])
        || path
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err(GraphContractError(
            "confined POSIX source path required".into(),
        ));
    }
    Ok(())
}

pub fn analyze(
    root_id: &str,
    source_revision: &str,
    files: &BTreeMap<String, Vec<u8>>,
) -> Result<Value, GraphContractError> {
    if root_id.is_empty()
        || root_id.len() > 128
        || !root_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        || source_revision.len() != 64
        || !source_revision
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || files.is_empty()
        || files.len() > 256
        || files.values().map(Vec::len).sum::<usize>() > 8_000_000
    {
        return Err(GraphContractError(
            "bounded root/revision source context required".into(),
        ));
    }
    let mut sources = Vec::new();
    let mut nodes = Vec::new();
    let mut edges = Vec::new();
    let mut imports = Vec::new();
    let mut omissions = Vec::new();
    for (path, raw) in files {
        validate_path(path)?;
        if raw.len() > 1_000_000 {
            return Err(GraphContractError("source byte ceiling exceeded".into()));
        }
        let hash = format!("{:x}", Sha256::digest(raw));
        sources.push(json!({"path":path,"sha256":hash}));
        let language = if path.ends_with(".rs") {
            "rust"
        } else if path.ends_with(".py") || path.ends_with(".pyi") {
            "python"
        } else {
            "resource"
        };
        let payload = crate::adapter_resource::adapt_source(path, raw)?;
        for record in payload["nodes"][0]["facts"]["import_records"]
            .as_array()
            .into_iter()
            .flatten()
        {
            imports.push(json!({"path":path,"source_sha256":hash,"record":record}));
        }
        nodes.extend(payload["nodes"].as_array().cloned().unwrap_or_default());
        edges.extend(payload["edges"].as_array().cloned().unwrap_or_default());
        if language == "rust" {
            omissions.push(json!({"path":path,"reason":"rust_syntax_only_no_macro_cfg_type_or_call_resolution"}));
        }
    }
    Ok(
        json!({"schema":SCHEMA,"root_id":root_id,"source_revision":source_revision,
        "sources":sources,"nodes":nodes,"edges":edges,"imports":imports,"omissions":omissions,
        "verification":"not_assessed"}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn binds_bytes_and_revision_with_no_verification_upgrade() {
        let files = BTreeMap::from([("src/lib.rs".into(), b"fn refund() {}\n".to_vec())]);
        let result = analyze("main", &"a".repeat(64), &files).unwrap();
        assert_eq!(result["schema"], json!(SCHEMA));
        assert_eq!(result["source_revision"], json!("a".repeat(64)));
        assert_eq!(result["verification"], json!("not_assessed"));
        assert_eq!(
            result["nodes"][0]["source"]["digest"],
            result["sources"][0]["sha256"]
        );
    }
    #[test]
    fn no_platform_path_reinterpretation() {
        for p in ["/x.rs", "../x.rs", "a//b.rs", "C:/x.rs", "a\\b.rs"] {
            assert!(validate_path(p).is_err());
        }
        assert!(validate_path("nested/source.rs").is_ok());
    }
}
