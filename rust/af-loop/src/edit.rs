//! The edit loop (Tier 0 closer): kernel tools wired for code editing —
//! context pack (repo map), full-proof reads, sandbox CAS edits, a check
//! command whose failures feed back to the model (the Aider auto-test loop),
//! and a host diagnostic gate the model cannot skip. Diagnostic success is
//! never requirement verification. Edit budget is the in-loop
//! ceiling; the durable per-task ceiling lives in af-agents::repair.

use crate::{KernelTool, LoopLimits, LoopOutcome, TurnLoop};
use af_llm::{Cancel, ChatMessage, Router};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

/// Kernel tools for one editing task inside a sandboxed root.
pub struct EditToolset<'a> {
    registry: &'a af_sandbox::SandboxRegistry,
    root_grant: Value,
    session: RefCell<af_sandbox::SandboxSession<'a>>,
    runner: RefCell<af_exec::JobRunner>,
    store_document: Value,
    pub root_id: String,
    pub check_argv: Vec<String>,
    pub check_cwd: PathBuf,
    pub max_edits: usize,
    edits_used: RefCell<usize>,
    changed: RefCell<Vec<String>>,
    write_scope: BTreeSet<String>,
    reviewed_replacements: BTreeMap<String, String>,
    effects_unknown: RefCell<bool>,
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
        let root = registry.get(root_id).map_err(|error| error.0)?;
        registry
            .require_root_effects_clear(root_id)
            .map_err(|error| error.0)?;
        let granted = root["path"]
            .as_str()
            .ok_or("granted root path is unavailable")?;
        if std::fs::canonicalize(granted).map_err(|_| "granted root is unavailable")?
            != std::fs::canonicalize(&check_cwd).map_err(|_| "check workspace is unavailable")?
        {
            return Err("check workspace must equal the granted task root".into());
        }
        Ok(EditToolset {
            registry,
            root_grant: root,
            session: RefCell::new(af_sandbox::SandboxSession::new(registry).map_err(|e| e.0)?),
            runner: RefCell::new(runner),
            store_document,
            root_id: root_id.to_string(),
            check_argv,
            check_cwd,
            max_edits,
            edits_used: RefCell::new(0),
            changed: RefCell::new(Vec::new()),
            write_scope: BTreeSet::new(),
            reviewed_replacements: BTreeMap::new(),
            effects_unknown: RefCell::new(false),
        })
    }

    /// Bind exact reviewed host paths before a model runs. Prompts, tool input
    /// and a broad root grant cannot expand this scope. An unbound toolset is
    /// read-only even when its root grant permits writes.
    pub fn with_write_scope(mut self, paths: &[String]) -> Result<Self, String> {
        if !self.write_scope.is_empty() || paths.is_empty() || paths.len() > 128 {
            return Err("a bounded nonempty reviewed write scope is required".into());
        }
        for path in paths {
            if path.len() > 512 || !self.write_scope.insert(path.clone()) {
                return Err("write scope paths must be bounded and unique".into());
            }
            af_sandbox::admission::admit_rel(&self.check_cwd, path).map_err(|error| error.0)?;
        }
        Ok(self)
    }

    /// For an endpoint that accepts an exact reviewed replacement, bind those
    /// bytes independently of the model prompt. It cannot substitute a different
    /// edit while staying inside the path scope.
    pub fn with_reviewed_replacement(
        mut self,
        path: &str,
        replacement: &str,
    ) -> Result<Self, String> {
        if !self.write_scope.contains(path)
            || self.reviewed_replacements.contains_key(path)
            || replacement.len() > 1_000_000
            || self
                .reviewed_replacements
                .values()
                .map(String::len)
                .sum::<usize>()
                + replacement.len()
                > 1_000_000
        {
            return Err(
                "reviewed replacement requires a unique scoped path and bounded exact bytes".into(),
            );
        }
        self.reviewed_replacements
            .insert(path.to_string(), replacement.to_string());
        Ok(self)
    }

    fn diagnostic_check(&self) -> Result<Value, String> {
        let before = af_sandbox::manifest(&self.check_cwd).map_err(|error| error.0)?;
        if self.check_argv.is_empty() {
            let revision =
                af_sandbox::sha256_hex(&af_graph::canonical::canonical_bytes(&json!(before)));
            return Ok(json!({"status":"not_configured", "diagnostic_passed":false,
                "verification":"not_assessed", "execution_started":false,
                "source_before":revision,"source_after":revision,"source_changed":false,
                "requirement_check":{"status":"not_assessed","reason":"no diagnostic or requirement checker is configured"}}));
        }
        let spec = af_exec::JobSpec::new(self.check_argv.clone(), self.check_cwd.clone(), 120)
            .map_err(|error| error.0)?;
        let mut status = self.runner.borrow().run(&spec).map_err(|_| {
            *self.effects_unknown.borrow_mut() = true;
            "diagnostic execution outcome requires reconciliation; never replay".to_string()
        })?;
        if status["status"] == json!("unknown") || status["cleanup_confirmed"] != json!(true) {
            *self.effects_unknown.borrow_mut() = true;
        }
        let after = af_sandbox::manifest(&self.check_cwd).map_err(|_| {
            *self.effects_unknown.borrow_mut() = true;
            "source audit unavailable after diagnostic execution; stop and reconcile".to_string()
        })?;
        status["source_before"] = json!(af_sandbox::sha256_hex(
            &af_graph::canonical::canonical_bytes(&json!(before))
        ));
        status["source_after"] = json!(af_sandbox::sha256_hex(
            &af_graph::canonical::canonical_bytes(&json!(after))
        ));
        status["source_changed"] = json!(before != after);
        status["diagnostic_passed"] = json!(
            status["status"] == json!("succeeded")
                && status["exit_code"] == json!(0)
                && status["cleanup_confirmed"] == json!(true)
                && status["timed_out"] == json!(false)
                && status["stdout_truncated"] == json!(false)
                && status["stderr_truncated"] == json!(false)
                && before == after
        );
        status["verification"] = json!("not_assessed");
        status["requirement_check"] = json!({"status":"not_assessed", "reason":"no host-bound completed requirement checker was supplied"});
        Ok(status)
    }

    pub fn edits_used(&self) -> usize {
        *self.edits_used.borrow()
    }

    /// Paths actually changed by this toolset (for the git auto-commit).
    pub fn changed_paths(&self) -> Vec<String> {
        self.changed.borrow().clone()
    }

    fn require_current_root(&self) -> Result<(), String> {
        if self.registry.get(&self.root_id).map_err(|error| error.0)? != self.root_grant {
            return Err("task root grant changed; requirement evidence is stale".into());
        }
        af_sandbox::registry::ensure_no_links(&self.check_cwd).map_err(|error| error.0)?;
        // Registry identities use canonical paths; the Windows fallback also
        // hashes those path bytes (including the canonical extended prefix).
        let canonical = std::fs::canonicalize(&self.check_cwd)
            .map_err(|_| "task root is unavailable during requirement audit")?;
        let identity = af_sandbox::registry::root_identity(&canonical).map_err(|error| error.0)?;
        if json!(identity) != self.root_grant["root_identity"] {
            return Err("task root identity changed; requirement evidence is stale".into());
        }
        self.registry
            .require_root_effects_clear(&self.root_id)
            .map_err(|error| error.0)
    }

    /// Keep the full filtered source inventory, plus exact edited bytes even
    /// for paths excluded by the snapshot filters. This is a freshness check
    /// for cooperating hosts, not isolation from arbitrary filesystem writers.
    fn requirement_inventory(&self) -> Result<Value, String> {
        use std::io::Read;
        self.require_current_root()?;
        let manifest = af_sandbox::manifest(&self.check_cwd).map_err(|error| error.0)?;
        let mut changed = BTreeMap::new();
        for path in self.changed_paths() {
            let resolved = af_sandbox::admission::admit_rel(&self.check_cwd, &path)
                .map_err(|error| error.0)?;
            let mut bytes = Vec::new();
            std::fs::File::open(resolved)
                .map_err(|_| "edited source unavailable during requirement audit")?
                .take(1_000_001)
                .read_to_end(&mut bytes)
                .map_err(|_| "edited source unreadable during requirement audit")?;
            if bytes.len() > 1_000_000 {
                return Err("edited source exceeds requirement audit byte cap".into());
            }
            changed.insert(path, af_sandbox::sha256_hex(&bytes));
        }
        self.require_current_root()?;
        Ok(json!({"manifest":manifest,"exact_changed_paths":changed}))
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
        if *self.effects_unknown.borrow() {
            return json!({"error": "an earlier effect is unknown; stop and reconcile, never replay"});
        }
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
                if !self.write_scope.contains(path) {
                    return json!({"error": "path is outside the exact host-reviewed write scope"});
                }
                if self
                    .reviewed_replacements
                    .get(path)
                    .is_some_and(|expected| expected != replacement)
                {
                    return json!({"error":"replacement differs from the exact host-reviewed bytes"});
                }
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
                    Err(_) => {
                        *self.effects_unknown.borrow_mut() = true;
                        json!({"error": "apply outcome is unknown; stop and reconcile, never replay"})
                    }
                }
            }
            "run_check" => match self.diagnostic_check() {
                Ok(status) => json!({"check": status}),
                Err(error) => json!({"error": error}),
            },
            other => json!({"error": format!("unknown op {other}")}),
        }
    }

    fn effects_unknown(&self) -> bool {
        *self.effects_unknown.borrow()
    }
    fn requires_durable_journal(&self) -> bool {
        true
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
    /// Answer or source change completed, but no admitted completed requirement
    /// check was provided. Generic exit-zero is diagnostic, never Verified.
    Unverified {
        content: String,
        edits: usize,
        check: Value,
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

/// Host-declared requirement verification bound to this edit task. The
/// checker is a trusted in-process adapter (af-evidence contract); digests
/// are host config, never model text. When supplied and the diagnostic
/// passed, the final gate mints a `HostRequirementReceipt` against the
/// post-edit source inventory and the outcome becomes `Completed`.
pub struct EditRequirement<'a> {
    pub checker: &'a mut dyn af_evidence::HostRequirementChecker,
    pub requirement_id: String,
    pub requirement_contract_sha256: String,
    pub check_inventory_sha256: String,
    pub workspace_id: String,
    pub revision: String,
}

fn hold_stale_requirement(
    toolset: &EditToolset<'_>,
    journal: &mut af_journal::SessionJournal,
    edits: usize,
) -> Result<EditOutcome, String> {
    *toolset.effects_unknown.borrow_mut() = true;
    let effect = af_entropy::token();
    journal
        .begin_effect(
            "tool_request",
            &effect,
            &json!({"name":"requirement_receipt_freshness"}),
        )
        .map_err(|_| "requirement freshness checkpoint failed; completion refused")?;
    journal
        .finish_effect(
            "tool_effect_unknown",
            &effect,
            &json!({"reason":"source or root authority changed before receipt use; reconcile"}),
            "unknown",
        )
        .map_err(|_| "requirement freshness failure persistence failed; durable hold retained")?;
    Ok(EditOutcome::Interrupted {
        edits,
        loop_outcome: LoopOutcome::Hold {
            backend: "host-check".into(),
            detail: "requirement receipt became stale; reconcile before continuation".into(),
        },
    })
}

/// Run one guided editing task: TurnLoop over the edit toolset, then the
/// host final gate. `host_approved` is an explicit operator decision bound
/// to this task (sandbox apply authority), never inferred from model text.
/// `git` is retained for compatibility. Without a bound completed requirement
/// checker this path never auto-commits, even when a diagnostic exits zero.
// Preserve the existing API with explicit host authority and requirement-checker inputs.
#[allow(clippy::too_many_arguments)]
pub fn run_edit_task(
    router: &Router,
    toolset: &EditToolset,
    journal: Option<&mut af_journal::SessionJournal>,
    model: &str,
    task: &str,
    host_approved: bool,
    limits: LoopLimits,
    git: Option<&af_exec::git::GitWorkflow>,
    requirement: Option<&mut EditRequirement<'_>>,
) -> Result<EditOutcome, String> {
    if !host_approved {
        return Err("edit tasks require explicit host approval for sandbox apply".into());
    }
    let journal =
        journal.ok_or("edit tasks require a durable journal before model or mutation effects")?;
    let tools: Vec<Box<dyn KernelTool + '_>> = vec![Box::new(RefCellTool(toolset))];
    let outcome = {
        let mut loop_ = TurnLoop::new(router, &tools, Some(&mut *journal), limits);
        loop_
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
            .map_err(|error| error.0)?
    };
    let edits = toolset.edits_used();
    match outcome {
        LoopOutcome::Final { content, .. } => {
            if edits == 0 {
                return Ok(EditOutcome::Unverified {
                    content,
                    edits: 0,
                    check: json!({"skipped": true, "verification":"not_assessed", "reason": "no edits were applied; no requirement check ran"}),
                    git: json!({"skipped": true, "reason": "no edits were applied"}),
                });
            }
            // This trusted arbitrary command is diagnostic. Persist its effect
            // boundary, audit source freshness and preserve its actual outcome;
            // it cannot substitute for a completed requirement check receipt.
            let check_id = af_entropy::token();
            journal
                .begin_effect(
                    "tool_request",
                    &check_id,
                    &json!({"name":"final_diagnostic_check", "verification":"not_assessed"}),
                )
                .map_err(|_| "final diagnostic checkpoint failed; execution was not authorized")?;
            let check = match toolset.diagnostic_check() {
                Ok(check) => check,
                Err(error) => {
                    journal
                        .finish_effect(
                            "tool_effect_unknown",
                            &check_id,
                            &json!({"reason":"final diagnostic outcome requires reconciliation"}),
                            "unknown",
                        )
                        .map_err(|_| {
                            "final diagnostic outcome could not be persisted; durable hold retained"
                        })?;
                    return Err(error);
                }
            };
            if toolset.effects_unknown() {
                journal
                    .finish_effect(
                        "tool_effect_unknown",
                        &check_id,
                        &json!({"check":check}),
                        "unknown",
                    )
                    .map_err(|_| {
                        "diagnostic completion persistence failed; durable hold retained"
                    })?;
                return Ok(EditOutcome::Interrupted {
                    edits,
                    loop_outcome: LoopOutcome::Hold {
                        backend: "host-check".into(),
                        detail: "diagnostic outcome is unknown; reconcile before continuation"
                            .into(),
                    },
                });
            }
            let diagnostic_passed = check["diagnostic_passed"] == json!(true);
            let not_configured = check["status"] == json!("not_configured");
            journal
                .finish_effect(
                    "tool_result",
                    &check_id,
                    &json!({"check":check}),
                    if diagnostic_passed {
                        "success"
                    } else {
                        "error"
                    },
                )
                .map_err(|_| {
                    "final diagnostic completion persistence failed; durable hold retained"
                })?;
            // Requirement verification (N03): only a supplied in-process host
            // checker against the post-edit inventory can mint a receipt.
            let mut requirement_check = json!({"status":"not_assessed",
                "reason":"no host-bound completed requirement checker was supplied"});
            let mut receipt_ok = false;
            let mut checked_inventory = None;
            if diagnostic_passed {
                if let Some(req) = requirement {
                    let manifest = toolset
                        .requirement_inventory()
                        .map_err(|error| format!("post-edit source audit failed: {error}"))?;
                    let inventory = af_sandbox::sha256_hex(&af_graph::canonical::canonical_bytes(
                        &json!(manifest),
                    ));
                    let scope = af_evidence::RequirementScope {
                        task_id: journal.session_id.clone(),
                        requirement_id: req.requirement_id.clone(),
                        requirement_contract_sha256: req.requirement_contract_sha256.clone(),
                        checker_id: "host-edit-gate".into(),
                        binding: af_evidence::SourceBinding {
                            workspace_id: req.workspace_id.clone(),
                            source_revision: req.revision.clone(),
                            inventory_sha256: inventory,
                            runtime_id: "af-loop-edit-gate".into(),
                        },
                        check_inventory_sha256: req.check_inventory_sha256.clone(),
                        expected_checks: 1,
                    };
                    let requirement_effect = af_entropy::token();
                    journal.begin_effect("tool_request", &requirement_effect,
                        &json!({"name":"host_requirement_check", "inventory_sha256":scope.binding.inventory_sha256}))
                        .map_err(|_| "requirement checkpoint failed; checker was not authorized")?;
                    let receipt = af_evidence::run_requirement_check(&scope, req.checker);
                    // A callback may execute code. Its result cannot authorize
                    // completion using an inventory captured before execution.
                    let fresh = toolset.requirement_inventory();
                    if fresh.as_ref().ok() != Some(&manifest) {
                        *toolset.effects_unknown.borrow_mut() = true;
                        journal.finish_effect("tool_effect_unknown", &requirement_effect,
                            &json!({"reason":"requirement checker changed source, root authority, or unresolved effects; reconcile before continuation"}), "unknown")
                            .map_err(|_| "requirement freshness failure persistence failed; durable hold retained")?;
                        return Ok(EditOutcome::Interrupted { edits, loop_outcome: LoopOutcome::Hold {
                            backend: "host-check".into(),
                            detail: "requirement checker freshness audit failed; reconcile before continuation".into(),
                        }});
                    }
                    match receipt {
                        Ok(receipt) => {
                            requirement_check = json!({"status":"verified",
                                "requirement_id": req.requirement_id,
                                "evidence_sha256": receipt.evidence_sha256()});
                            receipt_ok = true;
                            checked_inventory = Some(manifest);
                        }
                        Err(error) => {
                            requirement_check = json!({"status":"refused",
                                "reason": error.to_string()});
                        }
                    }
                    journal
                        .finish_effect(
                            "tool_result",
                            &requirement_effect,
                            &json!({"phase":"requirement_check", "status":requirement_check}),
                            if receipt_ok { "success" } else { "error" },
                        )
                        .map_err(|_| {
                            "requirement receipt persistence failed; durable hold retained"
                        })?;
                }
            }
            if receipt_ok && toolset.requirement_inventory().ok() != checked_inventory {
                return hold_stale_requirement(toolset, journal, edits);
            }
            let git_record = if receipt_ok {
                match git {
                    Some(workflow) => {
                        let changed = toolset.changed_paths();
                        let changed_refs: Vec<&str> = changed.iter().map(String::as_str).collect();
                        match workflow.auto_commit(
                            &changed_refs,
                            &format!("af: {task} (requirement verified)"),
                        ) {
                            Ok(head) => json!({"committed": true, "head": head}),
                            Err(error) => json!({"committed":false, "error": error.to_string(),
                                "reason":"auto-commit failed; recorded honestly, verdict unchanged"}),
                        }
                    }
                    None => json!({"committed":false, "workflow_supplied":false,
                        "reason":"requirement verified; no git workflow supplied"}),
                }
            } else {
                json!({"committed":false, "workflow_supplied":git.is_some(),
                    "reason":"no completed requirement receipt; diagnostic exit-zero cannot authorize auto-commit"})
            };
            if receipt_ok && toolset.requirement_inventory().ok() != checked_inventory {
                return hold_stale_requirement(toolset, journal, edits);
            }
            if diagnostic_passed || not_configured {
                if receipt_ok {
                    Ok(EditOutcome::Completed {
                        content,
                        edits,
                        check: {
                            let mut c = check;
                            c["requirement_check"] = requirement_check;
                            c
                        },
                        git: git_record,
                    })
                } else {
                    Ok(EditOutcome::Unverified {
                        content,
                        edits,
                        check: {
                            let mut c = check;
                            c["requirement_check"] = requirement_check;
                            c
                        },
                        git: git_record,
                    })
                }
            } else {
                Ok(EditOutcome::CheckFailed {
                    content,
                    edits,
                    check: {
                        let mut c = check;
                        c["requirement_check"] = requirement_check;
                        c
                    },
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
    fn effects_unknown(&self) -> bool {
        self.0.effects_unknown()
    }
    fn requires_durable_journal(&self) -> bool {
        true
    }
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
        _env: std::sync::MutexGuard<'static, ()>,
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
        // Held for the entire test via Temp: sibling tests run in parallel and
        // ATOMIC_FLOW_HOME is process-wide.
        let env_guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
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
        (
            Temp {
                home,
                root,
                store,
                _env: env_guard,
            },
            registry,
        )
    }

    fn unbound_toolset<'a>(
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

    fn toolset<'a>(
        temp: &'a Temp,
        registry: &'a af_sandbox::SandboxRegistry,
        check_exit: i32,
        max_edits: usize,
    ) -> EditToolset<'a> {
        unbound_toolset(temp, registry, check_exit, max_edits)
            .with_write_scope(&["lib.py".into()])
            .unwrap()
    }

    fn edit_journal(temp: &Temp) -> af_journal::SessionJournal {
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", &temp.home);
        let project = temp.home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        let result = af_journal::SessionJournal::create(
            &project,
            Some(&format!("edit{}", af_entropy::token())),
            None,
        );
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        result.unwrap()
    }

    #[test]
    fn zero_exit_with_no_tests_does_not_verify_or_commit_an_edit() {
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
            None,
        )
        .unwrap();
        match outcome {
            EditOutcome::Unverified {
                content,
                edits,
                check,
                git,
            } => {
                assert_eq!(content, "helper now returns 2");
                assert_eq!(edits, 1);
                assert_eq!(check["status"], json!("succeeded"));
                assert_eq!(check["verification"], json!("not_assessed"));
                assert_eq!(check["diagnostic_passed"], json!(true));
                assert_eq!(
                    git["committed"],
                    json!(false),
                    "exit-zero is not a completed requirement receipt"
                );
            }
            other => panic!("expected changed but unverified, got {other:?}"),
        }
        let on_disk = std::fs::read_to_string(temp.root.join("lib.py")).unwrap();
        assert!(on_disk.contains("return 2"));
    }

    #[test]
    fn edited_source_remains_unverified_without_a_requirement_checker() {
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
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 1  # v2\n"}"#,
                    )],
                ),
                completed("fixed", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let mut journal = edit_journal(&temp);
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "repair helper",
            true,
            LoopLimits::default(),
            None,
            None,
        )
        .unwrap();
        assert!(matches!(outcome, EditOutcome::Unverified { edits: 1, .. }));
    }

    /// A trusted host test adapter: echoes the exact scope as an observed,
    /// completed independent-behavioral execution. Test fixture only — real
    /// checkers pin their own oracle/inventory.
    struct EchoingChecker;
    impl af_evidence::HostRequirementChecker for EchoingChecker {
        fn execute(
            &mut self,
            scope: &af_evidence::RequirementScope,
        ) -> std::result::Result<af_evidence::CheckExecution, af_evidence::EvidenceError> {
            Ok(af_evidence::CheckExecution {
                task_id: scope.task_id.clone(),
                requirement_contract_sha256: scope.requirement_contract_sha256.clone(),
                checker_id: scope.checker_id.clone(),
                binding: scope.binding.clone(),
                requirement_id: scope.requirement_id.clone(),
                check_inventory_sha256: scope.check_inventory_sha256.clone(),
                kind: af_evidence::CheckKind::IndependentBehavioral,
                completed: true,
                checked: scope.expected_checks,
                passed: scope.expected_checks,
                failed: 0,
                skipped: 0,
                evidence_sha256: af_evidence::sha256(b"observed requirement checks"),
            })
        }
    }

    #[test]
    fn a_verified_requirement_receipt_completes_the_task() {
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
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 1  # verified\n"}"#,
                    )],
                ),
                completed("done", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let mut journal = edit_journal(&temp);
        let mut checker = EchoingChecker;
        let mut requirement = EditRequirement {
            checker: &mut checker,
            requirement_id: "tests-pass".into(),
            requirement_contract_sha256: af_evidence::sha256(b"tests must pass"),
            check_inventory_sha256: af_evidence::sha256(b"pytest -q"),
            workspace_id: "default".into(),
            revision: "staged".into(),
        };
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "verified repair",
            true,
            LoopLimits::default(),
            None,
            Some(&mut requirement),
        )
        .unwrap();
        match outcome {
            EditOutcome::Completed { check, git, .. } => {
                assert_eq!(check["requirement_check"]["status"], json!("verified"));
                assert_eq!(
                    check["requirement_check"]["requirement_id"],
                    json!("tests-pass")
                );
                assert!(git["workflow_supplied"] == json!(false));
            }
            other => panic!("expected a completed verified task, got {other:?}"),
        }
    }

    #[test]
    fn a_refusing_checker_keeps_the_task_unverified() {
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
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"def helper():\n    return 1  # refused\n"}"#,
                    )],
                ),
                completed("done", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let mut journal = edit_journal(&temp);
        let mut checker = af_evidence::UnavailableChecker;
        let mut requirement = EditRequirement {
            checker: &mut checker,
            requirement_id: "tests-pass".into(),
            requirement_contract_sha256: af_evidence::sha256(b"tests must pass"),
            check_inventory_sha256: af_evidence::sha256(b"pytest -q"),
            workspace_id: "default".into(),
            revision: "staged".into(),
        };
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "refused repair",
            true,
            LoopLimits::default(),
            None,
            Some(&mut requirement),
        )
        .unwrap();
        match outcome {
            EditOutcome::Unverified { check, git, .. } => {
                assert_eq!(check["requirement_check"]["status"], json!("refused"));
                assert!(git["committed"] == json!(false));
            }
            other => panic!("expected an unverified outcome, got {other:?}"),
        }
    }

    struct MutatingChecker<F: FnMut()>(F);
    impl<F: FnMut()> af_evidence::HostRequirementChecker for MutatingChecker<F> {
        fn execute(
            &mut self,
            scope: &af_evidence::RequirementScope,
        ) -> std::result::Result<af_evidence::CheckExecution, af_evidence::EvidenceError> {
            (self.0)();
            af_evidence::HostRequirementChecker::execute(&mut EchoingChecker, scope)
        }
    }

    fn assert_checker_mutation_is_held(
        edited_path: &str,
        mut mutate: impl FnMut(&Temp, &af_sandbox::SandboxRegistry),
    ) {
        let (temp, registry) = setup();
        std::fs::write(temp.root.join(edited_path), b"value = 1\n").unwrap();
        registry
            .grant("main", &temp.root, &[edited_path.into()], true)
            .unwrap();
        let toolset = unbound_toolset(&temp, &registry, 0, 4)
            .with_write_scope(&[edited_path.into()])
            .unwrap();
        let backend = ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call(
                        "read",
                        &json!({"op":"read_file","path":edited_path}).to_string(),
                    )],
                ),
                completed(
                    "",
                    vec![call(
                        "edit",
                        &json!({"op":"apply_edit","path":edited_path,"replacement":"value = 2\n"})
                            .to_string(),
                    )],
                ),
                completed("done", vec![]),
            ],
        );
        let router = Router::new(vec![Box::new(backend)]);
        let mut journal = edit_journal(&temp);
        let mut checker = MutatingChecker(|| mutate(&temp, &registry));
        let mut requirement = EditRequirement {
            checker: &mut checker,
            requirement_id: "tests-pass".into(),
            requirement_contract_sha256: af_evidence::sha256(b"tests must pass"),
            check_inventory_sha256: af_evidence::sha256(b"independent checks"),
            workspace_id: "default".into(),
            revision: "staged".into(),
        };
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "repair",
            true,
            LoopLimits::default(),
            None,
            Some(&mut requirement),
        )
        .unwrap();
        assert!(
            matches!(
                outcome,
                EditOutcome::Interrupted {
                    edits: 1,
                    loop_outcome: LoopOutcome::Hold { .. }
                }
            ),
            "mutated evidence must not complete: {outcome:?}"
        );
        assert!(toolset.effects_unknown());
        assert!(!journal.read().unwrap().unresolved_call_ids().is_empty());
    }

    #[test]
    fn requirement_checker_editing_checked_source_retains_a_durable_hold() {
        assert_checker_mutation_is_held("lib.py", |temp, _| {
            std::fs::write(temp.root.join("lib.py"), b"value = 3\n").unwrap();
        });
    }

    #[test]
    fn requirement_checker_editing_other_source_invalidates_the_full_inventory() {
        assert_checker_mutation_is_held("lib.py", |temp, _| {
            std::fs::write(temp.root.join("other.py"), b"unexpected = True\n").unwrap();
        });
    }

    #[test]
    fn requirement_checker_editing_a_filtered_changed_path_is_still_detected() {
        assert_checker_mutation_is_held(".hidden.py", |temp, _| {
            assert!(!af_sandbox::manifest(&temp.root)
                .unwrap()
                .contains_key(".hidden.py"));
            std::fs::write(temp.root.join(".hidden.py"), b"value = 3\n").unwrap();
        });
    }

    #[test]
    fn requirement_checker_editing_an_ignored_changed_path_is_still_detected() {
        assert_checker_mutation_is_held("bundle.min.js", |temp, _| {
            assert!(!af_sandbox::manifest(&temp.root)
                .unwrap()
                .contains_key("bundle.min.js"));
            std::fs::write(temp.root.join("bundle.min.js"), b"value = 3\n").unwrap();
        });
    }

    #[test]
    fn requirement_checker_revoking_the_grant_cannot_complete() {
        assert_checker_mutation_is_held("lib.py", |_, registry| {
            registry.revoke("main", true).unwrap();
        });
    }

    #[test]
    fn requirement_checker_leaving_a_root_effect_hold_cannot_complete() {
        assert_checker_mutation_is_held("lib.py", |temp, _| {
            std::fs::create_dir_all(temp.home.join("root-effects")).unwrap();
            std::fs::write(temp.home.join("root-effects/unfinished.json"), b"{}").unwrap();
        });
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
        let mut journal = edit_journal(&temp);
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "two edits",
            true,
            LoopLimits::default(),
            None,
            None,
        )
        .unwrap();
        match outcome {
            EditOutcome::Unverified { edits, .. } => assert_eq!(edits, 1),
            other => panic!("expected one unverified edit, got {other:?}"),
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
        let mut journal = edit_journal(&temp);
        let outcome = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "optimistic",
            true,
            LoopLimits::default(),
            None,
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
            None,
        )
        .unwrap_err();
        assert!(error.contains("explicit host approval"));
    }

    #[test]
    fn no_edits_is_answer_only_not_verified() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4);
        let router = Router::new(vec![Box::new(ScriptedBackend::echo())]);
        let mut journal = edit_journal(&temp);
        let result = run_edit_task(
            &router,
            &toolset,
            Some(&mut journal),
            "m",
            "answer",
            true,
            LoopLimits::default(),
            None,
            None,
        )
        .unwrap();
        match result {
            EditOutcome::Unverified { edits, check, .. } => {
                assert_eq!(edits, 0);
                assert_eq!(check["skipped"], json!(true));
                assert_eq!(check["verification"], json!("not_assessed"));
            }
            other => panic!("answer-only must not verify an edit: {other:?}"),
        }
    }

    #[test]
    fn missing_durable_journal_refuses_before_any_edit() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4);
        let router = Router::new(vec![Box::new(ScriptedBackend::new(
            "scripted",
            vec![
                completed(
                    "",
                    vec![call("read", r#"{"op":"read_file","path":"lib.py"}"#)],
                ),
                completed(
                    "",
                    vec![call(
                        "edit",
                        r#"{"op":"apply_edit","path":"lib.py","replacement":"x = 9\n"}"#,
                    )],
                ),
            ],
        ))]);
        let before = std::fs::read(temp.root.join("lib.py")).unwrap();
        let error = run_edit_task(
            &router,
            &toolset,
            None,
            "m",
            "edit",
            true,
            LoopLimits::default(),
            None,
            None,
        )
        .unwrap_err();
        assert!(error.contains("durable journal"));
        assert_eq!(toolset.edits_used(), 0);
        assert_eq!(std::fs::read(temp.root.join("lib.py")).unwrap(), before);
    }

    #[test]
    fn exact_scope_cannot_be_widened_by_model_arguments_or_a_root_grant() {
        let (temp, registry) = setup();
        std::fs::write(temp.root.join("other.py"), b"VALUE = 1\n").unwrap();
        registry
            .grant(
                "main",
                &temp.root,
                &["lib.py".into(), "other.py".into()],
                true,
            )
            .unwrap();
        let toolset = toolset(&temp, &registry, 0, 4);
        assert_eq!(
            toolset.execute(&json!({"op":"read_file","path":"other.py"}))["read_complete"],
            json!(true)
        );
        let refused = toolset.execute(
            &json!({"op":"apply_edit","path":"other.py", "replacement":"VALUE = 2\n",
            "scope_paths":["other.py"],"host_approved":true}),
        );
        assert!(refused["error"]
            .as_str()
            .unwrap()
            .contains("exact host-reviewed"));
        assert_eq!(
            std::fs::read(temp.root.join("other.py")).unwrap(),
            b"VALUE = 1\n"
        );
        assert_eq!(toolset.edits_used(), 0);
    }

    #[test]
    fn an_unbound_toolset_is_read_only_even_with_a_write_grant() {
        let (temp, registry) = setup();
        let toolset = unbound_toolset(&temp, &registry, 0, 4);
        toolset.execute(&json!({"op":"read_file","path":"lib.py"}));
        let result =
            toolset.execute(&json!({"op":"apply_edit","path":"lib.py","replacement":"x = 9\n"}));
        assert!(result["error"].is_string());
        assert_eq!(toolset.edits_used(), 0);
        assert!(std::fs::read_to_string(temp.root.join("lib.py"))
            .unwrap()
            .contains("return 1"));
    }

    #[test]
    fn stale_read_proof_never_overwrites_the_newer_source() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4);
        toolset.execute(&json!({"op":"read_file","path":"lib.py"}));
        std::fs::write(temp.root.join("lib.py"), b"EXTERNAL = 7\n").unwrap();
        let result =
            toolset.execute(&json!({"op":"apply_edit","path":"lib.py","replacement":"x = 9\n"}));
        assert!(result["error"].is_string());
        assert_eq!(toolset.edits_used(), 0);
        assert!(!toolset.effects_unknown());
        assert_eq!(
            std::fs::read(temp.root.join("lib.py")).unwrap(),
            b"EXTERNAL = 7\n"
        );
    }

    #[test]
    fn check_workspace_must_match_the_granted_root() {
        let (temp, registry) = setup();
        let runner = af_exec::JobRunner::open(&temp.home.join("jobs")).unwrap();
        let result = EditToolset::new(
            &registry,
            "main",
            runner,
            json!({"nodes":[],"edges":[]}),
            vec!["unused".into()],
            temp.home.clone(),
            4,
        );
        assert!(result.err().unwrap().contains("must equal the granted"));
    }

    #[test]
    fn zero_exit_source_mutation_invalidates_the_diagnostic_source_binding() {
        let (temp, registry) = setup();
        let mut toolset = toolset(&temp, &registry, 0, 4);
        #[cfg(windows)]
        {
            toolset.check_argv = vec![
                "cmd".into(),
                "/C".into(),
                "echo EXTERNAL = 3 > lib.py".into(),
            ];
        }
        #[cfg(not(windows))]
        {
            toolset.check_argv = vec![
                "sh".into(),
                "-c".into(),
                "printf 'EXTERNAL = 3\\n' > lib.py".into(),
            ];
        }
        let check = toolset.diagnostic_check().unwrap();
        assert_eq!(check["exit_code"], json!(0));
        assert_eq!(check["source_changed"], json!(true));
        assert_eq!(check["diagnostic_passed"], json!(false));
        assert_eq!(check["verification"], json!("not_assessed"));
    }

    #[test]
    fn model_cannot_replace_different_bytes_than_the_reviewed_http_change() {
        let (temp, registry) = setup();
        let toolset = toolset(&temp, &registry, 0, 4)
            .with_reviewed_replacement("lib.py", "REVIEWED = 2\n")
            .unwrap();
        toolset.execute(&json!({"op":"read_file","path":"lib.py"}));
        let refused = toolset.execute(&json!({"op":"apply_edit","path":"lib.py",
            "replacement":"UNREVIEWED = 3\n","host_approved":true}));
        assert!(refused["error"]
            .as_str()
            .unwrap()
            .contains("exact host-reviewed bytes"));
        assert_eq!(toolset.edits_used(), 0);
        assert!(std::fs::read_to_string(temp.root.join("lib.py"))
            .unwrap()
            .contains("return 1"));
    }
}
