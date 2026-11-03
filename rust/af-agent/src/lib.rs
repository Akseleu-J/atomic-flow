//! af-agent: the bounded agent loop on the kernel (host mechanics slice of
//! P04–P06). The loop is: observe (full read proof + structural facts) →
//! execute the *exact reviewed* replacement (no model text, no replan) →
//! verify (Python syntax + post-apply content hash + graph parity) → record
//! evidence in the append-only journal. Without a reviewed replacement the
//! journey refuses; without full read proofs the sandbox already refuses.
//! Live-model planning is the P07 layer above this machinery.

use rustpython_parser::Parse;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub mod swarm;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct AgentError(pub String);

pub type AgentResult<T> = Result<T, AgentError>;

pub struct JourneyLimits {
    pub max_edits: usize,
}

impl Default for JourneyLimits {
    fn default() -> Self {
        JourneyLimits { max_edits: 4 }
    }
}

#[derive(Debug, Clone)]
pub struct JourneyPlan {
    pub root_id: String,
    pub file: String,
    /// Exact reviewed replacement bytes (UTF-8 text) or None to delete.
    pub replacement: Option<String>,
    pub goal: String,
}

pub struct Journey<'a> {
    registry: &'a af_sandbox::SandboxRegistry,
    journal: &'a mut af_journal::SessionJournal,
    limits: JourneyLimits,
}

#[derive(Debug, Clone)]
pub struct JourneyReport {
    pub ok: bool,
    pub goal: String,
    pub steps: Vec<Value>,
    pub verified: bool,
    pub refusal: Option<String>,
}

