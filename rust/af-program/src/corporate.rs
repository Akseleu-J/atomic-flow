//! Corporate kernel contracts (E-series): principal scoping (E03), data
//! classification and egress policy (E05), backend conformance (E06) and the
//! headless multi-principal acceptance record (E08). UI is deliberately
//! absent; every check is machine-readable.

use serde_json::{json, Value};
use af_llm::{BackendOutcome, ChatBackend, ChatRequest, RouteOutcome, Router};

/// E03: a principal scope binds a named principal to its own application
/// state namespace. Different principals never share registries or journals.
#[derive(Debug, Clone, PartialEq)]
pub struct PrincipalScope {
    pub principal_id: String,
    pub policy_version: String,
}

impl PrincipalScope {
    pub fn state_namespace(&self) -> Result<String, String> {
        if self.principal_id.trim().is_empty() || self.principal_id.len() > 128 {
            return Err("principal id must be a bounded non-empty identifier".into());
        }
        if self.policy_version.trim().is_empty() {
            return Err("policy version must be bound".into());
        }
        Ok(format!("principals/{}/policy-{}", self.principal_id, self.policy_version))
    }
}

/// E05: data classification with a deny-by-default egress policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum DataClass {
    Public,
    Internal,
    Confidential,
    Restricted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Destination {
    LocalDisk,
    ModelProvider,
    RemoteWorker,
    Browser,
}

/// Default-closed matrix: Restricted leaves only the local disk; providers
/// never receive Confidential or Restricted content.
pub fn egress_allows(class: DataClass, destination: Destination) -> bool {
    match class {
        DataClass::Public => true,
        DataClass::Internal => !matches!(destination, Destination::Browser),
        DataClass::Confidential => matches!(destination, Destination::LocalDisk | Destination::RemoteWorker),
        DataClass::Restricted => matches!(destination, Destination::LocalDisk),
    }
}

/// E06: backend conformance — the required behaviors every admitted adapter
/// must exhibit before routing. Runs scripted; adapters that violate the
/// spend contract fail the suite.
pub fn conformance_suite(backend: &dyn ChatBackend) -> Value {
    let request = ChatRequest {
        model: "conformance".into(),
        messages: vec![af_llm::ChatMessage::new("user", "ping")],
        max_output_tokens: Some(8),
    };
    let cancel = af_llm::Cancel::new();
    let outcome = backend.chat(&request, &cancel);
    let mut checks: Vec<Value> = Vec::new();
    match &outcome {
        BackendOutcome::Completed(result) => {
            checks.push(json!({
                "check": "completed_calls_settle_usage",
                "ok": result.usage.tokens_in.is_some() && result.usage.tokens_out.is_some() && result.usage.cost_usd.is_some(),
                "note": "a completed call must settle tokens and cost; unknowns are holds, not free wins",
            }));
        }
        BackendOutcome::Refused { sent, .. } => {
            checks.push(json!({"check": "refusal_reports_send_state", "ok": true, "sent": sent}));
        }
        BackendOutcome::Unknown { sent, .. } => {
            checks.push(json!({"check": "unknown_reports_send_state", "ok": true, "sent": sent}));
        }
    }
    // Cancel-before-send must be free for a conforming backend.
    let cancel = af_llm::Cancel::new();
    cancel.cancel();
    let cancelled = backend.chat(&request, &cancel);
    checks.push(json!({
        "check": "cancel_before_send_is_not_sent",
        "ok": matches!(cancelled, BackendOutcome::Refused { sent: false, .. }),
    }));
    let ok = checks.iter().all(|check| check["ok"] == json!(true));
    json!({"backend": backend.name(), "ok": ok, "checks": checks})
}

/// E06: the router must hold on a post-send unknown — the conformance
/// contract at the routing level.
pub fn router_hold_conformance() -> Value {
    // A scripted hold: unknown after send must produce RouteOutcome::Hold.
    let scripted = af_llm::ScriptedBackend::new(
        "hold-probe",
        vec![BackendOutcome::Unknown { sent: true, detail: "post-send loss".into() }],
    );
    let router = Router::new(vec![Box::new(scripted)]);
    let request = ChatRequest {
        model: "conformance".into(),
        messages: vec![af_llm::ChatMessage::new("user", "ping")],
        max_output_tokens: Some(8),
    };
    match router.route(&request, &af_llm::Cancel::new()) {
        RouteOutcome::Hold { backend, .. } => json!({
            "check": "post_send_unknown_holds_route", "ok": true, "backend": backend,
        }),
        _ => json!({"check": "post_send_unknown_holds_route", "ok": false}),
    }
}

