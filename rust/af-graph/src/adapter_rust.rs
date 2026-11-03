//! Bounded Rust syntax extraction with syn; never cargo, macro expansion or execution.
use crate::schema::{all_fresh, edge_id, file_id, symbol_id, GraphContractError};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use syn::spanned::Spanned;

pub const EXTRACTOR: &str = "rust-syn/1";

pub fn adapt_file(path: &str, source: &[u8]) -> Result<Value, GraphContractError> {
    super::source_analysis::validate_path(path)?;
    if !path.ends_with(".rs") || source.len() > 1_000_000 || source.contains(&0) {
        return Err(GraphContractError("bounded Rust source required".into()));
    }
    let text = std::str::from_utf8(source)
        .map_err(|_| GraphContractError("Rust source must be UTF-8".into()))?;
    let digest = format!("{:x}", Sha256::digest(source));
    let parsed = syn::parse_file(text);
    let mut facts = Map::from_iter([
        ("extractor".into(), json!(EXTRACTOR)),
        ("language".into(), json!("rust")),
        ("parse_ok".into(), json!(parsed.is_ok())),
        ("imports".into(), json!([])),
        ("raw_calls".into(), json!([])),
        ("semantic_status".into(), json!("not_assessed")),
        (
            "capabilities".into(),
            json!(["syntax", "named_items", "inline_modules", "use_syntax"]),
        ),
        (
            "omissions".into(),
            json!([
                "macro_expansion",
                "cfg_evaluation",
                "external_modules",
                "type_resolution",
                "call_resolution",
                "trait_impl_methods",
                "non_ascii_identifiers",
                "type_aliases_constants_and_statics"
            ]),
        ),
    ]);
    let owner = file_id(path);
    let mut collector = Collector {
        path,
        digest: &digest,
        nodes: vec![],
        edges: vec![],
        imports: vec![],
        seen: BTreeMap::new(),
        limit_exceeded: false,
    };
    match parsed {
        Ok(file) => collector.items(&file.items, "", &owner),
        Err(error) => {
            facts.insert("parse_error".into(), json!(error.to_string()));
        }
    }
    if collector.limit_exceeded {
        return Err(GraphContractError(
            "Rust structural ceiling exceeded".into(),
        ));
    }
    facts.insert("import_records".into(), json!(collector.imports));
    let file = json!({"id": owner, "kind":"file", "path":path,
        "source":{"digest":digest,"algorithm":"sha256"}, "fact_status":all_fresh(&facts),
        "facts":facts,"facts_class":"extracted"});
    let mut nodes = vec![file];
    nodes.extend(collector.nodes);
    Ok(json!({"nodes":nodes,"edges":collector.edges}))
}

struct Collector<'a> {
    path: &'a str,
    digest: &'a str,
    nodes: Vec<Value>,
    edges: Vec<Value>,
    imports: Vec<Value>,
    seen: BTreeMap<String, u32>,
    limit_exceeded: bool,
}

