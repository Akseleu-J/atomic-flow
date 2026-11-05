//! Corporate governance contracts (B2B): RBAC over kernel actions (E03),
//! an append-only tamper-evident audit log (E07), OTel-shaped trace export,
//! and development signing for release manifests. Symmetric dev signing is
//! labelled as such; asymmetric signatures are a separate accepted change.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

/// Kernel roles (E03). Capabilities grow monotonically: viewer < operator <
/// admin. Nothing here grants authority by itself — the host binds roles to
/// principals; this matrix only refuses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Role {
    Viewer,
    Operator,
    Admin,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    ReadSource,
    ApplyEdit,
    RunJob,
    ReconcileUnknown,
    ManagePrincipals,
    ManagePolicy,
}

/// Deny-by-default matrix.
pub fn allows(role: Role, action: Action) -> bool {
    match action {
        Action::ReadSource => true,
        Action::ApplyEdit | Action::RunJob => role >= Role::Operator,
        Action::ReconcileUnknown | Action::ManagePrincipals | Action::ManagePolicy => {
            role >= Role::Admin
        }
    }
}

/// One tamper-evident audit record: `hash = sha256(prev_hash || payload)`.
#[derive(Debug, Clone)]
pub struct AuditEntry {
    pub seq: u64,
    pub actor: String,
    pub action: String,
    pub target: String,
    pub payload: Value,
    pub prev_hash: String,
    pub hash: String,
}

#[derive(Default)]
pub struct AuditLog {
    entries: Vec<AuditEntry>,
}

impl AuditLog {
    pub fn append(&mut self, actor: &str, action: &str, target: &str, payload: &Value) -> Value {
        let prev_hash = self
            .entries
            .last()
            .map(|entry| entry.hash.clone())
            .unwrap_or_else(|| "0".repeat(64));
        let seq = self.entries.len() as u64;
        let mut hasher = Sha256::new();
        hasher.update(prev_hash.as_bytes());
        hasher.update(actor.as_bytes());
        hasher.update(action.as_bytes());
        hasher.update(target.as_bytes());
        hasher.update(
            serde_json::to_string(payload)
                .unwrap_or_default()
                .as_bytes(),
        );
        let hash = format!("{:x}", hasher.finalize());
        let entry = AuditEntry {
            seq,
            actor: actor.to_string(),
            action: action.to_string(),
            target: target.to_string(),
            payload: payload.clone(),
            prev_hash: prev_hash.clone(),
            hash: hash.clone(),
        };
        let record = json!({
            "seq": seq, "actor": actor, "action": action, "target": target,
            "payload": payload, "prev_hash": prev_hash, "hash": hash,
        });
        self.entries.push(entry);
        record
    }

    /// Verify the whole chain; any edit breaks every later hash.
    pub fn verify(&self) -> Value {
        let mut previous = "0".repeat(64);
        for (expected_seq, entry) in self.entries.iter().enumerate() {
            let mut hasher = Sha256::new();
            hasher.update(entry.prev_hash.as_bytes());
            hasher.update(entry.actor.as_bytes());
            hasher.update(entry.action.as_bytes());
            hasher.update(entry.target.as_bytes());
            hasher.update(
                serde_json::to_string(&entry.payload)
                    .unwrap_or_default()
                    .as_bytes(),
            );
            let recomputed = format!("{:x}", hasher.finalize());
            if entry.seq != expected_seq as u64
                || entry.prev_hash != previous
                || entry.hash != recomputed
            {
                return json!({"ok": false, "broken_at_seq": entry.seq});
            }
            previous = entry.hash.clone();
        }
        json!({"ok": true, "entries": self.entries.len()})
    }
}

/// OTel-shaped span tree (export-shaped, not a full OTLP implementation):
/// a trace id, named spans with start/duration and child linkage.
#[derive(Debug, Clone)]
pub struct Span {
    pub name: String,
    pub start_unix: f64,
    pub duration_ms: f64,
    pub attributes: BTreeMap<String, Value>,
    pub children: Vec<Span>,
}

