//! af-context: retrieval and context assembly for the model plane (port of
//! `activation/`). Lexical scoring over an inverted index with field weights,
//! blended with personalized PageRank, plus a byte-budgeted context pack and
//! an Aider-style repo map — every included item carries its inclusion
//! reason (the P04 contract). A weak match is reported as weak; it is never
//! dressed up as confidence.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// Build a context projection only from a matching source-analysis/1 envelope.
/// Root/revision/hash binding is integrity context, never proof of correctness.
pub fn source_bound_context_pack(
    analysis: &Value,
    query: &str,
    byte_budget: usize,
) -> Result<Value, String> {
    if analysis["schema"] != json!(af_graph::source_analysis::SCHEMA)
        || analysis["verification"] != json!("not_assessed")
    {
        return Err("source-analysis/1 envelope required".into());
    }
    let root = analysis["root_id"]
        .as_str()
        .ok_or("root identity required")?;
    let revision = analysis["source_revision"]
        .as_str()
        .ok_or("source revision required")?;
    if root.is_empty()
        || revision.len() != 64
        || !revision
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("exact root/revision binding required".into());
    }
    let mut sources = BTreeMap::new();
    for source in analysis["sources"]
        .as_array()
        .ok_or("source inventory required")?
    {
        let path = source["path"].as_str().ok_or("source path required")?;
        af_graph::source_analysis::validate_path(path).map_err(|e| e.0)?;
        let hash = source["sha256"].as_str().ok_or("source hash required")?;
        if hash.len() != 64
            || !hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || sources.insert(path.to_string(), hash.to_string()).is_some()
        {
            return Err("duplicate or invalid source hash".into());
        }
    }
    let mut identities = BTreeSet::new();
    for node in analysis["nodes"].as_array().ok_or("nodes required")? {
        af_graph::schema::validate_node(node).map_err(|e| e.0)?;
        if node["source"]["algorithm"] != json!("sha256")
            || node["path"]
                .as_str()
                .and_then(|p| sources.get(p))
                .map(String::as_str)
                != node["source"]["digest"].as_str()
        {
            return Err("context node source binding differs".into());
        }
        if !identities.insert(
            node["id"]
                .as_str()
                .ok_or("node identity required")?
                .to_string(),
        ) {
            return Err("duplicate node identity".into());
        }
    }
    for edge in analysis["edges"].as_array().ok_or("edges required")? {
        af_graph::schema::validate_edge(edge).map_err(|e| e.0)?;
        if !identities.contains(edge["src"].as_str().ok_or("edge source required")?)
            || !identities.contains(edge["dst"].as_str().ok_or("edge target required")?)
        {
            return Err("context edge endpoint is outside source-bound inventory".into());
        }
    }
    let mut pack = context_pack(&ContextIndex::build(analysis), query, byte_budget, None);
    for item in pack["items"]
        .as_array_mut()
        .ok_or("context items required")?
    {
        let hash = item["path"]
            .as_str()
            .and_then(|p| sources.get(p))
            .ok_or("context source missing")?
            .clone();
        item["source_sha256"] = json!(hash);
    }
    pack["root_id"] = json!(root);
    pack["source_revision"] = json!(revision);
    pack["verification"] = json!("not_assessed");
    pack["omissions"] = analysis["omissions"].clone();
    pack["byte_budget_scope"] = json!(
        "selection entries before source-binding metadata; not token or complete response cap"
    );
    Ok(pack)
}

const STOP_WORDS: [&str; 18] = [
    "the", "a", "an", "of", "for", "and", "to", "in", "is", "are", "on", "at", "by", "with",
    "this", "that", "from", "as",
];

/// Split camelCase and snake_case identifiers into lowercase parts.
pub fn split_identifier(value: &str) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut previous_kind = 'x'; // 'l' lower, 'u' upper, 'd' digit, 'x' other
    for character in value.chars() {
        let kind = if character.is_ascii_lowercase() {
            'l'
        } else if character.is_ascii_uppercase() {
            'u'
        } else if character.is_ascii_digit() {
            'd'
        } else {
            'x'
        };
        let boundary = match (previous_kind, kind) {
            // Upper→lower and upper→upper continue the current word
            // (TaskScheduler, HTTPServer); everything else is a boundary.
            ('u', 'l') => false,
            ('u', 'u') => false,
            ('l', 'l') => false,
            ('d', 'd') => false,
            _ => true,
        };
        if boundary && !current.is_empty() {
            parts.push(current.to_lowercase());
            current.clear();
        }
        if character.is_ascii_alphanumeric() {
            current.push(character);
        }
        previous_kind = kind;
    }
    if !current.is_empty() {
        parts.push(current.to_lowercase());
    }
    parts.retain(|part| !part.is_empty());
    parts
}

