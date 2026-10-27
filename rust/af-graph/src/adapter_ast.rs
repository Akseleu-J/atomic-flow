//! Python AST adapter: source bytes -> structural graph facts (port of
//! `graph/adapter_ast.py`, roadmap P01). Extracted facts only — no inference,
//! no resolution. Edges emitted here are strictly `contains` (file -> symbol,
//! class -> method). Duplicate qualified names get a `#N` disambiguator like
//! the legacy tree, never a silent merge. Parser failure is reported in facts
//! (`parse_ok: false`), never raised.

use std::collections::{BTreeMap, BTreeSet};
use serde_json::{json, Map, Value};
use crate::schema::{all_fresh, edge_id, file_id, symbol_id};

pub const EXTRACTOR: &str = "python-ast/1";

struct LineIndex {
    starts: Vec<usize>,
}

impl LineIndex {
    fn new(source: &str) -> Self {
        let mut starts = vec![0usize];
        for (offset, byte) in source.bytes().enumerate() {
            if byte == b'\n' {
                starts.push(offset + 1);
            }
        }
        Self { starts }
    }

    /// 1-based line containing the byte offset (clamped to the last line).
    fn line_of(&self, offset: usize) -> usize {
        self.starts.partition_point(|&start| start <= offset)
    }
}

/// Build nodes/edges for one Python file.
pub fn adapt_file(path: &str, source: &[u8]) -> Value {
    let digest = {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(source);
        format!("{:x}", hasher.finalize())
    };
    let mut file_facts = Map::new();
    file_facts.insert("extractor".into(), json!(EXTRACTOR));
    file_facts.insert("parse_ok".into(), json!(true));
    file_facts.insert("imports".into(), json!([]));
    file_facts.insert("raw_calls".into(), json!([]));
    let mut file_node = json!({
        "id": file_id(path), "kind": "file", "path": path,
        "source": {"digest": digest, "algorithm": "sha256"},
        "facts": Value::Object(file_facts.clone()),
        "fact_status": all_fresh(&file_facts),
        "facts_class": "extracted",
    });
    let mut nodes: Vec<Value> = vec![file_node.clone()];
    let mut edges: Vec<Value> = Vec::new();

    let text = match std::str::from_utf8(source) {
        Ok(text) => text,
        Err(_) => return parse_failure(&mut file_node, "source is not valid UTF-8", &mut nodes),
    };
    let lines = LineIndex::new(text);
    let parsed = match rustpython_parser::ast::Suite::parse(text, "<af-graph>") {
        Ok(parsed) => parsed,
        Err(error) => {
            let line = lines.line_of(error.offset.to_u32() as usize);
            return parse_failure(&mut file_node, &format!("line {line}: {error}"), &mut nodes);
        }
    };
    let mut collector = Collector {
        path: path.to_string(),
        digest: digest.clone(),
        parent_symbol: String::new(),
        parent_id: file_id(path),
        seen: BTreeMap::new(),
        imports: BTreeSet::new(),
        raw_calls: BTreeSet::new(),
        lines,
        nodes: Vec::new(),
        edges: Vec::new(),
    };
    for stmt in parsed {
        collector.visit_stmt(stmt);
    }
    let facts = file_node["facts"].as_object_mut().expect("file facts object");
    facts.insert("imports".into(), json!(collector.imports.iter().collect::<Vec<_>>()));
    facts.insert("raw_calls".into(), json!(collector.raw_calls.iter().collect::<Vec<_>>()));
    file_node["fact_status"] = json!(all_fresh(facts));
    nodes[0] = file_node;
    nodes.extend(collector.nodes);
    edges.extend(collector.edges);
    json!({"nodes": nodes, "edges": edges})
}

fn parse_failure(file_node: &mut Value, message: &str, nodes: &mut Vec<Value>) -> Value {
    let facts = file_node["facts"].as_object_mut().expect("file facts object");
    facts.insert("parse_ok".into(), json!(false));
    facts.insert("parse_error".into(), json!(message));
    file_node["fact_status"] = json!(all_fresh(facts));
    nodes[0] = file_node.clone();
    json!({"nodes": nodes.clone(), "edges": Value::Array(Vec::new())})
}

