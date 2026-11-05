//! `af serve`: the self-host HTTP API (headless, E08-compatible). Loopback by
//! default; any non-loopback bind REQUIRES a server token, compared in
//! constant time. Sessions, edit journeys and job status over JSON.

use axum::{
    extract::{DefaultBodyLimit, Path as AxPath, Query, State},
    http::{HeaderMap, StatusCode},
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse,
    },
    routing::{delete, get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio_stream::wrappers::ReceiverStream;

#[path = "http_safety.rs"]
mod http_safety;
#[path = "studio_data.rs"]
mod studio_data;

/// Constant-time equality over byte strings (no early exit on mismatch).
fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

#[derive(Clone)]
struct ServerState {
    token: Option<String>,
    port: u16,
    streams: Arc<tokio::sync::Semaphore>,
    edits: Arc<tokio::sync::Semaphore>,
    picker: Arc<tokio::sync::Semaphore>,
}

fn authorized(state: &ServerState, headers: &HeaderMap) -> bool {
    if headers.get_all("authorization").iter().count() > 1 {
        return false;
    }
    let Some(expected) = &state.token else {
        return true; // no token configured (loopback bind only)
    };
    let supplied = headers
        .get("Authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or_default();
    ct_eq(supplied.as_bytes(), expected.as_bytes())
}

#[cfg(test)]
fn build_router(token: Option<String>) -> Router {
    build_router_for(token, 8787)
}

fn build_router_for(token: Option<String>, port: u16) -> Router {
    let token = token.filter(|value| !value.is_empty());
    let state = ServerState {
        token,
        port,
        streams: Arc::new(tokio::sync::Semaphore::new(4)),
        edits: Arc::new(tokio::sync::Semaphore::new(1)),
        picker: Arc::new(tokio::sync::Semaphore::new(1)),
    };
    Router::new()
        .route("/", get(root_redirect))
        .route("/health", get(health))
        .route("/dashboard", get(dashboard))
        .route("/v1/doctor", get(doctor_ep))
        .route("/v1/capabilities", get(capabilities))
        .route("/v1/sessions", get(list_sessions_ep).post(create_session))
        .route("/v1/sessions/{id}/events", post(append_event))
        .route("/v1/sessions/{id}", get(read_session))
        .route("/v1/edit-journeys", post(edit_journey))
        .route("/v1/chat/stream", post(chat_stream_sse))
        .route("/v1/jobs", get(job_status))
        .route("/v1/roots", get(list_roots_ep).post(grant_root_ep))
        .route("/v1/roots/{root_id}", delete(revoke_root_ep))
        .route("/v1/pick-folder", post(pick_folder_ep))
        .route("/v1/budget", get(budget_snapshot).post(budget_create))
        .route("/v1/graph/build", post(graph_build_ep))
        .route("/v1/graph/{root_id}", get(graph_read_ep))
        .merge(studio_data::routes())
        .layer(DefaultBodyLimit::max(1_048_576))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            http_safety::guard,
        ))
        .with_state(state)
}

/// Data-only protocol capabilities. No source or execution authority is exposed.
async fn capabilities(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    if let Some(error) = auth_denied(&state, &headers) {
        return Err(error);
    }
    Ok(Json(
        json!({"schema_version": 1, "verification_schema_version": af_evidence::VERSION,
        "authority": "single_owner", "requirement_checker": "unavailable",
        "verification_report_authorizes_execution": false,
        "publication_recovery": "host_cli_preview_digest",
        "unknown_replay": false}),
    ))
}

/// Bind a report to the exact delivered request, not to a claimed Git revision.
/// Coverage describes the selected excerpts only; it is not project coverage.
fn request_report(
    prompt: &str,
    sources: &[Value],
    session: &str,
) -> af_evidence::VerificationReport {
    let inventory = serde_json::to_vec(&(prompt, sources)).expect("bounded request is JSON");
    let binding = af_evidence::SourceBinding {
        workspace_id: format!("session:{session}"),
        source_revision: "delivered-request-snapshot-v1".into(),
        inventory_sha256: af_evidence::sha256(&inventory),
        runtime_id: concat!("af-cli/", env!("CARGO_PKG_VERSION")).into(),
    };
    let mut report = af_evidence::VerificationReport::not_assessed(binding).expect("host binding");
    report.evidence_coverage.requested = sources.len() as u64;
    report.evidence_coverage.delivered_partial = sources.len() as u64;
    report
}

async fn root_redirect() -> axum::response::Response {
    axum::response::Redirect::temporary("/dashboard").into_response()
}

async fn dashboard(State(_state): State<ServerState>) -> axum::response::Response {
    // Static UI is public; every data/effect endpoint still requires the
    // configured bearer. The UI keeps that bearer in memory only.
    let nonce = af_entropy::token();
    let html = crate::dashboard::DASHBOARD_HTML
        .replace("<script>", &format!("<script nonce=\"{nonce}\">"));
    let mut response = axum::response::Html(html).into_response();
    let policy = format!("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'");
    response.headers_mut().insert(
        "Content-Security-Policy",
        policy.parse().expect("host nonce is ASCII"),
    );
    response
}

async fn list_sessions_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let sessions = af_journal::list_sessions(200).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(json!({"ok": true, "sessions": sessions})))
}

/// Error response that carries the reason (the dashboard surfaces it verbatim).
fn api_error(status: StatusCode, message: impl std::fmt::Display) -> (StatusCode, Json<Value>) {
    (
        status,
        Json(json!({"ok": false, "error": message.to_string()})),
    )
}

/// Grant a sandbox root so graph build / journeys can operate on it. This is
/// a host-level mutation: it requires the explicit `host_approved` flag.
#[derive(serde::Deserialize)]
struct GrantRootBody {
    root_id: String,
    path: String,
    host_approved: bool,
    #[serde(default)]
    write_paths: Vec<String>,
}

async fn grant_root_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<GrantRootBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if auth_denied(&state, &headers).is_some() {
        return Err(api_error(StatusCode::UNAUTHORIZED, "unauthorized"));
    }
    if !body.host_approved {
        return Err(api_error(
            StatusCode::FORBIDDEN,
            "регистрация root требует host_approved — явного решения оператора",
        ));
    }
    let registry = af_sandbox::SandboxRegistry::new("default")
        .map_err(|e| api_error(StatusCode::INTERNAL_SERVER_ERROR, e.0))?;
    let item = registry
        .grant(
            &body.root_id,
            std::path::Path::new(&body.path),
            &body.write_paths,
            true,
        )
        .map_err(|e| api_error(StatusCode::BAD_REQUEST, e.0))?;
    Ok(Json(json!({"ok": true, "root": item})))
}

async fn list_roots_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if auth_denied(&state, &headers).is_some() {
        return Err(api_error(StatusCode::UNAUTHORIZED, "unauthorized"));
    }
    let registry = af_sandbox::SandboxRegistry::new("default")
        .map_err(|e| api_error(StatusCode::INTERNAL_SERVER_ERROR, e.0))?;
    let roots = registry
        .list_roots()
        .map_err(|e| api_error(StatusCode::INTERNAL_SERVER_ERROR, e.0))?;
    Ok(Json(json!({"ok": true, "roots": roots})))
}

