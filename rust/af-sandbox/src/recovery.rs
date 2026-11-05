//! Bounded trusted-host byte recovery. Never invokes tools, providers or session/budget reconciliation.
use crate::admission::admit_rel;
use crate::registry::{
    ensure_no_links, persist_new, replace_durable, root_identity, sync_parent, SandboxRegistry,
    MAX_SOURCE_BYTES,
};
use crate::{manifest, sha1_hex, sha256_hex, SandboxError, SandboxResult};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Path, PathBuf};

fn fail(message: &str) -> SandboxError {
    SandboxError(message.into())
}
fn read(path: &Path, cap: usize) -> SandboxResult<Vec<u8>> {
    ensure_no_links(path)?;
    let meta = std::fs::symlink_metadata(path).map_err(|_| fail("recovery file unavailable"))?;
    if !meta.is_file() || meta.len() > cap as u64 {
        return Err(fail("recovery file type/size refused"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() != 1 {
            return Err(fail("recovery hard links refused"));
        }
    }
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .and_then(|f| f.take(cap as u64 + 1).read_to_end(&mut bytes))
        .map_err(|_| fail("recovery read failed"))?;
    if bytes.len() > cap {
        return Err(fail("recovery byte cap exceeded"));
    }
    Ok(bytes)
}
fn current(path: &Path) -> SandboxResult<Option<Vec<u8>>> {
    ensure_no_links(path)?;
    match std::fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        _ => read(path, MAX_SOURCE_BYTES).map(Some),
    }
}
fn keys(value: &Value, expected: &[&str]) -> SandboxResult<()> {
    let object = value
        .as_object()
        .ok_or_else(|| fail("recovery schema object required"))?;
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(fail("recovery schema fields differ"));
    }
    Ok(())
}
fn hash(value: &Value) -> SandboxResult<Option<String>> {
    if value.is_null() {
        return Ok(None);
    }
    let value = value
        .as_str()
        .ok_or_else(|| fail("recovery hash type invalid"))?;
    if value.len() != 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(fail("recovery hash invalid"));
    }
    Ok(Some(value.into()))
}
// Value parsing validates JSON syntax; this second bounded pass rejects recursively
// duplicated object keys, including equivalent escaped spellings.
fn strict_json(bytes: &[u8]) -> SandboxResult<Value> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| fail("recovery JSON corrupt"))?;
    fn whitespace(bytes: &[u8], cursor: &mut usize) {
        while bytes.get(*cursor).is_some_and(u8::is_ascii_whitespace) {
            *cursor += 1;
        }
    }
    fn string(bytes: &[u8], cursor: &mut usize) -> SandboxResult<String> {
        let start = *cursor;
        *cursor += 1;
        while *cursor < bytes.len() {
            match bytes[*cursor] {
                b'\\' => {
                    *cursor += 2;
                }
                b'"' => {
                    *cursor += 1;
                    return serde_json::from_slice(&bytes[start..*cursor])
                        .map_err(|_| fail("JSON string invalid"));
                }
                _ => *cursor += 1,
            }
        }
        Err(fail("JSON string truncated"))
    }
    fn walk(bytes: &[u8], cursor: &mut usize, depth: usize) -> SandboxResult<()> {
        if depth > 128 {
            return Err(fail("recovery JSON depth exceeded"));
        }
        whitespace(bytes, cursor);
        match bytes.get(*cursor) {
            Some(b'{') => {
                *cursor += 1;
                whitespace(bytes, cursor);
                let mut keys = BTreeSet::new();
                if bytes.get(*cursor) == Some(&b'}') {
                    *cursor += 1;
                    return Ok(());
                }
                loop {
                    whitespace(bytes, cursor);
                    let key = string(bytes, cursor)?;
                    if !keys.insert(key) {
                        return Err(fail("duplicate recovery JSON key"));
                    }
                    whitespace(bytes, cursor);
                    *cursor += 1;
                    walk(bytes, cursor, depth + 1)?;
                    whitespace(bytes, cursor);
                    if bytes.get(*cursor) == Some(&b'}') {
                        *cursor += 1;
                        break;
                    }
                    *cursor += 1;
                }
            }
            Some(b'[') => {
                *cursor += 1;
                whitespace(bytes, cursor);
                if bytes.get(*cursor) == Some(&b']') {
                    *cursor += 1;
                    return Ok(());
                }
                loop {
                    walk(bytes, cursor, depth + 1)?;
                    whitespace(bytes, cursor);
                    if bytes.get(*cursor) == Some(&b']') {
                        *cursor += 1;
                        break;
                    }
                    *cursor += 1;
                }
            }
            Some(b'"') => {
                string(bytes, cursor)?;
            }
            Some(_) => {
                while bytes
                    .get(*cursor)
                    .is_some_and(|c| !b",]} \t\r\n".contains(c))
                {
                    *cursor += 1;
                }
            }
            None => return Err(fail("recovery JSON truncated")),
        }
        Ok(())
    }
    walk(bytes, &mut 0, 0)?;
    Ok(value)
}
type RecoveryFile = (String, Option<Vec<u8>>, Option<Vec<u8>>);

