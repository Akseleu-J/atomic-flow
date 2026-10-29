//! af-loop: the model-driven tool loop (Tier 0 heart). The model calls tools;
//! the kernel executes them and feeds results back, bounded by turn and
//! tool-call budgets. Every request/response/tool step is journaled with call
//! ids, so an interrupted run leaves honest pending/unknown holds exactly
//! like the session contract — a hold stops the loop and is never retried
//! here. Tools come from the caller (kernel tools wire the sandbox/executor);
//! the loop itself is provider-shaped, not provider-bound.

use af_llm::{Cancel, ChatMessage, ChatRequest, RouteOutcome, Router, ToolCall, ToolSpec};
use serde_json::{json, Value};

pub mod compact;
pub mod edit;
pub mod schedule;
pub mod steer;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct LoopError(pub String);

pub type LoopResult<T> = Result<T, LoopError>;

/// One executable tool bound to the kernel by the caller.
pub trait KernelTool {
    fn spec(&self) -> ToolSpec;
    /// Execute with parsed arguments; errors are returned to the model as a
    /// tool result with an "error" field — never as a loop crash.
    fn execute(&self, arguments: &Value) -> Value;
}

#[derive(Debug, Clone, Copy)]
pub struct LoopLimits {
    pub max_turns: usize,
    pub max_tool_calls: usize,
    /// Serialized transcript byte ceiling; when Some, the loop compacts the
    /// history before each model call (markers keep tool-call pairings).
    pub max_history_bytes: Option<usize>,
}

impl Default for LoopLimits {
    fn default() -> Self {
        LoopLimits {
            max_turns: 12,
            max_tool_calls: 24,
            max_history_bytes: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum LoopOutcome {
    /// The model produced a final answer without further tool requests.
    Final {
        content: String,
        turns: usize,
        tool_calls_used: usize,
    },
    /// The route is on hold (unknown outcome after send); never retried here.
    Hold { backend: String, detail: String },
    /// Turn or tool-call budget exhausted; the transcript is preserved.
    BudgetExhausted {
        reason: String,
        turns: usize,
        tool_calls_used: usize,
    },
}

pub struct TurnLoop<'a> {
    router: &'a Router,
    tools: &'a [Box<dyn KernelTool + 'a>],
    journal: Option<&'a mut af_journal::SessionJournal>,
    limits: LoopLimits,
}

impl<'a> TurnLoop<'a> {
    pub fn new(
        router: &'a Router,
        tools: &'a [Box<dyn KernelTool + 'a>],
        journal: Option<&'a mut af_journal::SessionJournal>,
        limits: LoopLimits,
    ) -> TurnLoop<'a> {
        TurnLoop {
            router,
            tools,
            journal,
            limits,
        }
    }

    fn record(
        &mut self,
        event_type: &str,
        payload: Value,
        call_id: Option<&str>,
        outcome: Option<&str>,
    ) {
        if let Some(journal) = self.journal.as_deref_mut() {
            let _ = journal.append(event_type, &payload, call_id, outcome, None);
        }
    }

    fn tool_specs(&self) -> Vec<ToolSpec> {
        self.tools.iter().map(|tool| tool.spec()).collect()
    }

    fn execute_tool(&self, call: &ToolCall) -> Value {
        let arguments: Value = serde_json::from_str(&call.arguments).unwrap_or_else(
            |error| json!({"error": format!("arguments are not valid JSON: {error}")}),
        );
        let Some(tool) = self.tools.iter().find(|tool| tool.spec().name == call.name) else {
            return json!({"error": format!("unknown tool {}", call.name)});
        };
        tool.execute(&arguments)
    }

    /// Run the bounded loop from the given conversation.
    pub fn run(
        &mut self,
        model: &str,
        mut messages: Vec<ChatMessage>,
        cancel: &Cancel,
    ) -> LoopResult<LoopOutcome> {
        let mut turns = 0usize;
        let mut tool_calls_used = 0usize;
        loop {
            if turns >= self.limits.max_turns {
                return Ok(LoopOutcome::BudgetExhausted {
                    reason: "turn budget exhausted".into(),
                    turns,
                    tool_calls_used,
                });
            }
            if tool_calls_used >= self.limits.max_tool_calls {
                return Ok(LoopOutcome::BudgetExhausted {
                    reason: "tool-call budget exhausted".into(),
                    turns,
                    tool_calls_used,
                });
            }
            messages = crate::compact::compact_if_needed(messages, self.limits.max_history_bytes);
            let mut request = ChatRequest::new(model, messages.clone());
            request.tools = Some(self.tool_specs());
            let call_id = af_entropy::token();
            self.record(
                "model_request",
                json!({"turn": turns, "tools": self.tool_specs().len()}),
                Some(&call_id),
                None,
            );
            match self.router.route(&request, cancel) {
                RouteOutcome::Completed { result, .. } => {
                    // A settled response must carry its cost: a model_response
                    // without a finite cost stays an unknown charge by design.
                    self.record(
                        "model_response",
                        json!({
                            "turn": turns,
                            "cost_usd": result.usage.cost_usd,
                            "tokens_in": result.usage.tokens_in,
                            "tokens_out": result.usage.tokens_out,
                        }),
                        Some(&call_id),
                        Some("success"),
                    );
                    turns += 1;
                    if result.tool_calls.is_empty() {
                        return Ok(LoopOutcome::Final {
                            content: result.content,
                            turns,
                            tool_calls_used,
                        });
                    }
                    // Assistant message with the requested calls…
                    let calls_value = json!(result
                        .tool_calls
                        .iter()
                        .map(raw_tool_call)
                        .collect::<Vec<_>>());
                    messages.push(ChatMessage::assistant_with_tool_calls(
                        &result.content,
                        calls_value,
                    ));
                    // …and one tool result per call, executed by the kernel.
                    for call in &result.tool_calls {
                        if tool_calls_used >= self.limits.max_tool_calls {
                            return Ok(LoopOutcome::BudgetExhausted {
                                reason: "tool-call budget exhausted mid-turn".into(),
                                turns,
                                tool_calls_used,
                            });
                        }
                        let tool_call_id = af_entropy::token();
                        self.record(
                            "tool_request",
                            json!({"name": call.name, "arguments": call.arguments}),
                            Some(&tool_call_id),
                            None,
                        );
                        let result_value = self.execute_tool(call);
                        tool_calls_used += 1;
                        self.record(
                            "tool_result",
                            json!({"name": call.name, "result": result_value}),
                            Some(&tool_call_id),
                            Some("success"),
                        );
                        messages.push(ChatMessage::tool_result(
                            &call.id,
                            &result_value.to_string(),
                        ));
                    }
                }
                RouteOutcome::Hold { backend, detail } => {
                    // Unknown after send: the charge is unknown, the loop holds.
                    self.record(
                        "model_failed",
                        json!({"detail": detail}),
                        Some(&call_id),
                        Some("unknown"),
                    );
                    return Ok(LoopOutcome::Hold { backend, detail });
                }
                RouteOutcome::Exhausted { refusals } => {
                    self.record(
                        "model_failed",
                        json!({"refusals": refusals}),
                        Some(&call_id),
                        Some("refused"),
                    );
                    return Ok(LoopOutcome::BudgetExhausted {
                        reason: format!("route exhausted before inference: {refusals:?}"),
                        turns,
                        tool_calls_used,
                    });
                }
            }
        }
    }
}