/// Minimal suffix stemming (the Python draft's "simple stemming").
pub fn stem(word: &str) -> String {
    for suffix in [
        "ations", "ation", "ities", "ingly", "ments", "ness", "ings", "ing", "ies", "ers", "ed",
        "es", "er", "ly", "s",
    ] {
        if word.len() > suffix.len() + 2 && word.ends_with(suffix) {
            let stem = &word[..word.len() - suffix.len()];
            return match suffix {
                "ies" => format!("{stem}y"),
                _ => stem.to_string(),
            };
        }
    }
    word.to_string()
}

pub fn tokenize(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    for raw in text.split(|c: char| !c.is_ascii_alphanumeric()) {
        for part in split_identifier(raw) {
            if part.len() < 2 || STOP_WORDS.contains(&part.as_str()) {
                continue;
            }
            tokens.push(stem(&part));
        }
    }
    tokens
}

/// One indexed graph node with weighted field tokens.
#[derive(Debug, Clone)]
struct Entry {
    id: String,
    kind: String,
    path: String,
    symbol: Option<String>,
    signature: Option<String>,
    lines: Option<(usize, usize)>,
    tokens: BTreeMap<String, f64>,
    neighbors: Vec<String>,
    is_test: bool,
}

/// Retrieval index over a graph document (nodes + edges from the store).
pub struct ContextIndex {
    entries: Vec<Entry>,
    by_id: BTreeMap<String, usize>,
}

/// Field weights mirroring the Python activation contract:
/// symbol×3, path/signature×1.5, summary×1, callees×0.7, imports×0.4.
const W_SYMBOL: f64 = 3.0;
const W_PATH: f64 = 1.5;
const W_SIGNATURE: f64 = 1.5;
const W_SUMMARY: f64 = 1.0;
const W_CALLEES: f64 = 0.7;
const W_IMPORTS: f64 = 0.4;