struct Recovery {
    root: PathBuf,
    directory: PathBuf,
    files: Vec<RecoveryFile>,
    report: Value,
}
impl SandboxRegistry {
    fn recovery_load(&self, root_id: &str, id: &str) -> SandboxResult<Recovery> {
        if id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(fail("invalid changeset id"));
        }
        let grant = self.get(root_id)?;
        let root = PathBuf::from(
            grant["path"]
                .as_str()
                .ok_or_else(|| fail("invalid root grant"))?,
        );
        ensure_no_links(&root)?;
        if root.canonicalize().map_err(|_| fail("root unavailable"))? != root
            || json!(root_identity(&root)?) != grant["root_identity"]
        {
            return Err(fail("root identity changed"));
        }
        let directory = self.directory.join("changesets");
        let raw = read(&directory.join(format!("{id}-intent.json")), 2_000_000)?;
        let intent: Value = strict_json(&raw)?;
        keys(
            &intent,
            &[
                "schema_version",
                "changeset_id",
                "workspace_id",
                "root_id",
                "grant",
                "status",
                "baseline",
                "files",
                "cross_root_atomic",
            ],
        )?;
        if intent["schema_version"] != 1
            || intent["workspace_id"] != self.id
            || intent["root_id"] != root_id
            || intent["changeset_id"] != id
            || intent["grant"] != grant
            || intent["status"] != "prepared"
            || intent["cross_root_atomic"] != false
        {
            return Err(fail("intent identity/schema mismatch"));
        }
        let hold_raw = read(&self.effect_path(&root), 4096)?;
        let hold: Value = strict_json(&hold_raw)?;
        keys(
            &hold,
            &[
                "schema_version",
                "workspace_id",
                "changeset_id",
                "intent_sha256",
            ],
        )?;
        if hold["schema_version"] != 1
            || hold["workspace_id"] != self.id
            || hold["changeset_id"] != id
            || hold["intent_sha256"] != sha256_hex(&raw)
        {
            return Err(fail("hold identity differs"));
        }
        let baseline: BTreeMap<String, String> = serde_json::from_value(intent["baseline"].clone())
            .map_err(|_| fail("baseline invalid"))?;
        if baseline.len() > 20_000 {
            return Err(fail("baseline exceeds cap"));
        }
        for (path, digest) in &baseline {
            admit_rel(&root, path)?;
            if digest.len() != 40 || !digest.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(fail("baseline digest invalid"));
            }
        }
        let entries = intent["files"]
            .as_array()
            .ok_or_else(|| fail("intent files invalid"))?;
        if entries.is_empty() || entries.len() > 32 {
            return Err(fail("intent file count refused"));
        }
        let mut files: Vec<RecoveryFile> = Vec::new();
        let mut seen = BTreeSet::new();
        let mut states = Vec::new();
        let mut total = 0;
        let mut expected = baseline.clone();
        for (index, entry) in entries.iter().enumerate() {
            keys(entry, &["path", "backup", "before_sha256", "after_sha256"])?;
            let rel = entry["path"]
                .as_str()
                .ok_or_else(|| fail("intent path invalid"))?;
            if !seen.insert(rel.to_owned())
                || !grant["write_paths"]
                    .as_array()
                    .ok_or_else(|| fail("grant scope invalid"))?
                    .iter()
                    .any(|p| p.as_str() == Some(rel))
            {
                return Err(fail("duplicate or ungranted recovery path"));
            }
            let before_hash = hash(&entry["before_sha256"])?;
            let after_hash = hash(&entry["after_sha256"])?;
            let before = if let Some(digest) = &before_hash {
                let name = format!("before-{index}");
                if entry["backup"] != name {
                    return Err(fail("backup name differs"));
                }
                let bytes = read(
                    &directory.join(format!("{id}-backups")).join(name),
                    MAX_SOURCE_BYTES,
                )?;
                if sha256_hex(&bytes) != *digest
                    || baseline
                        .get(rel)
                        .is_some_and(|saved| saved != &sha1_hex(&bytes))
                {
                    return Err(fail("backup/baseline digest differs"));
                }
                Some(bytes)
            } else {
                if !entry["backup"].is_null() || baseline.contains_key(rel) {
                    return Err(fail("absent backup/baseline differs"));
                }
                None
            };
            let observed = current(&admit_rel(&root, rel)?)?;
            total += before.as_ref().map_or(0, Vec::len) + observed.as_ref().map_or(0, Vec::len);
            if total > 4_000_000 {
                return Err(fail("recovery aggregate byte cap"));
            }
            let observed_hash = observed.as_deref().map(sha256_hex);
            if observed_hash != before_hash && observed_hash != after_hash {
                return Err(fail("recovery conflict; preserve external bytes"));
            }
            // The global snapshot intentionally filters ignored/hidden files. Every
            // explicitly granted changed path is checked independently above.
            expected.remove(rel);
            states.push(json!({"path":rel,"before_sha256":before_hash,"after_sha256":after_hash,"current_sha256":observed_hash}));
            files.push((rel.into(), before, observed));
        }
        let terminal_path = directory.join(format!("{id}.json"));
        let terminal_hash = match current(&terminal_path)? {
            None => None,
            Some(bytes) => {
                if bytes.len() > 16384 {
                    return Err(fail("terminal exceeds cap"));
                }
                let terminal: Value = strict_json(&bytes)?;
                let status = terminal["status"]
                    .as_str()
                    .ok_or_else(|| fail("terminal status invalid"))?;
                if !["succeeded", "aborted_restored", "rollback_unknown"].contains(&status)
                    || terminal["changeset_id"] != id
                    || terminal["root_id"] != root_id
                {
                    return Err(fail("terminal identity invalid"));
                }
                let expected_paths: Vec<_> =
                    files.iter().map(|(path, _, _)| path.clone()).collect();
                if terminal["paths"] != json!(expected_paths) {
                    return Err(fail("terminal paths differ"));
                }
                if status == "succeeded" {
                    keys(
                        &terminal,
                        &[
                            "changeset_id",
                            "root_id",
                            "status",
                            "paths",
                            "cross_root_atomic",
                        ],
                    )?;
                    if terminal["cross_root_atomic"] != false {
                        return Err(fail("terminal scope invalid"));
                    }
                } else {
                    keys(
                        &terminal,
                        &["changeset_id", "root_id", "status", "paths", "error"],
                    )?;
                    if !terminal["error"].is_string() {
                        return Err(fail("terminal error invalid"));
                    }
                }
                Some(sha256_hex(&bytes))
            }
        };
        let actual = manifest(&root)?;
        let mut unchanged = actual.clone();
        for rel in &seen {
            unchanged.remove(rel);
        }
        if unchanged != expected {
            return Err(fail("recovery manifest conflict"));
        }
        let review = json!({"schema_version":1,"workspace_id":self.id,"root_id":root_id,"changeset_id":id,
            "grant":grant,"intent_sha256":sha256_hex(&raw),"hold_sha256":sha256_hex(&hold_raw),"terminal_sha256":terminal_hash,"current_manifest":actual,"files":states});
        let digest =
            sha256_hex(&serde_json::to_vec(&review).map_err(|_| fail("review encoding failed"))?);
        Ok(Recovery {
            root,
            directory,
            files,
            report: json!({"review":review,"preview_digest":digest,"operation":"restore_before_bytes","requirement_status":"not_assessed","provider_calls":0}),
        })
    }
    /// Read-only preview; arguments are registered IDs, never filesystem paths.
    pub fn inspect_recovery(&self, root_id: &str, changeset_id: &str) -> SandboxResult<Value> {
        Ok(self.recovery_load(root_id, changeset_id)?.report)
    }
    /// Trusted-host authority only. A model/HTTP boolean must never supply approval.
    pub fn recover(
        &self,
        root_id: &str,
        id: &str,
        preview_digest: &str,
        host_approved: bool,
    ) -> SandboxResult<Value> {
        if !host_approved {
            return Err(fail("recovery requires explicit host approval"));
        }
        let first = self.recovery_load(root_id, id)?;
        let _lease = self.root_effect_lease(&first.root)?;
        let recovery = self.recovery_load(root_id, id)?;
        if recovery.report["preview_digest"].as_str() != Some(preview_digest) {
            return Err(fail("recovery preview changed"));
        }
        let attempt = af_entropy::token();
        persist_new(
            &recovery
                .directory
                .join(format!("{id}-recovery-{attempt}-intent.json")),
            &serde_json::to_vec(&recovery.report).map_err(|_| fail("recovery encoding failed"))?,
        )?;
        for (index, (rel, before, observed)) in recovery.files.iter().enumerate() {
            // Repeat full validation before each effect; cooperating writers share the lease.
            let _ = self.recovery_load(root_id, id)?;
            let path = admit_rel(&recovery.root, rel)?;
            if current(&path)? != *observed {
                return Err(fail("recovery compare-and-swap conflict"));
            }
            if before != observed {
                match before {
                    Some(bytes) => {
                        let temporary = recovery
                            .root
                            .join(format!("{}.atomic-tmp", af_entropy::token()));
                        persist_new(&temporary, bytes)?;
                        if current(&path)? != *observed {
                            return Err(fail("recovery CAS conflict"));
                        }
                        replace_durable(&temporary, &path)?;
                    }
                    None => {
                        std::fs::remove_file(&path).map_err(|_| fail("recovery delete failed"))?;
                        sync_parent(&path)?;
                    }
                }
            }
            if current(&path)? != *before {
                return Err(fail("restoration verification failed"));
            }
            persist_new(
                &recovery
                    .directory
                    .join(format!("{id}-recovery-{attempt}-checkpoint-{index}.json")),
                &serde_json::to_vec(
                    &json!({"path":rel,"restored_sha256":before.as_deref().map(sha256_hex)}),
                )
                .map_err(|_| fail("checkpoint encoding failed"))?,
            )?;
            #[cfg(test)]
            if index == 0 {
                test_boundary("recovery-checkpoint");
            }
        }
        let _ = self.recovery_load(root_id, id)?;
        for (rel, before, _) in &recovery.files {
            if current(&admit_rel(&recovery.root, rel)?)? != *before {
                return Err(fail("final restoration differs"));
            }
        }
        let report = json!({"changeset_id":id,"status":"before_bytes_restored","requirement_status":"not_assessed","provider_calls":0,"preview_digest":preview_digest});
        persist_new(
            &recovery
                .directory
                .join(format!("{id}-recovery-{attempt}-complete.json")),
            &serde_json::to_vec(&report).map_err(|_| fail("report encoding failed"))?,
        )?;
        self.finish_root_effect(&recovery.root, id)?;
        Ok(report)
    }
}

