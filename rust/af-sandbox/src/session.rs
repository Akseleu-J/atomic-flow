//! Sandbox session: fresh read proofs and opaque single-root previews, never
//! restored from text (port of `workspace.py::WorkspaceSession`). Publication
//! is a single-root recoverable changeset; there is no cross-root atomicity.
//! The staged-directory protocol of `agents/changeset.py` is reduced here to
//! backup-based rollback with a journal record; the authority checks
//! (grant identity, full-read proof, manifest equality, per-file content CAS)
//! are preserved exactly.

use crate::admission::admit_rel;
use crate::redact::redact;
use crate::registry::{
    SandboxRegistry, MANIFEST_FILE_BYTES, MANIFEST_TOTAL_BYTES, MAX_SOURCE_BYTES, SCAN_ENTRIES,
    SCAN_SECONDS,
};
use crate::{glob_match, sha1_hex, sha256_hex, SandboxError, SandboxResult, SOURCE_SUFFIXES};
use rustpython_parser::Parse;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const MAX_READ_PROOFS: usize = 256;
const MAX_PREVIEWS: usize = 16;
const MAX_PREVIEW_PATHS: usize = 32;
const MAX_PREVIEW_TOTAL: usize = 1_000_000;

pub struct SandboxSession<'a> {
    registry: &'a SandboxRegistry,
    reads: BTreeMap<String, Vec<(usize, usize)>>,
    previews: BTreeMap<String, Preview>,
}

struct Preview {
    grant: Value,
    changes: BTreeMap<String, PreparedChange>,
    manifest: BTreeMap<String, String>,
}

struct PreparedChange {
    before: Option<String>,
    before_source_hash: Option<String>,
    after: Option<Vec<u8>>,
    after_hash: Option<String>,
}

fn read_key(root_id: &str, generation: &str, path: &str, current: Option<&str>) -> String {
    format!("{root_id}|{generation}|{path}|{}", current.unwrap_or(""))
}

fn scan_guard(checks: &mut usize, deadline: std::time::Instant) -> SandboxResult<()> {
    *checks += 1;
    if *checks > SCAN_ENTRIES {
        return Err(SandboxError("workspace scan entry limit exceeded".into()));
    }
    if std::time::Instant::now() >= deadline {
        return Err(SandboxError("workspace scan deadline exceeded".into()));
    }
    Ok(())
}

const ALWAYS_KEEP_DIR: &str = ".github";
const ALWAYS_KEEP_FILES: [&str; 3] = [".gitignore", ".coveragerc", ".python-version"];
const DEFAULT_IGNORE: [&str; 15] = [
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".atomic_flow",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.egg-info",
    "package-lock.json",
    "poetry.lock",
    "*.min.js",
];

/// Bounded traversal matching the ChangeSet snapshot filters: hidden entries
/// are skipped except an explicit allowlist; matched names are never yielded.
fn scan_files(
    root: &Path,
    checks: &mut usize,
    deadline: std::time::Instant,
) -> SandboxResult<Vec<PathBuf>> {
    let mut result = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(folder) = stack.pop() {
        scan_guard(checks, deadline)?;
        ensure_regular_dir(&folder)?;
        let entries = match std::fs::read_dir(&folder) {
            Ok(entries) => entries,
            Err(error) => return Err(SandboxError(format!("workspace scan failed: {error}"))),
        };
        for entry in entries {
            scan_guard(checks, deadline)?;
            let entry =
                entry.map_err(|error| SandboxError(format!("workspace scan failed: {error}")))?;
            let name = entry.file_name().to_string_lossy().to_string();
            if DEFAULT_IGNORE
                .iter()
                .any(|pattern| glob_match(pattern, &name))
            {
                continue;
            }
            let path = entry.path();
            let is_dir = entry
                .file_type()
                .map_err(|e| SandboxError(format!("{e}")))?
                .is_dir();
            let hidden = name.starts_with('.');
            if is_dir {
                if hidden && name != ALWAYS_KEEP_DIR {
                    continue;
                }
                stack.push(path);
            } else if !(hidden && !ALWAYS_KEEP_FILES.contains(&name.as_str())) {
                if name.ends_with(".pyc") || name.ends_with(".atomic-tmp") {
                    continue;
                }
                let meta = std::fs::symlink_metadata(&path)
                    .map_err(|error| SandboxError(format!("workspace scan failed: {error}")))?;
                if !meta.is_file() {
                    return Err(SandboxError(
                        "workspace snapshot requires regular source files".into(),
                    ));
                }
                result.push(path);
            }
        }
    }
    Ok(result)
}