struct Collector {
    path: String,
    digest: String,
    parent_symbol: String,
    parent_id: String,
    seen: BTreeMap<String, u32>,
    imports: BTreeSet<String>,
    raw_calls: BTreeSet<String>,
    lines: LineIndex,
    nodes: Vec<Value>,
    edges: Vec<Value>,
}

impl Collector {
    fn qualified(parent: &str, name: &str) -> String {
        if parent.is_empty() { name.to_string() } else { format!("{parent}.{name}") }
    }

    fn record_symbol(&mut self, name: &str, kind: &str, start: usize, end: usize, signature: String) {
        let qualified = Self::qualified(&self.parent_symbol, name);
        let seen = self.seen.entry(qualified.clone()).or_insert(0);
        *seen += 1;
        let disambiguator = *seen;
        let id = symbol_id(&self.path, &qualified, disambiguator);
        let mut facts = Map::new();
        facts.insert("extractor".into(), json!(EXTRACTOR));
        facts.insert("start_line".into(), json!(start));
        facts.insert("end_line".into(), json!(end));
        facts.insert("signature".into(), json!(signature));
        let node = json!({
            "id": id, "kind": kind, "path": self.path, "symbol": qualified,
            "source": {"digest": self.digest, "algorithm": "sha256"},
            "start_line": start, "end_line": end,
            "facts": Value::Object(facts.clone()),
            "fact_status": all_fresh(&facts),
            "facts_class": "extracted",
        });
        self.edges.push(json!({
            "id": edge_id(&self.parent_id, "contains", node["id"].as_str().unwrap_or_default()),
            "kind": "contains", "src": self.parent_id, "dst": node["id"],
            "status": "active",
            "facts": {"extractor": EXTRACTOR}, "fact_status": {"extractor": "fresh"},
            "facts_class": "extracted",
        }));
        self.nodes.push(node.clone());
        self.parent_symbol = node["symbol"].as_str().unwrap_or_default().to_string();
        self.parent_id = node["id"].as_str().unwrap_or_default().to_string();
    }
}

use rustpython_parser::ast::{self, Visitor, Ranged};
use rustpython_parser::Parse;

impl Visitor for Collector {
    fn visit_stmt(&mut self, node: ast::Stmt) {
        use ast::Stmt;
        let is_def = matches!(node, Stmt::FunctionDef(_) | Stmt::AsyncFunctionDef(_) | Stmt::ClassDef(_));
        let saved = (self.parent_symbol.clone(), self.parent_id.clone());
        if is_def {
            let (name, kind, signature) = match &node {
                Stmt::FunctionDef(f) => (f.name.to_string(), "function",
                    format!("def {}({})", f.name,
                        f.args.args.iter().map(|a| a.def.arg.to_string()).collect::<Vec<_>>().join(", "))),
                Stmt::AsyncFunctionDef(f) => (f.name.to_string(), "function",
                    format!("def {}({})", f.name,
                        f.args.args.iter().map(|a| a.def.arg.to_string()).collect::<Vec<_>>().join(", "))),
                Stmt::ClassDef(c) => (c.name.to_string(), "class", format!("class {}", c.name)),
                _ => unreachable!(),
            };
            self.record_symbol(&name, kind, self.lines.line_of(node.range().start().to_u32() as usize),
                self.lines.line_of(node.range().end().to_u32() as usize), signature);
        }
        match &node {
            Stmt::Import(s) => {
                for alias in &s.names {
                    self.imports.insert(alias.name.to_string());
                }
            }
            Stmt::ImportFrom(s) => {
                let prefix = s.module.as_ref().map(|m| m.to_string()).unwrap_or_default();
                for alias in &s.names {
                    let name = if prefix.is_empty() { alias.name.to_string() }
                        else { format!("{prefix}.{}", alias.name) };
                    self.imports.insert(name);
                }
            }
            _ => {}
        }
        self.generic_visit_stmt(node);
        if is_def {
            self.parent_symbol = saved.0;
            self.parent_id = saved.1;
        }
    }

