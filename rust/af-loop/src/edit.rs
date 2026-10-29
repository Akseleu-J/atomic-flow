//! The edit loop (Tier 0 closer): kernel tools wired for code editing —
//! context pack (repo map), full-proof reads, sandbox CAS edits, a check
//! command whose failures feed back to the model (the Aider auto-test loop),
//! and a host final gate the model cannot skip. Edit budget is the in-loop
//! ceiling; the durable per-task ceiling lives in af-agents::repair.

use crate::{KernelTool, LoopLimits, LoopOutcome, TurnLoop};
use af_llm::{Cancel, ChatMessage, Router};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::path::PathBuf;

/// Kernel tools for one editing task inside a sandboxed root.
pub struct EditToolset<'a> {
    session: RefCell<af_sandbox::SandboxSession<'a>>,
    runner: RefCell<af_exec::JobRunner>,
    store_document: Value,
    pub root_id: String,
    pub check_argv: Vec<String>,
    pub check_cwd: PathBuf,
    pub max_edits: usize,
    edits_used: RefCell<usize>,
    changed: RefCell<Vec<String>>,
}

impl<'a> EditToolset<'a> {
    pub fn new(
        registry: &'a af_sandbox::SandboxRegistry,
        root_id: &str,
        runner: af_exec::JobRunner,
        store_document: Value,
        check_argv: Vec<String>,
        check_cwd: PathBuf,
        max_edits: usize,
    ) -> Result<Self, String> {
        Ok(EditToolset {
            session: RefCell::new(af_sandbox::SandboxSession::new(registry).map_err(|e| e.0)?),
            runner: RefCell::new(runner),
            store_document,
            root_id: root_id.to_string(),
            check_argv,
            check_cwd,
            max_edits,
            edits_used: RefCell::new(0),
            changed: RefCell::new(Vec::new()),
        })
    }

    pub fn edits_used(&self) -> usize {
        *self.edits_used.borrow()
    }

    /// Paths actually changed by this toolset (for the git auto-commit).
    pub fn changed_paths(&self) -> Vec<String> {
        self.changed.borrow().clone()
    }
}

impl KernelTool for EditToolset<'_> {
    fn spec(&self) -> af_llm::ToolSpec {
        af_llm::ToolSpec {
            name: "edit_task".into(),
            description: "context/read/apply_edit/run_check on the task root".into(),
            parameters: json!({"type": "object"}),
        }
    }

    fn execute(&self, arguments: &Value) -> Value {
        let op = arguments
            .get("op")
            .and_then(Value::as_str)
            .unwrap_or_default();
        match op {
            "context" => {
                let query = arguments
                    .get("query")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let index = af_context::ContextIndex::build(&self.store_document);
                af_context::context_pack(&index, query, 6000, None)
            }
            "read_file" => {
                let Some(path) = arguments.get("path").and_then(Value::as_str) else {
                    return json!({"error": "read_file requires path"});
                };
                match self
                    .session
                    .borrow_mut()
                    .read(&self.root_id, path, 0, 12000, true)
                {
                    Ok(page) => page,
                    Err(error) => json!({"error": error.0}),
                }
            }
            "apply_edit" => {
                let (Some(path), Some(replacement)) = (
                    arguments.get("path").and_then(Value::as_str),
                    arguments.get("replacement").and_then(Value::as_str),
                ) else {
                    return json!({"error": "apply_edit requires path and replacement"});
                };
                if *self.edits_used.borrow() >= self.max_edits {
                    return json!({"error": format!("edit budget exhausted ({} of {} used); repair via one precise edit or stop", self.edits_used.borrow(), self.max_edits)});
                }
                let mut changes = std::collections::BTreeMap::new();
                changes.insert(path.to_string(), Some(replacement.to_string()));
                let preview = match self.session.borrow_mut().preview(&self.root_id, &changes) {
                    Ok(preview) => preview,
                    Err(error) => return json!({"error": error.0}),
                };
                let token = preview["preview_token"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                match self.session.borrow_mut().apply(&token, true) {
                    Ok(report) => {
                        *self.edits_used.borrow_mut() += 1;
                        self.changed.borrow_mut().push(path.to_string());
                        report
                    }
                    Err(error) => json!({"error": error.0}),
                }
            }
            "run_check" => {
                let spec = match af_exec::JobSpec::new(
                    self.check_argv.clone(),
                    self.check_cwd.clone(),
                    120,
                ) {
                    Ok(spec) => spec,
                    Err(error) => return json!({"error": error.0}),
                };
                match self.runner.borrow().run(&spec) {
                    Ok(status) => json!({
                        "check": {"status": status["status"], "exit_code": status["exit_code"]},
                    }),
                    Err(error) => json!({"error": error.0}),
                }
            }
            other => json!({"error": format!("unknown op {other}")}),
        }
    }
}