fn ensure_regular_dir(path: &Path) -> SandboxResult<()> {
    let meta = std::fs::symlink_metadata(path)
        .map_err(|error| SandboxError(format!("workspace scan failed: {error}")))?;
    if meta.file_type().is_symlink() {
        return Err(SandboxError(
            "workspace paths may not contain links or junctions".into(),
        ));
    }
    Ok(())
}

/// sha1-per-file snapshot bounded by entry/byte caps (parity with the Python
/// `_manifest`, which is why this is sha1 and not the sha256 content hash).
pub fn manifest(root: &Path) -> SandboxResult<BTreeMap<String, String>> {
    let mut checks = 0usize;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(SCAN_SECONDS);
    let mut result = BTreeMap::new();
    let mut total: u64 = 0;
    for path in scan_files(root, &mut checks, deadline)? {
        scan_guard(&mut checks, deadline)?;
        let size = std::fs::metadata(&path)
            .map_err(|error| SandboxError(format!("manifest stat failed: {error}")))?
            .len();
        if size > MANIFEST_FILE_BYTES {
            return Err(SandboxError(
                "workspace snapshot file exceeds its byte cap".into(),
            ));
        }
        let bytes = std::fs::read(&path)
            .map_err(|error| SandboxError(format!("manifest read failed: {error}")))?;
        total += bytes.len() as u64;
        if bytes.len() as u64 > MANIFEST_FILE_BYTES || total > MANIFEST_TOTAL_BYTES {
            return Err(SandboxError(
                "workspace snapshot exceeds its byte cap".into(),
            ));
        }
        let rel = path
            .strip_prefix(root)
            .map_err(|_| SandboxError("manifest path escaped root".into()))?
            .to_string_lossy()
            .replace('\\', "/");
        result.insert(rel, sha1_hex(&bytes));
    }
    Ok(result)
}