    fn visit_expr(&mut self, node: ast::Expr) {
        if let ast::Expr::Call(call) = &node {
            let target = match &*call.func {
                ast::Expr::Name(name) => Some(name.id.to_string()),
                ast::Expr::Attribute(attr) => Some(attr.attr.to_string()),
                _ => None,
            };
            if let Some(target) = target {
                self.raw_calls.insert(target);
            }
        }
        self.generic_visit_expr(node);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::validate_node;

    fn source(b: &[u8]) -> Value {
        adapt_file("pkg/mod.py", b)
    }

    #[test]
    fn extracts_symbols_lines_and_contains_edges() {
        let payload = source(b"import os\nfrom . import x as y\n\nclass A:\n    def run(self):\n        return helper()\n");
        let nodes = payload["nodes"].as_array().unwrap();
        assert_eq!(nodes.len(), 3); // file, class A, function A.run
        let run = &nodes[2];
        validate_node(run).expect("valid symbol node");
        assert_eq!(run["symbol"], "A.run");
        assert_eq!(run["start_line"], 5);
        assert_eq!(run["end_line"], 6);
        assert_eq!(run["facts"]["signature"], "def run(self)");
        let edges = payload["edges"].as_array().unwrap();
        assert_eq!(edges.len(), 2); // file->A, A->A.run
        assert!(edges.iter().all(|e| e["kind"] == "contains" && e["facts_class"] == "extracted"));
    }

    #[test]
    fn duplicate_names_get_disambiguator() {
        let payload = source(b"def f(): pass\nclass C:\n    def f(): pass\nclass C:\n    def f(): pass\n");
        let symbols: Vec<&Value> = payload["nodes"].as_array().unwrap().iter()
            .filter(|n| n["kind"] != "file").collect();
        let ids: Vec<&str> = symbols.iter().map(|n| n["id"].as_str().unwrap()).collect();
        assert_eq!(ids.len(), 5);
        // Disambiguation keys on the raw qualified name: the second class is
        // C#2, and its method is C.f#2 — never a silent merge.
        for expected in ["sym:pkg/mod.py::f", "sym:pkg/mod.py::C", "sym:pkg/mod.py::C.f",
                         "sym:pkg/mod.py::C#2", "sym:pkg/mod.py::C.f#2"] {
            assert!(ids.contains(&expected), "missing {expected} in {ids:?}");
        }
    }

    #[test]
    fn facts_collect_sorted_deduped_imports_and_calls() {
        let payload = source(b"import os\nfrom a import b, b\ndef f():\n    helper()\n    helper()\n    obj.method()\n");
        let facts = &payload["nodes"][0]["facts"];
        assert_eq!(facts["imports"], json!(["a.b", "os"]));
        assert_eq!(facts["raw_calls"], json!(["helper", "method"]));
    }

    #[test]
    fn syntax_error_is_reported_not_raised() {
        let payload = source(b"def broken(:\n");
        let file_node = &payload["nodes"][0];
        assert_eq!(file_node["facts"]["parse_ok"], false);
        assert!(file_node["facts"]["parse_error"].as_str().unwrap().contains("line"));
        assert_eq!(payload["edges"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn async_and_nested_defs() {
        let payload = source(b"class Outer:\n    async def go(self):\n        def inner(): pass\n");
        let symbols: Vec<&Value> = payload["nodes"].as_array().unwrap().iter()
            .filter(|n| n["kind"] == "function").collect();
        assert_eq!(symbols.len(), 2);
        assert!(symbols.iter().any(|n| n["symbol"] == "Outer.go"));
        assert!(symbols.iter().any(|n| n["symbol"] == "Outer.go.inner"));
    }
}
