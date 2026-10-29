//! af-llm: the model backends plane (port of `backends/`): the chat contract
//! with per-call usage accounting, and fixed routing that follows the
//! project's spending contract — switching backends is allowed only on a
//! confirmed pre-inference refusal; an unknown outcome after send holds the
//! route and is never retried or failed over.

use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct Cancel(Arc<AtomicBool>);

impl Cancel {
    pub fn new() -> Cancel {
        Cancel(Arc::new(AtomicBool::new(false)))
    }

    pub fn cancel(&self) {
        self.0.store(true, Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

impl Default for Cancel {
    fn default() -> Self {
        Cancel::new()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    /// Assistant tool_calls (OpenAI shape) when the model requested tools.
    pub tool_calls: Option<Value>,
    /// tool role: the id of the call this result answers.
    pub tool_call_id: Option<String>,
}

impl ChatMessage {
    pub fn new(role: &str, content: &str) -> ChatMessage {
        ChatMessage {
            role: role.to_string(),
            content: content.to_string(),
            tool_calls: None,
            tool_call_id: None,
        }
    }

    pub fn assistant_with_tool_calls(content: &str, tool_calls: Value) -> ChatMessage {
        ChatMessage {
            role: "assistant".into(),
            content: content.to_string(),
            tool_calls: Some(tool_calls),
            tool_call_id: None,
        }
    }

    pub fn tool_result(call_id: &str, content: &str) -> ChatMessage {
        ChatMessage {
            role: "tool".into(),
            content: content.to_string(),
            tool_calls: None,
            tool_call_id: Some(call_id.to_string()),
        }
    }
}

/// A model-callable tool contract (JSON Schema parameters).
#[derive(Debug, Clone, PartialEq)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

/// One tool invocation requested by the model.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// Raw JSON-string arguments, exactly as the provider returned them.
    pub arguments: String,
}

#[derive(Debug, Clone)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub max_output_tokens: Option<u64>,
    pub tools: Option<Vec<ToolSpec>>,
}

impl ChatRequest {
    pub fn new(model: &str, messages: Vec<ChatMessage>) -> ChatRequest {
        ChatRequest {
            model: model.to_string(),
            messages,
            max_output_tokens: None,
            tools: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Usage {
    pub tokens_in: Option<u64>,
    pub tokens_out: Option<u64>,
    pub cost_usd: Option<f64>,
    pub latency_ms: Option<u64>,
}

impl Usage {
    #[allow(dead_code)]
    fn unknown() -> Usage {
        Usage {
            tokens_in: None,
            tokens_out: None,
            cost_usd: None,
            latency_ms: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatResult {
    pub content: String,
    pub usage: Usage,
    pub truncated: bool,
    /// Tool invocations the model requested (empty for plain answers).
    pub tool_calls: Vec<ToolCall>,
}

/// Outcome of one physical call. `sent` decides failover per the contract:
/// a confirmed pre-send refusal may route elsewhere; anything after send is a
/// hold — the accounting never assumes it was free.
#[derive(Debug, Clone, PartialEq)]
pub enum BackendOutcome {
    Completed(ChatResult),
    Refused { sent: bool, reason: String },
    Unknown { sent: bool, detail: String },
}

pub trait ChatBackend: Send + Sync {
    fn name(&self) -> &str;
    fn chat(&self, request: &ChatRequest, cancel: &Cancel) -> BackendOutcome;
}

/// Deterministic scripted backend (the `fake backend` of the Python tests).
#[derive(Default)]
pub struct ScriptedBackend {
    name: String,
    script: std::sync::Mutex<Vec<BackendOutcome>>,
}

impl ScriptedBackend {
    pub fn new(name: &str, script: Vec<BackendOutcome>) -> ScriptedBackend {
        ScriptedBackend {
            name: name.to_string(),
            script: std::sync::Mutex::new(script),
        }
    }

    pub fn echo() -> ScriptedBackend {
        ScriptedBackend::new(
            "scripted",
            vec![BackendOutcome::Completed(ChatResult {
                content: "scripted-echo".to_string(),
                usage: Usage {
                    tokens_in: Some(1),
                    tokens_out: Some(1),
                    cost_usd: Some(0.0),
                    latency_ms: Some(0),
                },
                truncated: false,
                tool_calls: Vec::new(),
            })],
        )
    }

    /// Script one assistant turn that requests tool calls.
    pub fn tool_calls(name: &str, calls: &[(&str, &str, &str)]) -> ScriptedBackend {
        let mapped: Vec<Value> = calls
            .iter()
            .map(|(id, tool, arguments)| {
                json!({"id": id, "type": "function", "function": {"name": tool, "arguments": arguments}})
            })
            .collect();
        ScriptedBackend::new(
            name,
            vec![BackendOutcome::Completed(ChatResult {
                content: String::new(),
                usage: Usage {
                    tokens_in: Some(1),
                    tokens_out: Some(1),
                    cost_usd: Some(0.0),
                    latency_ms: Some(0),
                },
                truncated: false,
                tool_calls: parsed_tool_calls(&json!(mapped)),
            })],
        )
    }
}

/// Parse provider tool_calls JSON into typed ToolCall records.
pub fn parsed_tool_calls(value: &Value) -> Vec<ToolCall> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let id = item.get("id")?.as_str()?.to_string();
                    let name = item.pointer("/function/name")?.as_str()?.to_string();
                    let arguments = item
                        .pointer("/function/arguments")
                        .and_then(Value::as_str)
                        .unwrap_or("{}")
                        .to_string();
                    Some(ToolCall {
                        id,
                        name,
                        arguments,
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

impl ChatBackend for ScriptedBackend {
    fn name(&self) -> &str {
        &self.name
    }

    fn chat(&self, _request: &ChatRequest, cancel: &Cancel) -> BackendOutcome {
        if cancel.is_cancelled() {
            return BackendOutcome::Refused {
                sent: false,
                reason: "cancelled before send".into(),
            };
        }
        let mut script = self.script.lock().expect("script lock");
        if script.is_empty() {
            return BackendOutcome::Completed(ChatResult {
                content: String::new(),
                usage: Usage {
                    tokens_in: Some(0),
                    tokens_out: Some(0),
                    cost_usd: Some(0.0),
                    latency_ms: Some(0),
                },
                truncated: false,
                tool_calls: Vec::new(),
            });
        }
        script.remove(0)
    }
}

/// OpenAI-compatible HTTP backend (the `openai_compatible.py` contract):
/// one call per request, JSON in/out, bearer key from the process
/// environment — never persisted, never logged.
pub struct OpenAiBackend {
    pub name: String,
    pub base_url: String,
    pub api_key_env: String,
}

impl OpenAiBackend {
    pub fn new(name: &str, base_url: &str, api_key_env: &str) -> OpenAiBackend {
        OpenAiBackend {
            name: name.to_string(),
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key_env: api_key_env.to_string(),
        }
    }

    pub fn payload(&self, request: &ChatRequest) -> Value {
        let mut payload = json!({
            "model": request.model,
            "messages": request.messages.iter()
            .map(|message| {
                    let mut entry = json!({"role": message.role, "content": message.content});
                    if let Some(tool_calls) = &message.tool_calls {
                        entry["tool_calls"] = tool_calls.clone();
                    }
                    if let Some(call_id) = &message.tool_call_id {
                        entry["tool_call_id"] = json!(call_id);
                    }
                    entry
                })
                .collect::<Vec<_>>(),
            "max_tokens": request.max_output_tokens,
        });
        if let Some(tools) = &request.tools {
            payload["tools"] = json!(tools.iter().map(|tool| json!({
                "type": "function",
                "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
            })).collect::<Vec<_>>());
        }
        payload
    }

    fn stream_payload(&self, request: &ChatRequest) -> Value {
        let mut payload = self.payload(request);
        payload["stream"] = json!(true);
        payload["stream_options"] = json!({"include_usage": true});
        payload
    }
}

/// SSE parsing for `chat/completions` streams: one `data:` line in, the
/// parsed chunk out. `[DONE]` and comments map to None.
pub fn parse_sse_line(line: &str) -> Option<Value> {
    let line = line.trim_end_matches(['\n', '\r']);
    let payload = line.strip_prefix("data:")?.trim();
    if payload == "[DONE]" || payload.is_empty() {
        return None;
    }
    serde_json::from_str(payload).ok()
}

/// Accumulate one stream chunk into the running answer; returns the finish
/// reason when the chunk carries one.
pub fn accumulate_chunk(content: &mut String, chunk: &Value) -> Option<String> {
    if let Some(delta) = chunk
        .pointer("/choices/0/delta/content")
        .and_then(Value::as_str)
    {
        content.push_str(delta);
    }
    chunk
        .pointer("/choices/0/finish_reason")
        .and_then(Value::as_str)
        .map(str::to_string)
}

impl ChatBackend for OpenAiBackend {
    fn name(&self) -> &str {
        &self.name
    }

    fn chat(&self, request: &ChatRequest, cancel: &Cancel) -> BackendOutcome {
        if cancel.is_cancelled() {
            return BackendOutcome::Refused {
                sent: false,
                reason: "cancelled before send".into(),
            };
        }
        let started = std::time::Instant::now();
        let Ok(api_key) = std::env::var(&self.api_key_env) else {
            return BackendOutcome::Refused {
                sent: false,
                reason: format!(
                    "api key environment variable {} is not set",
                    self.api_key_env
                ),
            };
        };
        let agent = match ureq::AgentBuilder::new()
            .timeout_connect(std::time::Duration::from_secs(15))
            .build()
        {
            agent => agent,
        };
        let response = agent
            .post(&format!("{}/chat/completions", self.base_url))
            .set("Authorization", &format!("Bearer {api_key}"))
            .set("Content-Type", "application/json")
            .timeout(std::time::Duration::from_secs(600))
            .send_json(self.payload(request));
        let latency_ms = Some(started.elapsed().as_millis() as u64);
        match response {
            Ok(body) => match body.into_string() {
                Ok(text) => {
                    let parsed: Result<Value, _> = serde_json::from_str(&text);
                    match parsed {
                        Ok(document) => {
                            let content = document
                                .pointer("/choices/0/message/content")
                                .and_then(Value::as_str)
                                .unwrap_or_default()
                                .to_string();
                            let finish = document
                                .pointer("/choices/0/finish_reason")
                                .and_then(Value::as_str)
                                .unwrap_or("unknown")
                                .to_string();
                            let tool_calls = document
                                .pointer("/choices/0/message/tool_calls")
                                .cloned()
                                .map(|calls| parsed_tool_calls(&calls))
                                .unwrap_or_default();
                            let usage = Usage {
                                tokens_in: document
                                    .pointer("/usage/prompt_tokens")
                                    .and_then(Value::as_u64),
                                tokens_out: document
                                    .pointer("/usage/completion_tokens")
                                    .and_then(Value::as_u64),
                                cost_usd: None, // tariff table is a later, separately accepted change
                                latency_ms,
                            };
                            BackendOutcome::Completed(ChatResult {
                                content,
                                usage,
                                truncated: finish == "length",
                                tool_calls,
                            })
                        }
                        Err(error) => BackendOutcome::Unknown {
                            sent: true,
                            detail: format!("response is not valid JSON: {error}"),
                        },
                    }
                }
                Err(error) => BackendOutcome::Unknown {
                    sent: true,
                    detail: format!("response body unreadable: {error}"),
                },
            },
            Err(ureq::Error::Status(code, _)) => {
                // The provider answered: the call happened, cost is real.
                if (400..500).contains(&code) {
                    BackendOutcome::Refused {
                        sent: true,
                        reason: format!("provider refused with status {code}"),
                    }
                } else {
                    BackendOutcome::Unknown {
                        sent: true,
                        detail: format!("provider error status {code}"),
                    }
                }
            }
            Err(ureq::Error::Transport(transport)) => {
                let sent = !matches!(
                    transport.kind(),
                    ureq::ErrorKind::Dns | ureq::ErrorKind::ConnectionFailed
                );
                if cancel.is_cancelled() && sent {
                    BackendOutcome::Unknown {
                        sent: true,
                        detail: "cancelled after send; charge unknown until reconciled".into(),
                    }
                } else {
                    BackendOutcome::Unknown {
                        sent,
                        detail: format!("transport error: {transport}"),
                    }
                }
            }
        }
    }
}

impl OpenAiBackend {
    /// Streaming variant: `stream: true` + `include_usage`, deltas pushed to
    /// `on_delta` as they arrive, final `ChatResult` carries the accumulated
    /// content and the settled usage from the trailing chunk. A drop
    /// mid-stream after send is Unknown (sent) — the charge already happened.
    /// No tool_calls in streaming v1: a stream that requests tools returns
    /// them via the final `tool_calls` field if the provider includes one.
    pub fn chat_stream(
        &self,
        request: &ChatRequest,
        cancel: &Cancel,
        on_delta: &mut dyn FnMut(&str),
    ) -> BackendOutcome {
        if cancel.is_cancelled() {
            return BackendOutcome::Refused {
                sent: false,
                reason: "cancelled before send".into(),
            };
        }
        let started = std::time::Instant::now();
        let Ok(api_key) = std::env::var(&self.api_key_env) else {
            return BackendOutcome::Refused {
                sent: false,
                reason: format!(
                    "api key environment variable {} is not set",
                    self.api_key_env
                ),
            };
        };
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(std::time::Duration::from_secs(15))
            .build();
        let response = agent
            .post(&format!("{}/chat/completions", self.base_url))
            .set("Authorization", &format!("Bearer {api_key}"))
            .set("Content-Type", "application/json")
            .timeout(std::time::Duration::from_secs(600))
            .send_json(self.stream_payload(request));
        let latency_ms = Some(started.elapsed().as_millis() as u64);
        let mut reader = match response {
            Ok(body) => body.into_reader(),
            Err(ureq::Error::Status(code, _)) => {
                return if (400..500).contains(&code) {
                    BackendOutcome::Refused {
                        sent: true,
                        reason: format!("provider refused with status {code}"),
                    }
                } else {
                    BackendOutcome::Unknown {
                        sent: true,
                        detail: format!("provider error status {code}"),
                    }
                };
            }
            Err(ureq::Error::Transport(transport)) => {
                let sent = !matches!(
                    transport.kind(),
                    ureq::ErrorKind::Dns | ureq::ErrorKind::ConnectionFailed
                );
                return BackendOutcome::Unknown {
                    sent,
                    detail: format!("transport error: {transport}"),
                };
            }
        };
        let mut content = String::new();
        let mut finish: Option<String> = None;
        let mut usage: Option<Usage> = None;
        let mut stream = std::io::BufReader::new(&mut reader);
        use std::io::BufRead as _;
        loop {
            if cancel.is_cancelled() {
                return BackendOutcome::Unknown {
                    sent: true,
                    detail: "cancelled mid-stream; charge unknown until reconciled".into(),
                };
            }
            let mut line = Vec::new();
            match stream.read_until(b'\n', &mut line) {
                Ok(0) => break,
                Ok(_) => {}
                Err(error) => {
                    return BackendOutcome::Unknown {
                        sent: true,
                        detail: format!("stream read failed mid-answer: {error}"),
                    };
                }
            }
            let Some(chunk) = parse_sse_line(&String::from_utf8_lossy(&line)) else {
                continue;
            };
            accumulate_chunk(&mut content, &chunk);
            if let Some(reason) = chunk
                .pointer("/choices/0/finish_reason")
                .and_then(Value::as_str)
                .map(str::to_string)
            {
                finish = Some(reason);
            }
            if let Some(usage_value) = chunk.get("usage") {
                usage = Some(Usage {
                    tokens_in: usage_value.get("prompt_tokens").and_then(Value::as_u64),
                    tokens_out: usage_value.get("completion_tokens").and_then(Value::as_u64),
                    cost_usd: None,
                    latency_ms,
                });
            }
        }
        match finish.as_deref() {
            Some(reason) => BackendOutcome::Completed(ChatResult {
                content,
                usage: usage.unwrap_or(Usage {
                    tokens_in: None,
                    tokens_out: None,
                    cost_usd: None,
                    latency_ms,
                }),
                truncated: reason == "length",
                tool_calls: Vec::new(),
            }),
            None => BackendOutcome::Unknown {
                sent: true,
                detail: "stream ended without finish_reason".into(),
            },
        }
    }
}

/// Result of routing one logical request across the fixed route.
#[derive(Debug, Clone, PartialEq)]
pub enum RouteOutcome {
    /// Served by the named backend with settled usage.
    Completed { backend: String, result: ChatResult },
    /// A hold: unknown or post-send refusal on the named backend. The route
    /// stops here and the caller must reconcile before any further calls.
    Hold { backend: String, detail: String },
    /// Every candidate refused before inference; nothing was spent.
    Exhausted { refusals: Vec<String> },
}

/// Fixed-order router: explicit backend > configured order. No retry, no
/// failover after send, no account rotation.
pub struct Router {
    backends: Vec<Box<dyn ChatBackend>>,
}

impl Router {
    pub fn new(backends: Vec<Box<dyn ChatBackend>>) -> Router {
        Router { backends }
    }

    pub fn route(&self, request: &ChatRequest, cancel: &Cancel) -> RouteOutcome {
        let mut refusals: Vec<String> = Vec::new();
        for backend in &self.backends {
            match backend.chat(request, cancel) {
                BackendOutcome::Completed(result) => {
                    return RouteOutcome::Completed {
                        backend: backend.name().to_string(),
                        result,
                    };
                }
                BackendOutcome::Refused {
                    sent: false,
                    reason,
                } => {
                    refusals.push(format!("{}: {reason}", backend.name()));
                }
                BackendOutcome::Refused { sent: true, reason } => {
                    return RouteOutcome::Hold {
                        backend: backend.name().to_string(),
                        detail: format!("refused after send: {reason}"),
                    };
                }
                BackendOutcome::Unknown { sent, detail } => {
                    if sent {
                        return RouteOutcome::Hold {
                            backend: backend.name().to_string(),
                            detail,
                        };
                    }
                    refusals.push(format!("{}: {detail}", backend.name()));
                }
            }
        }
        RouteOutcome::Exhausted { refusals }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> ChatRequest {
        ChatRequest {
            model: "test-model".into(),
            messages: vec![ChatMessage::new("user", "hello")],
            max_output_tokens: Some(16),
            tools: None,
        }
    }

    #[test]
    fn scripted_backend_echoes_with_settled_usage() {
        let backend = ScriptedBackend::echo();
        let outcome = backend.chat(&request(), &Cancel::new());
        match outcome {
            BackendOutcome::Completed(result) => {
                assert_eq!(result.content, "scripted-echo");
                assert_eq!(result.usage.cost_usd, Some(0.0));
            }
            other => panic!("expected completed, got {other:?}"),
        }
    }

    #[test]
    fn pre_send_refusal_fails_over_exhausted_route_is_reported() {
        let router = Router::new(vec![
            Box::new(ScriptedBackend::new(
                "first",
                vec![BackendOutcome::Refused {
                    sent: false,
                    reason: "no key".into(),
                }],
            )),
            Box::new(ScriptedBackend::new(
                "second",
                vec![BackendOutcome::Completed(ChatResult {
                    content: "ok".into(),
                    usage: Usage {
                        tokens_in: Some(2),
                        tokens_out: Some(1),
                        cost_usd: Some(0.01),
                        latency_ms: Some(5),
                    },
                    truncated: false,
                    tool_calls: Vec::new(),
                })],
            )),
        ]);
        match router.route(&request(), &Cancel::new()) {
            RouteOutcome::Completed { backend, result } => {
                assert_eq!(backend, "second");
                assert_eq!(result.content, "ok");
            }
            other => panic!("expected completed, got {other:?}"),
        }
        // Everything refused before send → exhausted with all refusals listed.
        let empty = Router::new(vec![Box::new(ScriptedBackend::new(
            "only",
            vec![BackendOutcome::Refused {
                sent: false,
                reason: "down".into(),
            }],
        ))]);
        match empty.route(&request(), &Cancel::new()) {
            RouteOutcome::Exhausted { refusals } => assert_eq!(refusals.len(), 1),
            other => panic!("expected exhausted, got {other:?}"),
        }
    }

    #[test]
    fn unknown_after_send_holds_and_never_fails_over() {
        let router = Router::new(vec![
            Box::new(ScriptedBackend::new(
                "flaky",
                vec![BackendOutcome::Unknown {
                    sent: true,
                    detail: "timeout after send".into(),
                }],
            )),
            Box::new(ScriptedBackend::echo()),
        ]);
        match router.route(&request(), &Cancel::new()) {
            RouteOutcome::Hold { backend, detail } => {
                assert_eq!(backend, "flaky");
                assert!(detail.contains("timeout after send"));
            }
            other => panic!("expected hold, got {other:?}"),
        }
    }

    #[test]
    fn post_send_refusal_also_holds() {
        let router = Router::new(vec![
            Box::new(ScriptedBackend::new(
                "strict",
                vec![BackendOutcome::Refused {
                    sent: true,
                    reason: "content policy after streaming started".into(),
                }],
            )),
            Box::new(ScriptedBackend::echo()),
        ]);
        match router.route(&request(), &Cancel::new()) {
            RouteOutcome::Hold { backend, detail } => {
                assert_eq!(backend, "strict");
                assert!(detail.contains("refused after send"));
            }
            other => panic!("expected hold, got {other:?}"),
        }
    }

    #[test]
    fn cancel_before_send_is_a_free_refusal() {
        let backend = ScriptedBackend::echo();
        let cancel = Cancel::new();
        cancel.cancel();
        match backend.chat(&request(), &cancel) {
            BackendOutcome::Refused { sent, reason } => {
                assert!(!sent);
                assert!(reason.contains("cancelled before send"));
            }
            other => panic!("expected refusal, got {other:?}"),
        }
    }

    #[test]
    fn unknown_transport_without_send_allows_failover() {
        let router = Router::new(vec![
            Box::new(ScriptedBackend::new(
                "unreachable",
                vec![BackendOutcome::Unknown {
                    sent: false,
                    detail: "connection refused".into(),
                }],
            )),
            Box::new(ScriptedBackend::echo()),
        ]);
        match router.route(&request(), &Cancel::new()) {
            RouteOutcome::Completed { backend, .. } => assert_eq!(backend, "scripted"),
            other => panic!("expected completed, got {other:?}"),
        }
    }

    #[test]
    fn openai_payload_shape() {
        let backend = OpenAiBackend::new("api", "http://localhost:1", "MISSING_KEY");
        let payload = backend.payload(&request());
        assert_eq!(payload["model"], json!("test-model"));
        assert_eq!(payload["messages"][0]["role"], json!("user"));
        assert_eq!(payload["max_tokens"], json!(16));
    }

    #[test]
    fn missing_api_key_is_a_pre_send_refusal() {
        let backend = OpenAiBackend::new("api", "http://localhost:1", "AF_TEST_MISSING_KEY_XYZ");
        match backend.chat(&request(), &Cancel::new()) {
            BackendOutcome::Refused { sent, reason } => {
                assert!(!sent);
                assert!(reason.contains("AF_TEST_MISSING_KEY_XYZ"));
            }
            other => panic!("expected refusal, got {other:?}"),
        }
    }

    #[test]
    fn sse_parsing_and_accumulation() {
        assert!(parse_sse_line("data: [DONE]").is_none());
        assert!(parse_sse_line(": keep-alive comment").is_none());
        let chunk = parse_sse_line(r#"data: {"choices":[{"delta":{"content":"Hel"}}]}"#)
            .expect("content chunk parses");
        let mut content = String::new();
        assert!(accumulate_chunk(&mut content, &chunk).is_none());
        assert_eq!(content, "Hel");
        let final_chunk = parse_sse_line(
            r#"data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}"#,
        )
        .expect("final chunk parses");
        accumulate_chunk(&mut content, &final_chunk);
        assert_eq!(
            accumulate_chunk(&mut content, &final_chunk),
            Some("stop".into())
        );
        assert_eq!(content, "Hel");
    }

    #[test]
    fn stream_payload_carries_stream_flags() {
        let backend = OpenAiBackend::new("api", "http://localhost:1", "K");
        let payload = backend.stream_payload(&request());
        assert_eq!(payload["stream"], json!(true));
        assert_eq!(payload["stream_options"]["include_usage"], json!(true));
        assert_eq!(payload["max_tokens"], json!(16));
    }

    #[test]
    fn stream_missing_key_is_pre_send_refusal() {
        let backend = OpenAiBackend::new("api", "http://localhost:1", "AF_TEST_MISSING_KEY_XYZ");
        let mut deltas: Vec<String> = Vec::new();
        match backend.chat_stream(&request(), &Cancel::new(), &mut |delta| {
            deltas.push(delta.into())
        }) {
            BackendOutcome::Refused { sent, .. } => {
                assert!(!sent);
                assert!(deltas.is_empty(), "no deltas before send");
            }
            other => panic!("expected refusal, got {other:?}"),
        }
    }
}