impl ContextIndex {
    pub fn build(document: &Value) -> ContextIndex {
        let mut entries: Vec<Entry> = Vec::new();
        let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
        for node in document["nodes"].as_array().cloned().unwrap_or_default() {
            let id = node["id"].as_str().unwrap_or_default().to_string();
            let kind = node["kind"].as_str().unwrap_or_default().to_string();
            let path = node["path"].as_str().unwrap_or_default().to_string();
            let symbol = node
                .get("symbol")
                .and_then(Value::as_str)
                .map(str::to_string);
            let signature = node
                .pointer("/facts/signature")
                .and_then(Value::as_str)
                .map(str::to_string);
            let summary = node
                .pointer("/facts/summary")
                .and_then(Value::as_str)
                .map(str::to_string);
            let lines = match (
                node.get("start_line").and_then(Value::as_u64),
                node.get("end_line").and_then(Value::as_u64),
            ) {
                (Some(start), Some(end)) => Some((start as usize, end as usize)),
                _ => None,
            };
            let mut tokens: BTreeMap<String, f64> = BTreeMap::new();
            let add = |text: &str, weight: f64, tokens: &mut BTreeMap<String, f64>| {
                for token in tokenize(text) {
                    *tokens.entry(token).or_insert(0.0) += weight;
                }
            };
            if let Some(symbol) = &symbol {
                add(symbol, W_SYMBOL, &mut tokens);
            }
            add(&path, W_PATH, &mut tokens);
            if let Some(signature) = &signature {
                add(signature, W_SIGNATURE, &mut tokens);
            }
            if let Some(summary) = &summary {
                add(summary, W_SUMMARY, &mut tokens);
            }
            for import in node
                .pointer("/facts/imports")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                if let Some(name) = import.as_str() {
                    add(name, W_IMPORTS, &mut tokens);
                }
            }
            for call in node
                .pointer("/facts/raw_calls")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                if let Some(name) = call.as_str() {
                    add(name, W_CALLEES, &mut tokens);
                }
            }
            let lowered = path.to_lowercase();
            let is_test = lowered.contains("test")
                || lowered.contains("bench")
                || lowered.contains("archive")
                || lowered.contains("fixture");
            let index = entries.len();
            by_id.insert(id.clone(), index);
            entries.push(Entry {
                id,
                kind,
                path,
                symbol,
                signature,
                lines,
                tokens,
                neighbors: Vec::new(),
                is_test,
            });
        }
        let mut index = ContextIndex { entries, by_id };
        for edge in document["edges"].as_array().cloned().unwrap_or_default() {
            let src = edge["src"].as_str().unwrap_or_default().to_string();
            let dst = edge["dst"].as_str().unwrap_or_default().to_string();
            let (Some(a), Some(b)) = (
                index.by_id.get(&src).copied(),
                index.by_id.get(&dst).copied(),
            ) else {
                continue;
            };
            index.entries[a].neighbors.push(dst);
            index.entries[b].neighbors.push(src);
        }
        index
    }

    /// Personalized PageRank over the graph edges (uniform reset).
    pub fn pagerank(&self, damping: f64, iterations: usize) -> BTreeMap<String, f64> {
        let n = self.entries.len();
        if n == 0 {
            return BTreeMap::new();
        }
        let reset = 1.0 / n as f64;
        let mut ranks = vec![reset; n];
        for _ in 0..iterations {
            let mut next = vec![reset; n];
            for (index, entry) in self.entries.iter().enumerate() {
                if entry.neighbors.is_empty() {
                    continue;
                }
                let share = damping * ranks[index] / entry.neighbors.len() as f64;
                for neighbor in &entry.neighbors {
                    if let Some(&target) = self.by_id.get(neighbor) {
                        next[target] += share;
                    }
                }
            }
            let total: f64 = next.iter().sum();
            if total > 0.0 {
                for value in &mut next {
                    *value /= total;
                }
            }
            ranks = next;
        }
        self.entries
            .iter()
            .enumerate()
            .map(|(index, entry)| (entry.id.clone(), ranks[index]))
            .collect()
    }

    /// Lexical search blended with PageRank (0.75/0.25), one-hop spreading,
    /// exact-symbol prior and test-path penalty. Weak coverage is reported.
    pub fn search(
        &self,
        query: &str,
        k: usize,
        graph_bias: Option<&BTreeMap<String, f64>>,
    ) -> Value {
        let query_terms: BTreeSet<String> = tokenize(query).into_iter().collect();
        let mut scored: Vec<(usize, f64, usize)> = Vec::new(); // index, lexical, covered terms
        for (index, entry) in self.entries.iter().enumerate() {
            let mut score = 0.0f64;
            let mut covered = 0usize;
            for term in &query_terms {
                let Some(weight) = entry.tokens.get(term) else {
                    continue;
                };
                covered += 1;
                score += weight;
            }
            if covered == 0 {
                continue;
            }
            // Exact full-symbol match is a strong prior.
            if let Some(symbol) = &entry.symbol {
                if tokenize(symbol).into_iter().collect::<BTreeSet<String>>() == query_terms {
                    score += 2.0 * W_SYMBOL;
                }
            }
            if entry.is_test {
                score *= 0.5;
            }
            scored.push((index, score, covered.min(query_terms.len())));
        }
        if scored.is_empty() {
            return json!({"results": [], "weak": true, "note": "no lexical match; weak fallback is not confidence"});
        }
        let max_lexical = scored
            .iter()
            .map(|(_, score, _)| *score)
            .fold(0.0f64, f64::max);
        let graph_bias = graph_bias
            .cloned()
            .unwrap_or_else(|| self.pagerank(0.85, 20));
        let max_rank = graph_bias
            .values()
            .cloned()
            .fold(0.0f64, f64::max)
            .max(f64::MIN_POSITIVE);
        let mut blended: BTreeMap<usize, f64> = BTreeMap::new();
        for (index, lexical, _) in &scored {
            let lexical_norm = lexical / max_lexical;
            let rank_norm = graph_bias
                .get(&self.entries[*index].id)
                .copied()
                .unwrap_or(0.0)
                / max_rank;
            *blended.entry(*index).or_insert(0.0) += 0.75 * lexical_norm + 0.25 * rank_norm;
        }
        // One-hop spreading: neighbors inherit a fraction of the entry score.
        let spread: Vec<(usize, f64)> = scored
            .iter()
            .flat_map(|(index, lexical, _)| {
                let base = 0.3 * lexical / max_lexical;
                self.entries[*index]
                    .neighbors
                    .iter()
                    .filter_map(move |neighbor| {
                        self.by_id
                            .get(neighbor)
                            .copied()
                            .map(|target| (target, base))
                    })
            })
            .collect();
        for (target, share) in spread {
            *blended.entry(target).or_insert(0.0) += share;
        }
        let mut ranked: Vec<(usize, f64)> = blended.into_iter().collect();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let full_coverage = scored
            .iter()
            .filter(|(_, _, covered)| *covered == query_terms.len())
            .count()
            > 0;
        let results: Vec<Value> = ranked
            .iter()
            .take(k)
            .map(|(index, score)| {
                let entry = &self.entries[*index];
                json!({
                    "id": entry.id, "kind": entry.kind, "path": entry.path,
                    "symbol": entry.symbol,
                    "signature": entry.signature,
                    "lines": entry.lines.map(|(start, end)| json!([start, end])),
                    "score": (score * 1000.0).round() / 1000.0,
                    "reason": reason_for(entry),
                })
            })
            .collect();
        json!({
            "results": results,
            "weak": !full_coverage,
            "note": "weak=true means partial query-term coverage; it is not a quality probability",
        })
    }
}

