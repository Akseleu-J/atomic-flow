//! P11/E00/E07 kernel contracts: exact artifact manifests, consumer
//! verification steps and upgrade/rollback records. Rollback of a binary
//! never reverts spend or external effects — the record says so explicitly.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn sha256_file(path: &Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|error| format!("artifact read failed: {error}"))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    Ok(format!("{:x}", hasher.finalize()))
}

#[derive(Debug, Clone)]
pub struct ArtifactRecord {
    pub name: String,
    pub path: std::path::PathBuf,
    pub sha256: String,
}

#[derive(Debug, Clone)]
pub struct ReleaseManifest {
    pub version: String,
    pub source_commit: String,
    pub artifacts: Vec<ArtifactRecord>,
    pub crates: Vec<(String, String)>,
    pub built_at_unix: f64,
}

impl ReleaseManifest {
    /// Build the manifest from on-disk artifacts and the crate inventory.
    pub fn build(
        version: &str,
        source_commit: &str,
        artifacts: &[(String, &Path)],
        crates: &[(String, String)],
    ) -> Result<ReleaseManifest, String> {
        let mut records = Vec::new();
        for (name, path) in artifacts {
            records.push(ArtifactRecord {
                name: name.to_string(),
                path: path.to_path_buf(),
                sha256: sha256_file(path)?,
            });
        }
        if records.is_empty() {
            return Err("release manifest requires at least one artifact".into());
        }
        Ok(ReleaseManifest {
            version: version.to_string(),
            source_commit: source_commit.to_string(),
            artifacts: records,
            crates: crates.to_vec(),
            built_at_unix: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs_f64(),
        })
    }

    pub fn as_value(&self) -> Value {
        json!({
            "schema_version": 1,
            "version": self.version,
            "source_commit": self.source_commit,
            "artifacts": self.artifacts.iter().map(|artifact| json!({
                "name": artifact.name, "sha256": artifact.sha256,
            })).collect::<Vec<_>>(),
            "crate_inventory": self.crates.iter()
                .map(|(name, version)| json!({"name": name, "version": version}))
                .collect::<Vec<_>>(),
            "built_at_unix": self.built_at_unix,
            "provenance": "source commit + artifact digests; no signature is claimed here",
        })
    }

    /// Consumer verification: re-hash the installed artifacts and compare.
    /// Returns per-artifact results; a mismatch is a failed verification,
    /// never a warning.
    pub fn verify_consumer(&self, installed: &[(String, &Path)]) -> Value {
        let mut results = Vec::new();
        for artifact in &self.artifacts {
            let found = installed.iter().find(|(name, _)| name == &artifact.name);
            let result = match found {
                None => json!({"name": artifact.name, "status": "missing"}),
                Some((_, path)) => match sha256_file(path) {
                    Ok(digest) if digest == artifact.sha256 => {
                        json!({"name": artifact.name, "status": "verified"})
                    }
                    Ok(_) => json!({"name": artifact.name, "status": "digest_mismatch"}),
                    Err(error) => {
                        json!({"name": artifact.name, "status": "unreadable", "detail": error})
                    }
                },
            };
            results.push(result);
        }
        let verified = results
            .iter()
            .all(|result| result["status"] == json!("verified"));
        json!({"ok": verified, "artifacts": results})
    }
}

/// Upgrade/rollback record: the binary rolls back, the ledger does not.
#[derive(Debug, Clone)]
pub struct UpgradeRecord {
    pub from_version: String,
    pub to_version: String,
    pub migration_notes: Vec<String>,
}

impl UpgradeRecord {
    pub fn as_value(&self) -> Value {
        json!({
            "from_version": self.from_version,
            "to_version": self.to_version,
            "migration_notes": self.migration_notes,
            "rollback_semantics": "binary/manifest rollback only; spend, journal bytes and external effects are never reverted by a rollback",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_scratch(name: &str, bytes: &[u8]) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!("af-rel-{}-{}", af_entropy::token(), name));
        std::fs::write(&path, bytes).unwrap();
        path
    }

    #[test]
    fn manifest_hashes_and_verifies_consumer_artifacts() {
        let binary = write_scratch("af.exe", b"fake binary bytes");
        let manifest = ReleaseManifest::build(
            "0.1.0",
            "cf67e31",
            &[("af.exe".to_string(), &binary)],
            &[("af-cli".into(), "0.1.0".into())],
        )
        .unwrap();
        let document = manifest.as_value();
        assert_eq!(
            document["artifacts"][0]["sha256"],
            json!(sha256_file(&binary).unwrap())
        );
        // Same bytes verify; changed bytes fail hard.
        let same = manifest.verify_consumer(&[("af.exe".into(), &binary)]);
        assert_eq!(same["ok"], json!(true));
        std::fs::write(&binary, b"tampered").unwrap();
        let tampered = manifest.verify_consumer(&[("af.exe".into(), &binary)]);
        assert_eq!(tampered["ok"], json!(false));
        assert_eq!(tampered["artifacts"][0]["status"], json!("digest_mismatch"));
        let missing = manifest.verify_consumer(&[]);
        assert_eq!(missing["artifacts"][0]["status"], json!("missing"));
        let _ = std::fs::remove_file(&binary);
    }

    #[test]
    fn manifest_requires_artifacts_and_records_rollback_semantics() {
        assert!(ReleaseManifest::build("0.1.0", "src", &[], &[]).is_err());
        let record = UpgradeRecord {
            from_version: "0.1.0".into(),
            to_version: "0.2.0".into(),
            migration_notes: vec!["journal schema unchanged".into()],
        };
        assert!(record.as_value()["rollback_semantics"]
            .as_str()
            .unwrap()
            .contains("never reverted"));
    }
}
