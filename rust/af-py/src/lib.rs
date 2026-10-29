//! af_core_py: the PyO3 facade over the atomic-flow Rust kernel. The Python
//! lab (evaluation harness, live-run protocols) drives the kernel through
//! this module; every function speaks JSON strings at the boundary so the
//! Python side needs no Rust types. Errors cross as Python exceptions; the
//! kernel's authority checks stay inside the crates.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::{json, Value};

fn to_py_error(error: String) -> PyErr {
    PyValueError::new_err(error)
}

fn dump(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "{}".into())
}

fn parse_json(text: &str) -> PyResult<Value> {
    serde_json::from_str(text).map_err(|error| PyValueError::new_err(format!("invalid JSON: {error}")))
}

/// Kernel version and crate inventory (single source: CARGO_PKG_VERSION).
#[pyfunction]
fn version() -> String {
    dump(&json!({
        "version": env!("CARGO_PKG_VERSION"),
        "kernel": "atomic-flow-rust",
    }))
}

/// Clean structural build over a project root; returns the parity-ready
/// summary (files, nodes, edges, unresolved count). No store is written.
#[pyfunction]
fn graph_build(py: Python, root: String) -> PyResult<String> {
    let files = af_graph::project::collect_source_files(std::path::Path::new(&root))
        .map_err(to_py_error)?;
    py.allow_threads(move || {
        let build = af_graph::project::clean_build(&files).map_err(|e| to_py_error(e.0))?;
        Ok(dump(&json!({
            "ok": true, "root": root,
            "files": build["parsed_files"],
            "nodes": build["nodes"].as_array().map(|n| n.len()).unwrap_or(0),
            "edges": build["edges"].as_array().map(|e| e.len()).unwrap_or(0),
            "unresolved": build["unresolved"].as_array().map(|u| u.len()).unwrap_or(0),
        })))
    })
}

/// The parity oracle from Python: build over `root`, then apply a synthetic
/// edit of `file` (content passed verbatim) and compare delta vs clean.
#[pyfunction]
fn graph_parity(
    py: Python,
    root: String,
    file: String,
    new_content: String,
) -> PyResult<String> {
    use sha2::{Digest, Sha256};
    let mut files = af_graph::project::collect_source_files(std::path::Path::new(&root))
        .map_err(to_py_error)?;
    let Some(old_bytes) = files.get(&file) else {
        return Err(to_py_error(format!("file {file} is not in the root snapshot")));
    };
    let old_digest = {
        let mut hasher = Sha256::new();
        hasher.update(old_bytes);
        format!("{:x}", hasher.finalize())
    };
    let new_bytes = new_content.into_bytes();
    py.allow_threads(move || {
        let build = af_graph::project::clean_build(&files).map_err(|e| to_py_error(e.0))?;
        let nodes = build["nodes"].as_array().cloned().unwrap_or_default();
        let edges = build["edges"].as_array().cloned().unwrap_or_default();
        let scratch = std::env::temp_dir().join(format!("af-py-parity-{}", af_entropy::token()));
        std::fs::create_dir_all(&scratch).map_err(|e| to_py_error(e.to_string()))?;
        let mut store = af_graph::GraphStore::open(&scratch.join("graph.json")).map_err(|e| to_py_error(e.0))?;
        store.commit(0, &nodes, &edges, &[], &[]).map_err(|e| to_py_error(e.0))?;
        let plan = af_graph::delta::file_delta(&file, Some(&new_bytes), Some(&old_digest), None)
            .map_err(|e| to_py_error(e.0))?;
        let report = af_graph::delta::apply_delta(&mut store, &plan).map_err(|e| to_py_error(e.0))?;
        files.insert(file.clone(), new_bytes);
        let clean = af_graph::project::clean_build(&files).map_err(|e| to_py_error(e.0))?;
        let mut delta_ids: Vec<String> = store
            .edges()
            .iter()
            .map(|edge| edge["id"].as_str().unwrap_or_default().to_string())
            .collect();
        let mut clean_ids: Vec<String> = clean["edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|edge| edge["id"].as_str().unwrap_or_default().to_string())
            .collect();
        delta_ids.sort();
        clean_ids.sort();
        let parity = delta_ids == clean_ids;
        Ok(dump(&json!({
            "ok": parity, "parity": parity,
            "delta_report": report,
            "edges_delta": delta_ids.len(), "edges_clean": clean_ids.len(),
        })))
    })
}

/// Create a session journal bound to `project`.
#[pyfunction]
fn session_create(project: String, id: Option<String>) -> PyResult<String> {
    let journal =
        af_journal::SessionJournal::create(std::path::Path::new(&project), id.as_deref(), None)
            .map_err(|e| to_py_error(e.0))?;
    Ok(dump(&json!({
        "ok": true, "session_id": journal.session_id,
        "path": journal.path.to_string_lossy(),
    })))
}

/// Append one journaled event (redaction and holds are enforced in the crate).
#[pyfunction]
fn session_append(
    id: String,
    event_type: String,
    payload_json: String,
    call_id: Option<String>,
    outcome: Option<String>,
) -> PyResult<String> {
    let payload = parse_json(&payload_json)?;
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|e| to_py_error(e.0))?;
    let event = journal
        .append(&event_type, &payload, call_id.as_deref(), outcome.as_deref(), None)
        .map_err(|e| to_py_error(e.0))?;
    Ok(dump(&event))
}