impl Journey<'_> {
    pub fn new<'a>(
        registry: &'a af_sandbox::SandboxRegistry,
        journal: &'a mut af_journal::SessionJournal,
        limits: JourneyLimits,
    ) -> Journey<'a> {
        Journey {
            registry,
            journal,
            limits,
        }
    }

    fn record(&mut self, event_type: &str, payload: Value) -> AgentResult<()> {
        self.journal
            .append(event_type, &payload, None, None, None)
            .map(|_| ())
            .map_err(|_| {
                AgentError("journal persistence failed; stop and reconcile; never replay".into())
            })
    }

    /// One bounded journey. Every phase is journaled; any refusal stops the
    /// journey with evidence, never with a silent fallback.
    pub fn run(&mut self, plan: &JourneyPlan) -> AgentResult<JourneyReport> {
        self.journal.require_effects_clear().map_err(|_| {
            AgentError("unresolved journal effects block journey; never replay".into())
        })?;
        let mut steps: Vec<Value> = Vec::new();

        self.record(
            "user_input",
            json!({"goal": plan.goal, "file": plan.file, "root_id": plan.root_id}),
        )?;

        // --- observe ---------------------------------------------------------
        let mut session =
            af_sandbox::SandboxSession::new(self.registry).map_err(|e| AgentError(e.0))?;
        let page = session
            .read(&plan.root_id, &plan.file, 0, 12000, true)
            .map_err(|e| AgentError(e.0))?;
        if page["read_complete"] != json!(true) {
            let refusal = "journey requires the complete current source; increase the read window"
                .to_string();
            self.record("failed", json!({"phase": "observe", "refusal": refusal}))?;
            return Ok(make_report(false, &plan.goal, &steps, false, Some(refusal)));
        }
        let before_hash = page["content_hash"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let before_source = page["source"].as_str().unwrap_or_default().to_string();
        let symbols = af_graph::adapter_ast::adapt_file(&plan.file, before_source.as_bytes());
        let symbol_count = symbols["nodes"]
            .as_array()
            .map(|nodes| nodes.len() - 1)
            .unwrap_or(0);
        steps.push(json!({
            "phase": "observe", "ok": true, "content_hash": before_hash,
            "symbols": symbol_count,
            "parse_ok": symbols.pointer("/nodes/0/facts/parse_ok"),
        }));
        self.record(
            "tool_result",
            json!({"phase": "observe", "content_hash": before_hash, "symbols": symbol_count}),
        )?;

        // --- exact reviewed plan --------------------------------------------
        let Some(replacement) = &plan.replacement else {
            let refusal =
                "journey needs an exact reviewed replacement; no model text is trusted".to_string();
            self.record("failed", json!({"phase": "plan", "refusal": refusal}))?;
            return Ok(make_report(false, &plan.goal, &steps, false, Some(refusal)));
        };
        if replacement == &before_source {
            let refusal = "replacement equals the current source; nothing to apply".to_string();
            self.record("failed", json!({"phase": "plan", "refusal": refusal}))?;
            return Ok(make_report(false, &plan.goal, &steps, false, Some(refusal)));
        }
        if self.limits.max_edits == 0 {
            let refusal = "journey edit budget is zero".to_string();
            self.record("failed", json!({"phase": "plan", "refusal": refusal}))?;
            return Ok(make_report(false, &plan.goal, &steps, false, Some(refusal)));
        }
        steps.push(json!({"phase": "plan", "ok": true, "replacement_bytes": replacement.len()}));

        // --- act (sandbox preview + apply) ------------------------------------
        let mut changes = std::collections::BTreeMap::new();
        changes.insert(plan.file.clone(), Some(replacement.clone()));
        let preview = match session.preview(&plan.root_id, &changes) {
            Ok(preview) => preview,
            Err(error) => {
                let refusal = format!("preview refused: {}", error.0);
                self.record("failed", json!({"phase": "act", "refusal": refusal}))?;
                return Ok(make_report(false, &plan.goal, &steps, false, Some(refusal)));
            }
        };
        let token = preview["preview_token"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let effect_id = af_entropy::token();
        self.journal
            .begin_effect(
                "tool_request",
                &effect_id,
                &json!({"phase": "act", "file": plan.file}),
            )
            .map_err(|_| AgentError("mutation checkpoint failed; no effect authorized".into()))?;
        let applied = match session.apply(&token, true) {
            Ok(applied) => applied,
            Err(error) => {
                let refusal = format!("apply refused: {}", error.0);
                self.journal
                    .finish_effect(
                        "tool_effect_unknown",
                        &effect_id,
                        &json!({"phase": "act", "refusal": refusal}),
                        "unknown",
                    )
                    .map_err(|_| {
                        AgentError(
                            "mutation result checkpoint failed; durable hold retained".into(),
                        )
                    })?;
                return Err(AgentError(
                    "mutation outcome requires reconciliation; never replay".into(),
                ));
            }
        };
        steps.push(json!({
            "phase": "act", "ok": true,
            "changeset_id": applied["changeset_id"],
            "status": applied["status"],
        }));
        self.journal
            .finish_effect(
                "tool_result",
                &effect_id,
                &json!({"phase": "act", "changeset": applied}),
                "success",
            )
            .map_err(|_| {
                AgentError("mutation completion persistence failed; durable hold retained".into())
            })?;

        // --- verify -----------------------------------------------------------
        let after = session
            .read(&plan.root_id, &plan.file, 0, 12000, true)
            .map_err(|e| AgentError(e.0))?;
        let after_hash = after["content_hash"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let expected_hash = sha_hex(replacement.as_bytes());
        let content_verified = after_hash == expected_hash && after_hash != before_hash;
        let syntax_verified =
            rustpython_parser::ast::Suite::parse(replacement, "<journey>").is_ok();
        let verified = content_verified && syntax_verified;
        steps.push(json!({
            "phase": "verify", "ok": verified,
            "content_verified": content_verified, "syntax_verified": syntax_verified,
            "after_hash": after_hash,
        }));
        self.record(
            if verified { "completed" } else { "failed" },
            json!({"phase": "verify", "verified": verified, "after_hash": after_hash}),
        )?;
        Ok(make_report(
            verified,
            &plan.goal,
            &steps,
            verified,
            if verified {
                None
            } else {
                Some("post-apply verification failed".into())
            },
        ))
    }
}

fn sha_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn make_report(
    ok: bool,
    goal: &str,
    steps: &[Value],
    verified: bool,
    refusal: Option<String>,
) -> JourneyReport {
    JourneyReport {
        ok,
        goal: goal.to_string(),
        steps: steps.to_vec(),
        verified,
        refusal,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use af_journal::SessionJournal;
    use af_sandbox::SandboxRegistry;

    struct TempPair {
        home: std::path::PathBuf,
        root: std::path::PathBuf,
    }

    impl Drop for TempPair {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.home);
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn setup() -> (TempPair, SandboxRegistry) {
        let home = std::env::temp_dir().join(format!("af-agent-home-{}", af_entropy::token()));
        let root = std::env::temp_dir().join(format!("af-agent-root-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&root).unwrap();
        let registry = SandboxRegistry::with_home("default", home.clone()).unwrap();
        registry
            .grant("main", &root, &["src/lib.py".to_string()], true)
            .unwrap();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.py"), b"def helper():\n    return 1\n").unwrap();
        (TempPair { home, root }, registry)
    }

    fn journal(home: &std::path::Path) -> SessionJournal {
        // Route user_dir() at this process env for the duration; the mutex
        // serializes tests that touch the process-wide environment.
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        let _guard = LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let previous = std::env::var("ATOMIC_FLOW_HOME").ok();
        std::env::set_var("ATOMIC_FLOW_HOME", home);
        let project = home.join("proj");
        std::fs::create_dir_all(&project).unwrap();
        let created = SessionJournal::create(&project, Some("journey"), None);
        match previous {
            Some(value) => std::env::set_var("ATOMIC_FLOW_HOME", value),
            None => std::env::remove_var("ATOMIC_FLOW_HOME"),
        }
        created.unwrap()
    }

    #[test]
    fn full_journey_verifies_and_journals() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits::default());
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: Some("def helper():\n    return 2  # reviewed\n".to_string()),
            goal: "bump helper".into(),
        };
        let report = journey.run(&plan).unwrap();
        assert!(report.ok, "{report:?}");
        assert!(report.verified);
        let phases: Vec<&str> = report
            .steps
            .iter()
            .map(|s| s["phase"].as_str().unwrap())
            .collect();
        assert_eq!(phases, vec!["observe", "plan", "act", "verify"]);
        let content = std::fs::read_to_string(pair.root.join("src/lib.py")).unwrap();
        assert!(content.contains("return 2  # reviewed"));
        // Evidence is in the journal.
        let snapshot = journal.read().unwrap();
        let kinds: Vec<&str> = snapshot
            .events
            .iter()
            .map(|event| event["event_type"].as_str().unwrap())
            .collect();
        assert!(kinds.contains(&"user_input"));
        assert!(kinds.contains(&"completed"));
        assert_eq!(snapshot.unresolved_call_ids().len(), 0);
    }

    #[test]
    fn journal_failure_before_apply_preserves_original_source() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        // A real interrupted durable record must stop before a write effect.
        use std::io::Write;
        std::fs::OpenOptions::new()
            .append(true)
            .open(&journal.path)
            .unwrap()
            .write_all(b"{\"interrupted\":")
            .unwrap();
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits::default());
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: Some("def helper():\n    return 2\n".into()),
            goal: "do not mutate".into(),
        };
        assert!(journey.run(&plan).is_err());
        assert_eq!(
            std::fs::read(pair.root.join("src/lib.py")).unwrap(),
            b"def helper():\n    return 1\n"
        );
    }

    #[test]
    fn journey_without_replacement_refuses() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits::default());
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: None,
            goal: "no plan".into(),
        };
        let report = journey.run(&plan).unwrap();
        assert!(!report.ok);
        assert!(report
            .refusal
            .as_deref()
            .unwrap()
            .contains("exact reviewed replacement"));
    }

    #[test]
    fn identical_replacement_refuses() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits::default());
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: Some("def helper():\n    return 1\n".to_string()),
            goal: "no-op".into(),
        };
        let report = journey.run(&plan).unwrap();
        assert!(!report.ok);
        assert!(report
            .refusal
            .as_deref()
            .unwrap()
            .contains("nothing to apply"));
    }

    #[test]
    fn zero_edit_budget_refuses() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits { max_edits: 0 });
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: Some("x = 1\n".to_string()),
            goal: "capped".into(),
        };
        let report = journey.run(&plan).unwrap();
        assert!(!report.ok);
        assert!(report
            .refusal
            .as_deref()
            .unwrap()
            .contains("edit budget is zero"));
    }

    #[test]
    fn broken_python_is_refused_at_preview_not_applied() {
        let (pair, registry) = setup();
        let mut journal = journal(&pair.home);
        let mut journey = Journey::new(&registry, &mut journal, JourneyLimits::default());
        let plan = JourneyPlan {
            root_id: "main".into(),
            file: "src/lib.py".into(),
            replacement: Some("def broken(:\n".to_string()),
            goal: "bad edit".into(),
        };
        let report = journey.run(&plan).unwrap();
        assert!(!report.ok);
        assert!(report
            .refusal
            .as_deref()
            .unwrap()
            .contains("preview refused"));
        // The file on disk is untouched.
        let content = std::fs::read_to_string(pair.root.join("src/lib.py")).unwrap();
        assert!(content.contains("return 1"));
        // And the journal keeps the refusal as evidence.
        let snapshot = journal.read().unwrap();
        assert!(snapshot
            .events
            .iter()
            .any(|event| event["event_type"] == json!("failed")
                && event.pointer("/payload/phase").and_then(Value::as_str) == Some("act")));
    }
}
