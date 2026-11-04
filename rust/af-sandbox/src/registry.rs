//! Explicit host-approved root registry (port of `workspace.py`
//! `WorkspaceRegistry`). Trusted-host registry; source/config/model content
//! never creates grants. Every mutation requires `approved: true` from the
//! host — never from repository text or model output.

use crate::{SandboxError, SandboxResult};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const SCAN_ENTRIES: usize = 20_000;
pub const SCAN_SECONDS: u64 = 5;
pub const MANIFEST_FILE_BYTES: u64 = 8 * 1024 * 1024;
pub const MANIFEST_TOTAL_BYTES: u64 = 32 * 1024 * 1024;
pub const MAX_SOURCE_BYTES: usize = 1_000_000;
pub const MAX_ROOTS: usize = 8;
pub const MAX_WRITE_PATHS: usize = 64;

/// Trusted-host application state directory: ATOMIC_FLOW_HOME or ~/.atomic_flow.
pub fn user_dir() -> PathBuf {
    if let Ok(home) = std::env::var("ATOMIC_FLOW_HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string());
    Path::new(&home).join(".atomic_flow")
}

/// `^[A-Za-z][A-Za-z0-9_-]{0,47}$`
pub fn valid_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    let rest = chars.count();
    rest <= 47
        && value[1..]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// Workspace paths may not contain links or junctions anywhere in the chain.
/// Paths that do not exist yet are allowed (write-scope admission happens
/// before the file is created); only existing links are refused.
pub fn ensure_no_links(path: &Path) -> SandboxResult<()> {
    let mut current = Some(path);
    while let Some(item) = current {
        match std::fs::symlink_metadata(item) {
            Ok(meta) => {
                #[cfg(windows)]
                let reparse = {
                    use std::os::windows::fs::MetadataExt;
                    meta.file_attributes() & 0x400 != 0
                };
                #[cfg(not(windows))]
                let reparse = false;
                if meta.file_type().is_symlink() || reparse {
                    return Err(SandboxError(
                        "workspace paths may not contain links or junctions".into(),
                    ));
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(SandboxError(format!(
                    "workspace path check failed: {error}"
                )))
            }
        }
        current = item.parent();
    }
    Ok(())
}

/// Host-visible root identity. Python's draft used `[st_dev, st_ino]`; the
/// stable Rust Windows API does not expose volume/file-index handles, so the
/// Windows fallback is `[creation_time, hash64(canonical path)]` — enough to
/// detect a root replaced under the same path. Unix keeps `[dev, ino]`.
pub fn root_identity(path: &Path) -> SandboxResult<[u64; 2]> {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        let meta = std::fs::metadata(path)
            .map_err(|error| SandboxError(format!("root identity unavailable: {error}")))?;
        let mut hash: u64 = 0x9E3779B97F4A7C15;
        for byte in path.to_string_lossy().as_bytes() {
            hash = hash.rotate_left(7) ^ (*byte as u64).wrapping_mul(0x100000001B3);
        }
        Ok([meta.creation_time(), hash])
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let meta = std::fs::metadata(path)
            .map_err(|error| SandboxError(format!("root identity unavailable: {error}")))?;
        Ok([meta.dev(), meta.ino()])
    }
    #[cfg(not(any(windows, unix)))]
    {
        Ok([0, 0])
    }
}

/// Exclusive lease on a marker lock file (persistent, never unlinked) — the
/// `locking.py::exclusive_file` contract, provided by af-entropy.
pub use af_entropy::FileLease as Lease;

/// Write-through on Windows and parent-directory fsync on Unix. Windows does
/// not pretend to provide Unix directory fsync: committed renames use the
/// platform write-through flag instead, and uncleared holds remain conservative.
pub(crate) fn sync_parent(path: &Path) -> SandboxResult<()> {
    #[cfg(unix)]
    if let Some(parent) = path.parent() {
        std::fs::File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| SandboxError("directory persistence acknowledgement failed".into()))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

pub(crate) fn persist_new(path: &Path, bytes: &[u8]) -> SandboxResult<()> {
    ensure_no_links(path)?;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x80000000); // FILE_FLAG_WRITE_THROUGH
    }
    let mut file = options
        .open(path)
        .map_err(|_| SandboxError("durable record creation failed".into()))?;
    file.write_all(bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| SandboxError("durable record persistence failed".into()))?;
    sync_parent(path)
}