#[cfg(test)]
pub(crate) fn test_boundary(phase: &str) {
    if std::env::var("AF_OWNED_RECOVERY_PHASE").ok().as_deref() == Some(phase) {
        let marker = std::env::var_os("AF_OWNED_RECOVERY_MARKER").expect("owned marker");
        std::fs::write(marker, phase).unwrap();
        loop {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::SandboxSession;
    use std::process::{Command, Stdio};
    struct Fixture(PathBuf);
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn fixture() -> (Fixture, SandboxRegistry, PathBuf) {
        let base = std::env::temp_dir().join(format!("af-recovery-{}", af_entropy::token()));
        let root = base.join("source").join("project");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("a.py"), b"value = 1\n").unwrap();
        let registry = SandboxRegistry::with_home("workspace", base.join("home")).unwrap();
        registry
            .grant("main", &root, &["a.py".into()], true)
            .unwrap();
        (Fixture(base), registry, root)
    }
    fn prepared(registry: &SandboxRegistry, root: &Path, after: bool) -> String {
        let canonical = root.canonicalize().unwrap();
        let root = canonical.as_path();
        let id = af_entropy::token();
        let directory = registry.directory.join("changesets");
        std::fs::create_dir_all(directory.join(format!("{id}-backups"))).unwrap();
        let before = std::fs::read(root.join("a.py")).unwrap();
        persist_new(&directory.join(format!("{id}-backups/before-0")), &before).unwrap();
        let intent = json!({"schema_version":1,"changeset_id":id,"workspace_id":registry.id,"root_id":"main","grant":registry.get("main").unwrap(),"status":"prepared","baseline":manifest(root).unwrap(),"files":[{"path":"a.py","backup":"before-0","before_sha256":sha256_hex(&before),"after_sha256":sha256_hex(b"value = 2\n")}],"cross_root_atomic":false});
        let raw = serde_json::to_vec(&intent).unwrap();
        persist_new(&directory.join(format!("{id}-intent.json")), &raw).unwrap();
        let _lease = registry.root_effect_lease(root).unwrap();
        registry
            .begin_root_effect(root, &id, &sha256_hex(&raw))
            .unwrap();
        if after {
            std::fs::write(root.join("a.py"), b"value = 2\n").unwrap();
        }
        id
    }
    #[test]
    fn approval_digest_conflict_and_corrupt_backup_fail_closed() {
        let (_fixture, registry, root) = fixture();
        let id = prepared(&registry, &root, true);
        let report = registry.inspect_recovery("main", &id).unwrap();
        let digest = report["preview_digest"].as_str().unwrap();
        assert!(registry.recover("main", &id, digest, false).is_err());
        assert!(registry.recover("main", &id, "stale", true).is_err());
        std::fs::write(root.join("a.py"), b"external edit\n").unwrap();
        assert!(registry.inspect_recovery("main", &id).is_err());
        assert_eq!(
            std::fs::read(root.join("a.py")).unwrap(),
            b"external edit\n"
        );
        std::fs::write(root.join("a.py"), b"value = 2\n").unwrap();
        std::fs::write(
            registry
                .directory
                .join(format!("changesets/{id}-backups/before-0")),
            b"corrupt",
        )
        .unwrap();
        assert!(registry.inspect_recovery("main", &id).is_err());
        assert!(registry.require_root_effects_clear("main").is_err());
    }
    #[test]
    fn recovery_restores_bytes_and_preserves_original_intent() {
        let (_fixture, registry, root) = fixture();
        let id = prepared(&registry, &root, true);
        let path = registry
            .directory
            .join(format!("changesets/{id}-intent.json"));
        let intent = std::fs::read(&path).unwrap();
        let report = registry.inspect_recovery("main", &id).unwrap();
        let result = registry
            .recover(
                "main",
                &id,
                report["preview_digest"].as_str().unwrap(),
                true,
            )
            .unwrap();
        assert_eq!(result["requirement_status"], "not_assessed");
        assert_eq!(std::fs::read(root.join("a.py")).unwrap(), b"value = 1\n");
        assert_eq!(std::fs::read(path).unwrap(), intent);
        assert!(registry.require_root_effects_clear("main").is_ok());
    }
    #[test]
    fn overlapping_grants_and_other_workspaces_cannot_bypass_hold() {
        let (fixture, registry, root) = fixture();
        let _id = prepared(&registry, &root, false);
        let nested = root.join("child");
        std::fs::create_dir(&nested).unwrap();
        let other = SandboxRegistry::with_home("other", fixture.0.join("home")).unwrap();
        other
            .grant("child", &nested, &["b.py".into()], true)
            .unwrap();
        let parent_registry =
            SandboxRegistry::with_home("parent_workspace", fixture.0.join("home")).unwrap();
        parent_registry
            .grant("parent", root.parent().unwrap(), &["b.py".into()], true)
            .unwrap();
        assert!(other.require_root_effects_clear("child").is_err());
        assert!(parent_registry
            .require_root_effects_clear("parent")
            .is_err());
    }
    #[test]
    fn intent_schema_foreign_grant_and_manifest_edits_refuse() {
        let (_fixture, registry, root) = fixture();
        let id = prepared(&registry, &root, false);
        std::fs::write(root.join("other.txt"), b"external").unwrap();
        assert!(registry.inspect_recovery("main", &id).is_err());
        std::fs::remove_file(root.join("other.txt")).unwrap();
        registry
            .grant("main", &root, &["a.py".into()], true)
            .unwrap();
        assert!(registry.inspect_recovery("main", &id).is_err());
        assert!(registry.inspect_recovery("main", "../outside").is_err());
    }
    #[test]
    fn malformed_intents_and_oversize_backups_are_refused() {
        for variant in 0..4 {
            let (_fixture, registry, root) = fixture();
            let id = prepared(&registry, &root, false);
            let path = registry
                .directory
                .join(format!("changesets/{id}-intent.json"));
            let mut intent: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
            match variant {
                0 => intent["files"][0]["path"] = json!("../outside.py"),
                1 => intent["files"][0]["backup"] = json!("../../outside"),
                2 => intent["extra_authority"] = json!(true),
                _ => {
                    std::fs::write(
                        registry
                            .directory
                            .join(format!("changesets/{id}-backups/before-0")),
                        vec![b'x'; MAX_SOURCE_BYTES + 1],
                    )
                    .unwrap();
                }
            }
            let raw = serde_json::to_vec(&intent).unwrap();
            std::fs::write(&path, &raw).unwrap();
            let hold = json!({"schema_version":1,"workspace_id":registry.id,"changeset_id":id,"intent_sha256":sha256_hex(&raw)});
            std::fs::write(
                registry.effect_path(&root.canonicalize().unwrap()),
                serde_json::to_vec(&hold).unwrap(),
            )
            .unwrap();
            assert!(registry.inspect_recovery("main", &id).is_err());
            assert_eq!(std::fs::read(root.join("a.py")).unwrap(), b"value = 1\n");
            assert!(registry.require_root_effects_clear("main").is_err());
        }
    }
    #[cfg(unix)]
    #[test]
    fn linked_backup_is_refused() {
        let (fixture, registry, root) = fixture();
        let id = prepared(&registry, &root, false);
        let backup = registry
            .directory
            .join(format!("changesets/{id}-backups/before-0"));
        std::fs::remove_file(&backup).unwrap();
        let outside = fixture.0.join("outside");
        std::fs::write(&outside, b"value = 1\n").unwrap();
        std::os::unix::fs::symlink(&outside, &backup).unwrap();
        assert!(registry.inspect_recovery("main", &id).is_err());
        assert!(registry.require_root_effects_clear("main").is_err());
    }
    #[test]
    fn duplicate_json_keys_including_nested_escaped_keys_are_refused() {
        assert!(strict_json(br#"{"schema_version":1,"schema_version":1}"#).is_err());
        assert!(strict_json(br#"{"files":[{"path":"a.py","pa\u0074h":"a.py"}]}"#).is_err());
        assert!(strict_json(br#"{"x":[{"a":1},{"a":2}],"escaped":"\\\"[]{}"}"#).is_ok());
    }
    #[test]
    fn explicitly_granted_ignored_paths_restore_independently_of_filtered_manifest() {
        let (_fixture, registry, root) = fixture();
        let paths = [".hidden.py", "package-lock.json"];
        for rel in paths {
            std::fs::write(root.join(rel), b"1\n").unwrap();
        }
        registry
            .grant(
                "main",
                &root,
                &paths.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
                true,
            )
            .unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        let mut changes = BTreeMap::new();
        for rel in paths {
            session.read("main", rel, 0, 1000, true).unwrap();
            changes.insert(rel.into(), Some("2\n".into()));
        }
        let preview = session.preview("main", &changes).unwrap();
        let result = session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .unwrap();
        let id = result["changeset_id"].as_str().unwrap();
        let intent = std::fs::read(
            registry
                .directory
                .join(format!("changesets/{id}-intent.json")),
        )
        .unwrap();
        registry
            .begin_root_effect(&root.canonicalize().unwrap(), id, &sha256_hex(&intent))
            .unwrap();
        let review = registry.inspect_recovery("main", id).unwrap();
        registry
            .recover("main", id, review["preview_digest"].as_str().unwrap(), true)
            .unwrap();
        for rel in paths {
            assert_eq!(std::fs::read(root.join(rel)).unwrap(), b"1\n");
        }
    }
    #[test]
    fn killed_multifile_recovery_restarts_from_fresh_review_without_replaying() {
        let (fixture, registry, root) = fixture();
        std::fs::write(root.join("b.py"), b"value = 1\n").unwrap();
        registry
            .grant("main", &root, &["a.py".into(), "b.py".into()], true)
            .unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        let mut changes = BTreeMap::new();
        for rel in ["a.py", "b.py"] {
            session.read("main", rel, 0, 1000, true).unwrap();
            changes.insert(rel.into(), Some("value = 2\n".into()));
        }
        let preview = session.preview("main", &changes).unwrap();
        let result = session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .unwrap();
        let id = result["changeset_id"].as_str().unwrap();
        let intent = std::fs::read(
            registry
                .directory
                .join(format!("changesets/{id}-intent.json")),
        )
        .unwrap();
        registry
            .begin_root_effect(&root.canonicalize().unwrap(), id, &sha256_hex(&intent))
            .unwrap();
        let initial = registry.inspect_recovery("main", id).unwrap();
        let marker = fixture.0.join("recovery-boundary");
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "recovery::tests::owned_child", "--nocapture"])
            .env("AF_OWNED_RECOVERY_HOME", &fixture.0)
            .env("AF_OWNED_RECOVERY_MARKER", &marker)
            .env("AF_OWNED_RECOVERY_PHASE", "recovery-checkpoint")
            .env("AF_OWNED_RECOVERY_CHANGESET", id)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        while !marker.exists() && std::time::Instant::now() < deadline {
            if let Some(status) = child.try_wait().unwrap() {
                panic!("recovery child exited: {status}");
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let reached = marker.exists();
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(reached);
        assert_eq!(std::fs::read(root.join("a.py")).unwrap(), b"value = 1\n");
        assert_eq!(std::fs::read(root.join("b.py")).unwrap(), b"value = 2\n");
        assert!(registry.require_root_effects_clear("main").is_err());
        assert!(registry
            .recover(
                "main",
                id,
                initial["preview_digest"].as_str().unwrap(),
                true
            )
            .is_err());
        let fresh = registry.inspect_recovery("main", id).unwrap();
        assert_ne!(fresh["preview_digest"], initial["preview_digest"]);
        registry
            .recover("main", id, fresh["preview_digest"].as_str().unwrap(), true)
            .unwrap();
        assert_eq!(std::fs::read(root.join("a.py")).unwrap(), b"value = 1\n");
        assert_eq!(std::fs::read(root.join("b.py")).unwrap(), b"value = 1\n");
        assert!(registry.require_root_effects_clear("main").is_ok());
        assert_eq!(
            std::fs::read(
                registry
                    .directory
                    .join(format!("changesets/{id}-intent.json"))
            )
            .unwrap(),
            intent
        );
        let checkpoints = std::fs::read_dir(registry.directory.join("changesets"))
            .unwrap()
            .filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().contains("checkpoint-0"))
            .count();
        assert_eq!(
            checkpoints, 2,
            "interrupted and fresh attempts retain distinct checkpoints"
        );
    }
    #[test]
    fn owned_child() {
        let Some(base) = std::env::var_os("AF_OWNED_RECOVERY_HOME") else {
            return;
        };
        let base = PathBuf::from(base);
        let registry = SandboxRegistry::with_home("workspace", base.join("home")).unwrap();
        if std::env::var("AF_OWNED_RECOVERY_PHASE").ok().as_deref() == Some("recovery-checkpoint") {
            let id = std::env::var("AF_OWNED_RECOVERY_CHANGESET").unwrap();
            let preview = registry.inspect_recovery("main", &id).unwrap();
            registry
                .recover(
                    "main",
                    &id,
                    preview["preview_digest"].as_str().unwrap(),
                    true,
                )
                .unwrap();
            panic!("recovery child should be killed");
        }
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "a.py", 0, 1000, true).unwrap();
        let preview = session
            .preview(
                "main",
                &BTreeMap::from([("a.py".into(), Some("value = 2\n".into()))]),
            )
            .unwrap();
        session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .unwrap();
        panic!("child should be killed while paused");
    }
    fn killed(phase: &str) {
        let (fixture, registry, root) = fixture();
        let marker = fixture.0.join("boundary");
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "recovery::tests::owned_child", "--nocapture"])
            .env("AF_OWNED_RECOVERY_HOME", &fixture.0)
            .env("AF_OWNED_RECOVERY_MARKER", &marker)
            .env("AF_OWNED_RECOVERY_PHASE", phase)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        while !marker.exists() && std::time::Instant::now() < deadline {
            if let Some(status) = child.try_wait().unwrap() {
                panic!("owned child exited before boundary: {status}");
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let reached = marker.exists();
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(reached, "boundary not reached");
        let directory = registry.directory.join("changesets");
        let name = std::fs::read_dir(&directory)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .find(|s| s.ends_with("-intent.json"))
            .unwrap();
        let id = name.strip_suffix("-intent.json").unwrap();
        assert_eq!(
            std::fs::read(root.join("a.py")).unwrap(),
            if phase == "prewrite" {
                b"value = 1\n"
            } else {
                b"value = 2\n"
            }
        );
        assert!(registry.require_root_effects_clear("main").is_err());
        let report = registry.inspect_recovery("main", id).unwrap();
        registry
            .recover("main", id, report["preview_digest"].as_str().unwrap(), true)
            .unwrap();
        assert_eq!(std::fs::read(root.join("a.py")).unwrap(), b"value = 1\n");
    }
    #[test]
    fn kill_at_prewrite_boundary_restores_without_replay() {
        killed("prewrite");
    }
    #[test]
    fn kill_at_postwrite_boundary_restores_without_replay() {
        killed("postwrite");
    }
}