/// Revoke a granted root (host-approved mutation).
#[derive(serde::Deserialize)]
struct RevokeRootBody {
    #[serde(default)]
    host_approved: bool,
}

async fn revoke_root_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
    AxPath(root_id): AxPath<String>,
    body: Option<Json<RevokeRootBody>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if auth_denied(&state, &headers).is_some() {
        return Err(api_error(StatusCode::UNAUTHORIZED, "unauthorized"));
    }
    let approved = body.map(|Json(b)| b.host_approved).unwrap_or(false);
    if !approved {
        return Err(api_error(
            StatusCode::FORBIDDEN,
            "отзыв root требует host_approved",
        ));
    }
    let registry = af_sandbox::SandboxRegistry::new("default")
        .map_err(|e| api_error(StatusCode::INTERNAL_SERVER_ERROR, e.0))?;
    let result = registry
        .revoke(&root_id, true)
        .map_err(|e| api_error(StatusCode::BAD_REQUEST, e.0))?;
    Ok(Json(json!({"ok": true, "revoke": result})))
}

/// Open the native OS folder picker (Explorer-style IFileOpenDialog) on the
/// host machine. The server shares the operator's interactive session, so the
/// dialog appears on their desktop; the choice never leaves the machine.
/// A cancelled dialog is reported as `cancelled`, not an error.
async fn pick_folder_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if auth_denied(&state, &headers).is_some() {
        return Err(api_error(StatusCode::UNAUTHORIZED, "unauthorized"));
    }
    let permit = state.picker.clone().try_acquire_owned().map_err(|_| {
        api_error(
            StatusCode::TOO_MANY_REQUESTS,
            "folder picker is already open",
        )
    })?;
    let picked = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        rfd::FileDialog::new()
            .set_title("Выберите папку проекта для Atomic Flow")
            .pick_folder()
    })
    .await
    .map_err(|e| {
        api_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("dialog task failed: {e}"),
        )
    })?;
    match picked {
        Some(path) => Ok(Json(json!({
            "ok": true, "cancelled": false,
            "path": path.to_string_lossy(),
            "name": path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default(),
        }))),
        None => Ok(Json(json!({"ok": true, "cancelled": true}))),
    }
}

/// Durable budget ledger (af-budget authority). One fixed owner/ledger per
/// installation; the dashboard reads it for the live budget line.
fn budget_ledger_path() -> std::path::PathBuf {
    af_sandbox::user_dir().join("budget").join("ledger.jsonl")
}
const BUDGET_OWNER: &str = "dashboard";

async fn budget_snapshot(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let path = budget_ledger_path();
    if !path.exists() {
        return Ok(Json(
            json!({"state": "missing", "enforced": true, "provider_mode": "host_configured_or_scripted"}),
        ));
    }
    let Ok(ledger) = af_budget::Ledger::open(&path, BUDGET_OWNER) else {
        return Ok(Json(
            json!({"state": "unreadable", "enforced": true, "provider_mode": "host_configured_or_scripted"}),
        ));
    };
    match ledger.snapshot() {
        Ok(snap) => {
            let spent = snap
                .calls
                .values()
                .filter(|call| call.status == af_budget::Status::Settled)
                .try_fold(af_budget::Money(0), |total, call| {
                    total.checked_add(
                        call.actual
                            .ok_or(af_budget::BudgetError("missing actual cost"))?,
                    )
                });
            let reserved = snap
                .calls
                .values()
                .filter(|call| {
                    matches!(
                        call.status,
                        af_budget::Status::Reserved
                            | af_budget::Status::Sent
                            | af_budget::Status::Unknown
                    )
                })
                .try_fold(af_budget::Money(0), |total, call| {
                    total.checked_add(call.reserved)
                });
            let unknown_cost = snap.send_checkpoint
                || snap.calls.values().any(|call| {
                    matches!(
                        call.status,
                        af_budget::Status::Sent | af_budget::Status::Unknown
                    )
                });
            Ok(Json(json!({
                "state": if snap.send_checkpoint || snap.has_hold() { "hold" } else { "ok" },
                "committed_usd": snap.committed().map(|m| m.usd_string()).unwrap_or_else(|_| "unknown".into()),
                "committed_basis": "known_spend_plus_reservations",
                "spent_usd_known": spent.ok().map(|m| m.usd_string()),
                "reserved_usd": reserved.ok().map(|m| m.usd_string()),
                "unknown_cost_present": unknown_cost,
                "available_usd": snap.available().ok().flatten().map(|m| m.usd_string()),
                "cap_usd": snap.cap.usd_string(),
                "calls": snap.calls.len(),
                "send_hold": snap.send_checkpoint,
                "enforced": true, "provider_mode": "host_configured_or_scripted",
            })))
        }
        Err(_) => Ok(Json(
            json!({"state": "unreadable", "enforced": true, "provider_mode": "host_configured_or_scripted"}),
        )),
    }
}

#[derive(serde::Deserialize)]
struct BudgetCreateBody {
    cap_usd: String,
    #[serde(default)]
    host_approved: bool,
}

async fn budget_create(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<BudgetCreateBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if auth_denied(&state, &headers).is_some() {
        return Err(api_error(StatusCode::UNAUTHORIZED, "unauthorized"));
    }
    if !body.host_approved {
        return Err(api_error(
            StatusCode::FORBIDDEN,
            "создание бюджета требует host_approved",
        ));
    }
    let cap = af_budget::Money::from_usd_str(&body.cap_usd)
        .map_err(|e| api_error(StatusCode::BAD_REQUEST, e.0))?;
    let path = budget_ledger_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|_| api_error(StatusCode::INTERNAL_SERVER_ERROR, "budget dir unavailable"))?;
    }
    af_budget::Ledger::create(&path, BUDGET_OWNER, cap, 256)
        .map_err(|e| api_error(StatusCode::CONFLICT, e.0))?;
    Ok(Json(json!({"ok": true, "cap_usd": cap.usd_string()})))
}

/// Build (or rebuild) the structural graph for a granted root and persist it
/// under the server's graph store; returns the summary.
#[derive(serde::Deserialize)]
struct GraphBuildBody {
    root_id: String,
}

async fn graph_build_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<GraphBuildBody>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let registry = af_sandbox::SandboxRegistry::new("default")
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let root_item = registry
        .get(&body.root_id)
        .map_err(|_| StatusCode::NOT_FOUND)?;
    let root = PathBuf::from(root_item["path"].as_str().unwrap_or_default());
    let files = af_graph::project::collect_source_files(&root).map_err(|e| {
        eprintln!("af serve graph build: {e}");
        StatusCode::BAD_REQUEST
    })?;
    let build = af_graph::project::clean_build(&files).map_err(|e| {
        eprintln!("af serve graph build: {e}");
        StatusCode::BAD_REQUEST
    })?;
    let store_dir = af_sandbox::user_dir().join("graphs");
    std::fs::create_dir_all(&store_dir).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let document = json!({
        "schema_version": 1, "generation": 1,
        "nodes": build["nodes"], "edges": build["edges"],
    });
    let store_path = store_dir.join(format!("{}.json", body.root_id));
    std::fs::write(&store_path, af_graph::canonical::canonical_bytes(&document))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(json!({
        "ok": true, "root_id": body.root_id,
        "files": build["parsed_files"],
        "nodes": build["nodes"].as_array().map(|n| n.len()).unwrap_or(0),
        "edges": build["edges"].as_array().map(|e| e.len()).unwrap_or(0),
        "unresolved": build["unresolved"].as_array().map(|u| u.len()).unwrap_or(0),
        "store": store_path.to_string_lossy(),
    })))
}