pub(crate) fn replace_durable(from: &Path, to: &Path) -> SandboxResult<()> {
    ensure_no_links(from)?;
    ensure_no_links(to)?;
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        extern "system" {
            fn MoveFileExW(existing: *const u16, new: *const u16, flags: u32) -> i32;
        }
        let from: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
        let to: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
        // REPLACE_EXISTING | WRITE_THROUGH; no cross-volume copy fallback.
        if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), 0x1 | 0x8) } == 0 {
            return Err(SandboxError("atomic source replacement failed".into()));
        }
    }
    #[cfg(not(windows))]
    std::fs::rename(from, to)
        .map_err(|_| SandboxError("atomic source replacement failed".into()))?;
    sync_parent(to)
}

pub struct RootRecord {
    pub root_id: String,
    pub path: PathBuf,
    pub generation: String,
    pub write_paths: Vec<String>,
    pub identity: [u64; 2],
}

impl RootRecord {
    pub fn to_value(&self) -> Value {
        json!({
            "root_id": self.root_id, "path": self.path.to_string_lossy(),
            "generation": self.generation,
            "write_paths": self.write_paths,
            "root_identity": [self.identity[0], self.identity[1]],
        })
    }
}

pub struct SandboxRegistry {
    pub id: String,
    home: PathBuf,
    pub(crate) directory: PathBuf,
    path: PathBuf,
    lock_path: PathBuf,
}

impl SandboxRegistry {
    pub fn new(workspace_id: &str) -> SandboxResult<SandboxRegistry> {
        Self::with_home(workspace_id, user_dir())
    }

    /// Explicit application-state home; tests use this instead of the
    /// process-wide environment variable (parallel test safety).
    pub fn with_home(workspace_id: &str, home: PathBuf) -> SandboxResult<SandboxRegistry> {
        if !valid_identifier(workspace_id) {
            return Err(SandboxError(
                "workspace/root ID must start with a letter and contain at most 48 safe characters"
                    .into(),
            ));
        }
        let directory = home.join("workspaces").join(workspace_id);
        let registry = SandboxRegistry {
            id: workspace_id.to_string(),
            home,
            directory: directory.clone(),
            path: directory.join("roots.json"),
            lock_path: directory.join("roots.lock"),
        };
        registry.check_paths()?;
        Ok(registry)
    }

    fn check_paths(&self) -> SandboxResult<()> {
        for path in [&self.path, &self.lock_path] {
            if path.exists() {
                let meta = std::fs::symlink_metadata(path).map_err(|error| {
                    SandboxError(format!("workspace registry check failed: {error}"))
                })?;
                if meta.file_type().is_symlink() || !meta.is_file() {
                    return Err(SandboxError(
                        "workspace registry requires regular files".into(),
                    ));
                }
            }
        }
        Ok(())
    }

    fn lease(&self) -> SandboxResult<Lease> {
        self.check_paths()?;
        std::fs::create_dir_all(&self.directory)
            .map_err(|error| SandboxError(format!("workspace directory failed: {error}")))?;
        self.check_paths()?;
        Lease::acquire(&self.lock_path, true, "workspace registry is being updated")
            .map_err(SandboxError)
    }

    /// Canonical, strict root validation (read-only checks; no grants created).
    pub fn canonical_root(raw: &Path, home: &Path) -> SandboxResult<PathBuf> {
        if !raw.is_absolute() {
            return Err(SandboxError(
                "root must be an explicit absolute directory".into(),
            ));
        }
        ensure_no_links(raw)?;
        let canonical = std::fs::canonicalize(raw)
            .map_err(|error| SandboxError(format!("root is unavailable: {error}")))?;
        if !canonical.is_dir() {
            return Err(SandboxError("root must be a directory".into()));
        }
        if canonical.to_str().is_none() {
            return Err(SandboxError(
                "source roots must have lossless UTF-8 paths".into(),
            ));
        }
        let home = std::fs::canonicalize(home).unwrap_or_else(|_| home.to_path_buf());
        if canonical.starts_with(&home) || home.starts_with(&canonical) {
            return Err(SandboxError(
                "application state and source roots must be disjoint".into(),
            ));
        }
        for part in canonical.components() {
            if part.as_os_str().eq_ignore_ascii_case(".git") {
                return Err(SandboxError("Git metadata cannot be a source root".into()));
            }
        }
        Ok(canonical)
    }

