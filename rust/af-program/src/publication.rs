//! P14/E02 kernel contract: the verifiable publication report. It binds
//! requirement → subtask steps → impact → checks → evidence into one
//! manifest. A missing section is an explicit omission — the manifest
//! refuses to be built from silence.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

/// One bound section of the publication report.
#[derive(Debug, Clone, PartialEq)]
pub enum Section {
    Requirement { text: String },
    Steps { records: Vec<Value> },
    Impact { affected_paths: Vec<String> },
    Checks { specs: Vec<Value> },
    Evidence { journal_digest: String },
}

#[derive(Clone)]
pub struct PublicationBuilder {
    requirement: Option<String>,
    steps: Option<Vec<Value>>,
    impact: Option<Vec<String>>,
    checks: Option<Vec<Value>>,
    evidence: Option<String>,
}

impl Default for PublicationBuilder {
    fn default() -> Self {
        Self::new()
    }
}

impl PublicationBuilder {
    pub fn new() -> PublicationBuilder {
        PublicationBuilder {
            requirement: None,
            steps: None,
            impact: None,
            checks: None,
            evidence: None,
        }
    }

    pub fn requirement(mut self, text: &str) -> Self {
        self.requirement = Some(text.to_string());
        self
    }

    /// Journey step records (phase, ok) from the bounded agent.
    pub fn steps(mut self, records: Vec<Value>) -> Self {
        self.steps = Some(records);
        self
    }

    /// Name-level blast radius from the graph reverse index.
    pub fn impact(mut self, affected_paths: Vec<String>) -> Self {
        self.impact = Some(affected_paths);
        self
    }

    /// Host check specifications bound to this change.
    pub fn checks(mut self, specs: Vec<Value>) -> Self {
        self.checks = Some(specs);
        self
    }

    pub fn evidence(mut self, journal_digest: String) -> Self {
        self.evidence = Some(journal_digest);
        self
    }

    /// Build the manifest; every omitted section is listed, none invented.
    pub fn build(self) -> Result<Value, String> {
        let mut omissions: Vec<String> = Vec::new();
        let requirement = self.requirement.ok_or("requirement section is missing")?;
        let steps = self.steps.ok_or("steps section is missing")?;
        let impact = self.impact.ok_or("impact section is missing")?;
        let checks = self.checks.ok_or("checks section is missing")?;
        let evidence = self.evidence.ok_or("evidence section is missing")?;
        let _ = &mut omissions;
        if steps.is_empty() {
            return Err("steps section is empty; a publication needs the journey record".into());
        }
        if checks.is_empty() {
            return Err(
                "checks section is empty; unknown impact must widen checks, not skip them".into(),
            );
        }
        if evidence.len() != 64 || !evidence.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("evidence must be a 64-hex journal digest".into());
        }
        let mut hasher = Sha256::new();
        hasher.update(requirement.as_bytes());
        let requirement_digest = format!("{:x}", hasher.finalize());
        let manifest = json!({
            "schema_version": 1,
            "requirement": {"text": requirement, "sha256": requirement_digest},
            "steps": steps,
            "impact": {
                "affected_paths": impact,
                "note": "name-level advisory radius; unresolved or dynamic relations are not proof of absence",
            },
            "checks": checks,
            "evidence_journal_sha256": evidence,
            "claim": "verifiable report, not a mathematical proof",
        });
        Ok(manifest)
    }
}

/// Compute the impact radius of a changed file's exported names over a clean
/// build's reverse index (advisory name-level radius, P04/P14 contract).
pub fn impact_of_exports(clean_build: &Value, exported_names: &[&str]) -> Vec<String> {
    let mut mentioned: BTreeMap<String, ()> = BTreeMap::new();
    for name in exported_names {
        for kind in ["imports", "calls"] {
            let index = &clean_build["reverse"][kind][*name];
            if let Some(paths) = index.as_array() {
                for path in paths {
                    if let Some(path) = path.as_str() {
                        mentioned.insert(path.to_string(), ());
                    }
                }
            }
        }
    }
    mentioned.into_keys().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn full_publication_manifest_builds_with_digest() {
        let manifest = PublicationBuilder::new()
            .requirement("helper returns 2")
            .steps(vec![json!({"phase": "verify", "ok": true})])
            .impact(vec!["app.py".into()])
            .checks(vec![
                json!({"runner": "pytest", "paths": ["tests/test_helper.py"]}),
            ])
            .evidence("a".repeat(64))
            .build()
            .unwrap();
        assert!(manifest["requirement"]["sha256"].as_str().unwrap().len() == 64);
        assert_eq!(
            manifest["claim"],
            json!("verifiable report, not a mathematical proof")
        );
        assert!(manifest["impact"]["note"]
            .as_str()
            .unwrap()
            .contains("not proof of absence"));
    }

    #[test]
    fn missing_sections_are_named_never_filled() {
        let base = PublicationBuilder::new().requirement("r");
        assert!(base
            .clone()
            .build()
            .unwrap_err()
            .contains("steps section is missing"));
        let base = base.steps(vec![json!({"phase": "act"})]);
        assert!(base
            .clone()
            .build()
            .unwrap_err()
            .contains("impact section is missing"));
        let base = base.impact(vec![]);
        assert!(base
            .clone()
            .build()
            .unwrap_err()
            .contains("checks section is missing"));
        let base = base.checks(vec![json!({"runner": "pytest"})]);
        assert!(base
            .clone()
            .build()
            .unwrap_err()
            .contains("evidence section is missing"));
        assert!(base
            .evidence("short".into())
            .build()
            .unwrap_err()
            .contains("64-hex"));
        let empty_steps = PublicationBuilder::new()
            .requirement("r")
            .steps(vec![])
            .impact(vec![])
            .checks(vec![])
            .evidence("a".repeat(64));
        assert!(empty_steps
            .build()
            .unwrap_err()
            .contains("steps section is empty"));
        // Empty checks refuse: unknown impact must widen checks, not skip them.
        let empty_checks = PublicationBuilder::new()
            .requirement("r")
            .steps(vec![json!({"phase": "act"})])
            .impact(vec![])
            .checks(vec![])
            .evidence("a".repeat(64));
        assert!(empty_checks
            .build()
            .unwrap_err()
            .contains("checks section is empty"));
    }

    #[test]
    fn impact_radius_names_mentioning_files() {
        let clean = json!({
            "reverse": {
                "imports": {"helper": ["app.py"]},
                "calls": {"helper": ["app.py", "other.py"]},
            }
        });
        let affected = impact_of_exports(&clean, &["helper"]);
        assert_eq!(affected, vec!["app.py".to_string(), "other.py".to_string()]);
        assert!(impact_of_exports(&clean, &["nothing"]).is_empty());
    }
}
