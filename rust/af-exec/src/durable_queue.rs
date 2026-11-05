//! Trusted-host admission prototype (N02/E04 slice): an append-only ledger of
//! request-bound idempotency keys, reservation epochs, and explicit transitions.
//! This is not an authenticated multi-user or integrated durable API boundary.
//! Same fail-closed contract as the budget
//! ledger: sequence-numbered JSONL, lease locking, size bounds, poison on
//! uncertain appends, no silent recovery of a torn tail.
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const MAX_ENTRIES: usize = 1024;
const MAX_RECORD: usize = 4096;
const MAX_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct DurableQueueError(pub String);
type Result<T> = std::result::Result<T, DurableQueueError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryState {
    Submitted,
    Reserved,
    Completed,
    Failed,
}
impl EntryState {
    fn as_str(self) -> &'static str {
        match self {
            EntryState::Submitted => "submitted",
            EntryState::Reserved => "reserved",
            EntryState::Completed => "completed",
            EntryState::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Entry {
    pub entry_id: String,
    pub task_id: String,
    pub kind: String,
    pub idempotency_key: String,
    pub priority: i64,
    pub payload_digest: String,
    pub state: EntryState,
    pub worker: Option<String>,
    pub reservation_fence: Option<u64>,
    pub receipt_digest: Option<String>,
    pub sequence: u64,
}

#[derive(Debug, Clone)]
pub struct Snapshot {
    pub fence: u64,
    pub sequence: u64,
    pub entries: BTreeMap<String, Entry>,
}
impl Snapshot {
    /// Admission order: priority descending, then submission sequence.
    pub fn admission_order(&self) -> Vec<&Entry> {
        let mut list: Vec<&Entry> = self
            .entries
            .values()
            .filter(|entry| matches!(entry.state, EntryState::Submitted | EntryState::Reserved))
            .collect();
        list.sort_by(|a, b| {
            b.priority
                .cmp(&a.priority)
                .then(a.sequence.cmp(&b.sequence))
        });
        list
    }
    pub fn counts(&self) -> BTreeMap<&'static str, usize> {
        let mut counts = BTreeMap::new();
        for entry in self.entries.values() {
            *counts.entry(entry.state.as_str()).or_insert(0) += 1;
        }
        counts
    }
}

fn identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn plain(path: &Path) -> Result<()> {
    if !path.is_absolute() {
        return Err(DurableQueueError("queue path must be absolute".into()));
    }
    for part in path.ancestors() {
        if let Ok(meta) = std::fs::symlink_metadata(part) {
            if meta.file_type().is_symlink() {
                return Err(DurableQueueError(
                    "queue storage cannot contain links".into(),
                ));
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err(DurableQueueError(
                        "queue storage cannot contain reparse points".into(),
                    ));
                }
            }
        }
    }
    Ok(())
}

fn lease(path: &Path) -> Result<af_entropy::FileLease> {
    let lock = path.with_extension("queue-lock");
    plain(&lock)?;
    af_entropy::FileLease::acquire(&lock, true, "queue is busy")
        .map_err(|_| DurableQueueError("queue lease unavailable; another writer is active".into()))
}

// Adapted from this repository's af-sandbox recovery duplicate-key scanner.
// Value parsing validates JSON syntax; this second bounded pass rejects recursively
// duplicated object keys, including equivalent escaped spellings.
fn strict_json(bytes: &[u8]) -> Result<Value> {
    if bytes.len() > MAX_RECORD {
        return Err(DurableQueueError("queue JSON record bound exceeded".into()));
    }
    let value: Value = serde_json::from_slice(bytes)
        .map_err(|_| DurableQueueError("queue JSON corrupt".into()))?;
    fn whitespace(bytes: &[u8], cursor: &mut usize) {
        while bytes.get(*cursor).is_some_and(u8::is_ascii_whitespace) {
            *cursor += 1;
        }
    }
    fn string(bytes: &[u8], cursor: &mut usize) -> Result<String> {
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
                        .map_err(|_| DurableQueueError("JSON string invalid".into()));
                }
                _ => *cursor += 1,
            }
        }
        Err(DurableQueueError("JSON string truncated".into()))
    }
    fn walk(bytes: &[u8], cursor: &mut usize, depth: usize) -> Result<()> {
        if depth > 128 {
            return Err(DurableQueueError("queue JSON depth exceeded".into()));
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
                        return Err(DurableQueueError("duplicate queue JSON key".into()));
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
            None => return Err(DurableQueueError("queue JSON truncated".into())),
        }
        Ok(())
    }
    walk(bytes, &mut 0, 0)?;
    Ok(value)
}

