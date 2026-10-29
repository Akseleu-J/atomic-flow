//! af-journal: bounded, redacted, append-only session observations, never
//! executable replay (port of `sessions.py`). A terminated line is a committed
//! event. An interrupted final line is exposed as untrusted tail bytes and
//! blocks further appends until explicit recovery. A model or tool request
//! without a terminal event remains pending: reading never retries it.

use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{BufRead, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::OnceLock;

pub mod segments;

pub const SCHEMA_VERSION: i64 = 1;
pub const MAX_EVENT_BYTES: usize = 256 * 1024;
pub const MAX_SESSION_BYTES: u64 = 64 * 1024 * 1024;

pub const EVENT_TYPES: [&str; 22] = [
    "session_created",
    "user_input",
    "model_request",
    "model_response",
    "model_failed",
    "tool_request",
    "tool_result",
    "tool_effect_unknown",
    "question",
    "answer",
    "completed",
    "failed",
    "tail_recovered",
    "steering_applied",
    "response_discarded",
    "context_summary",
    "continuation",
    "dialogue_state",
    "snapshot_created",
    "rewind_applied",
    "fork_created",
    "host_reconciliation",
];

const OUTCOMES: [&str; 8] = [
    "success",
    "error",
    "denied",
    "cancelled",
    "unknown",
    "pending",
    "refused",
    "not_sent",
];

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct JournalError(pub String);

pub type JournalResult<T> = Result<T, JournalError>;

fn err<T>(message: impl Into<String>) -> JournalResult<T> {
    Err(JournalError(message.into()))
}

fn valid_session_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn valid_call_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b':' | b'-'))
}

fn is_hex32(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn is_hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

#[allow(dead_code)]
fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

/// Application state directory (same contract as af-sandbox).
pub fn user_dir() -> PathBuf {
    if let Ok(home) = std::env::var("ATOMIC_FLOW_HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string());
    std::path::Path::new(&home).join(".atomic_flow")
}

static SECRET_KEY: OnceLock<Regex> = OnceLock::new();
static SECRET_RE: &str =
    "(?i)(?:password|passwd|secret|authorization|api[_-]?key|(?:^|_)token(?:$|_))";

/// Recursive payload sanitizer: strings redacted, finite JSON only, secret
/// keys replaced wholesale. Depth is bounded.
pub fn clean(value: &Value, depth: usize) -> JournalResult<Value> {
    if depth > 16 {
        return err("session payload nesting exceeds 16 levels");
    }
    let secret_key = SECRET_KEY.get_or_init(|| Regex::new(SECRET_RE).expect("static pattern"));
    match value {
        Value::Null | Value::Bool(_) => Ok(value.clone()),
        Value::Number(number) => {
            if let Some(float) = number.as_f64() {
                if float.is_finite() {
                    return Ok(value.clone());
                }
            }
            err("session payload must contain finite JSON values only")
        }
        Value::String(text) => Ok(Value::String(redact_text(text))),
        Value::Array(items) => {
            let mut out = Vec::with_capacity(items.len());
            for item in items {
                out.push(clean(item, depth + 1)?);
            }
            Ok(Value::Array(out))
        }
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                if secret_key.is_match(key) {
                    out.insert(key.clone(), json!("[REDACTED]"));
                } else {
                    out.insert(key.clone(), clean(item, depth + 1)?);
                }
            }
            Ok(Value::Object(out))
        }
    }
}