/// Read a stored graph: per-file layer assignment (deterministic P03 slice)
/// plus node/edge summary and unresolved list.
async fn graph_read_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
    AxPath(root_id): AxPath<String>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    // The registry validates the identifier, current grant and root identity.
    // A graph pathname alone is neither a root grant nor filesystem authority.
    af_sandbox::SandboxRegistry::new("default")
        .and_then(|registry| registry.get(&root_id))
        .map_err(|_| StatusCode::NOT_FOUND)?;
    let store_path = af_sandbox::user_dir()
        .join("graphs")
        .join(format!("{root_id}.json"));
    af_sandbox::registry::ensure_no_links(&store_path).map_err(|_| StatusCode::BAD_REQUEST)?;
    if std::fs::metadata(&store_path)
        .map_err(|_| StatusCode::NOT_FOUND)?
        .len()
        > 32 * 1024 * 1024
    {
        return Err(StatusCode::PAYLOAD_TOO_LARGE);
    }
    let text = std::fs::read_to_string(&store_path).map_err(|_| StatusCode::NOT_FOUND)?;
    let document: Value =
        serde_json::from_str(&text).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let files: Vec<Value> = document["nodes"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|node| node["kind"] == json!("file"))
        .collect();
    let files: Vec<Value> = files
        .iter()
        .map(|node| {
            let path = node["path"].as_str().unwrap_or_default();
            json!({
                "path": path,
                "layer": af_graph::semantic::layer_for(path),
                "symbols": document["nodes"].as_array().cloned().unwrap_or_default()
                    .iter()
                    .filter(|n| n["path"].as_str() == Some(path) && n["kind"] != json!("file"))
                    .map(|n| n["symbol"].as_str().unwrap_or_default().to_string())
                    .collect::<Vec<_>>(),
            })
        })
        .collect();
    Ok(Json(json!({
        "ok": true, "root_id": root_id,
        "files": files,
        "nodes": document["nodes"].as_array().map(|n| n.len()).unwrap_or(0),
        "edges": document["edges"].as_array().map(|e| e.len()).unwrap_or(0),
    })))
}

fn auth_denied(state: &ServerState, headers: &HeaderMap) -> Option<StatusCode> {
    if authorized(state, headers) {
        None
    } else {
        Some(StatusCode::UNAUTHORIZED)
    }
}

async fn health(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    Ok(Json(json!({
        "ok": true,
        "version": env!("CARGO_PKG_VERSION"),
        "kernel": "atomic-flow-rust",
    })))
}

async fn doctor_ep(
    State(state): State<ServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    Ok(Json(crate::doctor::run_checks()))
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateSessionBody {
    id: Option<String>,
    project: Option<String>,
    root_id: Option<String>,
}

async fn create_session(
    State(state): State<ServerState>,
    headers: HeaderMap,
    body: Option<Json<CreateSessionBody>>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let body = body.map(|Json(body)| body).unwrap_or(CreateSessionBody {
        id: None,
        project: None,
        root_id: None,
    });
    if body.project.is_some() {
        return Err(StatusCode::BAD_REQUEST);
    }
    let (project, metadata) = if let Some(root_id) = body.root_id {
        let root = af_sandbox::SandboxRegistry::new("default")
            .and_then(|registry| registry.get(&root_id))
            .map_err(|_| StatusCode::BAD_REQUEST)?;
        (
            root["path"]
                .as_str()
                .ok_or(StatusCode::BAD_REQUEST)?
                .to_string(),
            json!({"root_id": root_id, "root_generation": root["generation"], "project_binding": "granted_root"}),
        )
    } else {
        (
            std::env::current_dir()
                .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
                .to_string_lossy()
                .into_owned(),
            json!({"root_id": null, "project_binding": "unbound_chat"}),
        )
    };
    let journal = af_journal::SessionJournal::create(
        std::path::Path::new(&project),
        body.id.as_deref(),
        Some(&metadata),
    )
    .map_err(|_| StatusCode::BAD_REQUEST)?;
    Ok(Json(json!({
        "ok": true, "session_id": journal.session_id,
        "path": journal.path.to_string_lossy(),
    })))
}

#[derive(serde::Deserialize)]
struct AppendEventBody {
    event_type: String,
    payload: Value,
    call_id: Option<String>,
    outcome: Option<String>,
}

async fn append_event(
    State(state): State<ServerState>,
    headers: HeaderMap,
    AxPath(id): AxPath<String>,
    Json(body): Json<AppendEventBody>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    // Browser input is advisory. It cannot create model/tool terminal events,
    // paid reconciliation, snapshots, approval or verification authority.
    if body.event_type != "user_input" || body.call_id.is_some() || body.outcome.is_some() {
        return Err(StatusCode::FORBIDDEN);
    }
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|_| StatusCode::NOT_FOUND)?;
    let event = journal
        .append(
            &body.event_type,
            &body.payload,
            body.call_id.as_deref(),
            body.outcome.as_deref(),
            None,
        )
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    Ok(Json(event))
}

async fn read_session(
    State(state): State<ServerState>,
    headers: HeaderMap,
    AxPath(id): AxPath<String>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|_| StatusCode::NOT_FOUND)?;
    let snapshot = journal.read().map_err(|_| StatusCode::CONFLICT)?;
    Ok(Json(json!({
        "ok": true, "session_id": snapshot.session_id,
        "events": snapshot.events.len(),
        "incomplete_tail": snapshot.incomplete_tail,
        "pending_call_ids": snapshot.pending_call_ids(),
        "unresolved_call_ids": snapshot.unresolved_call_ids(),
        "root_id": snapshot.events.first().and_then(|event| event.pointer("/payload/metadata/root_id")).cloned().unwrap_or(Value::Null),
    })))
}

#[derive(serde::Deserialize)]
struct EditJourneyBody {
    backend: BackendConfig,
    root_id: String,
    file: String,
    replacement: String,
    goal: String,
    session_id: Option<String>,
    host_approved: bool,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct BackendConfig {
    #[serde(default)]
    base_url: String,
    #[serde(default)]
    api_key_env: String,
    model: String,
    provider_id: Option<String>,
}

async fn edit_journey(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<EditJourneyBody>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    if !body.host_approved {
        return Err(StatusCode::FORBIDDEN);
    }
    if body.goal.is_empty()
        || body.goal.len() > 8192
        || body.file.len() > 512
        || body.replacement.len() > 512 * 1024
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let permit = state
        .edits
        .clone()
        .try_acquire_owned()
        .map_err(|_| StatusCode::TOO_MANY_REQUESTS)?;
    let backend = backend_from_config(&body.backend)?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        run_edit_journey(body, backend)
    })
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
}