/// Read the journal snapshot: event count, pending and unresolved holds.
#[pyfunction]
fn session_read(id: String) -> PyResult<String> {
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|e| to_py_error(e.0))?;
    let snapshot = journal.read().map_err(|e| to_py_error(e.0))?;
    Ok(dump(&json!({
        "ok": true, "session_id": snapshot.session_id,
        "events": snapshot.events.len(),
        "incomplete_tail": snapshot.incomplete_tail,
        "pending_call_ids": snapshot.pending_call_ids(),
        "unresolved_call_ids": snapshot.unresolved_call_ids(),
    })))
}

/// Grant a sandbox root (host decision — the Python caller IS the host here).
#[pyfunction]
fn sandbox_grant(root_id: String, path: String, write: Vec<String>) -> PyResult<String> {
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| to_py_error(e.0))?;
    let granted = registry
        .grant(&root_id, std::path::Path::new(&path), &write, true)
        .map_err(|e| to_py_error(e.0))?;
    Ok(dump(&json!({"ok": true, "granted": granted})))
}

/// Read a bounded, redacted, root-qualified source page.
#[pyfunction]
fn sandbox_read(root_id: String, path: String, limit: Option<usize>) -> PyResult<String> {
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| to_py_error(e.0))?;
    let mut session = af_sandbox::SandboxSession::new(&registry).map_err(|e| to_py_error(e.0))?;
    let page = session
        .read(&root_id, &path, 0, limit.unwrap_or(6000), false)
        .map_err(|e| to_py_error(e.0))?;
    Ok(dump(&page))
}

/// Backend-plane smoke through the scripted router (settled usage, no spend).
#[pyfunction]
fn backend_ping() -> PyResult<String> {
    let router = af_llm::Router::new(vec![Box::new(af_llm::ScriptedBackend::echo())]);
    let request = af_llm::ChatRequest::new("scripted", vec![af_llm::ChatMessage::new("user", "ping")]);
    match router.route(&request, &af_llm::Cancel::new()) {
        af_llm::RouteOutcome::Completed { backend, result } => Ok(dump(&json!({
            "ok": true, "backend": backend, "content": result.content,
            "tokens_in": result.usage.tokens_in, "tokens_out": result.usage.tokens_out,
            "cost_usd": result.usage.cost_usd,
        }))),
        af_llm::RouteOutcome::Hold { backend, detail } => {
            Err(to_py_error(format!("HOLD {backend}: {detail}")))
        }
        af_llm::RouteOutcome::Exhausted { refusals } => {
            Err(to_py_error(format!("exhausted: {refusals:?}")))
        }
    }
}

#[pymodule]
fn af_core_py(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(version, module)?)?;
    module.add_function(wrap_pyfunction!(graph_build, module)?)?;
    module.add_function(wrap_pyfunction!(graph_parity, module)?)?;
    module.add_function(wrap_pyfunction!(session_create, module)?)?;
    module.add_function(wrap_pyfunction!(session_append, module)?)?;
    module.add_function(wrap_pyfunction!(session_read, module)?)?;
    module.add_function(wrap_pyfunction!(sandbox_grant, module)?)?;
    module.add_function(wrap_pyfunction!(sandbox_read, module)?)?;
    module.add_function(wrap_pyfunction!(backend_ping, module)?)?;
    Ok(())
}