fn validate_op(op: &Value) -> Result<()> {
    let kind = op
        .get("kind")
        .and_then(Value::as_str)
        .ok_or(DurableQueueError("operation kind missing".into()))?;
    let (expected, id_fields, digest_fields): (&[&str], &[&str], &[&str]) = match kind {
        "submit" => (
            &[
                "kind",
                "entry_id",
                "task_id",
                "idempotency_key",
                "priority",
                "payload_digest",
            ],
            &["entry_id", "task_id", "idempotency_key"],
            &["payload_digest"],
        ),
        "reserve" => (
            &["kind", "entry_id", "worker"],
            &["entry_id", "worker"],
            &[],
        ),
        "complete" => (
            &[
                "kind",
                "entry_id",
                "worker",
                "reservation_fence",
                "receipt_digest",
            ],
            &["entry_id", "worker"],
            &["receipt_digest"],
        ),
        "fail" => (
            &["kind", "entry_id", "worker", "reservation_fence", "reason"],
            &["entry_id", "worker"],
            &[],
        ),
        "fence" => (&["kind", "fence_id"], &["fence_id"], &[]),
        other => return Err(DurableQueueError(format!("unknown operation kind {other}"))),
    };
    let object = op
        .as_object()
        .ok_or(DurableQueueError("operation must be an object".into()))?;
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(DurableQueueError(
            "operation fields do not match its strict schema".into(),
        ));
    }
    for field in id_fields {
        let value = op[*field].as_str().unwrap_or_default();
        if !identity(value) {
            return Err(DurableQueueError(format!("invalid {field} identity")));
        }
    }
    for field in digest_fields {
        let value = op[*field].as_str().unwrap_or_default();
        if !digest(value) {
            return Err(DurableQueueError(format!("invalid {field} digest")));
        }
    }
    if kind == "submit" && op["priority"].as_i64().is_none() {
        return Err(DurableQueueError(
            "priority must be a signed integer".into(),
        ));
    }
    if matches!(kind, "complete" | "fail")
        && op["reservation_fence"]
            .as_u64()
            .filter(|epoch| *epoch > 0)
            .is_none()
    {
        return Err(DurableQueueError(
            "reservation fence must be a positive integer".into(),
        ));
    }
    if kind == "fail" {
        let reason = op["reason"].as_str().unwrap_or_default();
        if reason.is_empty() || reason.len() > 512 {
            return Err(DurableQueueError("fail reason must be bounded text".into()));
        }
    }
    Ok(())
}