fn reason_for(entry: &Entry) -> String {
    match (&entry.symbol, &entry.signature) {
        (Some(_symbol), Some(signature)) => format!("symbol match: {signature} at {}", entry.path),
        (Some(symbol), None) => format!("symbol match: {symbol} at {}", entry.path),
        (None, _) => format!("path match: {}", entry.path),
    }
}

/// Byte-budgeted context pack (P04): repo-map block (paths + signatures) plus
/// ranked symbol entries, each with its inclusion reason and byte count.
pub fn context_pack(
    index: &ContextIndex,
    query: &str,
    byte_budget: usize,
    graph_bias: Option<&BTreeMap<String, f64>>,
) -> Value {
    let search = index.search(query, 32, graph_bias);
    let mut repo_map = String::new();
    let mut items: Vec<Value> = Vec::new();
    let mut used = 0usize;
    for result in search["results"].as_array().cloned().unwrap_or_default() {
        let entry_json = json!({
            "path": result["path"],
            "symbol": result["symbol"],
            "lines": result["lines"],
            "signature": result["signature"],
            "reason": result["reason"],
        });
        let rendered = serde_json::to_string(&entry_json).unwrap_or_default();
        let bytes = rendered.len();
        if used + bytes > byte_budget {
            items.push(
                json!({"omitted": true, "reason": "byte budget exhausted", "path": result["path"]}),
            );
            continue;
        }
        used += bytes;
        items.push(entry_json);
        if let Some(path) = result["path"].as_str() {
            let line = match (result["symbol"].as_str(), result["signature"].as_str()) {
                (Some(symbol), Some(signature)) => format!("{path}: {signature}  # {symbol}\n"),
                (Some(symbol), None) => format!("{path}: {symbol}\n"),
                (None, _) => format!("{path}\n"),
            };
            if used + line.len() <= byte_budget {
                repo_map.push_str(&line);
            }
        }
    }
    json!({
        "query": query,
        "repo_map": repo_map,
        "items": items,
        "bytes_used": used,
        "byte_budget": byte_budget,
        "weak": search["weak"],
        "note": "reasons state why each item is included; weak coverage stays weak",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn bound_pack_keeps_root_revision_and_refuses_stale_hashes() {
        let files = BTreeMap::from([("lib.rs".into(), b"pub fn refund() {}\n".to_vec())]);
        let mut analysis =
            af_graph::source_analysis::analyze("main", &"a".repeat(64), &files).unwrap();
        let pack = source_bound_context_pack(&analysis, "refund", 10000).unwrap();
        assert_eq!(pack["verification"], json!("not_assessed"));
        assert_eq!(pack["source_revision"], json!("a".repeat(64)));
        analysis["nodes"][0]["source"]["digest"] = json!("b".repeat(64));
        assert!(source_bound_context_pack(&analysis, "refund", 10000).is_err());
    }

    fn document() -> Value {
        json!({
            "nodes": [
                {"id": "file:src/scheduler.py", "kind": "file", "path": "src/scheduler.py",
                 "source": {"digest": "d", "algorithm": "sha256"},
                 "facts": {"imports": [], "raw_calls": ["dispatch"]}, "fact_status": {}, "facts_class": "extracted"},
                {"id": "sym:src/scheduler.py::TaskScheduler", "kind": "class", "path": "src/scheduler.py",
                 "symbol": "TaskScheduler", "source": {"digest": "d", "algorithm": "sha256"},
                 "start_line": 10, "end_line": 80,
                 "facts": {"signature": "class TaskScheduler", "summary": "schedules pending tasks and runs dispatch"},
                 "fact_status": {}, "facts_class": "extracted"},
                {"id": "sym:src/scheduler.py::TaskScheduler.run", "kind": "function", "path": "src/scheduler.py",
                 "symbol": "TaskScheduler.run", "source": {"digest": "d", "algorithm": "sha256"},
                 "start_line": 20, "end_line": 40,
                 "facts": {"signature": "def run(self)", "summary": "dispatch loop for tasks"},
                 "fact_status": {}, "facts_class": "extracted"},
                {"id": "file:tests/test_scheduler.py", "kind": "file", "path": "tests/test_scheduler.py",
                 "source": {"digest": "d", "algorithm": "sha256"},
                 "facts": {"imports": [], "raw_calls": []}, "fact_status": {}, "facts_class": "extracted"},
            ],
            "edges": [
                {"id": "file:src/scheduler.py-contains->sym:src/scheduler.py::TaskScheduler",
                 "kind": "contains", "src": "file:src/scheduler.py",
                 "dst": "sym:src/scheduler.py::TaskScheduler", "status": "active"},
                {"id": "sym:src/scheduler.py::TaskScheduler-contains->sym:src/scheduler.py::TaskScheduler.run",
                 "kind": "contains", "src": "sym:src/scheduler.py::TaskScheduler",
                 "dst": "sym:src/scheduler.py::TaskScheduler.run", "status": "active"},
                {"id": "tests/test_scheduler.py-imports->file:src/scheduler.py",
                 "kind": "imports", "src": "file:tests/test_scheduler.py",
                 "dst": "file:src/scheduler.py", "status": "active"},
            ],
        })
    }

    #[test]
    fn identifiers_split_and_stem() {
        assert_eq!(split_identifier("TaskScheduler"), vec!["task", "scheduler"]);
        assert_eq!(
            split_identifier("HTTPServer_v2"),
            vec!["httpserver", "v", "2"]
        );
        assert_eq!(
            tokenize("ScheduledTasks runner"),
            vec!["schedul", "task", "runn"]
        );
        assert!(tokenize("the of and").is_empty(), "stop words dropped");
    }

    #[test]
    fn ranking_prefers_symbol_and_penalizes_tests() {
        let index = ContextIndex::build(&document());
        let results = index.search("scheduler tasks", 5, None);
        assert_eq!(results["weak"], json!(false));
        let first = &results["results"][0];
        assert_eq!(first["symbol"], json!("TaskScheduler"));
        assert!(first["reason"]
            .as_str()
            .unwrap()
            .starts_with("symbol match"));
        // Exact symbol name beats the test file despite the same stem.
        let exact = index.search("TaskScheduler", 5, None);
        assert_eq!(exact["results"][0]["symbol"], json!("TaskScheduler"));
        let paths: Vec<&str> = exact["results"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|r| r["path"].as_str())
            .collect();
        assert!(
            paths.contains(&"tests/test_scheduler.py"),
            "test file still retrievable, ranked lower"
        );
    }

    #[test]
    fn spreading_lifts_connected_neighbors() {
        let index = ContextIndex::build(&document());
        let results = index.search("dispatch", 5, None);
        let paths: Vec<&str> = results["results"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|r| r["path"].as_str())
            .collect();
        assert!(
            paths.contains(&"src/scheduler.py"),
            "file node lifted by run-method match"
        );
    }

    #[test]
    fn no_match_is_weak_and_explicit() {
        let index = ContextIndex::build(&document());
        let results = index.search("quantum_entanglement", 5, None);
        assert_eq!(results["weak"], json!(true));
        assert!(results["results"].as_array().unwrap().is_empty());
    }

    #[test]
    fn context_pack_respects_byte_budget_with_reasons() {
        let index = ContextIndex::build(&document());
        let pack = context_pack(&index, "scheduler tasks", 400, None);
        assert!(pack["bytes_used"].as_u64().unwrap() <= 400);
        assert!(pack["repo_map"]
            .as_str()
            .unwrap()
            .contains("src/scheduler.py"));
        let items = pack["items"].as_array().unwrap();
        assert!(items
            .iter()
            .all(|item| item.get("reason").is_some() || item.get("omitted").is_some()));
        // Tiny budget: nothing fits, omissions are named — never silent.
        let tiny = context_pack(&index, "scheduler tasks", 10, None);
        assert!(tiny["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item["omitted"] == json!(true)));
    }

    #[test]
    fn pagerank_ranks_referenced_files() {
        let index = ContextIndex::build(&document());
        let ranks = index.pagerank(0.85, 30);
        let scheduler = ranks["file:src/scheduler.py"];
        assert!(scheduler > 0.0);
        // The scheduler file has in-edges from tests; it should beat a bare test file.
        assert!(scheduler > ranks["file:tests/test_scheduler.py"]);
    }
}