fn run_edit_journey(
    body: EditJourneyBody,
    backend: Box<dyn af_llm::ChatBackend>,
) -> Result<Json<Value>, StatusCode> {
    let router = af_llm::Router::new(vec![backend]);
    let registry = af_sandbox::SandboxRegistry::new("default")
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let project = std::env::current_dir().map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let session_id = body
        .session_id
        .unwrap_or_else(|| format!("serve-{}", af_entropy::token()));
    let mut journal = af_journal::SessionJournal::create(&project, Some(&session_id), None)
        .or_else(|_| af_journal::SessionJournal::open(&session_id))
        .map_err(|_| StatusCode::CONFLICT)?;
    let toolset = crate::edit_toolset_for(&registry, &body.root_id)
        .map_err(|_| StatusCode::BAD_REQUEST)?
        .with_write_scope(std::slice::from_ref(&body.file))
        .map_err(|_| StatusCode::BAD_REQUEST)?
        .with_reviewed_replacement(&body.file, &body.replacement)
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    // The journey's target file and replacement are part of the task the
    // model executes — not silently dropped client fields.
    let task = format!(
        "{}\nTarget file: {}\nDesired replacement content:\n{}",
        body.goal, body.file, body.replacement
    );
    let outcome = af_loop::edit::run_edit_task(
        &router,
        &toolset,
        Some(&mut journal),
        &body.backend.model,
        &task,
        true,
        af_loop::LoopLimits::default(),
        None,
        None,
    )
    .map_err(|_| StatusCode::BAD_REQUEST)?;
    let (ok, verified, refusal) = match &outcome {
        af_loop::edit::EditOutcome::Completed {
            content,
            check,
            git,
            ..
        } => (
            true,
            false,
            json!({"git": git, "content_chars": content.chars().count(), "check": check}),
        ),
        af_loop::edit::EditOutcome::Unverified {
            content,
            check,
            git,
            ..
        } => (
            true,
            false,
            json!({"git": git, "content_chars": content.chars().count(), "check": check, "reason": "requirement verification not assessed"}),
        ),
        af_loop::edit::EditOutcome::CheckFailed { check, git, .. } => (
            false,
            false,
            json!({"git": git, "reason": "final gate failed", "check": check}),
        ),
        af_loop::edit::EditOutcome::Interrupted { loop_outcome, .. } => {
            (false, false, json!({"reason": format!("{loop_outcome:?}")}))
        }
    };
    Ok(Json(json!({
        "ok": ok, "verified": verified, "refusal": refusal,
        "session_id": journal.session_id,
        "edits": toolset.edits_used(),
        "verification": "not_assessed",
        "status": if ok && toolset.edits_used() > 0 { "changed_unverified" } else if ok { "answered_unverified" } else { "paused" },
    })))
}

#[derive(serde::Deserialize)]
struct JobsQuery {
    store: Option<PathBuf>,
}

use std::path::PathBuf;

/// Core streaming engine: runs one backend call on a blocking thread, pushing
/// delta events and the final event (settled usage or hold) into the channel.
/// Used by the SSE endpoint and directly testable with the scripted backend.
/// `budget` is the optional af-budget authority: reserve → durable send
/// checkpoint → settle/unknown/not_sent around the single provider call.
// Keep stream, journal, and budget authority inputs explicit at the host boundary.
#[allow(clippy::too_many_arguments)]
fn spawn_chat_stream(
    backend: Box<dyn af_llm::ChatBackend>,
    model: String,
    prompt: String,
    sources: Vec<Value>,
    mut journal: Option<af_journal::SessionJournal>,
    call_id: String,
    permit: tokio::sync::OwnedSemaphorePermit,
    budget: Option<(af_budget::Ledger, af_budget::Money)>,
) -> (tokio::sync::mpsc::Receiver<Value>, af_llm::Cancel) {
    let (tx, rx) = tokio::sync::mpsc::channel(32);
    let cancel = af_llm::Cancel::new();
    let worker_cancel = cancel.clone();
    std::thread::spawn(move || {
        let _permit = permit;
        let mut budget = budget;
        let task_id = journal
            .as_ref()
            .map(|journal| journal.session_id.clone())
            .unwrap_or_else(|| "serve".to_string());
        if let Some((ledger, amount)) = budget.as_mut() {
            if let Err(error) = ledger.reserve(&call_id, &task_id, *amount) {
                let _ = tx.blocking_send(json!({"type": "error", "unknown": false,
                    "error": format!("budget reserve refused; nothing was sent ({})", error.0)}));
                return;
            }
            if let Err(error) = ledger.begin_send(&call_id, &task_id) {
                let _ = tx.blocking_send(json!({"type": "error", "unknown": true,
                    "error": format!("budget send checkpoint failed; treat the outcome as unknown ({})", error.0)}));
                return;
            }
        }
        let verification_report = request_report(
            &prompt,
            &sources,
            journal
                .as_ref()
                .map(|j| j.session_id.as_str())
                .unwrap_or("unbound"),
        );
        let mut request =
            af_llm::ChatRequest::new(&model, vec![af_llm::ChatMessage::new("user", &prompt)]);
        request.host_attempt = journal.as_ref().map(|journal| af_llm::HostAttempt {
            session_id: journal.session_id.clone(),
            call_id: call_id.clone(),
        });
        request.max_output_tokens = Some(256);
        let mut bytes = 0usize;
        let mut overflow = false;
        let outcome = backend.chat_stream(&request, &worker_cancel, &mut |delta| {
            bytes = bytes.saturating_add(delta.len());
            if bytes > 256 * 1024 {
                overflow = true;
                worker_cancel.cancel();
                return;
            }
            let mut remaining = delta;
            while !remaining.is_empty() {
                let mut end = remaining.len().min(8192);
                while !remaining.is_char_boundary(end) {
                    end -= 1;
                }
                if tx
                    .blocking_send(json!({"type": "delta", "text": &remaining[..end]}))
                    .is_err()
                {
                    worker_cancel.cancel();
                    break;
                }
                remaining = &remaining[end..];
            }
        });
        let (mut final_event, terminal, payload, outcome) = match outcome {
            af_llm::BackendOutcome::Completed(result) => {
                let valid_cost = result
                    .usage
                    .cost_usd
                    .is_some_and(|cost| cost.is_finite() && cost >= 0.0);
                let bounded = !overflow && result.content.len() <= 128 * 1024;
                let content = if bounded {
                    result.content
                } else {
                    String::new()
                };
                let payload = json!({"content": content, "cost_usd": result.usage.cost_usd,
                    "tokens_in": result.usage.tokens_in, "tokens_out": result.usage.tokens_out,
                    "truncated": result.truncated || !bounded, "verification": "not_assessed", "verification_report": verification_report, "coverage_scope": "selected_request_excerpts", "sources": sources});
                (
                    json!({"type": if bounded && valid_cost {"final"} else {"error"}, "ok": bounded && valid_cost,
                    "content": content, "tokens_in": result.usage.tokens_in, "tokens_out": result.usage.tokens_out,
                    "cost_usd": result.usage.cost_usd, "truncated": result.truncated || !bounded,
                    "verification": "not_assessed", "verification_report": verification_report, "coverage_scope": "selected_request_excerpts", "sources": sources, "unknown": !valid_cost,
                    "error": if !bounded {"stream output limit exceeded"} else if !valid_cost {"usage is unresolved"} else {""}}),
                    "model_response",
                    payload,
                    if valid_cost { "success" } else { "unknown" },
                )
            }
            af_llm::BackendOutcome::Refused { sent, .. } => (
                json!({"type": "error", "refused": true, "sent": sent, "unknown": sent, "error": "backend refused request"}),
                "model_failed",
                json!({"sent": sent}),
                if sent { "unknown" } else { "not_sent" },
            ),
            af_llm::BackendOutcome::Unknown { sent, .. } => (
                json!({"type": "error", "unknown": true, "sent": sent, "error": "backend outcome is unresolved; do not replay"}),
                "model_failed",
                json!({"sent": sent}),
                "unknown",
            ),
        };
        // Account a completely received result before checking receiver/cancel.
        // Unknown terminal outcomes retain the independent durable effect hold.
        if let Some(journal) = journal.as_mut() {
            if journal
                .finish_effect(terminal, &call_id, &payload, outcome)
                .is_err()
            {
                final_event = json!({"type": "error", "unknown": true,
                    "error": "durable result recording failed; inspect session before continuing"});
            }
        }
        // Budget settlement mirrors the terminal outcome. Without a tariff
        // table the settled actual is the reserved commitment itself —
        // conservative, never cheaper than evidence.
        if let Some((mut ledger, amount)) = budget {
            let settlement = match outcome {
                "success" => ledger.settle(&call_id, &task_id, amount).map(|_| "settled"),
                "unknown" => ledger.mark_unknown(&call_id, &task_id).map(|_| "unknown"),
                "not_sent" => ledger
                    .release_not_sent(&call_id, &task_id)
                    .map(|_| "released"),
                _ => Ok(()).map(|_: ()| "noop"),
            };
            if let Err(error) = settlement {
                final_event = json!({"type": "error", "unknown": true,
                    "error": format!("budget settlement failed; durable hold retained ({})", error.0)});
            }
        }
        let _ = tx.blocking_send(final_event);
    });
    (rx, cancel)
}