    fn load(&self, validate_roots: bool) -> SandboxResult<(BTreeMap<String, Value>, Value)> {
        self.check_paths()?;
        if !self.path.exists() {
            return Ok((
                BTreeMap::new(),
                json!({"schema_version": 1, "workspace_id": self.id, "roots": {}}),
            ));
        }
        let text = std::fs::read_to_string(&self.path)
            .map_err(|error| SandboxError(format!("workspace registry read failed: {error}")))?;
        if text.len() > 65536 {
            return Err(SandboxError(
                "workspace registry exceeds its size limit".into(),
            ));
        }
        let state: Value = serde_json::from_str(&text)
            .map_err(|_| SandboxError("invalid or unavailable workspace registry".into()))?;
        let object = state
            .as_object()
            .ok_or_else(|| SandboxError("invalid or unavailable workspace registry".into()))?;
        if object.len() != 3
            || object.get("schema_version").and_then(Value::as_i64) != Some(1)
            || object.get("workspace_id").and_then(Value::as_str) != Some(self.id.as_str())
            || !state["roots"].is_object()
            || state["roots"].as_object().map(|r| r.len()).unwrap_or(0) > MAX_ROOTS
        {
            return Err(SandboxError(
                "invalid or unavailable workspace registry".into(),
            ));
        }
        let mut roots = BTreeMap::new();
        for (root_id, item) in state["roots"].as_object().expect("checked") {
            if !valid_identifier(root_id) {
                return Err(SandboxError(
                    "invalid or unavailable workspace registry".into(),
                ));
            }
            let fields_ok = item.as_object().is_some_and(|o| {
                o.len() == 5
                    && o.contains_key("root_id")
                    && o.contains_key("path")
                    && o.contains_key("generation")
                    && o.contains_key("write_paths")
                    && o.contains_key("root_identity")
            });
            if !fields_ok || item["root_id"].as_str() != Some(root_id.as_str()) {
                return Err(SandboxError(
                    "invalid or unavailable workspace registry".into(),
                ));
            }
            let generation = item["generation"].as_str().unwrap_or_default();
            let generation_ok =
                generation.len() == 32 && generation.bytes().all(|b| b.is_ascii_hexdigit());
            let write_paths: Vec<String> = item["write_paths"]
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            let write_paths_ok = item["write_paths"].is_array()
                && write_paths.len() <= MAX_WRITE_PATHS
                && write_paths.iter().collect::<BTreeSet<_>>().len() == write_paths.len();
            let identity: Vec<u64> = item["root_identity"]
                .as_array()
                .map(|items| items.iter().filter_map(Value::as_u64).collect())
                .unwrap_or_default();
            if !generation_ok || !write_paths_ok || identity.len() != 2 {
                return Err(SandboxError(
                    "invalid or unavailable workspace registry".into(),
                ));
            }
            if validate_roots {
                let raw = PathBuf::from(item["path"].as_str().unwrap_or_default());
                if !raw.is_absolute() {
                    return Err(SandboxError(
                        "invalid or unavailable workspace registry".into(),
                    ));
                }
                let root = Self::canonical_root(&raw, &self.home)?;
                let found = root_identity(&root)?;
                if found != [identity[0], identity[1]] {
                    return Err(SandboxError(
                        "invalid or unavailable workspace registry".into(),
                    ));
                }
                for rel in &write_paths {
                    crate::admission::admit_rel(&root, rel)?;
                }
            }
            roots.insert(root_id.clone(), item.clone());
        }
        Ok((roots, state))
    }