impl<'a> SandboxSession<'a> {
    pub fn new(registry: &'a SandboxRegistry) -> SandboxResult<SandboxSession<'a>> {
        Ok(SandboxSession {
            registry,
            reads: BTreeMap::new(),
            previews: BTreeMap::new(),
        })
    }

    fn source(
        &self,
        root_id: &str,
        rel: &str,
    ) -> SandboxResult<(Value, PathBuf, Option<Vec<u8>>, Option<String>)> {
        let item = self.registry.get(root_id)?;
        let root = PathBuf::from(item["path"].as_str().unwrap_or_default());
        let path = admit_rel(&root, rel)?;
        let mut data: Option<Vec<u8>> = None;
        if path.exists() {
            let handle = std::fs::File::open(&path)
                .map_err(|error| SandboxError(format!("source open failed: {error}")))?;
            use std::io::Read;
            let mut limited = handle.take((MAX_SOURCE_BYTES + 1) as u64);
            let mut buffer = Vec::new();
            limited
                .read_to_end(&mut buffer)
                .map_err(|error| SandboxError(format!("source read failed: {error}")))?;
            data = Some(buffer);
        }
        if let Some(bytes) = &data {
            if bytes.contains(&0u8) || bytes.len() > MAX_SOURCE_BYTES {
                return Err(SandboxError(
                    "workspace source must be bounded UTF-8 text".into(),
                ));
            }
        }
        let current = data.as_ref().map(|bytes| sha256_hex(bytes));
        Ok((item, path, data, current))
    }

    fn rendered(&self, data: &Option<Vec<u8>>) -> SandboxResult<String> {
        match data {
            None => Ok(String::new()),
            Some(bytes) => {
                let text = std::str::from_utf8(bytes)
                    .map_err(|_| SandboxError("workspace source is not UTF-8 text".into()))?;
                let text = text.strip_prefix('\u{feff}').unwrap_or(text);
                Ok(redact(text))
            }
        }
    }

    /// Paged, redacted, root-qualified read with merged read proofs.
    pub fn read(
        &mut self,
        root_id: &str,
        path: &str,
        offset: usize,
        limit: usize,
        record_proof: bool,
    ) -> SandboxResult<Value> {
        if limit == 0 || limit > 12000 {
            return Err(SandboxError("invalid source page".into()));
        }
        let (item, _path, data, current) = self.source(root_id, path)?;
        let rendered = self.rendered(&data)?;
        let key = read_key(
            root_id,
            item["generation"].as_str().unwrap_or_default(),
            path,
            current.as_deref(),
        );
        if record_proof && !self.reads.contains_key(&key) && self.reads.len() >= MAX_READ_PROOFS {
            return Err(SandboxError("session read-evidence limit reached".into()));
        }
        let start = offset.min(rendered.len());
        let stop = (offset + limit).min(rendered.len());
        let mut ranges: Vec<(usize, usize)> = if record_proof {
            self.reads.get(&key).cloned().unwrap_or_default()
        } else {
            Vec::new()
        };
        ranges.push((start, stop));
        ranges.sort();
        let mut merged: Vec<(usize, usize)> = Vec::new();
        for (left, right) in ranges {
            match merged.last_mut() {
                Some(last) if left <= last.1 => last.1 = last.1.max(right),
                _ => merged.push((left, right)),
            }
        }
        // Recheck access and bytes after rendering, before handing out evidence.
        let (after, _, _, fresh) = self.source(root_id, path)?;
        if after != item || fresh != current {
            return Err(SandboxError("root grant/source changed during read".into()));
        }
        if record_proof {
            self.reads.insert(key, merged.clone());
        }
        let total_chars = rendered.chars().count();
        let read_complete =
            rendered.is_empty() || (merged.len() == 1 && merged[0] == (0, rendered.len()));
        Ok(json!({
            "workspace_id": self.registry.id, "root_id": root_id, "path": path,
            "source": rendered.chars().skip(start).take(stop - start).collect::<String>(),
            "exists": data.is_some(),
            "content_hash": current, "hash_algorithm": "sha256", "offset": start,
            "next_offset": if stop < rendered.len() { json!(stop) } else { Value::Null },
            "total_chars": total_chars,
            "read_complete": read_complete,
            "representation": "redacted", "data_only": true,
        }))
    }

    pub fn list_files(&mut self, root_id: &str, limit: usize) -> SandboxResult<Value> {
        if limit == 0 || limit > 1000 {
            return Err(SandboxError("invalid source-list limit".into()));
        }
        let item = self.registry.get(root_id)?;
        let root = PathBuf::from(item["path"].as_str().unwrap_or_default());
        let mut checks = 0usize;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(SCAN_SECONDS);
        let mut files: Vec<String> = Vec::new();
        let mut truncated = false;
        let mut reason: Option<String> = None;
        match scan_files(&root, &mut checks, deadline) {
            Ok(paths) => {
                for path in paths {
                    let rel = match path.strip_prefix(&root) {
                        Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
                        Err(_) => continue,
                    };
                    let dot = rel
                        .rfind('.')
                        .map(|i| rel[i..].to_ascii_lowercase())
                        .unwrap_or_default();
                    if !SOURCE_SUFFIXES.contains(&dot.as_str()) {
                        continue;
                    }
                    if admit_rel(&root, &rel).is_err() {
                        continue;
                    }
                    if files.len() >= limit {
                        truncated = true;
                        reason = Some("result_limit".into());
                        break;
                    }
                    files.push(rel);
                }
            }
            Err(error) => {
                truncated = true;
                reason = Some(error.0);
            }
        }
        if self.registry.get(root_id)? != item {
            return Err(SandboxError("root grant changed during enumeration".into()));
        }
        files.sort();
        Ok(json!({
            "workspace_id": self.registry.id, "root_id": root_id,
            "files": files, "truncated": truncated,
            "limit_reason": reason, "data_only": true,
        }))
    }

    /// Prepare a bounded, read-proof-backed change set; nothing is written yet.
    pub fn preview(
        &mut self,
        root_id: &str,
        changes: &BTreeMap<String, Option<String>>,
    ) -> SandboxResult<Value> {
        if changes.is_empty()
            || changes.len() > MAX_PREVIEW_PATHS
            || self.previews.len() >= MAX_PREVIEWS
        {
            return Err(SandboxError(
                "preview requires 1-32 paths and at most 16 pending previews".into(),
            ));
        }
        let item = self.registry.get(root_id)?;
        self.registry.require_root_effects_clear(root_id)?;
        let write_paths: BTreeSet<&str> = item["write_paths"]
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<&str>>()
            })
            .unwrap_or_default()
            .into_iter()
            .collect();
        let mut prepared: BTreeMap<String, PreparedChange> = BTreeMap::new();
        let mut total = 0usize;
        for (rel, text) in changes {
            if !write_paths.contains(rel.as_str()) {
                return Err(SandboxError(
                    "path is outside the exact approved root write scope".into(),
                ));
            }
            let (before_item, _, data, current) = self.source(root_id, rel)?;
            if before_item != item {
                return Err(SandboxError("root grant changed".into()));
            }
            let rendered = self.rendered(&data)?;
            let key = read_key(
                root_id,
                item["generation"].as_str().unwrap_or_default(),
                rel,
                current.as_deref(),
            );
            let full = vec![(0usize, rendered.len())];
            if self.reads.get(&key) != Some(&full) {
                return Err(SandboxError(
                    "read the complete current root-qualified source before preview".into(),
                ));
            }
            let after: Option<Vec<u8>> = match text {
                Some(text) => Some(text.as_bytes().to_vec()),
                None => None,
            };
            if let Some(bytes) = &after {
                total += bytes.len();
                if total > MAX_PREVIEW_TOTAL {
                    return Err(SandboxError("preview exceeds its content cap".into()));
                }
                if rel.ends_with(".py") || rel.ends_with(".pyi") {
                    let source = std::str::from_utf8(bytes)
                        .map_err(|_| SandboxError("replacement must be UTF-8 text".into()))?;
                    if rustpython_parser::ast::Suite::parse(source, "<preview>").is_err() {
                        return Err(SandboxError("replacement Python does not compile".into()));
                    }
                }
            }
            prepared.insert(
                rel.clone(),
                PreparedChange {
                    before: current,
                    before_source_hash: data.as_ref().map(|bytes| sha256_hex(bytes)),
                    after_hash: after.as_ref().map(|bytes| sha256_hex(bytes)),
                    after,
                },
            );
        }
        let root = PathBuf::from(item["path"].as_str().unwrap_or_default());
        let manifest = manifest(&root)?;
        if self.registry.get(root_id)? != item {
            return Err(SandboxError("root grant changed during preview".into()));
        }
        let token = af_entropy::token64();
        let change_report: Vec<Value> = prepared
            .iter()
            .map(|(rel, change)| {
                let replacement = change.after.as_ref().map(|bytes| {
                    let text = String::from_utf8_lossy(bytes);
                    let shown: String = text.chars().take(12000).collect();
                    json!({"preview": redact(&shown), "truncated": text.chars().count() > 12000})
                });
                json!({
                    "path": rel,
                    "before_hash": change.before,
                    "after_hash": change.after_hash,
                    "replacement": replacement,
                })
            })
            .collect();
        self.previews.insert(
            token.clone(),
            Preview {
                grant: item,
                changes: prepared,
                manifest,
            },
        );
        Ok(json!({
            "workspace_id": self.registry.id, "root_id": root_id, "preview_token": token,
            "changes": change_report,
            "publication": "single-root recoverable changeset; no cross-root atomicity",
        }))
    }

    /// Publish the previewed change set; `approved` must be an explicit host
    /// decision bound to this token. On any failure, already-applied files are
    /// restored from backups and the journal records the abort.
    pub fn apply(&mut self, preview_token: &str, approved: bool) -> SandboxResult<Value> {
        if approved != true {
            return Err(SandboxError(
                "publication needs explicit host approval of the preview".into(),
            ));
        }
        let preview = self
            .previews
            .remove(preview_token)
            .ok_or_else(|| SandboxError("unknown, consumed or foreign preview token".into()))?;
        let item = preview.grant.clone();
        let root_id = item["root_id"].as_str().unwrap_or_default().to_string();
        let root = PathBuf::from(item["path"].as_str().unwrap_or_default());

        let authority = || -> SandboxResult<()> {
            if self.registry.get(&root_id)? != item {
                return Err(SandboxError(
                    "root grant was revoked or changed after preview".into(),
                ));
            }
            Ok(())
        };
        authority()?;
        for (rel, change) in &preview.changes {
            let (_, _, _, current) = self.source(&root_id, rel)?;
            if current != change.before {
                return Err(SandboxError("root source changed after preview".into()));
            }
        }
        if manifest(&root)? != preview.manifest {
            return Err(SandboxError(
                "root source manifest changed after preview".into(),
            ));
        }
        let _lease = self.registry.root_effect_lease(&root)?;
        self.registry.require_path_effects_clear(&root)?;
        authority()?;
        if manifest(&root)? != preview.manifest {
            return Err(SandboxError(
                "source changed before publication lease".into(),
            ));
        }
        let changeset_id = af_entropy::token();
        let journal_dir = self.registry.directory.join("changesets");
        crate::registry::ensure_no_links(&journal_dir)?;
        std::fs::create_dir_all(&journal_dir)
            .map_err(|_| SandboxError("changeset store unavailable".into()))?;
        let backup_dir = journal_dir.join(format!("{changeset_id}-backups"));
        std::fs::create_dir(&backup_dir)
            .map_err(|_| SandboxError("backup store creation failed".into()))?;
        let mut originals = BTreeMap::<String, Option<Vec<u8>>>::new();
        let mut backups = Vec::new();
        for (index, (rel, change)) in preview.changes.iter().enumerate() {
            let path = admit_rel(&root, rel)?;
            let original = if path.exists() {
                Some(std::fs::read(&path).map_err(|_| SandboxError("backup read failed".into()))?)
            } else {
                None
            };
            if original.as_deref().map(sha256_hex) != change.before_source_hash {
                return Err(SandboxError("source changed before durable backup".into()));
            }
            let backup = format!("before-{index}");
            if let Some(raw) = &original {
                crate::registry::persist_new(&backup_dir.join(&backup), raw)?;
            }
            backups.push(json!({"path":rel,"backup":if original.is_some(){Some(backup)}else{None},
                "before_sha256":change.before_source_hash,"after_sha256":change.after.as_deref().map(sha256_hex)}));
            originals.insert(rel.clone(), original);
        }
        let intent = json!({"schema_version":1,"changeset_id":changeset_id,"workspace_id":self.registry.id,
            "root_id":root_id,"grant":item,"status":"prepared","baseline":preview.manifest,"files":backups,
            "cross_root_atomic":false});
        let raw = serde_json::to_vec(&intent)
            .map_err(|_| SandboxError("intent encoding failed".into()))?;
        crate::registry::persist_new(
            &journal_dir.join(format!("{changeset_id}-intent.json")),
            &raw,
        )?;
        self.registry
            .begin_root_effect(&root, &changeset_id, &sha256_hex(&raw))?;
        #[cfg(test)]
        crate::recovery::test_boundary("prewrite");
        let mut applied = Vec::<String>::new();
        let result = (|| -> SandboxResult<()> {
            for (rel, change) in &preview.changes {
                authority()?;
                let path = admit_rel(&root, rel)?;
                let observed = if path.exists() {
                    Some(
                        std::fs::read(&path)
                            .map_err(|_| SandboxError("source read failed".into()))?,
                    )
                } else {
                    None
                };
                if observed != originals[rel] {
                    return Err(SandboxError("source changed during publication".into()));
                }
                // Record possible mutation before invoking the filesystem effect.
                applied.push(rel.clone());
                match &change.after {
                    Some(bytes) => {
                        let temp = root.join(format!("{}.atomic-tmp", af_entropy::token()));
                        crate::registry::persist_new(&temp, bytes)?;
                        crate::registry::replace_durable(&temp, &path)?;
                    }
                    None => {
                        if path.exists() {
                            std::fs::remove_file(&path)
                                .map_err(|_| SandboxError("source deletion failed".into()))?;
                            crate::registry::sync_parent(&path)?;
                        }
                    }
                }
                #[cfg(test)]
                crate::recovery::test_boundary("postwrite");
                let actual = if path.exists() {
                    Some(
                        std::fs::read(&path)
                            .map_err(|_| SandboxError("publication recheck failed".into()))?,
                    )
                } else {
                    None
                };
                if actual != change.after {
                    return Err(SandboxError("published source differs".into()));
                }
            }
            authority()?;
            Ok(())
        })();
        if let Err(error) = result {
            let mut restored = true;
            for rel in applied.iter().rev() {
                let restore = (|| -> SandboxResult<()> {
                    let path = admit_rel(&root, rel)?;
                    let current = if path.exists() {
                        Some(
                            std::fs::read(&path)
                                .map_err(|_| SandboxError("rollback read failed".into()))?,
                        )
                    } else {
                        None
                    };
                    if current != originals[rel] && current != preview.changes[rel].after {
                        return Err(SandboxError(
                            "rollback conflict; preserve external bytes".into(),
                        ));
                    }
                    if current != originals[rel] {
                        match &originals[rel] {
                            Some(bytes) => {
                                let temp = root.join(format!("{}.atomic-tmp", af_entropy::token()));
                                crate::registry::persist_new(&temp, bytes)?;
                                crate::registry::replace_durable(&temp, &path)?;
                            }
                            None => {
                                std::fs::remove_file(&path)
                                    .map_err(|_| SandboxError("rollback delete failed".into()))?;
                                crate::registry::sync_parent(&path)?;
                            }
                        }
                    }
                    let final_bytes = if path.exists() {
                        Some(
                            std::fs::read(&path)
                                .map_err(|_| SandboxError("rollback recheck failed".into()))?,
                        )
                    } else {
                        None
                    };
                    if final_bytes != originals[rel] {
                        return Err(SandboxError("rollback not verified".into()));
                    }
                    Ok(())
                })();
                restored &= restore.is_ok();
            }
            let status = if restored {
                "aborted_restored"
            } else {
                "rollback_unknown"
            };
            let terminal=serde_json::to_vec(&json!({"changeset_id":changeset_id,"root_id":root_id,"status":status,
                "paths":preview.changes.keys().collect::<Vec<_>>(),"error":"publication failed; inspect retained intent and backups"}))
                .map_err(|_| SandboxError("terminal encoding failed".into()))?;
            crate::registry::persist_new(
                &journal_dir.join(format!("{changeset_id}.json")),
                &terminal,
            )?;
            // Even a restored failure requires explicit inspection. Never silently
            // remove the cross-session hold after a possible write error.
            return Err(SandboxError(format!(
                "{}; durable publication hold retained ({status})",
                error.0
            )));
        }
        let terminal = serde_json::to_vec(
            &json!({"changeset_id":changeset_id,"root_id":root_id,"status":"succeeded",
            "paths":preview.changes.keys().collect::<Vec<_>>(),"cross_root_atomic":false}),
        )
        .map_err(|_| SandboxError("terminal encoding failed".into()))?;
        crate::registry::persist_new(&journal_dir.join(format!("{changeset_id}.json")), &terminal)?;
        self.registry.finish_root_effect(&root, &changeset_id)?;
        Ok(json!({
            "workspace_id": self.registry.id, "root_id": root_id,
            "changeset_id": changeset_id, "status": "succeeded",
            "paths": preview.changes.keys().cloned().collect::<Vec<_>>(),
            "cross_root_atomic": false,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::SandboxRegistry;

    /// Unique scratch directory pair (home + source root) per test, replacing
    /// the tempfile dev-dependency: this machine's toolchain chokes on the
    /// raw-dylib crates it pulls in.
    struct TempPair {
        _home: std::path::PathBuf,
        root: std::path::PathBuf,
    }

    impl Drop for TempPair {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self._home);
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn temp_pair() -> TempPair {
        let base = std::env::temp_dir();
        let home = base.join(format!("af-sb-home-{}", af_entropy::token()));
        let root = base.join(format!("af-sb-root-{}", af_entropy::token()));
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&root).unwrap();
        TempPair { _home: home, root }
    }

    fn setup() -> (TempPair, SandboxRegistry) {
        let pair = temp_pair();
        let registry = SandboxRegistry::with_home("default", pair._home.clone()).unwrap();
        let root_path = pair.root.clone();
        registry
            .grant("main", &root_path, &["src/lib.py".to_string()], true)
            .unwrap();
        std::fs::create_dir_all(root_path.join("src")).unwrap();
        std::fs::write(
            root_path.join("src/lib.py"),
            b"def helper():\n    return 1\n",
        )
        .unwrap();
        (pair, registry)
    }

    #[test]
    fn read_requires_and_records_proofs() {
        let (_pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        let page = session.read("main", "src/lib.py", 0, 5, true).unwrap();
        assert_eq!(page["read_complete"], json!(false));
        assert_eq!(page["next_offset"], json!(5));
        // Preview without the complete proof is refused.
        let changes = BTreeMap::from([(
            "src/lib.py".to_string(),
            Some("def helper():\n    return 2\n".to_string()),
        )]);
        assert!(session.preview("main", &changes).is_err());
        // Full read then preview works.
        let full = session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        assert_eq!(full["read_complete"], json!(true));
        assert!(session.preview("main", &changes).is_ok());
    }

    #[test]
    fn failed_intent_storage_prevents_all_source_writes() {
        let (pair, registry) = setup();
        let original = std::fs::read(pair.root.join("src/lib.py")).unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let preview = session
            .preview(
                "main",
                &BTreeMap::from([("src/lib.py".into(), Some("VALUE = 2\n".into()))]),
            )
            .unwrap();
        std::fs::write(
            registry.directory.join("changesets"),
            b"block directory creation",
        )
        .unwrap();
        assert!(session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .is_err());
        assert_eq!(
            std::fs::read(pair.root.join("src/lib.py")).unwrap(),
            original
        );
    }

    #[test]
    fn publication_hold_blocks_another_session_and_preserves_backups() {
        let (pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let preview = session
            .preview(
                "main",
                &BTreeMap::from([("src/lib.py".into(), Some("VALUE = 2\n".into()))]),
            )
            .unwrap();
        let canonical = PathBuf::from(registry.get("main").unwrap()["path"].as_str().unwrap());
        {
            let _lease = registry.root_effect_lease(&canonical).unwrap();
            registry
                .begin_root_effect(&canonical, "interrupted-change", &"a".repeat(64))
                .unwrap();
        }
        assert!(registry.require_root_effects_clear("main").is_err());
        assert!(session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .is_err());
        assert!(std::fs::read_to_string(pair.root.join("src/lib.py"))
            .unwrap()
            .contains("return 1"));
        let second = SandboxRegistry::with_home("second", pair._home.clone()).unwrap();
        second
            .grant("other", &pair.root, &["src/lib.py".into()], true)
            .unwrap();
        assert!(second.require_root_effects_clear("other").is_err());
    }

    #[test]
    fn successful_publication_retains_durable_intent_and_exact_before_bytes() {
        let (pair, registry) = setup();
        let before = std::fs::read(pair.root.join("src/lib.py")).unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let preview = session
            .preview(
                "main",
                &BTreeMap::from([("src/lib.py".into(), Some("VALUE = 2\n".into()))]),
            )
            .unwrap();
        let report = session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .unwrap();
        let id = report["changeset_id"].as_str().unwrap();
        let directory = registry.directory.join("changesets");
        let intent: Value = serde_json::from_slice(
            &std::fs::read(directory.join(format!("{id}-intent.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(intent["status"], "prepared");
        assert_eq!(
            std::fs::read(directory.join(format!("{id}-backups/before-0"))).unwrap(),
            before
        );
        assert!(registry.require_root_effects_clear("main").is_ok());
    }

    #[test]
    fn partial_publication_restores_bytes_and_keeps_cross_session_hold() {
        let (pair, registry) = setup();
        registry
            .grant(
                "main",
                &pair.root,
                &["src/lib.py".into(), "zz-missing/new.py".into()],
                true,
            )
            .unwrap();
        let before = std::fs::read(pair.root.join("src/lib.py")).unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        for path in ["src/lib.py", "zz-missing/new.py"] {
            session.read("main", path, 0, 12000, true).unwrap();
        }
        let preview = session
            .preview(
                "main",
                &BTreeMap::from([
                    ("src/lib.py".into(), Some("VALUE = 2\n".into())),
                    ("zz-missing/new.py".into(), Some("VALUE = 3\n".into())),
                ]),
            )
            .unwrap();
        let error = session
            .apply(preview["preview_token"].as_str().unwrap(), true)
            .unwrap_err();
        assert!(error.0.contains("hold retained"));
        assert_eq!(std::fs::read(pair.root.join("src/lib.py")).unwrap(), before);
        assert!(!pair.root.join("zz-missing/new.py").exists());
        assert!(registry.require_root_effects_clear("main").is_err());
    }

    #[test]
    fn secrets_are_redacted_in_reads() {
        let (pair, registry) = setup();
        std::fs::write(
            pair.root.join("src/lib.py"),
            b"token = 'ghq_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n",
        )
        .unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        let page = session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let source = page["source"].as_str().unwrap();
        assert!(source.contains("[REDACTED]"));
        assert!(!source.contains("ghq_"));
    }

    #[test]
    fn preview_outside_write_scope_refused() {
        let (_pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        session
            .read("main", "src/other.py", 0, 12000, true)
            .unwrap();
        let changes = BTreeMap::from([("src/other.py".to_string(), Some("x = 1\n".to_string()))]);
        let error = session.preview("main", &changes).unwrap_err();
        assert!(error
            .0
            .contains("outside the exact approved root write scope"));
    }

    #[test]
    fn broken_python_replacement_refused() {
        let (_pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let changes =
            BTreeMap::from([("src/lib.py".to_string(), Some("def broken(:\n".to_string()))]);
        let error = session.preview("main", &changes).unwrap_err();
        assert!(error.0.contains("does not compile"));
    }

    #[test]
    fn apply_requires_approval_and_publishes() {
        let (pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let changes = BTreeMap::from([(
            "src/lib.py".to_string(),
            Some("def helper():\n    return 2\n".to_string()),
        )]);
        let preview = session.preview("main", &changes).unwrap();
        let token = preview["preview_token"].as_str().unwrap().to_string();
        assert!(session.apply(&token, false).is_err());
        let report = session.apply(&token, true).unwrap();
        assert_eq!(report["status"], "succeeded");
        let content = std::fs::read_to_string(pair.root.join("src/lib.py")).unwrap();
        assert!(content.contains("return 2"));
        // Token is consumed.
        assert!(session.apply(&token, true).is_err());
    }

    #[test]
    fn apply_refuses_when_source_changed_after_preview() {
        let (pair, registry) = setup();
        let mut session = SandboxSession::new(&registry).unwrap();
        session.read("main", "src/lib.py", 0, 12000, true).unwrap();
        let changes = BTreeMap::from([("src/lib.py".to_string(), Some("x = 1\n".to_string()))]);
        let preview = session.preview("main", &changes).unwrap();
        let token = preview["preview_token"].as_str().unwrap().to_string();
        // Out-of-band edit between preview and apply.
        std::fs::write(
            pair.root.join("src/lib.py"),
            b"def helper():\n    return 9\n",
        )
        .unwrap();
        let error = session.apply(&token, true).unwrap_err();
        assert!(error.0.contains("changed after preview"));
    }

    #[test]
    fn list_files_is_bounded_and_suffix_filtered() {
        let (pair, registry) = setup();
        std::fs::write(pair.root.join("notes.txt"), b"skip me").unwrap();
        std::fs::write(pair.root.join("main.py"), b"def main(): pass\n").unwrap();
        let mut session = SandboxSession::new(&registry).unwrap();
        let listing = session.list_files("main", 200).unwrap();
        let files = listing["files"].as_array().unwrap();
        assert!(files.contains(&json!("main.py")));
        assert!(files.contains(&json!("src/lib.py")));
        assert!(!files.contains(&json!("notes.txt")));
    }
}
