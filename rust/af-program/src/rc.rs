//! P12 kernel contract: the evidence-bound acceptance registry. A chapter
//! status moves only on a manifest with an artifact digest; the overall RC
//! decision is Accepted only when every required chapter is Accepted in a
//! stated scope. Renaming a failing gate is refused by construction.

use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq)]
pub enum ChapterStatus {
    /// Accepted in an explicitly stated scope (never "everything").
    Accepted {
        scope: String,
        manifest_sha256: String,
    },
    /// Open with the exact remainder named.
    Open { remainder: String },
}

#[derive(Debug, Clone)]
pub struct Chapter {
    pub id: String,
    pub required_for_rc: bool,
    pub status: ChapterStatus,
}

/// The RC decision registry.
#[derive(Debug, Clone, Default)]
pub struct RcRegistry {
    pub chapters: Vec<Chapter>,
}

impl RcRegistry {
    pub fn new() -> RcRegistry {
        RcRegistry::default()
    }

    pub fn push(&mut self, chapter: Chapter) {
        self.chapters.push(chapter);
    }

    pub fn accept(&mut self, id: &str, scope: &str, manifest_sha256: &str) -> Result<(), String> {
        if manifest_sha256.len() != 64 || !manifest_sha256.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("acceptance requires a 64-hex evidence manifest digest".into());
        }
        let Some(chapter) = self.chapters.iter_mut().find(|chapter| chapter.id == id) else {
            return Err(format!("unknown chapter {id}"));
        };
        if scope.trim().is_empty() {
            return Err("acceptance scope must be stated explicitly".into());
        }
        chapter.status = ChapterStatus::Accepted {
            scope: scope.to_string(),
            manifest_sha256: manifest_sha256.to_string(),
        };
        Ok(())
    }

    /// The P12 decision: required chapters must all be Accepted in scope.
    /// The decision names every open remainder instead of hiding it.
    pub fn rc_decision(&self) -> Value {
        let mut missing: Vec<Value> = Vec::new();
        // This is a declaration registry, not an evidence verifier. An empty
        // or ambiguous registry must never accidentally admit a release.
        if !self.chapters.iter().any(|chapter| chapter.required_for_rc) {
            missing.push(json!({"chapter": null, "remainder": "no required chapters configured"}));
        }
        let mut ids = std::collections::BTreeSet::new();
        for chapter in &self.chapters {
            if chapter.id.trim().is_empty() || !ids.insert(&chapter.id) {
                missing.push(json!({"chapter": chapter.id, "remainder": "empty or duplicate chapter identity"}));
            }
        }
        for chapter in &self.chapters {
            if chapter.required_for_rc {
                match &chapter.status {
                    ChapterStatus::Accepted {
                        scope,
                        manifest_sha256,
                    } => {
                        if scope.trim().is_empty()
                            || manifest_sha256.len() != 64
                            || !manifest_sha256.bytes().all(|b| b.is_ascii_hexdigit())
                        {
                            missing.push(json!({"chapter": chapter.id, "remainder": "invalid acceptance declaration"}));
                        }
                    }
                    ChapterStatus::Open { remainder } => {
                        missing.push(json!({"chapter": chapter.id, "remainder": remainder}));
                    }
                }
            }
        }
        json!({
            "schema_version": 1,
            "decision": if missing.is_empty() { "accepted_in_stated_scopes" } else { "not_ready" },
            "open_required_chapters": missing,
            "chapters": self.chapters.iter().map(|chapter| json!({
                "id": chapter.id,
                "required_for_rc": chapter.required_for_rc,
                "status": match &chapter.status {
                    ChapterStatus::Accepted { scope, manifest_sha256 } => json!({
                        "state": "accepted", "scope": scope, "manifest_sha256": manifest_sha256,
                    }),
                    ChapterStatus::Open { remainder } => json!({
                        "state": "open", "remainder": remainder,
                    }),
                },
            })).collect::<Vec<_>>(),
            "evidence_audited": false,
            "production_authorized": false,
            "note": "declared scoped statuses only; artifact verification and the complete P00-P14/E00-E08 production profile remain separate host gates",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registry() -> RcRegistry {
        let mut registry = RcRegistry::new();
        registry.push(Chapter {
            id: "P05".into(),
            required_for_rc: true,
            status: ChapterStatus::Open {
                remainder: "evidence follow-up".into(),
            },
        });
        registry.push(Chapter {
            id: "P09".into(),
            required_for_rc: true,
            status: ChapterStatus::Open {
                remainder: "crash matrix".into(),
            },
        });
        registry.push(Chapter {
            id: "P13".into(),
            required_for_rc: false,
            status: ChapterStatus::Open {
                remainder: "surfaces".into(),
            },
        });
        registry
    }

    #[test]
    fn decision_refuses_until_required_chapters_accept_in_scope() {
        let mut registry = registry();
        assert_eq!(registry.rc_decision()["decision"], json!("not_ready"));
        // Manifest digest is mandatory: renaming a gate cannot pass it.
        assert!(registry
            .accept("P05", "functional host core", "short")
            .is_err());
        registry
            .accept("P05", "functional host core", &"a".repeat(64))
            .expect("accept with manifest");
        assert_eq!(registry.rc_decision()["decision"], json!("not_ready"));
        registry
            .accept("P09", "crash matrix slice", &"b".repeat(64))
            .unwrap();
        let decision = registry.rc_decision();
        assert_eq!(decision["decision"], json!("accepted_in_stated_scopes"));
        // Optional chapters never block the RC gate.
        let open: Vec<&Value> = decision["chapters"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|chapter| chapter["status"]["state"] == json!("open"))
            .collect();
        assert_eq!(open.len(), 1);
        assert_eq!(open[0]["id"], json!("P13"));
    }

    #[test]
    fn empty_optional_duplicate_and_forged_registry_cannot_admit_rc() {
        assert_eq!(RcRegistry::new().rc_decision()["decision"], "not_ready");
        let mut r = RcRegistry::new();
        r.push(Chapter {
            id: "P00".into(),
            required_for_rc: false,
            status: ChapterStatus::Accepted {
                scope: "demo".into(),
                manifest_sha256: "a".repeat(64),
            },
        });
        assert_eq!(r.rc_decision()["decision"], "not_ready");
        r.chapters[0].required_for_rc = true;
        r.chapters[0].status = ChapterStatus::Accepted {
            scope: "".into(),
            manifest_sha256: "forged".into(),
        };
        assert_eq!(r.rc_decision()["decision"], "not_ready");
        r.accept("P00", "scope", &"a".repeat(64)).unwrap();
        r.push(r.chapters[0].clone());
        assert_eq!(r.rc_decision()["decision"], "not_ready");
    }

    #[test]
    fn unknown_chapters_and_empty_scopes_refuse() {
        let mut registry = registry();
        assert!(registry.accept("P99", "scope", &"a".repeat(64)).is_err());
        assert!(registry.accept("P05", "  ", &"a".repeat(64)).is_err());
    }
}