/// Text redaction (the three credential shapes from `activation/redact.py`).
pub fn redact_text(text: &str) -> String {
    static BLOCK: OnceLock<Regex> = OnceLock::new();
    static TOKENS: OnceLock<Regex> = OnceLock::new();
    static LITERAL: OnceLock<Regex> = OnceLock::new();
    let block = BLOCK.get_or_init(|| {
        Regex::new(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")
            .unwrap()
    });
    let tokens = TOKENS.get_or_init(|| {
        Regex::new(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}\b").unwrap()
    });
    let literal = LITERAL.get_or_init(|| {
        Regex::new(r#"(?i)(\b(?:api[_-]?key|secret|token|password|passwd))(\w*)(\s*[:=]\s*)(['"])([^'"\s]{8,})(['"])"#).unwrap()
    });
    let after_block = block.replace_all(text, |caps: &regex::Captures| {
        format!("[REDACTED]{}", "\n".repeat(caps[0].matches('\n').count()))
    });
    let after_tokens = tokens.replace_all(&after_block, "[REDACTED]");
    literal
        .replace_all(&after_tokens, |caps: &regex::Captures| {
            if caps[4] != caps[6] || caps[2].to_lowercase().contains("_env") {
                caps[0].to_string()
            } else {
                format!(
                    "{}{}{}{}[REDACTED]{}",
                    &caps[1], &caps[2], &caps[3], &caps[4], &caps[4]
                )
            }
        })
        .into_owned()
}

fn encode(event: &Value) -> Vec<u8> {
    let mut line = serde_json::to_string(event).expect("event is valid JSON");
    line.push('\n');
    line.into_bytes()
}

fn now_unix() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

#[derive(Debug, Clone)]
pub struct SessionSnapshot {
    pub session_id: String,
    pub events: Vec<Value>,
    pub incomplete_tail: bool,
    pub tail_bytes: usize,
}

impl SessionSnapshot {
    /// Calls sent but never terminally observed.
    pub fn pending_call_ids(&self) -> Vec<String> {
        let mut pending: BTreeMap<String, bool> = BTreeMap::new();
        for event in &self.events {
            let Some(call_id) = call_id_of(event) else {
                continue;
            };
            match event["event_type"].as_str().unwrap_or_default() {
                "model_request" | "tool_request" => {
                    pending.insert(call_id, true);
                }
                "model_response" | "model_failed" | "tool_result" => {
                    pending.remove(&call_id);
                }
                "host_reconciliation" => {
                    pending.remove(&call_id);
                }
                _ => {}
            }
        }
        pending.into_keys().collect()
    }

    /// Closed transport observations do not establish settled charges/effects.
    pub fn unresolved_call_ids(&self) -> Vec<String> {
        let mut charges: BTreeMap<String, ()> = BTreeMap::new();
        let mut effects: BTreeMap<String, ()> = BTreeMap::new();
        let mut unknown_charges: BTreeMap<String, ()> = BTreeMap::new();
        let mut unknown_effects: BTreeMap<String, ()> = BTreeMap::new();
        for event in &self.events {
            let Some(call_id) = call_id_of(event) else {
                continue;
            };
            let kind = event["event_type"].as_str().unwrap_or_default();
            let outcome = event
                .get("outcome")
                .and_then(Value::as_str)
                .unwrap_or_default();
            match kind {
                "model_request" => {
                    charges.insert(call_id.clone(), ());
                }
                "tool_request" => {
                    effects.insert(call_id.clone(), ());
                }
                "tool_effect_unknown" => {
                    effects.insert(call_id.clone(), ());
                    unknown_effects.insert(call_id.clone(), ());
                }
                "model_failed" => {
                    if outcome == "not_sent" || outcome == "refused" {
                        if !unknown_charges.contains_key(&call_id) {
                            charges.remove(&call_id);
                        }
                    } else {
                        charges.insert(call_id.clone(), ());
                        unknown_charges.insert(call_id.clone(), ());
                    }
                }
                "model_response" => {
                    let cost = event.pointer("/payload/cost_usd").and_then(Value::as_f64);
                    match cost {
                        Some(cost) if cost.is_finite() && cost >= 0.0 => {
                            if !unknown_charges.contains_key(&call_id) {
                                charges.remove(&call_id);
                            }
                        }
                        _ => {
                            charges.insert(call_id.clone(), ());
                            unknown_charges.insert(call_id.clone(), ());
                        }
                    }
                }
                "tool_result" => {
                    let mut uncertain = outcome == "unknown" || outcome == "pending";
                    if !uncertain {
                        let payload = event.get("payload").cloned().unwrap_or(Value::Null);
                        let mut candidates: Vec<&Value> = Vec::new();
                        if let Some(result) = payload.get("result") {
                            if result.is_object() {
                                candidates.push(result);
                            }
                        }
                        if let Some(call) = payload.get("call") {
                            if let Some(summary) = call.get("result_summary") {
                                if summary.is_object() {
                                    candidates.push(summary);
                                }
                            }
                        }
                        uncertain = candidates.iter().any(|item| {
                            item.get("status").and_then(Value::as_str) == Some("rollback_failed")
                                || item
                                    .get("side_effects_may_have_occurred")
                                    .is_some_and(|flag| {
                                        !flag.is_boolean() || flag.as_bool() != Some(false)
                                    })
                                || matches!(
                                    item.get("outcome").and_then(Value::as_str),
                                    Some("unknown") | Some("pending")
                                )
                        });
                    }
                    if uncertain {
                        effects.insert(call_id.clone(), ());
                        unknown_effects.insert(call_id.clone(), ());
                    } else if !unknown_effects.contains_key(&call_id) {
                        effects.remove(&call_id);
                    }
                }
                "host_reconciliation" => {
                    let scope = event
                        .pointer("/payload/scope")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    if scope == "charge" {
                        charges.remove(&call_id);
                        unknown_charges.remove(&call_id);
                    } else {
                        effects.remove(&call_id);
                        unknown_effects.remove(&call_id);
                    }
                }
                _ => {}
            }
        }
        let mut all: BTreeMap<String, ()> = BTreeMap::new();
        for key in charges.keys().chain(effects.keys()) {
            all.insert(key.clone(), ());
        }
        all.into_keys().collect()
    }
}

fn call_id_of(event: &Value) -> Option<String> {
    if let Some(id) = event.get("call_id").and_then(Value::as_str) {
        return Some(id.to_string());
    }
    event
        .pointer("/payload/call_id")
        .and_then(Value::as_str)
        .map(str::to_string)
}

pub struct SessionJournal {
    pub session_id: String,
    pub path: PathBuf,
    /// (last sequence, byte offset, running SHA-256 state over file[0..offset]).
    validated: Option<(usize, u64, Sha256)>,
}

impl SessionJournal {
    fn new(session_id: &str) -> JournalResult<SessionJournal> {
        if !valid_session_id(session_id) {
            return err("invalid session id: use 1-64 letters, digits, underscore or hyphen");
        }
        // Fixed prefix also avoids Windows reserved device names, e.g. CON.
        Ok(SessionJournal {
            session_id: session_id.to_string(),
            path: user_dir()
                .join("sessions")
                .join(format!("session_{session_id}.jsonl")),
            validated: None,
        })
    }

    pub fn create(
        project: &std::path::Path,
        session_id: Option<&str>,
        metadata: Option<&Value>,
    ) -> JournalResult<SessionJournal> {
        let journal = SessionJournal::new(session_id.unwrap_or(&af_entropy::token()))?;
        std::fs::create_dir_all(journal.path.parent().expect("sessions dir"))
            .map_err(|error| JournalError(format!("sessions directory failed: {error}")))?;
        let _lease = Self::lease(&journal.path)?;
        let mut event = Map::new();
        event.insert("project".into(), json!(project.to_string_lossy()));
        if let Some(meta) = metadata {
            event.insert("metadata".into(), clean(meta, 0)?);
        } else {
            event.insert("metadata".into(), json!({}));
        }
        let event = journal.build_event(1, "session_created", &Value::Object(event), None, None)?;
        let encoded = encode(&event);
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&journal.path)
            .map_err(|error| JournalError(format!("session create failed: {error}")))?;
        file.write_all(&encoded)
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| JournalError(format!("session fsync failed: {error}")))?;
        Ok(journal)
    }

    pub fn open(session_id: &str) -> JournalResult<SessionJournal> {
        let mut journal = SessionJournal::new(session_id)?;
        journal.read()?; // Validate committed records without modifying any bytes.
        Ok(journal)
    }

    fn lease(path: &std::path::Path) -> JournalResult<af_entropy::FileLease> {
        let lock = path.with_extension("lock");
        af_entropy::FileLease::acquire(&lock, true, "session is being updated by another process")
            .map_err(JournalError)
    }

    fn build_event(
        &self,
        sequence: usize,
        event_type: &str,
        payload: &Value,
        call_id: Option<&str>,
        outcome: Option<&str>,
    ) -> JournalResult<Value> {
        if !EVENT_TYPES.contains(&event_type) {
            return err(format!("unsupported session event type: {event_type}"));
        }
        if !payload.is_object() {
            return err("session event payload must be an object");
        }
        if event_type == "host_reconciliation" {
            let scope_ok = matches!(
                payload.get("scope").and_then(Value::as_str),
                Some("charge") | Some("effect")
            );
            let recon_ok = payload
                .get("reconciliation_id")
                .and_then(Value::as_str)
                .is_some_and(|id| id.strip_prefix("reconcile_").is_some_and(is_hex64));
            let evidence_ok = payload
                .get("evidence_sha256")
                .and_then(Value::as_str)
                .is_some_and(is_hex64);
            let actor = payload.get("actor").is_some_and(Value::is_string);
            let reason = payload.get("reason").is_some_and(Value::is_string);
            if !scope_ok || !recon_ok || !evidence_ok || !actor || !reason || call_id.is_none() {
                return err("invalid host reconciliation observation");
            }
        }
        if let Some(inner) = payload.get("call_id") {
            if !valid_call_id(inner.as_str().unwrap_or_default()) {
                return err("invalid session payload call_id");
            }
        }
        if let Some(id) = call_id {
            if !valid_call_id(id) {
                return err("invalid session call_id");
            }
        }
        if let Some(outcome) = outcome {
            if !OUTCOMES.contains(&outcome) {
                return err("invalid session outcome");
            }
        }
        let cleaned = clean(payload, 0)?;
        let mut event = Map::new();
        event.insert("schema_version".into(), json!(SCHEMA_VERSION));
        event.insert("session_id".into(), json!(self.session_id));
        event.insert("event_id".into(), json!(af_entropy::token()));
        event.insert("sequence".into(), json!(sequence));
        event.insert("timestamp".into(), json!(now_unix()));
        event.insert("event_type".into(), json!(event_type));
        event.insert("payload".into(), cleaned);
        let mut value = Value::Object(event);
        if let Some(id) = call_id {
            value["call_id"] = json!(id);
        }
        if let Some(outcome) = outcome {
            value["outcome"] = json!(outcome);
        }
        if encode(&value).len() > MAX_EVENT_BYTES {
            return err(format!(
                "session event exceeds {MAX_EVENT_BYTES} bytes; store a bounded summary/reference"
            ));
        }
        Ok(value)
    }

    pub fn append(
        &mut self,
        event_type: &str,
        payload: &Value,
        call_id: Option<&str>,
        outcome: Option<&str>,
        expected_sequence: Option<usize>,
    ) -> JournalResult<Value> {
        let _lease = Self::lease(&self.path)?;
        let (sequence, size, mut hasher) = self.append_position()?;
        if let Some(expected) = expected_sequence {
            if sequence != expected {
                return err(
                    "session changed before host checkpoint; inspect the latest observations",
                );
            }
        }
        let event = self.build_event(sequence + 1, event_type, payload, call_id, outcome)?;
        let encoded = encode(&event);
        if size + encoded.len() as u64 > MAX_SESSION_BYTES {
            return err("session exceeds 64 MiB; start a new session");
        }
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&self.path)
            .map_err(|error| JournalError(format!("session append failed: {error}")))?;
        file.write_all(&encoded)
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| JournalError(format!("session fsync failed: {error}")))?;
        hasher.update(&encoded);
        self.validated = Some((sequence + 1, size + encoded.len() as u64, hasher));
        Ok(event)
    }

    /// Validate new records only; rehash the prefix to detect any old-byte
    /// edit. A stat-only cache would miss same-size/mtime tampering. This
    /// deliberately retains linear byte hashing per append until a
    /// transactional store exists (P09 debt, preserved for parity).
    fn append_position(&mut self) -> JournalResult<(usize, u64, Sha256)> {
        let Some((sequence, offset, known)) = self.validated.clone() else {
            let snapshot = self.read()?;
            if snapshot.incomplete_tail {
                return err("session has an interrupted tail; call recover_tail() before appending; never replay pending calls");
            }
            return Ok(self.validated.clone().expect("read sets validated"));
        };
        let metadata = std::fs::metadata(&self.path)
            .map_err(|error| JournalError(format!("session stat failed: {error}")))?;
        if metadata.len() > MAX_SESSION_BYTES {
            return err("session exceeds 64 MiB; inspect it offline");
        }
        let mut file = std::fs::File::open(&self.path)
            .map_err(|error| JournalError(format!("session open failed: {error}")))?;
        file.seek(SeekFrom::Start(0))
            .map_err(|error| JournalError(format!("session seek failed: {error}")))?;
        let mut hasher = Sha256::new();
        let mut remaining = offset;
        let mut buffer = vec![0u8; 1024 * 1024];
        while remaining > 0 {
            let chunk = buffer.len().min(remaining as usize);
            let read = file
                .read(&mut buffer[..chunk])
                .map_err(|error| JournalError(format!("session read failed: {error}")))?;
            if read == 0 {
                return err("validated session prefix was truncated");
            }
            hasher.update(&buffer[..read]);
            remaining -= read as u64;
        }
        // The recomputed prefix digest must equal the digest recorded when
        // these bytes were validated; anything else is tampering.
        if hasher.clone().finalize().to_vec() != known.clone().finalize().to_vec() {
            return err("validated session prefix changed; inspect integrity before appending");
        }
        let mut sequence = sequence;
        let mut offset = offset;
        let mut reader = std::io::BufReader::new(file);
        loop {
            let mut line = Vec::new();
            let read = reader
                .read_until(b'\n', &mut line)
                .map_err(|error| JournalError(format!("session read failed: {error}")))?;
            if read == 0 {
                break;
            }
            if line.len() > MAX_EVENT_BYTES {
                return err("session event exceeds size limit");
            }
            if line.last() != Some(&b'\n') {
                return err("session has an interrupted tail; call recover_tail() before appending; never replay pending calls");
            }
            sequence += 1;
            let event: Value = serde_json::from_slice(&line).map_err(|error| {
                JournalError(format!("invalid committed session event: {error}"))
            })?;
            self.validate_event(&event, sequence)?;
            hasher.update(&line);
            offset += line.len() as u64;
        }
        self.validated = Some((sequence, offset, hasher.clone()));
        Ok((sequence, offset, hasher))
    }

    pub fn read(&mut self) -> JournalResult<SessionSnapshot> {
        let metadata = std::fs::metadata(&self.path)
            .map_err(|error| JournalError(format!("session stat failed: {error}")))?;
        if metadata.len() > MAX_SESSION_BYTES {
            return err("session exceeds 64 MiB; inspect it offline");
        }
        let mut events: Vec<Value> = Vec::new();
        let mut total: u64 = 0;
        let mut complete_end: u64 = 0;
        let mut tail_bytes: usize = 0;
        let mut fingerprint = Sha256::new();
        let mut file = std::fs::File::open(&self.path)
            .map_err(|error| JournalError(format!("session open failed: {error}")))?;
        let mut reader = std::io::BufReader::new(&mut file);
        loop {
            let mut line = Vec::new();
            let read = reader
                .read_until(b'\n', &mut line)
                .map_err(|error| JournalError(format!("session read failed: {error}")))?;
            if read == 0 {
                break;
            }
            total += read as u64;
            if total > MAX_SESSION_BYTES {
                return err("session exceeds 64 MiB; inspect it offline");
            }
            let sequence = events.len() + 1;
            if line.last() != Some(&b'\n') {
                // A long malformed *unterminated* tail is still uncommitted.
                // Distinguish it from an oversized committed event in bounded
                // blocks without loading the entire tail in memory.
                tail_bytes = line.len();
                loop {
                    if line.len() != MAX_EVENT_BYTES + 1 {
                        break;
                    }
                    line.clear();
                    let mut limited = reader.by_ref().take((MAX_EVENT_BYTES + 1) as u64);
                    let more = limited
                        .read_to_end(&mut line)
                        .map_err(|error| JournalError(format!("session read failed: {error}")))?;
                    if more == 0 {
                        break;
                    }
                    if line.last() == Some(&b'\n') {
                        return err(format!("session event {sequence} exceeds size limit"));
                    }
                    tail_bytes += line.len();
                }
                break;
            }
            if line.len() > MAX_EVENT_BYTES {
                return err(format!("session event {sequence} exceeds size limit"));
            }
            let event: Value = serde_json::from_slice(&line).map_err(|error| {
                JournalError(format!(
                    "invalid committed session event {sequence}; unsupported schema or corrupt data: update atomic-flow or restore known-good journal, do not replay actions: {error}"
                ))
            })?;
            self.validate_event(&event, sequence)?;
            events.push(event);
            fingerprint.update(&line);
            complete_end += line.len() as u64;
        }
        if events.is_empty() || events[0]["event_type"] != json!("session_created") {
            return err("session has no committed creation event");
        }
        self.validated = Some((events.len(), complete_end, fingerprint));
        Ok(SessionSnapshot {
            session_id: self.session_id.clone(),
            events,
            incomplete_tail: tail_bytes > 0,
            tail_bytes,
        })
    }

    fn validate_event(&self, event: &Value, sequence: usize) -> JournalResult<()> {
        if !event.is_object() {
            return err("expected event object");
        }
        if event.get("schema_version").and_then(Value::as_i64) != Some(SCHEMA_VERSION) {
            return err("unsupported session schema version; update atomic-flow before opening");
        }
        if event.get("session_id").and_then(Value::as_str) != Some(self.session_id.as_str()) {
            return err("session identity/sequence mismatch");
        }
        match event.get("sequence").and_then(Value::as_u64) {
            Some(value) if value == sequence as u64 => {}
            _ => return err("session identity/sequence mismatch"),
        }
        let event_id = event
            .get("event_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !is_hex32(event_id) {
            return err("invalid event id");
        }
        match event.get("timestamp") {
            Some(Value::Number(number)) => {
                let timestamp = number.as_f64().unwrap_or(-1.0);
                if !timestamp.is_finite() || timestamp < 0.0 {
                    return err("invalid event timestamp");
                }
            }
            _ => return err("invalid event timestamp"),
        }
        let event_type = event
            .get("event_type")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if event_type.is_empty() {
            return err("unsupported session event type: ");
        }
        let payload = event.get("payload").cloned().unwrap_or_else(|| json!({}));
        let call_id = event.get("call_id").and_then(Value::as_str);
        let outcome = event.get("outcome").and_then(Value::as_str);
        let checked = self.build_event(sequence, event_type, &payload, call_id, outcome)?;
        if checked.get("payload") != event.get("payload") {
            return err("unredacted session payload");
        }
        Ok(())
    }

    /// Preserve and remove only uncommitted tail bytes; do not replay effects.
    pub fn recover_tail(&mut self) -> JournalResult<Option<PathBuf>> {
        let _lease = Self::lease(&self.path)?;
        let snapshot = self.read()?;
        if !snapshot.incomplete_tail {
            return Ok(None);
        }
        let (sequence, split, _known) = self.validated.clone().expect("read sets validated");
        let backup = self.path.with_file_name(format!(
            "{}.interrupted-{}.tail",
            self.path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("session.jsonl"),
            af_entropy::token()
        ));
        {
            let mut source = std::fs::File::open(&self.path)
                .map_err(|error| JournalError(format!("session open failed: {error}")))?;
            source
                .seek(SeekFrom::Start(split))
                .map_err(|error| JournalError(format!("session seek failed: {error}")))?;
            let mut target = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&backup)
                .map_err(|error| JournalError(format!("tail backup failed: {error}")))?;
            let mut remaining = snapshot.tail_bytes;
            let mut buffer = vec![0u8; MAX_EVENT_BYTES];
            while remaining > 0 {
                let chunk = buffer.len().min(remaining);
                let read = source
                    .read(&mut buffer[..chunk])
                    .map_err(|error| JournalError(format!("tail read failed: {error}")))?;
                if read == 0 {
                    return err("session tail changed during recovery; inspect preserved backup");
                }
                target
                    .write_all(&buffer[..read])
                    .map_err(|error| JournalError(format!("tail backup failed: {error}")))?;
                remaining -= read;
            }
            let mut probe = [0u8; 1];
            if source
                .read(&mut probe)
                .map_err(|e| JournalError(format!("{e}")))?
                != 0
            {
                return err("session tail changed during recovery; inspect preserved backup");
            }
            target
                .flush()
                .and_then(|_| target.sync_all())
                .map_err(|error| JournalError(format!("tail backup fsync failed: {error}")))?;
        }
        {
            let mut handle = std::fs::OpenOptions::new()
                .write(true)
                .open(&self.path)
                .map_err(|error| JournalError(format!("session open failed: {error}")))?;
            handle
                .set_len(split)
                .and_then(|_| handle.flush())
                .and_then(|_| handle.sync_all())
                .map_err(|error| JournalError(format!("session truncate failed: {error}")))?;
        }
        let event = self.build_event(
            sequence + 1,
            "tail_recovered",
            &json!({"discarded_bytes": snapshot.tail_bytes, "backup": backup.file_name().and_then(|n| n.to_str()).unwrap_or("")}),
            None,
            None,
        )?;
        let encoded = encode(&event);
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&self.path)
            .map_err(|error| JournalError(format!("session append failed: {error}")))?;
        file.write_all(&encoded)
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| JournalError(format!("session fsync failed: {error}")))?;
        self.validated = None;
        Ok(Some(backup))
    }

    /// Export a bounded JSON inspection document; replay is never supported.
    pub fn export(&mut self, destination: &std::path::Path) -> JournalResult<PathBuf> {
        let snapshot = self.read()?;
        if destination
            .canonicalize()
            .unwrap_or_else(|_| destination.to_path_buf())
            == self
                .path
                .canonicalize()
                .unwrap_or_else(|_| self.path.clone())
        {
            return err("export destination must differ from journal");
        }
        let document = json!({
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "events": snapshot.events,
            "incomplete_tail": snapshot.incomplete_tail,
            "tail_bytes": snapshot.tail_bytes,
            "pending_call_ids": snapshot.pending_call_ids(),
            "unresolved_call_ids": snapshot.unresolved_call_ids(),
            "replay_supported": false,
        });
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(destination)
            .map_err(|error| JournalError(format!("export failed: {error}")))?;
        file.write_all(
            &serde_json::to_vec_pretty(&document).map_err(|e| JournalError(format!("{e}")))?,
        )
        .and_then(|_| file.write_all(b"\n"))
        .and_then(|_| file.flush())
        .map_err(|error| JournalError(format!("export write failed: {error}")))?;
        Ok(destination.to_path_buf())
    }
}