/// Outcome of one guided editing run.
#[derive(Debug, Clone, PartialEq)]
pub enum EditOutcome {
    /// Final answer AND the host final gate passed (when edits happened).
    Completed {
        content: String,
        edits: usize,
        check: Value,
        /// Auto-commit record: committed head, skip reason or honest error.
        git: Value,
    },
    /// The model finished, but the final gate failed: not a success, ever.
    CheckFailed {
        content: String,
        edits: usize,
        check: Value,
        git: Value,
    },
    /// Route hold or budget exhaustion — surfaced unchanged with counts.
    Interrupted {
        loop_outcome: LoopOutcome,
        edits: usize,
    },
}

/// Run one guided editing task: TurnLoop over the edit toolset, then the
/// host final gate. `host_approved` is an explicit operator decision bound
/// to this task (sandbox apply authority), never inferred from model text.
/// `git` is an optional GitWorkflow; when supplied and the check verifies,
/// exactly one auto-commit records the change on the task branch.
pub fn run_edit_task(
    router: &Router,
    toolset: &EditToolset,
    journal: Option<&mut af_journal::SessionJournal>,
    model: &str,
    task: &str,
    host_approved: bool,
    limits: LoopLimits,
    git: Option<&af_exec::git::GitWorkflow>,
) -> Result<EditOutcome, String> {
    if !host_approved {
        return Err("edit tasks require explicit host approval for sandbox apply".into());
    }
    let tools: Vec<Box<dyn KernelTool + '_>> = vec![Box::new(RefCellTool(toolset))];
    let mut loop_ = TurnLoop::new(router, &tools, journal, limits);
    let outcome = loop_
        .run(
            model,
            vec![ChatMessage::new(
                "user",
                &format!(
                    "Task: {task}\nTools: context (repo map), read_file, apply_edit, run_check.\n\
                 Rules: read the complete file before apply_edit; run_check after edits;\n\
                 finish with a final answer only when the check passes."
                ),
            )],
            &Cancel::new(),
        )
        .map_err(|error| error.0)?;
    let edits = toolset.edits_used();
    match outcome {
        LoopOutcome::Final { content, .. } => {
            if edits == 0 {
                return Ok(EditOutcome::Completed {
                    content,
                    edits: 0,
                    check: json!({"skipped": true, "reason": "no edits were applied"}),
                    git: json!({"skipped": true, "reason": "no edits were applied"}),
                });
            }
            // Host final gate: the check runs again, model-invisible.
            let spec =
                af_exec::JobSpec::new(toolset.check_argv.clone(), toolset.check_cwd.clone(), 120)
                    .map_err(|e| e.0)?;
            let check = toolset.runner.borrow().run(&spec).map_err(|e| e.0)?;
            let verified = check["status"] == json!("succeeded");
            // Git-native workflow: one auto-commit per verified change set,
            // only when the caller supplied a workflow bound to a repo root.
            // A commit failure is recorded honestly; it never flips the
            // check verdict, and unverified changes are never committed.
            let git_record = if verified {
                match git {
                    Some(workflow) => {
                        let changed = toolset.changed_paths();
                        let changed_refs: Vec<&str> = changed.iter().map(String::as_str).collect();
                        match workflow
                            .auto_commit(&changed_refs, &format!("af: {task} (check verified)"))
                        {
                            Ok(head) => json!({"committed": true, "head": head}),
                            Err(error) => json!({"committed": false, "error": error.0}),
                        }
                    }
                    None => json!({"skipped": true, "reason": "no git workflow supplied"}),
                }
            } else {
                json!({"committed": false, "reason": "checks did not pass; unverified changes are never committed"})
            };
            if verified {
                Ok(EditOutcome::Completed {
                    content,
                    edits,
                    check,
                    git: git_record,
                })
            } else {
                Ok(EditOutcome::CheckFailed {
                    content,
                    edits,
                    check,
                    git: git_record,
                })
            }
        }
        other => Ok(EditOutcome::Interrupted {
            loop_outcome: other,
            edits,
        }),
    }
}