impl Collector<'_> {
    fn record(
        &mut self,
        name: &str,
        kind: &str,
        syntax_kind: &str,
        span: proc_macro2::Span,
        parent: &str,
        owner: &str,
    ) -> (String, String) {
        let name = name.strip_prefix("r#").unwrap_or(name);
        let qualified = if parent.is_empty() {
            name.into()
        } else {
            format!("{parent}.{name}")
        };
        let count = self.seen.entry(qualified.clone()).or_insert(0);
        *count += 1;
        let id = symbol_id(self.path, &qualified, *count);
        let facts = Map::from_iter([
            ("extractor".into(), json!(EXTRACTOR)),
            ("language".into(), json!("rust")),
            ("syntax_kind".into(), json!(syntax_kind)),
            ("semantic_status".into(), json!("not_assessed")),
        ]);
        self.nodes.push(
            json!({"id":id,"kind":kind,"path":self.path,"symbol":qualified,
            "source":{"digest":self.digest,"algorithm":"sha256"},
            "start_line":span.start().line,"end_line":span.end().line,
            "fact_status":all_fresh(&facts),"facts":facts,"facts_class":"extracted"}),
        );
        self.edges.push(
            json!({"id":edge_id(owner,"contains",&id),"kind":"contains","src":owner,"dst":id,
            "status":"active","facts":{"extractor":EXTRACTOR},
            "fact_status":{"extractor":"fresh"},"facts_class":"extracted"}),
        );
        (qualified, id)
    }

    fn items(&mut self, items: &[syn::Item], parent: &str, owner: &str) {
        if parent.matches('.').count() > 128 {
            self.limit_exceeded = true;
            return;
        }
        for item in items {
            if self.nodes.len() >= 10_000 || self.imports.len() >= 20_000 {
                self.limit_exceeded = true;
                return;
            }
            let ident = match item {
                syn::Item::Fn(f) => Some(&f.sig.ident),
                syn::Item::Struct(s) => Some(&s.ident),
                syn::Item::Enum(e) => Some(&e.ident),
                syn::Item::Trait(t) => Some(&t.ident),
                syn::Item::Mod(m) => Some(&m.ident),
                _ => None,
            };
            if let Some(ident) = ident {
                let text = ident.to_string();
                let name = text.strip_prefix("r#").unwrap_or(&text);
                if !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
                    continue;
                }
            }
            match item {
                syn::Item::Fn(f) => {
                    self.record(
                        &f.sig.ident.to_string(),
                        "function",
                        "function",
                        f.span(),
                        parent,
                        owner,
                    );
                }
                syn::Item::Struct(s) => {
                    self.record(
                        &s.ident.to_string(),
                        "class",
                        "struct",
                        s.span(),
                        parent,
                        owner,
                    );
                }
                syn::Item::Enum(e) => {
                    self.record(
                        &e.ident.to_string(),
                        "class",
                        "enum",
                        e.span(),
                        parent,
                        owner,
                    );
                }
                syn::Item::Trait(t) => {
                    self.record(
                        &t.ident.to_string(),
                        "class",
                        "trait",
                        t.span(),
                        parent,
                        owner,
                    );
                }
                syn::Item::Mod(m) => {
                    let (qualified, id) = self.record(
                        &m.ident.to_string(),
                        "module",
                        "module",
                        m.span(),
                        parent,
                        owner,
                    );
                    if let Some((_, items)) = &m.content {
                        self.items(items, &qualified, &id);
                    }
                }
                syn::Item::Use(u) => {
                    self.use_tree(&u.tree, String::new(), u.leading_colon.is_some(), u.span())
                }
                _ => {} // Excluded syntax is not an empty semantic proof.
            }
        }
    }

    fn use_tree(
        &mut self,
        tree: &syn::UseTree,
        prefix: String,
        absolute: bool,
        span: proc_macro2::Span,
    ) {
        if self.imports.len() >= 20_000 {
            self.limit_exceeded = true;
            return;
        }
        match tree {
            syn::UseTree::Path(p) => {
                self.use_tree(&p.tree, format!("{prefix}{}::", p.ident), absolute, span)
            }
            syn::UseTree::Group(g) => {
                for item in &g.items {
                    self.use_tree(item, prefix.clone(), absolute, span);
                }
            }
            leaf => {
                let (specifier, alias, glob) = match leaf {
                    syn::UseTree::Name(n) => (format!("{prefix}{}", n.ident), None, false),
                    syn::UseTree::Rename(r) => (
                        format!("{prefix}{}", r.ident),
                        Some(r.rename.to_string()),
                        false,
                    ),
                    syn::UseTree::Glob(_) => (format!("{prefix}*"), None, true),
                    _ => unreachable!(),
                };
                self.imports.push(json!({"specifier":specifier,"alias":alias,"glob":glob,"absolute":absolute,
                    "start_line":span.start().line,"end_line":span.end().line,"resolution":"not_assessed"}));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn extracts_syntax_without_claiming_macro_or_type_resolution() {
        let p = adapt_file(
            "src/lib.rs",
            b"use std::{io, fmt::Debug as D};\nmod nested { pub fn refund() {} }\nstruct Budget;\n",
        )
        .unwrap();
        assert_eq!(p["nodes"].as_array().unwrap().len(), 4);
        assert_eq!(
            p["nodes"][0]["facts"]["import_records"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(p["nodes"][2]["symbol"], json!("nested.refund"));
        for node in p["nodes"].as_array().unwrap() {
            crate::schema::validate_node(node).unwrap();
        }
        assert_eq!(
            p["nodes"][0]["facts"]["semantic_status"],
            json!("not_assessed")
        );
    }
    #[test]
    fn invalid_source_retains_hash_and_explicit_failure() {
        let p = adapt_file("lib.rs", b"fn broken(").unwrap();
        assert_eq!(p["nodes"][0]["facts"]["parse_ok"], json!(false));
        assert_eq!(p["nodes"].as_array().unwrap().len(), 1);
        assert!(adapt_file("../outside.rs", b"fn x() {}").is_err());
    }
}