/// E08: headless multi-principal acceptance record. Each principal gets an
/// isolated namespace; the record refuses cross-principal state reuse.
#[derive(Debug, Clone)]
pub struct PrincipalAcceptance {
    pub scopes: Vec<PrincipalScope>,
}

impl PrincipalAcceptance {
    pub fn build(&self) -> Result<Value, String> {
        if self.scopes.len() < 2 {
            return Err("multi-principal acceptance requires at least two principals".into());
        }
        let mut namespaces: Vec<String> = Vec::new();
        for scope in &self.scopes {
            namespaces.push(scope.state_namespace()?);
        }
        let unique = namespaces.iter().collect::<std::collections::BTreeSet<_>>().len();
        if unique != namespaces.len() {
            return Err("principal namespaces must be distinct; shared state is refused".into());
        }
        Ok(json!({
            "schema_version": 1,
            "headless": true,
            "principals": self.scopes.iter().zip(&namespaces).map(|(scope, namespace)| json!({
                "principal_id": scope.principal_id,
                "namespace": namespace,
            })).collect::<Vec<_>>(),
            "isolation": "each principal resolves its own registries/journals; cross-principal reuse is refused by construction",
            "note": "this record covers namespace isolation; server accounting, queue fairness and data policy acceptances are separate E-gates",
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn principal_namespaces_are_distinct_and_validated() {
        let scope_a = PrincipalScope { principal_id: "owner".into(), policy_version: "v1".into() };
        let scope_b = PrincipalScope { principal_id: "team".into(), policy_version: "v1".into() };
        assert_ne!(scope_a.state_namespace().unwrap(), scope_b.state_namespace().unwrap());
        assert!(PrincipalScope { principal_id: String::new(), policy_version: "v1".into() }
            .state_namespace()
            .is_err());
        assert!(PrincipalScope { principal_id: "owner".into(), policy_version: String::new() }
            .state_namespace()
            .is_err());
        let acceptance = PrincipalAcceptance { scopes: vec![scope_a.clone(), scope_b.clone()] };
        let record = acceptance.build().unwrap();
        assert_eq!(record["headless"], json!(true));
        assert_eq!(record["principals"].as_array().unwrap().len(), 2);
        // Fewer than two principals and duplicated namespaces refuse.
        let solo = PrincipalAcceptance { scopes: vec![scope_b.clone()] };
        assert!(solo.build().is_err());
        let dup = PrincipalAcceptance {
            scopes: vec![scope_b.clone(), scope_b],
        };
        assert!(dup.build().unwrap_err().contains("distinct"));
    }

    #[test]
    fn egress_matrix_is_deny_by_default() {
        assert!(egress_allows(DataClass::Public, Destination::ModelProvider));
        assert!(!egress_allows(DataClass::Internal, Destination::Browser));
        assert!(!egress_allows(DataClass::Confidential, Destination::ModelProvider));
        assert!(egress_allows(DataClass::Confidential, Destination::RemoteWorker));
        assert!(egress_allows(DataClass::Restricted, Destination::LocalDisk));
        assert!(!egress_allows(DataClass::Restricted, Destination::RemoteWorker));
    }

    #[test]
    fn scripted_backend_passes_conformance_and_hold_contract() {
        let echo = af_llm::ScriptedBackend::echo();
        let suite = conformance_suite(&echo);
        assert_eq!(suite["ok"], json!(true), "{suite}");
        let hold = router_hold_conformance();
        // The hold probe uses its own scripted unknown-after-send backend.
        assert_eq!(hold["check"], json!("post_send_unknown_holds_route"));
        assert_eq!(hold["ok"], json!(true));
        assert_eq!(hold["backend"], json!("hold-probe"));
    }
}