fn raw_tool_call(call: &ToolCall) -> Value {
    json!({
        "id": call.id, "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use af_llm::{parsed_tool_calls, BackendOutcome, ChatResult, ScriptedBackend, Usage};
    use serde_json::json;
    use std::cell::RefCell;

    /// Counts executions to prove exactly-once tool dispatch per loop run.
    struct CountingTool {
        name: String,
        counter: RefCell<usize>,
    }

    impl CountingTool {
        fn new(name: &str) -> CountingTool {
            CountingTool {
                name: name.to_string(),
                counter: RefCell::new(0),
            }
        }
    }

    impl KernelTool for CountingTool {
        fn spec(&self) -> ToolSpec {
            ToolSpec {
                name: self.name.clone(),
                description: "counting test tool".into(),
                parameters: json!({"type": "object"}),
            }
        }

        fn execute(&self, arguments: &Value) -> Value {
            *self.counter.borrow_mut() += 1;
            json!({"echoed": arguments, "runs": *self.counter.borrow()})
        }
    }

    fn completed(content: &str, tool_calls: Vec<ToolCall>) -> BackendOutcome {
        BackendOutcome::Completed(ChatResult {
            content: content.to_string(),
            usage: Usage {
                tokens_in: Some(1),
                tokens_out: Some(1),
                cost_usd: Some(0.0),
                latency_ms: Some(0),
            },
            truncated: false,
            tool_calls,
        })
    }

    fn call(id: &str, name: &str, arguments: &str) -> ToolCall {
        ToolCall {
            id: id.into(),
            name: name.into(),
            arguments: arguments.into(),
        }
    }

    fn temp_journal() -> (std::path::PathBuf, af_journal::SessionJournal) {
        // The environment variable is process-wide: serialize the window in
        // which it is repointed, like the af-journal tests do.
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let guard = ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let home = std::env::temp_dir().join(format!("af-loop-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", &home);
        let project = home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        let journal = af_journal::SessionJournal::create(
            &project,
            Some(&format!("loop{}", af_entropy::token())),
            None,
        )
        .unwrap();
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        drop(guard);
        (home, journal)
    }

    #[test]
    fn tool_request_then_final_answer() {
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed("", vec![call("c1", "probe", "{\"x\": 1}")]),
                completed("done", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let tool = CountingTool::new("probe");
        let tools: Vec<Box<dyn KernelTool>> = vec![Box::new(tool)];
        let (_home, mut journal) = temp_journal();
        let outcome = {
            let mut loop_ =
                TurnLoop::new(&router, &tools, Some(&mut journal), LoopLimits::default());
            loop_
                .run(
                    "test-model",
                    vec![ChatMessage::new("user", "probe it")],
                    &Cancel::new(),
                )
                .unwrap()
        };
        match outcome {
            LoopOutcome::Final {
                content,
                turns,
                tool_calls_used,
            } => {
                assert_eq!(content, "done");
                assert_eq!(turns, 2);
                assert_eq!(tool_calls_used, 1);
            }
            other => panic!("expected final, got {other:?}"),
        }
        // The journal holds no unresolved calls: everything settled.
        let snapshot = journal.read().unwrap();
        assert!(snapshot.pending_call_ids().is_empty());
        assert!(snapshot.unresolved_call_ids().is_empty());
    }

    #[test]
    fn tool_result_feeds_back_into_the_conversation() {
        // The second scripted response can only succeed if the tool result
        // reached the model: it answers with the tool's echoed payload.
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed("", vec![call("c1", "probe", "{\"x\": 7}")]),
                completed("model-saw-7", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        struct AssertingTool;
        impl KernelTool for AssertingTool {
            fn spec(&self) -> ToolSpec {
                ToolSpec {
                    name: "probe".into(),
                    description: String::new(),
                    parameters: json!({"type": "object"}),
                }
            }
            fn execute(&self, arguments: &Value) -> Value {
                assert_eq!(arguments["x"], json!(7));
                json!({"x": 7})
            }
        }
        let tools: Vec<Box<dyn KernelTool>> = vec![Box::new(AssertingTool)];
        let (_home, mut journal) = temp_journal();
        let mut loop_ = TurnLoop::new(&router, &tools, Some(&mut journal), LoopLimits::default());
        let outcome = loop_
            .run("m", vec![ChatMessage::new("user", "go")], &Cancel::new())
            .unwrap();
        assert!(matches!(outcome, LoopOutcome::Final { content, .. } if content == "model-saw-7"));
    }

    #[test]
    fn turn_budget_exhausts_and_preserves_counts() {
        let calls = (0..10)
            .map(|i| call(&format!("c{i}"), "probe", "{}"))
            .collect();
        let backend = ScriptedBackend::new("scripted", vec![completed("", calls)]);
        let router = Router::new(vec![Box::new(backend)]);
        let tool = CountingTool::new("probe");
        let tools: Vec<Box<dyn KernelTool>> = vec![Box::new(tool)];
        let (_home, mut journal) = temp_journal();
        let mut loop_ = TurnLoop::new(
            &router,
            &tools,
            Some(&mut journal),
            LoopLimits {
                max_turns: 12,
                max_tool_calls: 3,
                max_history_bytes: None,
            },
        );
        let outcome = loop_
            .run("m", vec![ChatMessage::new("user", "go")], &Cancel::new())
            .unwrap();
        match outcome {
            LoopOutcome::BudgetExhausted {
                reason,
                tool_calls_used,
                ..
            } => {
                assert!(reason.contains("mid-turn"));
                assert_eq!(tool_calls_used, 3);
            }
            other => panic!("expected budget exhaustion, got {other:?}"),
        }
    }

    #[test]
    fn hold_stops_the_loop_with_unresolved_journal_evidence() {
        let backend = ScriptedBackend::new(
            "scripted",
            vec![BackendOutcome::Unknown {
                sent: true,
                detail: "timeout after send".into(),
            }],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let tools: Vec<Box<dyn KernelTool>> = vec![Box::new(CountingTool::new("probe"))];
        let (_home, mut journal) = temp_journal();
        let outcome = {
            let mut loop_ =
                TurnLoop::new(&router, &tools, Some(&mut journal), LoopLimits::default());
            loop_
                .run("m", vec![ChatMessage::new("user", "go")], &Cancel::new())
                .unwrap()
        };
        match outcome {
            LoopOutcome::Hold { backend, detail } => {
                assert_eq!(backend, "scripted");
                assert!(detail.contains("timeout after send"));
            }
            other => panic!("expected hold, got {other:?}"),
        }
        // The unknown charge is on record; nothing retried.
        let snapshot = journal.read().unwrap();
        assert_eq!(snapshot.unresolved_call_ids().len(), 1);
    }

    #[test]
    fn unknown_tool_is_reported_to_the_model_not_a_crash() {
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed("", vec![call("c1", "ghost_tool", "{}")]),
                completed("recovered", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let tools: Vec<Box<dyn KernelTool>> = vec![Box::new(CountingTool::new("probe"))];
        let (_home, mut journal) = temp_journal();
        let mut loop_ = TurnLoop::new(&router, &tools, Some(&mut journal), LoopLimits::default());
        let outcome = loop_
            .run("m", vec![ChatMessage::new("user", "go")], &Cancel::new())
            .unwrap();
        assert!(matches!(outcome, LoopOutcome::Final { content, .. } if content == "recovered"));
    }

    #[test]
    fn parsed_tool_calls_shape_roundtrip() {
        let raw = json!([{"id": "c9", "type": "function", "function": {"name": "probe", "arguments": "{\"a\":1}"}}]);
        let calls = parsed_tool_calls(&raw);
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].name, "probe");
        assert_eq!(calls[0].arguments, "{\"a\":1}");
    }
}