    fn save(&self, state: &Value) -> SandboxResult<()> {
        self.check_paths()?;
        let temp = self.directory.join(format!("{}.tmp", af_entropy::token()));
        let payload = serde_json::to_vec(state)
            .map_err(|error| SandboxError(format!("registry encode failed: {error}")))?;
        {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)
                .map_err(|error| SandboxError(format!("registry temp write failed: {error}")))?;
            file.write_all(&payload)
                .and_then(|_| file.flush())
                .and_then(|_| file.sync_all())
                .map_err(|error| SandboxError(format!("registry fsync failed: {error}")))?;
        }
        std::fs::rename(&temp, &self.path)
            .map_err(|error| SandboxError(format!("registry replace failed: {error}")))?;
        Ok(())
    }

    /// Grant a root; `approved` must be an explicit host decision.
    pub fn grant(
        &self,
        root_id: &str,
        path: &Path,
        write_paths: &[String],
        approved: bool,
    ) -> SandboxResult<Value> {
        if approved != true {
            return Err(SandboxError(
                "root access needs explicit host approval".into(),
            ));
        }
        if !valid_identifier(root_id) {
            return Err(SandboxError(
                "workspace/root ID must start with a letter and contain at most 48 safe characters"
                    .into(),
            ));
        }
        let root = Self::canonical_root(path, &self.home)?;
        if write_paths.len() > MAX_WRITE_PATHS {
            return Err(SandboxError(
                "write scope must contain at most 64 exact source paths".into(),
            ));
        }
        for rel in write_paths {
            crate::admission::admit_rel(&root, rel)?;
        }
        let _lease = self.lease()?;
        let (mut roots, mut state) = self.load(true)?;
        for (other_id, item) in &roots {
            if other_id == root_id {
                continue;
            }
            let other = Self::canonical_root(
                Path::new(item["path"].as_str().unwrap_or_default()),
                &self.home,
            )?;
            if root.starts_with(&other) || other.starts_with(&root) {
                return Err(SandboxError(
                    "overlapping or aliased roots are not permitted".into(),
                ));
            }
        }
        if !roots.contains_key(root_id) && roots.len() >= MAX_ROOTS {
            return Err(SandboxError("workspace has reached its root limit".into()));
        }
        let identity = root_identity(&root)?;
        let mut sorted_paths = write_paths.to_vec();
        sorted_paths.sort();
        sorted_paths.dedup();
        let item = RootRecord {
            root_id: root_id.to_string(),
            path: root,
            generation: af_entropy::token(),
            write_paths: sorted_paths,
            identity,
        };
        let value = item.to_value();
        roots.insert(root_id.to_string(), value.clone());
        state["roots"] = Value::Object(
            roots
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<Map<String, Value>>(),
        );
        self.save(&state)?;
        Ok(value)
    }

    pub fn revoke(&self, root_id: &str, approved: bool) -> SandboxResult<Value> {
        if approved != true {
            return Err(SandboxError(
                "revocation needs explicit host approval".into(),
            ));
        }
        if !valid_identifier(root_id) {
            return Err(SandboxError("invalid root id".into()));
        }
        let _lease = self.lease()?;
        let (mut roots, mut state) = self.load(false)?;
        let existed = roots.remove(root_id).is_some();
        state["roots"] = Value::Object(
            roots
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<Map<String, Value>>(),
        );
        self.save(&state)?;
        Ok(json!({"root_id": root_id, "revoked": existed}))
    }

    pub fn list_roots(&self) -> SandboxResult<Vec<Value>> {
        let _lease = self.lease()?;
        let (roots, _) = self.load(true)?;
        Ok(roots.into_values().collect())
    }

    pub fn get(&self, root_id: &str) -> SandboxResult<Value> {
        if !valid_identifier(root_id) {
            return Err(SandboxError("invalid root id".into()));
        }
        let _lease = self.lease()?;
        let (roots, _) = self.load(true)?;
        roots
            .get(root_id)
            .cloned()
            .ok_or_else(|| SandboxError("root access is not granted".into()))
    }

    pub(crate) fn effect_path(&self, root: &Path) -> PathBuf {
        let key = root.to_string_lossy().to_string();
        #[cfg(windows)]
        let key = key.to_lowercase();
        self.home
            .join("root-effects")
            .join(format!("{}.json", crate::sha256_hex(key.as_bytes())))
    }

    pub(crate) fn root_effect_lease(&self, root: &Path) -> SandboxResult<Lease> {
        let path = self.effect_path(root);
        ensure_no_links(&path)?;
        std::fs::create_dir_all(path.parent().expect("root effect parent"))
            .map_err(|_| SandboxError("root effect store is unavailable".into()))?;
        ensure_no_links(&path)?;
        Lease::acquire(
            &path
                .parent()
                .expect("effect parent")
                .join("publication.lock"),
            true,
            "another publication owns this root",
        )
        .map_err(SandboxError)
    }

    pub(crate) fn require_path_effects_clear(&self, root: &Path) -> SandboxResult<()> {
        let path = self.effect_path(root);
        ensure_no_links(&path)?;
        let directory = path.parent().expect("effect parent");
        if !directory.exists() {
            return Ok(());
        }
        for (index, entry) in std::fs::read_dir(directory)
            .map_err(|_| SandboxError("publication hold store unavailable".into()))?
            .enumerate()
        {
            if index > 1024 {
                return Err(SandboxError("publication hold scan limit".into()));
            }
            let entry =
                entry.map_err(|_| SandboxError("publication hold entry unavailable".into()))?;
            ensure_no_links(&entry.path())?;
            if entry.path().extension().and_then(|x| x.to_str()) == Some("json") {
                return Err(SandboxError("publication has an unresolved durable hold; inspect and reconcile, never replay".into()));
            }
        }
        Ok(())
    }

    /// Shared across sessions, root IDs and workspaces in the same state home.
    /// Re-registering a root never clears an interrupted publication.
    pub fn require_root_effects_clear(&self, root_id: &str) -> SandboxResult<()> {
        let item = self.get(root_id)?;
        self.require_path_effects_clear(Path::new(
            item["path"]
                .as_str()
                .ok_or_else(|| SandboxError("invalid root path".into()))?,
        ))
    }

    pub(crate) fn begin_root_effect(
        &self,
        root: &Path,
        changeset: &str,
        intent_sha256: &str,
    ) -> SandboxResult<()> {
        self.require_path_effects_clear(root)?;
        let payload = serde_json::to_vec(&json!({"schema_version":1,"workspace_id":self.id,
            "changeset_id":changeset,"intent_sha256":intent_sha256}))
        .map_err(|_| SandboxError("root effect encoding failed".into()))?;
        persist_new(&self.effect_path(root), &payload)
    }

    pub(crate) fn finish_root_effect(&self, root: &Path, changeset: &str) -> SandboxResult<()> {
        use std::io::Read;
        let path = self.effect_path(root);
        ensure_no_links(&path)?;
        let mut bytes = Vec::new();
        std::fs::File::open(&path)
            .and_then(|file| file.take(4097).read_to_end(&mut bytes))
            .map_err(|_| SandboxError("root publication hold is unavailable".into()))?;
        if bytes.len() > 4096 {
            return Err(SandboxError(
                "root publication hold exceeds its bound".into(),
            ));
        }
        let saved: Value = serde_json::from_slice(&bytes)
            .map_err(|_| SandboxError("root publication hold is corrupt".into()))?;
        if saved["changeset_id"] != json!(changeset)
            || saved["workspace_id"] != json!(self.id)
            || saved["schema_version"] != json!(1)
        {
            return Err(SandboxError(
                "root publication hold identity differs".into(),
            ));
        }
        std::fs::remove_file(&path)
            .map_err(|_| SandboxError("root publication hold removal failed".into()))?;
        if let Err(error) = sync_parent(&path) {
            // Restore the conservative marker when removal acknowledgement fails.
            let _ = persist_new(&path, &bytes);
            return Err(error);
        }
        Ok(())
    }
}

use std::collections::BTreeSet;