fn apply(state: &mut Snapshot, sequence: u64, op: &Value) -> Result<()> {
    let kind = op["kind"].as_str().unwrap_or_default();
    let next_fence = state
        .fence
        .checked_add(1)
        .ok_or(DurableQueueError("fence overflow".into()))?;
    let next_sequence = state
        .sequence
        .checked_add(1)
        .ok_or(DurableQueueError("sequence overflow".into()))?;
    if sequence != next_sequence {
        return Err(DurableQueueError("queue sequence gap".into()));
    }
    let fence = op
        .get("fence_id")
        .and_then(Value::as_str)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(next_fence);
    // Every mutation advances the epoch; reserve binds its epoch to the worker.
    if fence != next_fence {
        return Err(DurableQueueError(
            "fence must advance by exactly one".into(),
        ));
    }
    match kind {
        "submit" => {
            let entry_id = op["entry_id"].as_str().unwrap_or_default().to_string();
            let idempotency_key = op["idempotency_key"]
                .as_str()
                .unwrap_or_default()
                .to_string();
            if state.entries.contains_key(&entry_id) {
                return Err(DurableQueueError(format!("duplicate entry {entry_id}")));
            }
            if state
                .entries
                .values()
                .any(|entry| entry.idempotency_key == idempotency_key)
            {
                return Err(DurableQueueError(format!(
                    "idempotency key {idempotency_key} already used"
                )));
            }
            if state.entries.len() >= MAX_ENTRIES {
                return Err(DurableQueueError("entry limit (1024) reached".into()));
            }
            state.entries.insert(
                entry_id.clone(),
                Entry {
                    entry_id,
                    task_id: op["task_id"].as_str().unwrap_or_default().into(),
                    kind: "task".into(),
                    idempotency_key,
                    priority: op["priority"].as_i64().unwrap_or(0),
                    payload_digest: op["payload_digest"].as_str().unwrap_or_default().into(),
                    state: EntryState::Submitted,
                    worker: None,
                    reservation_fence: None,
                    receipt_digest: None,
                    sequence,
                },
            );
        }
        "reserve" => {
            let entry_id = op["entry_id"].as_str().unwrap_or_default();
            let entry = state
                .entries
                .get_mut(entry_id)
                .ok_or(DurableQueueError(format!("unknown entry {entry_id}")))?;
            if entry.state != EntryState::Submitted {
                return Err(DurableQueueError(format!(
                    "entry {entry_id} is {}, not submitted",
                    entry.state.as_str()
                )));
            }
            entry.state = EntryState::Reserved;
            entry.worker = Some(op["worker"].as_str().unwrap_or_default().into());
            entry.reservation_fence = Some(fence);
        }
        "complete" => {
            let entry_id = op["entry_id"].as_str().unwrap_or_default();
            let entry = state
                .entries
                .get_mut(entry_id)
                .ok_or(DurableQueueError(format!("unknown entry {entry_id}")))?;
            if entry.state != EntryState::Reserved {
                return Err(DurableQueueError(format!(
                    "entry {entry_id} is {}, not reserved",
                    entry.state.as_str()
                )));
            }
            check_reservation(entry, op)?;
            entry.state = EntryState::Completed;
            entry.receipt_digest = Some(op["receipt_digest"].as_str().unwrap_or_default().into());
        }
        "fail" => {
            let entry_id = op["entry_id"].as_str().unwrap_or_default();
            let entry = state
                .entries
                .get_mut(entry_id)
                .ok_or(DurableQueueError(format!("unknown entry {entry_id}")))?;
            if entry.state != EntryState::Reserved {
                return Err(DurableQueueError(format!(
                    "entry {entry_id} is {}, not reserved",
                    entry.state.as_str()
                )));
            }
            check_reservation(entry, op)?;
            entry.state = EntryState::Failed;
        }
        "fence" => {
            let declared = op
                .get("fence_id")
                .and_then(Value::as_str)
                .and_then(|value| value.parse::<u64>().ok())
                .ok_or(DurableQueueError(
                    "fence_id must be a numeric string".into(),
                ))?;
            if declared != next_fence {
                return Err(DurableQueueError(
                    "fence must increment by exactly one".into(),
                ));
            }
        }
        other => return Err(DurableQueueError(format!("unknown operation kind {other}"))),
    }
    state.fence = fence;
    state.sequence = sequence;
    Ok(())
}

fn check_reservation(entry: &Entry, op: &Value) -> Result<()> {
    if entry.worker.as_deref() != op["worker"].as_str()
        || entry.reservation_fence != op["reservation_fence"].as_u64()
    {
        return Err(DurableQueueError(
            "reservation worker or fence mismatch".into(),
        ));
    }
    Ok(())
}

