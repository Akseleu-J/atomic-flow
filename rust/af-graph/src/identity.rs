//! Host-supplied graph security namespace (port of `graph/identity.py`).
//! Graph IDs are local within this scope; identity binds
//! principal/workspace/project/root/policy and read contexts require the
//! binding to match host grants.

use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use crate::schema::GraphContractError;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct GraphIdentity {
    pub principal_id: String,
    pub workspace_id: String,
    pub project_id: String,
    pub root_id: String,
    pub policy_version: String,
}

impl GraphIdentity {
    pub fn new(
        principal_id: &str,
        workspace_id: &str,
        project_id: &str,
        root_id: &str,
        policy_version: &str,
    ) -> Result<Self, GraphContractError> {
        for value in [principal_id, workspace_id, project_id, root_id, policy_version] {
            validate_component(value)?;
        }
        Ok(GraphIdentity {
            principal_id: principal_id.to_string(),
            workspace_id: workspace_id.to_string(),
            project_id: project_id.to_string(),
            root_id: root_id.to_string(),
            policy_version: policy_version.to_string(),
        })
    }

    /// Stable namespace digest: same fields, canonical JSON, sha256.
    pub fn namespace(&self) -> String {
        let document = json!({
            "policy_version": self.policy_version,
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "root_id": self.root_id,
            "workspace_id": self.workspace_id,
        });
        let mut hasher = Sha256::new();
        hasher.update(serde_json::to_string(&document).unwrap_or_default());
        format!("{:x}", hasher.finalize())
    }
}

fn validate_component(value: &str) -> Result<(), GraphContractError> {
    if value.is_empty()
        || value.len() > 256
        || value.bytes().any(|b| b == 0 || b == b'\r' || b == b'\n')
    {
        return Err(GraphContractError("invalid host graph identity".into()));
    }
    Ok(())
}

#[derive(Debug, Clone)]
pub struct GraphReadContext {
    pub identity: GraphIdentity,
    grants: BTreeSet<(String, String, String)>,
}

impl GraphReadContext {
    /// Grants are immutable trusted (workspace, project, root) tuples.
    pub fn new(identity: GraphIdentity, grants: &[(String, String, String)]) -> Result<Self, GraphContractError> {
        let mut set = BTreeSet::new();
        for grant in grants {
            let (workspace, project, root) = grant;
            if workspace.is_empty() || project.is_empty() || root.is_empty() {
                return Err(GraphContractError("graph grants require immutable trusted root tuples".into()));
            }
            set.insert((workspace.clone(), project.clone(), root.clone()));
        }
        Ok(GraphReadContext { identity, grants: set })
    }

    /// The read context must bind exactly this identity, and its
    /// workspace/project/root triple must be inside the host grants.
    pub fn require(&self, expected: &GraphIdentity) -> Result<(), GraphContractError> {
        if self.identity != *expected {
            return Err(GraphContractError("graph principal/project/root/policy binding differs".into()));
        }
        let key = (expected.workspace_id.clone(), expected.project_id.clone(), expected.root_id.clone());
        if !self.grants.contains(&key) {
            return Err(GraphContractError("graph root is outside host grants".into()));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity() -> GraphIdentity {
        GraphIdentity::new("principal-1", "ws-1", "proj-1", "root-1", "policy-v1").expect("valid identity")
    }

    #[test]
    fn namespace_is_stable_and_field_order_independent() {
        let a = identity().namespace();
        let b = GraphIdentity::new("principal-1", "ws-1", "proj-1", "root-1", "policy-v1")
            .expect("valid")
            .namespace();
        assert_eq!(a, b);
        assert_ne!(a, GraphIdentity::new("principal-1", "ws-1", "proj-1", "root-1", "policy-v2").unwrap().namespace());
        assert_eq!(a.len(), 64);
    }

    #[test]
    fn components_are_validated() {
        assert!(GraphIdentity::new("", "ws", "p", "r", "v").is_err());
        // Duplicate component values are legal; only empty/overlong/control
        // components are refused (parity with the Python contract).
        assert!(GraphIdentity::new("p", "ws", "p", "r", "v").is_ok());
        let long = "x".repeat(257);
        assert!(GraphIdentity::new(&long, "ws", "p", "r", "v").is_err());
        assert!(GraphIdentity::new("bad\nid", "ws", "p", "r", "v").is_err());
        assert!(identity().namespace().len() == 64);
    }

    #[test]
    fn read_context_requires_identity_and_grant() {
        let identity = identity();
        let grants = vec![("ws-1".to_string(), "proj-1".to_string(), "root-1".to_string())];
        let context = GraphReadContext::new(identity.clone(), &grants).expect("context");
        context.require(&identity).expect("bound identity passes");
        let other = GraphIdentity::new("principal-1", "ws-1", "proj-1", "root-2", "policy-v1").unwrap();
        let error = context.require(&other).unwrap_err();
        assert!(error.0.contains("binding differs"));
        // Identity matches, but its triple is not granted.
        let other_root = GraphIdentity::new("principal-1", "ws-1", "proj-2", "root-1", "policy-v1").unwrap();
        let other_context = GraphReadContext::new(other_root.clone(), &grants).expect("context");
        let error = other_context.require(&other_root).unwrap_err();
        assert!(error.0.contains("outside host grants"));
        // Empty grant components are refused at construction.
        assert!(GraphReadContext::new(identity, &[("ws-1".into(), String::new(), "root-1".into())]).is_err());
    }
}