fn backend_from_config(config: &BackendConfig) -> Result<Box<dyn af_llm::ChatBackend>, StatusCode> {
    // A browser cannot choose a host environment variable and an exfiltration
    // URL. Real providers require a separately admitted host configuration and
    // the durable budget gate. The local scripted path sends nothing externally.
    crate::trusted_provider::select(
        config.provider_id.as_deref(),
        &config.base_url,
        &config.api_key_env,
        &config.model,
    )
    .map_err(|_| StatusCode::FORBIDDEN)
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ChatStreamBody {
    backend: BackendConfig,
    prompt: String,
    session_id: Option<String>,
    root_id: Option<String>,
    /// Operator-set per-call reservation against the af-budget ledger. When
    /// present the call is refused before any provider contact if the
    /// reservation does not fit under the cap or a hold exists.
    #[serde(default)]
    reserve_usd: Option<String>,
}

struct CancellableSse {
    inner: ReceiverStream<Value>,
    cancel: af_llm::Cancel,
}
impl Drop for CancellableSse {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}
impl tokio_stream::Stream for CancellableSse {
    type Item = Result<Event, std::convert::Infallible>;
    fn poll_next(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        let this = self.get_mut();
        match tokio_stream::Stream::poll_next(std::pin::Pin::new(&mut this.inner), cx) {
            std::task::Poll::Ready(Some(value)) => std::task::Poll::Ready(Some(Ok(
                Event::default().json_data(value).unwrap_or_else(|_| {
                    Event::default().data("{\"type\":\"error\",\"error\":\"invalid host event\"}")
                }),
            ))),
            std::task::Poll::Ready(None) => std::task::Poll::Ready(None),
            std::task::Poll::Pending => std::task::Poll::Pending,
        }
    }
}

async fn chat_stream_sse(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Json(body): Json<ChatStreamBody>,
) -> Result<axum::response::Response, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    if body.prompt.is_empty()
        || body.prompt.len() > 64 * 1024
        || body.backend.model.is_empty()
        || body.backend.model.len() > 128
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    if body
        .backend
        .provider_id
        .as_deref()
        .is_some_and(|id| id != "scripted")
        && body.session_id.is_none()
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let backend = backend_from_config(&body.backend)?;
    let (augmented_prompt, sources) =
        studio_data::context_for(body.root_id.as_deref(), &body.prompt)
            .map_err(|_| StatusCode::BAD_REQUEST)?;
    let permit = state
        .streams
        .clone()
        .try_acquire_owned()
        .map_err(|_| StatusCode::TOO_MANY_REQUESTS)?;
    let call_id = af_entropy::token();
    let journal = if let Some(session_id) = body.session_id {
        let mut journal =
            af_journal::SessionJournal::open(&session_id).map_err(|_| StatusCode::NOT_FOUND)?;
        let previous = journal.read().map_err(|_| StatusCode::CONFLICT)?;
        let bound_root = previous
            .events
            .first()
            .and_then(|event| event.pointer("/payload/metadata/root_id"))
            .and_then(Value::as_str);
        if bound_root.is_some() && body.root_id.as_deref() != bound_root {
            return Err(StatusCode::CONFLICT);
        }
        journal.begin_effect("model_request", &call_id, &json!({"model": body.backend.model,
            "transport": if body.backend.provider_id.is_some() {"host_configured"} else {"scripted"},
            "provider_id": body.backend.provider_id, "root_id": body.root_id,
            "prompt": body.prompt, "sources": sources}))
            .map_err(|_| StatusCode::CONFLICT)?;
        Some(journal)
    } else {
        None
    };
    // Operator-requested budget enforcement: the ledger must already exist
    // (Settings → Бюджет). A missing ledger is a conflict, not a silent
    // unaccounted send.
    let budget = match body.reserve_usd.as_deref() {
        Some(amount_text) => {
            let ledger_path = budget_ledger_path();
            if !ledger_path.exists() {
                return Err(StatusCode::CONFLICT);
            }
            let amount =
                af_budget::Money::from_usd_str(amount_text).map_err(|_| StatusCode::BAD_REQUEST)?;
            match af_budget::Ledger::open(&ledger_path, BUDGET_OWNER) {
                Ok(ledger) => Some((ledger, amount)),
                Err(_) => return Err(StatusCode::CONFLICT),
            }
        }
        None => None,
    };
    let (rx, cancel) = spawn_chat_stream(
        backend,
        body.backend.model,
        augmented_prompt,
        sources,
        journal,
        call_id,
        permit,
        budget,
    );
    let stream = CancellableSse {
        inner: ReceiverStream::new(rx),
        cancel,
    };
    Ok(Sse::new(stream)
        .keep_alive(KeepAlive::default())
        .into_response())
}