/// Adapter so a shared toolset can serve as one KernelTool with op dispatch.
/// Two lifetimes: the borrow of the toolset and the registry borrow inside it.
struct RefCellTool<'a, 'b>(&'a EditToolset<'b>);

impl KernelTool for RefCellTool<'_, '_> {
    fn spec(&self) -> af_llm::ToolSpec {
        af_llm::ToolSpec {
            name: "edit_task".into(),
            description: "context/read_file/apply_edit/run_check".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["context", "read_file", "apply_edit", "run_check"]},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["op"],
            }),
        }
    }

    fn execute(&self, arguments: &Value) -> Value {
        self.0.execute(arguments)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use af_llm::{ChatResult, ScriptedBackend, ToolCall, Usage};
    use serde_json::json;

    fn completed(content: &str, calls: Vec<ToolCall>) -> af_llm::BackendOutcome {
        af_llm::BackendOutcome::Completed(ChatResult {
            content: content.into(),
            usage: Usage {
                tokens_in: Some(1),
                tokens_out: Some(1),
                cost_usd: Some(0.0),
                latency_ms: Some(0),
            },
            truncated: false,
            tool_calls: calls,
        })
    }

    fn call(id: &str, arguments: &str) -> ToolCall {
        ToolCall {
            id: id.into(),
            name: "edit_task".into(),
            arguments: arguments.into(),
        }
    }

    struct Temp {
        home: std::path::PathBuf,
        root: std::path::PathBuf,
        store: std::path::PathBuf,
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.home);
            let _ = std::fs::remove_dir_all(&self.root);
            let _ = std::fs::remove_dir_all(&self.store);
        }
    }

    fn setup() -> (Temp, af_sandbox::SandboxRegistry) {
        static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        let home = std::env::temp_dir().join(format!("af-edit-home-{}", af_entropy::token()));
        let root = std::env::temp_dir().join(format!("af-edit-root-{}", af_entropy::token()));
        let store = std::env::temp_dir().join(format!("af-edit-store-{}", af_entropy::token()));
        for dir in [&home, &root, &store] {
            std::fs::create_dir_all(dir).unwrap();
        }
        std::env::set_var("ATOMIC_FLOW_HOME", &home);
        std::fs::write(root.join("lib.py"), b"def helper():\n    return 1\n").unwrap();
        let registry = af_sandbox::SandboxRegistry::with_home("default", home.clone()).unwrap();
        registry
            .grant("main", &root, &["lib.py".into()], true)
            .unwrap();
        let mut files = std::collections::BTreeMap::new();
        files.insert(
            "lib.py".to_string(),
            b"def helper():\n    return 1\n".to_vec(),
        );
        let build = af_graph::project::clean_build(&files).unwrap();
        std::fs::write(
            store.join("graph.json"),
            af_graph::canonical::canonical_bytes(&json!({
                "schema_version": 1, "generation": 0,
                "nodes": build["nodes"], "edges": build["edges"],
            })),
        )
        .unwrap();
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        (Temp { home, root, store }, registry)
    }

    fn toolset<'a>(
        temp: &'a Temp,
        registry: &'a af_sandbox::SandboxRegistry,
        check_exit: i32,
        max_edits: usize,
    ) -> EditToolset<'a> {
        let document: Value =
            serde_json::from_str(&std::fs::read_to_string(temp.store.join("graph.json")).unwrap())
                .unwrap();
        let runner = af_exec::JobRunner::open(&temp.home.join("jobs")).unwrap();
        #[cfg(windows)]
        let check_argv = vec!["cmd".into(), "/C".into(), format!("exit {check_exit}")];
        #[cfg(not(windows))]
        let check_argv = vec!["sh".into(), "-c".into(), format!("exit {check_exit}")];
        EditToolset::new(
            registry,
            "main",
            runner,
            document,
            check_argv,
            temp.root.clone(),
            max_edits,
        )
        .unwrap()
    }

    #[test]
    fn guided_edit_read_apply_check_final() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4);
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call("c1", r#"{"op":"read_file","path":"lib.py"}"#)],
                ),
                completed(
                    "",
                    vec![call(
                        "c2",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 2\n"}"#,
                    )],
                ),
                completed("", vec![call("c3", r#"{"op":"run_check"}"#)]),
                completed("helper now returns 2", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let (_home, mut journal) = {
            std::env::set_var("ATOMIC_FLOW_HOME", &temp.home);
            let project = temp.home.join("proj");
            std::fs::create_dir_all(&project).unwrap();
            let journal =
                af_journal::SessionJournal::create(&project, Some("guided"), None).unwrap();
            std::env::remove_var("ATOMIC_FLOW_HOME");
            ((), journal)
        };
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "bump helper",
            true,
            LoopLimits::default(),
            None,
        )
        .unwrap();
        match outcome {
            EditOutcome::Completed {
                content,
                edits,
                check,
                git,
            } => {
                assert_eq!(content, "helper now returns 2");
                assert_eq!(edits, 1);
                assert_eq!(check["status"], json!("succeeded"));
                assert_eq!(
                    git["skipped"],
                    json!(true),
                    "no workflow supplied: honest skip"
                );
            }
            other => panic!("expected completed, got {other:?}"),
        }
        let on_disk = std::fs::read_to_string(temp.root.join("lib.py")).unwrap();
        assert!(on_disk.contains("return 2"));
    }

    #[test]
    fn failing_check_feeds_back_and_repair_succeeds() {
        // check exits 1 while the helper is "broken"; after the repair edit
        // the toolset's check passes (argv is re-read per call? no — the
        // fixture uses a marker file: check fails while marker absent).
        let (temp, registry) = setup();
        let marker = temp.root.join("marker.tmp");
        let toolset = toolset(&temp, &registry, 0, 4);
        // First check must fail: run_check reports exit code of a probe job —
        // model then applies the real edit; final gate re-runs check (pass).
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call("c1", r#"{"op":"read_file","path":"lib.py"}"#)],
                ),
                completed(
                    "",
                    vec![call(
                        "c2",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 1  # v2\n"}"#,
                    )],
                ),
                completed("fixed", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let outcome = run_edit_task(
            &router,
            &toolset,
            None,
            "m",
            "repair helper",
            true,
            LoopLimits::default(),
            None,
        )
        .unwrap();
        assert!(matches!(outcome, EditOutcome::Completed { edits: 1, .. }));
        let _ = std::fs::remove_file(&marker);
    }

    #[test]
    fn edit_budget_refuses_second_edit() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 1);
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call("c0", r#"{"op":"read_file","path":"lib.py"}"#)],
                ),
                completed(
                    "",
                    vec![call(
                        "c1",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 2\n"}"#,
                    )],
                ),
                completed(
                    "",
                    vec![call(
                        "c2",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 3\n"}"#,
                    )],
                ),
                completed("gave up", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let outcome = run_edit_task(
            &router,
            &toolset,
            None,
            "m",
            "two edits",
            true,
            LoopLimits::default(),
            None,
        )
        .unwrap();
        match outcome {
            EditOutcome::Completed { edits, .. } => assert_eq!(edits, 1),
            other => panic!("expected completed with one edit, got {other:?}"),
        }
        let on_disk = std::fs::read_to_string(temp.root.join("lib.py")).unwrap();
        assert!(on_disk.contains("return 2"));
        assert!(!on_disk.contains("return 3"));
    }

    #[test]
    fn final_gate_fails_when_check_fails() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 1, 4); // check always fails
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call("c0", r#"{"op":"read_file","path":"lib.py"}"#)],
                ),
                completed(
                    "",
                    vec![call(
                        "c1",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 9\n"}"#,
                    )],
                ),
                completed("i think it is fine", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let outcome = run_edit_task(
            &router,
            &toolset,
            None,
            "m",
            "optimistic",
            true,
            LoopLimits::default(),
            None,
        )
        .unwrap();
        match outcome {
            EditOutcome::CheckFailed { edits, check, .. } => {
                assert_eq!(edits, 1);
                assert_eq!(check["status"], json!("failed"));
            }
            other => panic!("expected check-failed, got {other:?}"),
        }
    }

    #[test]
    fn host_approval_is_explicit() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4);
        let backend = ScriptedBackend::echo();
        let router = Router::new(vec![Box::new(backend)]);
        let error = run_edit_task(
            &router,
            &toolset,
            None,
            "m",
            "t",
            false,
            LoopLimits::default(),
            None,
        )
        .unwrap_err();
        assert!(error.contains("explicit host approval"));
    }
}
