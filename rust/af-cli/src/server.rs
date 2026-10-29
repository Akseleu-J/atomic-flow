//! `af serve`: the self-host HTTP API (headless, E08-compatible). Loopback by
//! default; any non-loopback bind REQUIRES a server token, compared in
//! constant time. Sessions, edit journeys and job status over JSON.

use axum::{
    extract::{Path as AxPath, Query, State},
    http::{HeaderMap, StatusCode},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};

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
}

fn authorized(state: &ServerState, headers: &HeaderMap) -> bool {
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

fn build_router(token: Option<String>) -> Router {
    let state = ServerState { token };
    Router::new()
        .route("/health", get(health))
        .route("/v1/sessions", post(create_session))
        .route("/v1/sessions/{id}/events", post(append_event))
        .route("/v1/sessions/{id}", get(read_session))
        .route("/v1/edit-journeys", post(edit_journey))
        .route("/v1/jobs", get(job_status))
        .with_state(state)
}

fn auth_denied(state: &ServerState, headers: &HeaderMap) -> Option<StatusCode> {
    if authorized(state, headers) {
        None
    } else {
        Some(StatusCode::UNAUTHORIZED)
    }
}

async fn health(State(state): State<ServerState>, headers: HeaderMap) -> StatusCode {
    if auth_denied(&state, &headers).is_some() {
        return StatusCode::UNAUTHORIZED;
    }
    StatusCode::OK
}

#[derive(serde::Deserialize)]
struct CreateSessionBody {
    id: Option<String>,
    project: Option<String>,
}

async fn create_session(
    State(state): State<ServerState>,
    headers: HeaderMap,
    body: Option<Json<CreateSessionBody>>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let body = body.map(|Json(body)| body).unwrap_or(CreateSessionBody { id: None, project: None });
    let project = body.project.unwrap_or_else(|| ".".into());
    let journal = af_journal::SessionJournal::create(
        std::path::Path::new(&project),
        body.id.as_deref(),
        None,
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
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|_| StatusCode::NOT_FOUND)?;
    let event = journal
        .append(&body.event_type, &body.payload, body.call_id.as_deref(), body.outcome.as_deref(), None)
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
struct BackendConfig {
    base_url: String,
    api_key_env: String,
    model: String,
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
    let backend = af_llm::OpenAiBackend::new("openai", &body.backend.base_url, &body.backend.api_key_env);
    let router = af_llm::Router::new(vec![Box::new(backend)]);
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let project = std::env::current_dir().map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let session_id = body.session_id.unwrap_or_else(|| format!("serve-{}", af_entropy::token()));
    let mut journal = af_journal::SessionJournal::create(&project, Some(&session_id), None)
        .or_else(|_| af_journal::SessionJournal::open(&session_id))
        .map_err(|_| StatusCode::CONFLICT)?;
    let toolset = crate::edit_toolset_for(&registry, &body.root_id)
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    let outcome = af_loop::edit::run_edit_task(
        &router,
        &toolset,
        Some(&mut journal),
        &body.backend.model,
        &body.goal,
        true,
        af_loop::LoopLimits::default(),
        None,
    )
    .map_err(|error| StatusCode::BAD_REQUEST)?;
    let (ok, verified, refusal) = match &outcome {
        af_loop::edit::EditOutcome::Completed { content, check, git, .. } => (
            true,
            check["status"] == json!("succeeded"),
            json!({"git": git, "content_chars": content.chars().count()}),
        ),
        af_loop::edit::EditOutcome::CheckFailed { check, git, .. } => {
            (false, false, json!({"git": git, "reason": "final gate failed"}))
        }
        af_loop::edit::EditOutcome::Interrupted { loop_outcome, .. } => {
            (false, false, json!({"reason": format!("{loop_outcome:?}")}))
        }
    };
    Ok(Json(json!({
        "ok": ok, "verified": verified, "refusal": refusal,
        "session_id": journal.session_id,
        "edits": toolset.edits_used(),
    })))
}

#[derive(serde::Deserialize)]
struct JobsQuery {
    store: PathBuf,
}

use std::path::PathBuf;

async fn job_status(
    State(state): State<ServerState>,
    headers: HeaderMap,
    Query(query): Query<JobsQuery>,
) -> Result<Json<Value>, StatusCode> {
    if auth_denied(&state, &headers).is_some() {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let runner = af_exec::JobRunner::open(&query.store).map_err(|_| StatusCode::BAD_REQUEST)?;
    let jobs = runner.jobs().map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let unknown = runner.unresolved_unknown_ids().map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
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
    let app = build_router(token);
    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .map_err(|error| format!("bind {bind} failed: {error}"))?;
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

    #[tokio::test]
    async fn health_is_open_loopback_style_without_token() {
        let app = build_router(None);
        let response = app
            .oneshot(
                axum::http::Request::builder()
                    .uri("/health")
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
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
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
        drop(guard);
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
}