/// Append-only durable admission store. One writer at a time (lease); every
/// mutation re-reads the full ledger, validates it, then appends.
pub struct DurableAdmission {
    path: PathBuf,
    poisoned: bool,
}
impl DurableAdmission {
    pub fn create(path: &Path) -> Result<Self> {
        plain(path)?;
        let parent = path
            .parent()
            .ok_or(DurableQueueError("queue parent unavailable".into()))?;
        std::fs::create_dir_all(parent)
            .map_err(|_| DurableQueueError("queue directory creation failed".into()))?;
        let _lease = lease(path)?;
        let record = json!({"schema_version": 1u32, "sequence": 1u64, "op": {"kind": "created"}});
        let mut raw = serde_json::to_vec(&record)
            .map_err(|_| DurableQueueError("queue encoding failed".into()))?;
        raw.push(b'\n');
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|_| DurableQueueError("queue already exists or cannot be created".into()))?;
        file.write_all(&raw)
            .and_then(|_| file.sync_all())
            .map_err(|_| DurableQueueError("queue create persistence failed".into()))?;
        Ok(Self {
            path: path.into(),
            poisoned: false,
        })
    }

    pub fn open(path: &Path) -> Result<Self> {
        plain(path)?;
        let store = Self {
            path: path.into(),
            poisoned: false,
        };
        store.snapshot()?;
        Ok(store)
    }

    pub fn snapshot(&self) -> Result<Snapshot> {
        if self.poisoned {
            return Err(DurableQueueError(
                "queue write uncertain; close and inspect durable state".into(),
            ));
        }
        plain(&self.path)?;
        let _lease = lease(&self.path)?;
        self.load()
    }

    fn load(&self) -> Result<Snapshot> {
        use std::io::{BufRead, BufReader};
        let file = std::fs::File::open(&self.path)
            .map_err(|_| DurableQueueError("queue unavailable".into()))?;
        if file
            .metadata()
            .map_err(|_| DurableQueueError("queue stat failed".into()))?
            .len()
            > MAX_BYTES
        {
            return Err(DurableQueueError("queue size limit exceeded".into()));
        }
        let mut reader = BufReader::new(file);
        let mut state = Snapshot {
            fence: 0,
            sequence: 0,
            entries: BTreeMap::new(),
        };
        let mut first = true;
        loop {
            let mut raw = Vec::new();
            let size = reader
                .read_until(b'\n', &mut raw)
                .map_err(|_| DurableQueueError("queue read failed".into()))?;
            if size == 0 {
                break;
            }
            if size > MAX_RECORD || raw.last() != Some(&b'\n') {
                return Err(DurableQueueError(
                    "queue torn tail or record bound exceeded; no automatic recovery".into(),
                ));
            }
            let row = strict_json(&raw)?;
            let fields = row
                .as_object()
                .ok_or(DurableQueueError("queue row must be an object".into()))?;
            if fields.len() != 3
                || ["schema_version", "sequence", "op"]
                    .iter()
                    .any(|key| !fields.contains_key(*key))
            {
                return Err(DurableQueueError(
                    "queue row fields differ from strict schema".into(),
                ));
            }
            if row["schema_version"].as_u64() != Some(1) {
                return Err(DurableQueueError("queue schema version changed".into()));
            }
            let sequence = row["sequence"]
                .as_u64()
                .ok_or(DurableQueueError("queue sequence missing".into()))?;
            let op = row["op"]
                .as_object()
                .ok_or(DurableQueueError("queue operation missing".into()))?;
            let op_value = Value::Object(op.clone());
            if first {
                if sequence != 1 || op.len() != 1 || op_value["kind"] != json!("created") {
                    return Err(DurableQueueError("missing queue creation".into()));
                }
                first = false;
                state.sequence = 1;
                continue;
            }
            validate_op(&op_value)?;
            if Some(sequence) != state.sequence.checked_add(1) {
                return Err(DurableQueueError("queue sequence gap".into()));
            }
            apply(&mut state, sequence, &op_value)?;
        }
        if first {
            return Err(DurableQueueError("empty queue file".into()));
        }
        Ok(state)
    }

    fn transition(&mut self, op: Value) -> Result<Snapshot> {
        if self.poisoned {
            return Err(DurableQueueError(
                "queue persistence uncertain; no effects authorized".into(),
            ));
        }
        plain(&self.path)?;
        validate_op(&op)?;
        let _lease = lease(&self.path)?;
        let mut state = self.load()?;
        if op["kind"] == "submit" {
            let entry_id = op["entry_id"].as_str().unwrap_or_default();
            let key = op["idempotency_key"].as_str().unwrap_or_default();
            let by_id = state.entries.get(entry_id);
            let by_key = state
                .entries
                .values()
                .find(|entry| entry.idempotency_key == key);
            if by_id.is_some() || by_key.is_some() {
                let original = by_key.ok_or(DurableQueueError(
                    "entry identity conflicts with original submission".into(),
                ))?;
                if by_id.is_some_and(|entry| entry.entry_id != original.entry_id)
                    || original.task_id != op["task_id"].as_str().unwrap_or_default()
                    || original.priority != op["priority"].as_i64().unwrap()
                    || original.payload_digest != op["payload_digest"].as_str().unwrap_or_default()
                {
                    return Err(DurableQueueError(
                        "idempotency key conflicts with original submission".into(),
                    ));
                }
                return Ok(state);
            }
        }
        let sequence = state
            .sequence
            .checked_add(1)
            .ok_or(DurableQueueError("sequence overflow".into()))?;
        apply(&mut state, sequence, &op)?;
        let record = json!({"schema_version": 1u32, "sequence": sequence, "op": op});
        let mut raw = serde_json::to_vec(&record)
            .map_err(|_| DurableQueueError("queue encoding failed".into()))?;
        raw.push(b'\n');
        if raw.len() > MAX_RECORD {
            return Err(DurableQueueError("queue record bound exceeded".into()));
        }
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&self.path)
            .map_err(|_| DurableQueueError("queue append unavailable".into()))?;
        let current_bytes = file
            .metadata()
            .map_err(|_| DurableQueueError("queue stat failed".into()))?
            .len();
        if current_bytes
            .checked_add(raw.len() as u64)
            .filter(|size| *size <= MAX_BYTES)
            .is_none()
        {
            return Err(DurableQueueError(
                "queue size limit exceeded before append".into(),
            ));
        }
        if file.write_all(&raw).and_then(|_| file.sync_all()).is_err() {
            self.poisoned = true;
            return Err(DurableQueueError(
                "queue append uncertain; reopen and inspect durable state".into(),
            ));
        }
        Ok(state)
    }

    /// Submit one task entry. A matching key returns the original submission
    /// only when task, priority and payload match. Conflicting identities fail.
    /// The duplicate check and append share one lease.
    pub fn submit(
        &mut self,
        entry_id: &str,
        task_id: &str,
        idempotency_key: &str,
        priority: i64,
        payload_digest: &str,
    ) -> Result<Snapshot> {
        if !identity(entry_id) || !identity(task_id) || !identity(idempotency_key) {
            return Err(DurableQueueError("invalid submit identity".into()));
        }
        if !digest(payload_digest) {
            return Err(DurableQueueError("invalid payload digest".into()));
        }
        self.transition(json!({
            "kind": "submit", "entry_id": entry_id, "task_id": task_id,
            "idempotency_key": idempotency_key, "priority": priority,
            "payload_digest": payload_digest,
        }))
    }

    pub fn reserve(&mut self, entry_id: &str, worker: &str) -> Result<Snapshot> {
        self.transition(json!({"kind": "reserve", "entry_id": entry_id, "worker": worker}))
    }

    pub fn complete(
        &mut self,
        entry_id: &str,
        worker: &str,
        reservation_fence: u64,
        receipt_digest: &str,
    ) -> Result<Snapshot> {
        self.transition(json!({
            "kind": "complete", "entry_id": entry_id, "worker": worker,
            "reservation_fence": reservation_fence, "receipt_digest": receipt_digest,
        }))
    }

    pub fn fail(
        &mut self,
        entry_id: &str,
        worker: &str,
        reservation_fence: u64,
        reason: &str,
    ) -> Result<Snapshot> {
        self.transition(
            json!({"kind": "fail", "entry_id": entry_id, "worker": worker,
            "reservation_fence": reservation_fence, "reason": reason}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_path(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("af-durable-queue-{}-{name}", af_entropy::token()));
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("queue.jsonl")
    }

    fn submit_op(entry: &str, key: &str) -> Value {
        json!({
            "kind": "submit", "entry_id": entry, "task_id": "t1",
            "idempotency_key": key, "priority": 5,
            "payload_digest": &"a".repeat(64),
        })
    }

    #[test]
    fn lifecycle_and_priority_ordering() {
        let path = temp_path("lifecycle");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e2", "t1", "k2", 1, &"a".repeat(64)).unwrap();
        queue.submit("e1", "t1", "k1", 9, &"b".repeat(64)).unwrap();
        let snapshot = queue.snapshot().unwrap();
        let order: Vec<&str> = snapshot
            .admission_order()
            .iter()
            .map(|entry| entry.entry_id.as_str())
            .collect();
        assert_eq!(order, vec!["e1", "e2"], "higher priority first");
        let mut queue = DurableAdmission::open(&path).unwrap();
        queue.reserve("e1", "worker-1").unwrap();
        queue
            .complete("e1", "worker-1", 3, &"c".repeat(64))
            .unwrap();
        let snapshot = queue.snapshot().unwrap();
        assert_eq!(snapshot.entries["e1"].state, EntryState::Completed);
        assert_eq!(snapshot.counts()[EntryState::Completed.as_str()], 1);
        assert_eq!(snapshot.counts()[EntryState::Submitted.as_str()], 1);
    }

    #[test]
    fn duplicate_idempotency_key_and_entry_never_create_a_second_entry() {
        let path = temp_path("idempotent");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        let again = queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        assert_eq!(again.entries.len(), 1);
        let same_key = queue.submit("e2", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        assert_eq!(same_key.entries.len(), 1, "same key returns original");
        let reopened = DurableAdmission::open(&path).unwrap();
        assert_eq!(reopened.snapshot().unwrap().entries.len(), 1);
    }

    #[test]
    fn fence_must_advance_by_exactly_one() {
        let path = temp_path("fence");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        // Sequence is 2 now (created + submit = fence 1); a fence jumping to 9
        // via transition() is refused by the strict +1 rule.
        let state = queue.snapshot().unwrap();
        assert_eq!(state.fence, 1);
        assert!(queue
            .transition(json!({"kind": "fence", "fence_id": "9"}))
            .is_err());
    }

    #[test]
    fn conflicting_submissions_leave_original_bytes_unchanged() {
        let path = temp_path("conflicting");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        queue.submit("e2", "t1", "k2", 1, &"a".repeat(64)).unwrap();
        let original = std::fs::read(&path).unwrap();
        for (entry, task, key, priority, payload) in [
            ("e3", "t2", "k1", 1, "a".repeat(64)),
            ("e3", "t1", "k1", 2, "a".repeat(64)),
            ("e3", "t1", "k1", 1, "b".repeat(64)),
            ("e1", "t1", "new-key", 1, "a".repeat(64)),
            ("e2", "t1", "k1", 1, "a".repeat(64)),
        ] {
            assert!(queue.submit(entry, task, key, priority, &payload).is_err());
            assert_eq!(std::fs::read(&path).unwrap(), original);
        }
    }

    #[test]
    fn reservation_worker_and_epoch_survive_reopen_and_reject_forgery() {
        let path = temp_path("ownership");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        let reserved = queue.reserve("e1", "owner").unwrap();
        let epoch = reserved.entries["e1"].reservation_fence.unwrap();
        let mut queue = DurableAdmission::open(&path).unwrap();
        let original = std::fs::read(&path).unwrap();
        assert!(queue
            .complete("e1", "foreign", epoch, &"c".repeat(64))
            .is_err());
        assert!(queue.fail("e1", "foreign", epoch, "failed").is_err());
        assert!(queue
            .complete("e1", "owner", epoch + 1, &"c".repeat(64))
            .is_err());
        assert!(queue.fail("e1", "owner", epoch - 1, "failed").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), original);
        queue
            .complete("e1", "owner", epoch, &"c".repeat(64))
            .unwrap();
        assert_eq!(
            DurableAdmission::open(&path)
                .unwrap()
                .snapshot()
                .unwrap()
                .entries["e1"]
                .state,
            EntryState::Completed
        );
    }

    #[test]
    fn malformed_priority_and_epoch_are_refused_before_append() {
        let path = temp_path("typed");
        let mut queue = DurableAdmission::create(&path).unwrap();
        let original = std::fs::read(&path).unwrap();
        for priority in [json!("5"), json!(null), json!(1.5), json!(u64::MAX)] {
            let mut op = submit_op("e1", "k1");
            op["priority"] = priority;
            assert!(queue.transition(op).is_err());
        }
        assert_eq!(std::fs::read(&path).unwrap(), original);
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        queue.reserve("e1", "w").unwrap();
        assert!(queue.transition(json!({"kind":"fail", "entry_id":"e1", "worker":"w", "reservation_fence":"2", "reason":"failed"})).is_err());
    }

    #[test]
    fn replay_rejects_forged_terminal_owner() {
        let path = temp_path("forged-replay");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        queue.reserve("e1", "owner").unwrap();
        let forged = json!({"schema_version":1, "sequence":4, "op":{
            "kind":"complete", "entry_id":"e1", "worker":"foreign",
            "reservation_fence":2, "receipt_digest":"c".repeat(64)
        }});
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        writeln!(file, "{forged}").unwrap();
        assert!(DurableAdmission::open(&path).is_err());
    }

    #[test]
    fn checked_counters_refuse_overflow_without_mutating_state() {
        let mut state = Snapshot {
            fence: u64::MAX,
            sequence: 1,
            entries: BTreeMap::new(),
        };
        assert!(apply(&mut state, 2, &submit_op("e1", "k1")).is_err());
        assert!(state.entries.is_empty());
        state.fence = 0;
        state.sequence = u64::MAX;
        assert!(apply(&mut state, 0, &submit_op("e1", "k1")).is_err());
        assert!(state.entries.is_empty());
    }

    #[test]
    fn concurrent_identical_submissions_both_return_the_original() {
        let path = temp_path("duplicate-race");
        DurableAdmission::create(&path).unwrap();
        let gate = std::sync::Arc::new(std::sync::Barrier::new(2));
        let threads: Vec<_> = (0..2)
            .map(|_| {
                let path = path.clone();
                let gate = gate.clone();
                std::thread::spawn(move || {
                    let mut queue = DurableAdmission::open(&path).unwrap();
                    gate.wait();
                    queue
                        .submit("e1", "t1", "k1", 1, &"a".repeat(64))
                        .unwrap()
                        .sequence
                })
            })
            .collect();
        for thread in threads {
            assert_eq!(thread.join().unwrap(), 2);
        }
        assert_eq!(
            DurableAdmission::open(&path)
                .unwrap()
                .snapshot()
                .unwrap()
                .entries
                .len(),
            1
        );
    }

    #[test]
    fn total_byte_limit_refuses_append_without_stranding_the_ledger() {
        let path = temp_path("byte-limit");
        let mut raw = Vec::new();
        for index in 0..(MAX_BYTES as usize / MAX_RECORD) {
            let op = if index == 0 {
                json!({"kind":"created"})
            } else {
                json!({"kind":"fence", "fence_id":index.to_string()})
            };
            let mut row =
                serde_json::to_vec(&json!({"schema_version":1, "sequence":index + 1, "op":op}))
                    .unwrap();
            row.resize(MAX_RECORD - 1, b' ');
            row.push(b'\n');
            raw.extend(row);
        }
        std::fs::write(&path, &raw).unwrap();
        let mut queue = DurableAdmission::open(&path).unwrap();
        assert!(queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), raw);
        assert!(DurableAdmission::open(&path).is_ok());
    }

    #[test]
    fn wrong_state_transitions_are_refused() {
        let path = temp_path("states");
        let mut queue = DurableAdmission::create(&path).unwrap();
        queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        assert!(queue.complete("e1", "w", 1, &"c".repeat(64)).is_err());
        queue.reserve("e1", "w").unwrap();
        assert!(queue.reserve("e1", "w2").is_err());
        queue.complete("e1", "w", 2, &"c".repeat(64)).unwrap();
        assert!(queue.fail("e1", "w", 2, "late").is_err());
        assert!(queue.reserve("ghost", "w").is_err());
    }

    #[test]
    fn torn_tail_blocks_reopen_without_truncation() {
        let path = temp_path("torn");
        {
            let _queue = DurableAdmission::create(&path).unwrap();
        }
        let original = std::fs::read(&path).unwrap();
        let mut raw = original.clone();
        raw.extend_from_slice(b"{\"unfinished\":");
        std::fs::write(&path, &raw).unwrap();
        assert!(DurableAdmission::open(&path).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), raw);
    }

    #[test]
    fn corrupt_schema_rows_are_refused_without_rewriting_evidence() {
        let path = temp_path("corrupt-schema");
        for row in [
            r#"{"schema_version":1,"schema_version":1,"sequence":1,"op":{"kind":"created"}}"#,
            r#"{"schema_version":1,"sequence":1,"op":{"kind":"created","ki\u006ed":"created"}}"#,
            r#"{"schema_version":1,"sequence":1,"op":{"kind":"created"},"extra":true}"#,
            r#"{"schema_version":1,"sequence":1,"op":{"kind":"created","extra":true}}"#,
            r#"{"schema_version":1.0,"sequence":1,"op":{"kind":"created"}}"#,
            r#"{"schema_version":"1","sequence":1,"op":{"kind":"created"}}"#,
            r#"{"schema_version":true,"sequence":1,"op":{"kind":"created"}}"#,
            r#"{"schema_version":2,"sequence":1,"op":{"kind":"created"}}"#,
        ] {
            let raw = format!("{row}\n").into_bytes();
            std::fs::write(&path, &raw).unwrap();
            assert!(DurableAdmission::open(&path).is_err(), "accepted {row}");
            assert_eq!(std::fs::read(&path).unwrap(), raw);
        }
    }

    #[test]
    fn strict_json_allows_independent_keys_and_escaped_string_content() {
        assert!(strict_json(br#"{"x":[{"a":1},{"a":2}],"escaped":"\\\"[]{}"}"#).is_ok());
        assert!(strict_json(br#"{"x":[{"a":1,"\u0061":2}]}"#).is_err());
    }

    #[test]
    fn extra_fields_in_operations_are_refused() {
        let path = temp_path("strict");
        let mut queue = DurableAdmission::create(&path).unwrap();
        let op = submit_op("e1", "k1");
        let mut forged = op.clone();
        forged["api_key"] = json!("must not deserialize");
        assert!(queue.transition(forged).is_err());
        let missing = json!({
            "kind": "submit", "entry_id": "e1", "task_id": "t1",
            "idempotency_key": "k1", "priority": 5,
        });
        assert!(queue.transition(missing).is_err());
        let snapshot = queue.snapshot().unwrap();
        assert_eq!(snapshot.entries.len(), 0, "refused op left no trace");
        queue.submit("e1", "t1", "k1", 5, &"a".repeat(64)).unwrap();
    }

    #[test]
    fn cooperating_writers_cannot_oversubscribe_the_same_entry() {
        let path = temp_path("race");
        {
            let mut queue = DurableAdmission::create(&path).unwrap();
            queue.submit("e1", "t1", "k1", 1, &"a".repeat(64)).unwrap();
        }
        let gate = std::sync::Arc::new(std::sync::Barrier::new(2));
        let threads: Vec<_> = (0..2)
            .map(|i| {
                let path = path.clone();
                let gate = gate.clone();
                std::thread::spawn(move || {
                    let mut queue = DurableAdmission::open(&path).unwrap();
                    gate.wait();
                    queue.reserve("e1", &format!("worker-{i}")).is_ok()
                })
            })
            .collect();
        let winners = threads
            .into_iter()
            .map(|thread| thread.join().unwrap())
            .filter(|ok| *ok)
            .count();
        assert_eq!(winners, 1, "exactly one reservation wins");
        let reopened = DurableAdmission::open(&path).unwrap();
        let snapshot = reopened.snapshot().unwrap();
        assert_eq!(snapshot.entries["e1"].state, EntryState::Reserved);
    }
}