async fn job_status(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Query(query): Query<JobsQuery>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let store = af_sandbox::user_dir().join("jobs");
    if query
        .store
        .as_ref()
        .is_some_and(|requested| requested != &store)
    {
        return Err(StatusCode::FORBIDDEN);
    }
    af_sandbox::registry::ensure_no_links(&store).map_err(|_| StatusCode::BAD_REQUEST)?;
    let runner = af_exec::JobRunner::open(&store).map_err(|_| StatusCode::BAD_REQUEST)?;
    let jobs = runner
        .jobs()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let unknown = runner
        .unresolved_unknown_ids()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(json!({
        "ok": true, "jobs": jobs.values().collect::<Vec<_>>(),
        "unresolved_unknown_ids": unknown,
    })))
}

/// Bind policy: loopback by default; non-loopback requires a server token.
pub fn bind_allowed(bind: &str, token: &Option<String>) -> Result<(), String> {
    let host = bind.rsplit_once(':').map(|(host, _)| host).unwrap_or(bind);
    let loopback = matches!(host, "127.0.0.1" | "localhost" | "[::1]" | "::1");
    if !loopback && token.as_ref().is_none_or(String::is_empty) {
        return Err("non-loopback bind requires --token (server auth)".into());
    }
    Ok(())
}

pub async fn run(bind: &str, token: Option<String>) -> Result<(), String> {
    bind_allowed(bind, &token)?;
    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .map_err(|error| format!("bind {bind} failed: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|_| "listener identity unavailable".to_string())?
        .port();
    let app = build_router_for(token, port);
    axum::serve(listener, app)
        .await
        .map_err(|error| format!("serve failed: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    /// ATOMIC_FLOW_HOME is process-wide: every test that redirects it must
    /// hold this lock for its whole body, or a concurrently running test
    /// reads/writes another test's state home (observed as a 404 mid-test).
    static HOME_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

    #[tokio::test]
    async fn health_is_open_loopback_style_without_token() {
        let app = build_router(None);
        let response = app
            .oneshot(
                axum::http::Request::builder()
                    .uri("/health")
                    .header("Host", "127.0.0.1:8787")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn token_is_enforced_with_constant_time_path() {
        let app = build_router(Some("s3cret-token".into()));
        let denied = app
            .clone()
            .oneshot(
                axum::http::Request::builder()
                    .uri("/health")
                    .header("Host", "127.0.0.1:8787")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
        let wrong = app
            .clone()
            .oneshot(
                axum::http::Request::builder()
                    .uri("/health")
                    .header("Host", "127.0.0.1:8787")
                    .header("Authorization", "Bearer wrong-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(wrong.status(), StatusCode::UNAUTHORIZED);
        let ok = app
            .oneshot(
                axum::http::Request::builder()
                    .uri("/health")
                    .header("Host", "127.0.0.1:8787")
                    .header("Authorization", "Bearer s3cret-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(ok.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn session_endpoints_roundtrip() {
        let _home_guard = HOME_LOCK.lock().await;
        let home = std::env::temp_dir().join(format!("af-serve-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", &home);
        let app = build_router(None);

        let created = app
            .clone()
            .oneshot(
                axum::http::Request::builder()
                    .method("POST")
                    .uri("/v1/sessions")
                    .header("Host", "127.0.0.1:8787")
                    .header("Content-Type", "application/json")
                    .body(Body::from(json!({"id": "serve-test"}).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(created.status(), StatusCode::OK);
        let created_json: Value = {
            let bytes = created.into_body().collect().await.unwrap().to_bytes();
            serde_json::from_slice(&bytes).unwrap()
        };
        assert_eq!(created_json["ok"], json!(true));

        let appended = app
            .clone()
            .oneshot(
                axum::http::Request::builder()
                    .method("POST")
                    .uri("/v1/sessions/serve-test/events")
                    .header("Host", "127.0.0.1:8787")
                    .header("Content-Type", "application/json")
                    .body(Body::from(
                        json!({"event_type": "user_input", "payload": {"text": "hi"}}).to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(appended.status(), StatusCode::OK);

        let read = app
            .oneshot(
                axum::http::Request::builder()
                    .uri("/v1/sessions/serve-test")
                    .header("Host", "127.0.0.1:8787")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(read.status(), StatusCode::OK);
        let read_json: Value = {
            let bytes = read.into_body().collect().await.unwrap().to_bytes();
            serde_json::from_slice(&bytes).unwrap()
        };
        assert_eq!(read_json["events"], json!(2));
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        drop(_home_guard);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn bind_policy_requires_token_off_loopback() {
        assert!(bind_allowed("127.0.0.1:8787", &None).is_ok());
        assert!(bind_allowed("localhost:8787", &None).is_ok());
        assert!(bind_allowed("0.0.0.0:8787", &None).is_err());
        assert!(bind_allowed("0.0.0.0:8787", &Some("t".into())).is_ok());
        assert!(bind_allowed("192.168.0.2:8787", &None).is_err());
    }

    #[tokio::test]
    async fn sse_stream_via_scripted_backend() {
        let app = build_router(None);
        let response = app
            .oneshot(
                axum::http::Request::builder()
                    .method("POST")
                    .uri("/v1/chat/stream")
                    .header("Host", "127.0.0.1:8787")
                    .header("Content-Type", "application/json")
                    .body(Body::from(
                        json!({
                            "backend": {"base_url": "scripted://local", "api_key_env": "", "model": "scripted"},
                            "prompt": "ping"
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert!(response
            .headers()
            .get("Content-Type")
            .is_some_and(|value| value.to_str().unwrap().starts_with("text/event-stream")));
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        let body = String::from_utf8(bytes.to_vec()).unwrap();
        assert!(
            body.contains("\"type\":\"final\""),
            "final event present: {body}"
        );
        assert!(
            body.contains("scripted-echo"),
            "settled content in final: {body}"
        );
    }

    #[tokio::test]
    async fn dashboard_is_served() {
        let app = build_router(None);
        let response = app
            .oneshot(
                axum::http::Request::builder()
                    .uri("/dashboard")
                    .header("Host", "127.0.0.1:8787")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        let html = String::from_utf8(bytes.to_vec()).unwrap();
        assert!(
            html.contains("Atomic Flow Studio") && html.contains("Context Explorer"),
            "studio markup present"
        );
    }

    fn request(method: &str, uri: &str, body: Value) -> axum::http::Request<Body> {
        axum::http::Request::builder()
            .method(method)
            .uri(uri)
            .header("Host", "127.0.0.1:8787")
            .header("Content-Type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    #[tokio::test]
    async fn hostile_origin_is_denied_even_with_an_operator_bearer() {
        let app = build_router(Some("operator-test-bearer".into()));
        let mut req = request("POST", "/v1/pick-folder", json!({}));
        req.headers_mut()
            .insert("Origin", "http://evil.example".parse().unwrap());
        req.headers_mut().insert(
            "Authorization",
            "Bearer operator-test-bearer".parse().unwrap(),
        );
        // Must reject before opening a native dialog or touching host state.
        assert_eq!(
            app.oneshot(req).await.unwrap().status(),
            StatusCode::FORBIDDEN
        );
    }

    #[tokio::test]
    async fn static_authentication_ui_has_nonce_csp_but_api_remains_private() {
        let app = build_router(Some("operator-test-bearer".into()));
        let response = app
            .clone()
            .oneshot(request("GET", "/dashboard", json!({})))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let policy = response.headers()["Content-Security-Policy"]
            .to_str()
            .unwrap()
            .to_string();
        assert!(policy.contains("script-src 'nonce-"));
        assert!(!policy.contains("script-src 'unsafe-inline'"));
        let html = String::from_utf8(
            response
                .into_body()
                .collect()
                .await
                .unwrap()
                .to_bytes()
                .to_vec(),
        )
        .unwrap();
        let nonce = policy
            .split("script-src 'nonce-")
            .nth(1)
            .unwrap()
            .split('\'')
            .next()
            .unwrap();
        assert!(html.contains(&format!("<script nonce=\"{nonce}\">")));
        assert_eq!(
            app.oneshot(request("GET", "/health", json!({})))
                .await
                .unwrap()
                .status(),
            StatusCode::UNAUTHORIZED
        );
    }

    #[tokio::test]
    async fn browser_cannot_choose_credential_env_or_provider_destination() {
        let app = build_router(None);
        let body = json!({"backend": {"base_url": "https://attacker.invalid/v1", "api_key_env": "PATH", "model": "arbitrary"}, "prompt": "test"});
        assert_eq!(
            app.oneshot(request("POST", "/v1/chat/stream", body))
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
    }

    #[tokio::test]
    async fn browser_cannot_forge_kernel_terminal_history() {
        for event_type in [
            "model_response",
            "tool_result",
            "host_reconciliation",
            "snapshot_created",
            "completed",
        ] {
            let response = build_router(None)
                .oneshot(request(
                    "POST",
                    "/v1/sessions/does-not-exist/events",
                    json!({"event_type": event_type, "payload": {"verified": true}}),
                ))
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::FORBIDDEN, "{event_type}");
        }
    }

    #[tokio::test]
    async fn graph_and_job_queries_never_grant_arbitrary_host_paths() {
        assert_eq!(
            build_router(None)
                .oneshot(request("GET", "/v1/graph/..%2Fsecret", json!({})))
                .await
                .unwrap()
                .status(),
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            build_router(None)
                .oneshot(request("GET", "/v1/jobs?store=/not-approved", json!({})))
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
    }

    #[tokio::test]
    async fn body_and_prompt_limits_refuse_before_backend_work() {
        let mut body = json!({"backend": {"base_url": "scripted://local", "api_key_env": "", "model": "scripted"}, "prompt": "x".repeat(65537)});
        assert_eq!(
            build_router(None)
                .oneshot(request("POST", "/v1/chat/stream", body.clone()))
                .await
                .unwrap()
                .status(),
            StatusCode::BAD_REQUEST
        );
        body["prompt"] = json!("x".repeat(1_048_577));
        assert_eq!(
            build_router(None)
                .oneshot(request("POST", "/v1/chat/stream", body))
                .await
                .unwrap()
                .status(),
            StatusCode::PAYLOAD_TOO_LARGE
        );
    }

    #[tokio::test]
    async fn oversized_terminal_content_is_never_reported_as_a_complete_answer() {
        let backend = af_llm::ScriptedBackend::new(
            "oversized",
            vec![af_llm::BackendOutcome::Completed(af_llm::ChatResult {
                content: "x".repeat(128 * 1024 + 1),
                truncated: false,
                tool_calls: vec![],
                usage: af_llm::Usage {
                    tokens_in: Some(1),
                    tokens_out: Some(1),
                    cost_usd: Some(0.0),
                    latency_ms: Some(0),
                },
            })],
        );
        let semaphore = Arc::new(tokio::sync::Semaphore::new(1));
        let (mut receiver, _) = spawn_chat_stream(
            Box::new(backend),
            "scripted".into(),
            "test".into(),
            vec![],
            None,
            "bounded-output".into(),
            semaphore.clone().try_acquire_owned().unwrap(),
            None,
        );
        let terminal = receiver.recv().await.unwrap();
        assert_eq!(terminal["type"], json!("error"));
        assert_eq!(terminal["ok"], json!(false));
        assert_eq!(terminal["truncated"], json!(true));
        assert_eq!(terminal["content"], json!(""));
    }

    #[tokio::test]
    async fn budget_gates_the_stream_and_settles_by_outcome() {
        fn temp_ledger(name: &str) -> (std::path::PathBuf, af_budget::Ledger) {
            let dir = std::env::temp_dir()
                .join(format!("af-serve-budget-{}-{name}", af_entropy::token()));
            std::fs::create_dir_all(&dir).unwrap();
            let path = dir.join("ledger.jsonl");
            af_budget::Ledger::create(&path, BUDGET_OWNER, af_budget::Money(10), 8).unwrap();
            let ledger = af_budget::Ledger::open(&path, BUDGET_OWNER).unwrap();
            (path, ledger)
        }

        fn scripted(outcome: af_llm::BackendOutcome) -> Box<dyn af_llm::ChatBackend> {
            Box::new(af_llm::ScriptedBackend::new(
                "budget-scripted",
                vec![outcome],
            ))
        }

        fn completed() -> af_llm::BackendOutcome {
            af_llm::BackendOutcome::Completed(af_llm::ChatResult {
                content: "ok".into(),
                truncated: false,
                tool_calls: vec![],
                usage: af_llm::Usage {
                    tokens_in: Some(3),
                    tokens_out: Some(4),
                    cost_usd: Some(0.0),
                    latency_ms: Some(1),
                },
            })
        }

        // Settled success: reserved amount is charged, no hold, one more
        // reservation still fits under the cap.
        let (path, ledger) = temp_ledger("settled");
        let (mut receiver, _) = spawn_chat_stream(
            scripted(completed()),
            "scripted".into(),
            "test".into(),
            vec![],
            None,
            "call-ok".into(),
            Arc::new(tokio::sync::Semaphore::new(1))
                .try_acquire_owned()
                .unwrap(),
            Some((ledger, af_budget::Money(4))),
        );
        let terminal = receiver.recv().await.unwrap();
        assert_eq!(terminal["type"], json!("final"));
        let ledger = af_budget::Ledger::open(&path, BUDGET_OWNER).unwrap();
        let snapshot = ledger.snapshot().unwrap();
        assert_eq!(snapshot.committed().unwrap(), af_budget::Money(4));
        assert!(!snapshot.has_hold());

        // Unknown outcome: the call keeps its durable hold and blocks every
        // further reservation — fail-closed.
        let (path, ledger) = temp_ledger("unknown");
        let (mut receiver, _) = spawn_chat_stream(
            scripted(af_llm::BackendOutcome::Unknown {
                sent: true,
                detail: "scripted unresolved".into(),
            }),
            "scripted".into(),
            "test".into(),
            vec![],
            None,
            "call-unknown".into(),
            Arc::new(tokio::sync::Semaphore::new(1))
                .try_acquire_owned()
                .unwrap(),
            Some((ledger, af_budget::Money(3))),
        );
        let terminal = receiver.recv().await.unwrap();
        assert_eq!(terminal["type"], json!("error"));
        assert_eq!(terminal["unknown"], json!(true));
        let mut ledger = af_budget::Ledger::open(&path, BUDGET_OWNER).unwrap();
        assert!(ledger.snapshot().unwrap().has_hold());
        assert!(ledger.reserve("next", "task", af_budget::Money(1)).is_err());

        // Reserve over the cap: the provider is never contacted.
        let (_, ledger) = temp_ledger("over-cap");
        let (mut receiver, _) = spawn_chat_stream(
            scripted(completed()),
            "scripted".into(),
            "test".into(),
            vec![],
            None,
            "call-over-cap".into(),
            Arc::new(tokio::sync::Semaphore::new(1))
                .try_acquire_owned()
                .unwrap(),
            Some((ledger, af_budget::Money(11))),
        );
        let terminal = receiver.recv().await.unwrap();
        assert_eq!(terminal["type"], json!("error"));
        assert_eq!(terminal["unknown"], json!(false));
        assert!(terminal["error"]
            .as_str()
            .unwrap()
            .contains("budget reserve refused"));
    }

    #[tokio::test]
    async fn disconnected_stream_cancels_the_actual_worker_and_releases_capacity() {
        struct WaitForCancel(Arc<std::sync::atomic::AtomicBool>);
        impl af_llm::ChatBackend for WaitForCancel {
            fn name(&self) -> &str {
                "wait-for-cancel"
            }
            fn chat(
                &self,
                _: &af_llm::ChatRequest,
                cancel: &af_llm::Cancel,
            ) -> af_llm::BackendOutcome {
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
                while !cancel.is_cancelled() && std::time::Instant::now() < deadline {
                    std::thread::sleep(std::time::Duration::from_millis(1));
                }
                self.0
                    .store(cancel.is_cancelled(), std::sync::atomic::Ordering::SeqCst);
                af_llm::BackendOutcome::Refused {
                    sent: false,
                    reason: "cancelled before send".into(),
                }
            }
        }
        let observed = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let semaphore = Arc::new(tokio::sync::Semaphore::new(1));
        let (rx, cancel) = spawn_chat_stream(
            Box::new(WaitForCancel(observed.clone())),
            "scripted".into(),
            "test".into(),
            vec![],
            None,
            "cancel-test".into(),
            semaphore.clone().try_acquire_owned().unwrap(),
            None,
        );
        drop(CancellableSse {
            inner: ReceiverStream::new(rx),
            cancel,
        });
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while semaphore.available_permits() != 1 && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        assert!(observed.load(std::sync::atomic::Ordering::SeqCst));
        assert_eq!(semaphore.available_permits(), 1);
    }

    #[tokio::test]
    async fn settled_chat_is_durable_and_an_existing_unknown_is_never_replayed() {
        let _home_guard = HOME_LOCK.lock().await;
        struct IsolatedHome {
            path: PathBuf,
            previous: Option<std::ffi::OsString>,
        }
        impl Drop for IsolatedHome {
            fn drop(&mut self) {
                match &self.previous {
                    Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
                    None => std::env::remove_var("ATOMIC_FLOW_HOME"),
                }
                let _ = std::fs::remove_dir_all(&self.path);
            }
        }
        let isolated = IsolatedHome {
            path: std::env::temp_dir().join(format!("af-http-chat-{}", af_entropy::token())),
            previous: std::env::var_os("ATOMIC_FLOW_HOME"),
        };
        std::fs::create_dir_all(&isolated.path).unwrap();
        std::env::set_var("ATOMIC_FLOW_HOME", &isolated.path);
        af_journal::SessionJournal::create(&isolated.path, Some("settled-chat"), None).unwrap();
        let body = json!({"backend": {"base_url": "scripted://local", "model": "scripted"}, "prompt": "ping", "session_id": "settled-chat"});
        let response = build_router(None)
            .oneshot(request("POST", "/v1/chat/stream", body))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let raw = response.into_body().collect().await.unwrap().to_bytes();
        assert!(String::from_utf8_lossy(&raw).contains("\"type\":\"final\""));
        let snapshot = af_journal::SessionJournal::open("settled-chat")
            .unwrap()
            .read()
            .unwrap();
        assert!(snapshot.unresolved_call_ids().is_empty());
        assert!(snapshot.pending_call_ids().is_empty());
        assert_eq!(
            snapshot.events.last().unwrap()["payload"]["content"],
            json!("scripted-echo")
        );
        assert_eq!(
            snapshot.events.last().unwrap()["payload"]["verification"],
            json!("not_assessed")
        );
        let mut journal =
            af_journal::SessionJournal::create(&isolated.path, Some("unknown-chat"), None).unwrap();
        journal
            .begin_effect("model_request", "old-call", &json!({"model": "scripted"}))
            .unwrap();
        let before = std::fs::read(&journal.path).unwrap();
        let body = json!({"backend": {"base_url": "scripted://local", "model": "scripted"}, "prompt": "do not replay", "session_id": "unknown-chat"});
        assert_eq!(
            build_router(None)
                .oneshot(request("POST", "/v1/chat/stream", body))
                .await
                .unwrap()
                .status(),
            StatusCode::CONFLICT
        );
        assert_eq!(std::fs::read(&journal.path).unwrap(), before);
        assert!(journal.path.with_extension("effect-hold.json").exists());
        // Financial projection distinguishes measured spend from reservations,
        // and never turns unresolved exposure into a zero available balance.
        let amount = |value| af_budget::Money(value * af_budget::NANODOLLARS_PER_USD);
        let mut ledger =
            af_budget::Ledger::create(&budget_ledger_path(), BUDGET_OWNER, amount(10), 10).unwrap();
        ledger.reserve("settled", "owner", amount(3)).unwrap();
        ledger.begin_send("settled", "owner").unwrap();
        ledger.settle("settled", "owner", amount(2)).unwrap();
        ledger.reserve("held", "owner", amount(4)).unwrap();
        let response = build_router(None)
            .oneshot(request("GET", "/v1/budget", json!({})))
            .await
            .unwrap();
        let projection: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(projection["spent_usd_known"], json!("2"));
        assert_eq!(projection["reserved_usd"], json!("4"));
        assert_eq!(projection["committed_usd"], json!("6"));
        assert_eq!(projection["available_usd"], json!("4"));
        ledger.begin_send("held", "owner").unwrap();
        let response = build_router(None)
            .oneshot(request("GET", "/v1/budget", json!({})))
            .await
            .unwrap();
        let projection: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(projection["state"], json!("hold"));
        assert_eq!(projection["unknown_cost_present"], json!(true));
        assert_eq!(projection["available_usd"], Value::Null);
        assert_eq!(projection["spent_usd_known"], json!("2"));
    }
}

#[cfg(test)]
mod evidence_adapter_tests {
    #[test]
    fn delivered_context_is_bound_without_inventing_requirement_success() {
        let sources =
            vec![serde_json::json!({"root_id":"r", "path":"a.rs", "sha256":"a".repeat(64)})];
        let report = super::request_report("delivered body", &sources, "test");
        let parsed =
            af_evidence::parse_report(&af_evidence::report_json(&report).unwrap()).unwrap();
        assert_eq!(parsed.requirements.checked, 0);
        assert_eq!(
            parsed.requirements.status,
            af_evidence::Assessment::NotAssessed
        );
        assert_eq!(parsed.evidence_coverage.delivered_complete, 0);
        assert_ne!(
            report.binding.inventory_sha256,
            super::request_report("changed body", &sources, "test")
                .binding
                .inventory_sha256
        );
    }
}