/// Recent sessions with creation metadata; invalid sessions are reported, not
/// silently skipped.
pub fn list_sessions(limit: usize) -> JournalResult<Vec<Value>> {
    if limit == 0 || limit > 1000 {
        return err("session list limit must be 1-1000");
    }
    let directory = user_dir().join("sessions");
    if !directory.is_dir() {
        return Ok(Vec::new());
    }
    let mut paths: Vec<PathBuf> = std::fs::read_dir(&directory)
        .map_err(|error| JournalError(format!("sessions list failed: {error}")))?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with("session_") && name.ends_with(".jsonl"))
        })
        .collect();
    paths.sort_by_key(|path| std::fs::metadata(path).and_then(|m| m.modified()).ok());
    paths.reverse();
    let mut out = Vec::new();
    for path in paths.into_iter().take(limit) {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default();
        let session_id = &name["session_".len()..name.len() - ".jsonl".len()];
        let result = (|| -> JournalResult<Value> {
            let mut file = std::io::BufReader::new(
                std::fs::File::open(&path).map_err(|error| JournalError(format!("{error}")))?,
            );
            let mut line = Vec::new();
            file.read_until(b'\n', &mut line)
                .map_err(|error| JournalError(format!("{error}")))?;
            if line.last() != Some(&b'\n') || line.len() > MAX_EVENT_BYTES {
                return err("invalid creation record");
            }
            let first: Value =
                serde_json::from_slice(&line).map_err(|error| JournalError(format!("{error}")))?;
            let journal = SessionJournal::new(session_id)?;
            journal.validate_event(&first, 1)?;
            if first["event_type"] != json!("session_created") {
                return err("missing creation record");
            }
            Ok(json!({
                "session_id": session_id,
                "created_at": first.get("timestamp"),
                "project": first.pointer("/payload/project"),
            }))
        })();
        match result {
            Ok(item) => out.push(item),
            Err(_) => out.push(json!({"session_id": session_id, "error": "invalid session; inspect before opening"})),
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_home() -> PathBuf {
        let home = std::env::temp_dir().join(format!("af-journal-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        home
    }

    /// Serializes tests that touch the process-wide ATOMIC_FLOW_HOME.
    fn with_env<T>(home: &PathBuf, body: impl FnOnce() -> T) -> T {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", home);
        let result = body();
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        result
    }

    fn make_journal(home: &PathBuf, id: &str) -> SessionJournal {
        let project = home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        SessionJournal::create(&project, Some(id), None).unwrap()
    }

    #[test]
    fn create_append_read_roundtrip() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "roundtrip");
            let event = journal
                .append("user_input", &json!({"text": "hello"}), None, None, None)
                .unwrap();
            assert_eq!(event["sequence"], json!(2));
            let snapshot = journal.read().unwrap();
            assert_eq!(snapshot.events.len(), 2);
            assert!(snapshot.pending_call_ids().is_empty());
            assert!(snapshot.unresolved_call_ids().is_empty());
        });
    }

    #[test]
    fn secrets_are_cleaned_in_payloads() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "secrets");
            journal
                .append(
                    "user_input",
                    &json!({"api_key": "super-secret-value", "note": "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "safe_env": "keep"}),
                    None,
                    None,
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            let payload = &snapshot.events[1]["payload"];
            assert_eq!(payload["api_key"], json!("[REDACTED]"));
            assert_eq!(payload["note"], json!("[REDACTED]"));
            assert_eq!(payload["safe_env"], json!("keep"));
        });
    }

    #[test]
    fn pending_and_unresolved_holds() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "holds");
            journal
                .append(
                    "model_request",
                    &json!({"model": "m"}),
                    Some("call-1"),
                    None,
                    None,
                )
                .unwrap();
            journal
                .append(
                    "tool_request",
                    &json!({"tool": "run"}),
                    Some("call-2"),
                    None,
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            assert_eq!(
                snapshot.pending_call_ids(),
                vec!["call-1".to_string(), "call-2".to_string()]
            );
            assert_eq!(
                snapshot.unresolved_call_ids(),
                vec!["call-1".to_string(), "call-2".to_string()]
            );
            // Terminal response with a settled cost clears the charge;
            // rollback_failed tool keeps its effect on hold.
            journal
                .append(
                    "model_response",
                    &json!({"cost_usd": 0.01}),
                    Some("call-1"),
                    Some("success"),
                    None,
                )
                .unwrap();
            journal
                .append(
                    "tool_result",
                    &json!({"result": {"status": "rollback_failed"}}),
                    Some("call-2"),
                    Some("error"),
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            assert!(snapshot.pending_call_ids().is_empty());
            assert_eq!(snapshot.unresolved_call_ids(), vec!["call-2".to_string()]);
            // Host reconciliation closes the effect hold.
            journal
                .append(
                    "host_reconciliation",
                    &json!({
                        "scope": "effect",
                        "reconciliation_id": format!("reconcile_{}", af_entropy::token64()),
                        "evidence_sha256": af_entropy::token64(),
                        "actor": "host",
                        "reason": "verified no effects occurred",
                    }),
                    Some("call-2"),
                    None,
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            assert!(snapshot.unresolved_call_ids().is_empty());
        });
    }

    #[test]
    fn unknown_charge_survives_and_not_sent_clears() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "unknown");
            journal
                .append("model_request", &json!({}), Some("call-x"), None, None)
                .unwrap();
            journal
                .append(
                    "model_failed",
                    &json!({"error": "timeout after send"}),
                    Some("call-x"),
                    Some("error"),
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            assert_eq!(snapshot.unresolved_call_ids(), vec!["call-x".to_string()]);
            journal
                .append("model_request", &json!({}), Some("call-y"), None, None)
                .unwrap();
            journal
                .append(
                    "model_failed",
                    &json!({"error": "pre-send cancel"}),
                    Some("call-y"),
                    Some("not_sent"),
                    None,
                )
                .unwrap();
            let snapshot = journal.read().unwrap();
            assert!(!snapshot
                .unresolved_call_ids()
                .contains(&"call-y".to_string()));
        });
    }

    #[test]
    fn interrupted_tail_blocks_and_recovers() {
        let home = temp_home();
        with_env(&home, || {
            {
                let _journal = make_journal(&home, "tail");
            }
            let path = user_dir().join("sessions").join("session_tail.jsonl");
            // Simulate a crash mid-append: bytes without the trailing newline.
            let mut file = std::fs::OpenOptions::new()
                .append(true)
                .open(&path)
                .unwrap();
            file.write_all(b"{\"partial\": tru").unwrap();
            drop(file);
            let mut journal = SessionJournal::new("tail").unwrap();
            let snapshot = journal.read().unwrap();
            assert!(snapshot.incomplete_tail);
            assert!(snapshot.tail_bytes > 0);
            assert!(journal
                .append("user_input", &json!({}), None, None, None)
                .is_err());
            let backup = journal.recover_tail().unwrap().expect("backup path");
            assert!(backup.exists());
            let snapshot = journal.read().unwrap();
            assert!(!snapshot.incomplete_tail);
            assert_eq!(
                snapshot.events.last().unwrap()["event_type"],
                json!("tail_recovered")
            );
            assert!(journal
                .append("user_input", &json!({"text": "ok"}), None, None, None)
                .is_ok());
        });
    }

    #[test]
    fn expected_sequence_cas_refuses_stale_writer() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "cas");
            assert!(journal
                .append("user_input", &json!({}), None, None, None)
                .is_ok());
            let stale = journal
                .append("user_input", &json!({}), None, None, Some(1))
                .unwrap_err();
            assert!(stale.0.contains("session changed before host checkpoint"));
        });
    }

    #[test]
    fn oversize_event_refused() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "big");
            let blob = "x".repeat(MAX_EVENT_BYTES + 10);
            let error = journal
                .append("user_input", &json!({"text": blob}), None, None, None)
                .unwrap_err();
            assert!(error.0.contains("exceeds"));
        });
    }

    #[test]
    fn tampered_prefix_is_detected_on_append() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "tamper");
            journal
                .append("user_input", &json!({"text": "original"}), None, None, None)
                .unwrap();
            // Out-of-band rewrite of committed bytes.
            let path = user_dir().join("sessions").join("session_tamper.jsonl");
            let bytes = std::fs::read(&path).unwrap();
            let poisoned: Vec<u8> = bytes
                .iter()
                .map(|b| if *b == b'o' { b'0' } else { *b })
                .collect();
            std::fs::write(&path, &poisoned).unwrap();
            let error = journal
                .append("user_input", &json!({}), None, None, None)
                .unwrap_err();
            assert!(
                error.0.contains("changed") || error.0.contains("invalid committed session event")
            );
        });
    }

    #[test]
    fn list_sessions_reports_entries() {
        let home = temp_home();
        with_env(&home, || {
            make_journal(&home, "listed");
            let sessions = list_sessions(100).unwrap();
            assert!(sessions
                .iter()
                .any(|item| item["session_id"] == json!("listed")));
        });
    }

    #[test]
    fn export_document_has_replay_supported_false() {
        let home = temp_home();
        with_env(&home, || {
            let mut journal = make_journal(&home, "exported");
            journal
                .append("user_input", &json!({"text": "hi"}), None, None, None)
                .unwrap();
            let target = home.join("export.json");
            journal.export(&target).unwrap();
            let document: Value =
                serde_json::from_str(&std::fs::read_to_string(&target).unwrap()).unwrap();
            assert_eq!(document["replay_supported"], json!(false));
            assert_eq!(document["events"].as_array().unwrap().len(), 2);
        });
    }

    #[test]
    fn sha256_hex_matches_known_vector() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