impl Span {
    pub fn export(&self, trace_id: &str) -> Value {
        let count = count_spans(self);
        json!({
            "trace_id": trace_id,
            "span_count": count,
            "span": span_value(self),
            "format": "otel-shaped; OTLP transport is a separate accepted change",
        })
    }
}

fn count_spans(span: &Span) -> usize {
    1 + span.children.iter().map(count_spans).sum::<usize>()
}

fn span_value(span: &Span) -> Value {
    json!({
        "name": span.name,
        "start_unix": span.start_unix,
        "duration_ms": span.duration_ms,
        "attributes": span.attributes,
        "children": span.children.iter().map(span_value).collect::<Vec<_>>(),
    })
}

/// Development signing for release manifests: keyed SHA-256 over the
/// canonical manifest bytes. Explicitly a symmetric dev signature.
pub fn dev_sign(manifest: &Value, key: &str) -> Result<String, String> {
    if key.len() < 16 {
        return Err("dev signing key must be at least 16 characters".into());
    }
    let canonical = serde_json::to_vec(&manifest).map_err(|error| format!("{error}"))?;
    let mut hasher = Sha256::new();
    hasher.update(key.as_bytes());
    hasher.update(&canonical);
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn dev_verify(manifest: &Value, key: &str, signature: &str) -> bool {
    dev_sign(manifest, key).is_ok_and(|expected| expected == signature)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rbac_is_deny_by_default_and_monotonic() {
        assert!(allows(Role::Viewer, Action::ReadSource));
        assert!(!allows(Role::Viewer, Action::ApplyEdit));
        assert!(allows(Role::Operator, Action::ApplyEdit));
        assert!(allows(Role::Operator, Action::RunJob));
        assert!(!allows(Role::Operator, Action::ReconcileUnknown));
        assert!(allows(Role::Admin, Action::ManagePrincipals));
        assert!(allows(Role::Admin, Action::ManagePolicy));
    }

    #[test]
    fn audit_chain_detects_any_tamper() {
        let mut log = AuditLog::default();
        log.append(
            "agent",
            "apply_edit",
            "src/lib.py",
            &json!({"before": "1", "after": "2"}),
        );
        log.append(
            "owner",
            "reconcile",
            "job-1",
            &json!({"reason": "verified"}),
        );
        assert_eq!(log.verify()["ok"], json!(true));
        // Tamper with the first record's payload: the chain breaks.
        let first = &mut log.entries[0];
        first.payload = json!({"before": "1", "after": "999"});
        let verdict = log.verify();
        assert_eq!(verdict["ok"], json!(false));
        assert_eq!(verdict["broken_at_seq"], json!(0));
    }

    #[test]
    fn span_export_counts_children() {
        let span = Span {
            name: "turn".into(),
            start_unix: 1.0,
            duration_ms: 120.0,
            attributes: BTreeMap::from([("model".into(), json!("test"))]),
            children: vec![Span {
                name: "tool".into(),
                start_unix: 1.1,
                duration_ms: 30.0,
                attributes: BTreeMap::new(),
                children: Vec::new(),
            }],
        };
        let exported = span.export("trace-1");
        assert_eq!(exported["span_count"], json!(2));
        assert!(exported["format"]
            .as_str()
            .unwrap()
            .contains("OTLP transport"));
    }

    #[test]
    fn dev_signing_verifies_and_refuses_weak_keys() {
        let manifest = json!({"version": "0.1.0", "artifacts": []});
        let signature = dev_sign(&manifest, "release-signing-key-1").unwrap();
        assert!(dev_verify(&manifest, "release-signing-key-1", &signature));
        assert!(!dev_verify(&manifest, "other-key-1234567890", &signature));
        let tampered = json!({"version": "0.2.0", "artifacts": []});
        assert!(!dev_verify(&tampered, "release-signing-key-1", &signature));
        assert!(dev_sign(&manifest, "short").is_err());
    }
}
