//! New-schema integer accounting. Never imports or rewrites Python paid holds.
//! Reservations estimate admission; they do not guarantee provider billing caps.
//! A durable Sent/Unknown entry blocks further reservations; no replay/reconcile
//! API is supplied until authoritative provider evidence is separately designed.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};

pub const NANODOLLARS_PER_USD: u64 = 1_000_000_000;
const MAX_CALLS: usize = 1024;
const MAX_BYTES: u64 = 4 * 1024 * 1024;
const MAX_RECORD: usize = 4096;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct BudgetError(pub &'static str);
pub type Result<T> = std::result::Result<T, BudgetError>;

/// Exact nonnegative nanodollars. No floating-point currency enters storage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Money(pub u64);
impl Money {
    /// Exact decimal conversion; rejects exponent, sign, noncanonical leading
    /// zeroes and fractions smaller than one nanodollar. Never rounds a cap.
    pub fn from_usd_str(value: &str) -> Result<Money> {
        let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
        if whole.is_empty()
            || (whole.len() > 1 && whole.starts_with('0'))
            || !whole.bytes().all(|c| c.is_ascii_digit())
            || fraction.len() > 9
            || !fraction.bytes().all(|c| c.is_ascii_digit())
            || (value.contains('.') && fraction.is_empty())
        {
            return Err(BudgetError("invalid exact decimal USD amount"));
        }
        let whole: u64 = whole.parse().map_err(|_| BudgetError("money overflow"))?;
        let fraction: u64 = if fraction.is_empty() {
            0
        } else {
            fraction
                .parse::<u64>()
                .map_err(|_| BudgetError("invalid decimal fraction"))?
                .checked_mul(10u64.pow(9 - fraction.len() as u32))
                .ok_or(BudgetError("money overflow"))?
        };
        whole
            .checked_mul(NANODOLLARS_PER_USD)
            .and_then(|v| v.checked_add(fraction))
            .map(Money)
            .ok_or(BudgetError("money overflow"))
    }
    pub fn usd_string(self) -> String {
        let whole = self.0 / NANODOLLARS_PER_USD;
        let fraction = self.0 % NANODOLLARS_PER_USD;
        if fraction == 0 {
            whole.to_string()
        } else {
            format!("{whole}.{:09}", fraction)
                .trim_end_matches('0')
                .to_string()
        }
    }
    pub fn checked_add(self, other: Money) -> Result<Money> {
        self.0
            .checked_add(other.0)
            .map(Money)
            .ok_or(BudgetError("money overflow"))
    }
    pub fn checked_sub(self, other: Money) -> Result<Money> {
        self.0
            .checked_sub(other.0)
            .map(Money)
            .ok_or(BudgetError("money underflow"))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Reserved,
    Sent,
    Unknown,
    Settled,
    NotSent,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Call {
    pub task_id: String,
    pub reserved: Money,
    pub status: Status,
    pub actual: Option<Money>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Snapshot {
    pub owner: String,
    pub cap: Money,
    pub max_calls: usize,
    pub calls: BTreeMap<String, Call>,
    pub sequence: u64,
    pub send_checkpoint: bool,
}
impl Snapshot {
    pub fn committed(&self) -> Result<Money> {
        self.calls.values().try_fold(Money(0), |total, call| {
            total.checked_add(match call.status {
                Status::Settled => call.actual.ok_or(BudgetError("missing actual amount"))?,
                Status::NotSent => Money(0),
                _ => call.reserved,
            })
        })
    }
    pub fn has_hold(&self) -> bool {
        self.send_checkpoint
            || self
                .calls
                .values()
                .any(|call| matches!(call.status, Status::Sent | Status::Unknown))
            || self.committed().map_or(true, |cost| cost > self.cap)
    }
    /// Unknown exposure is not a measured zero available balance.
    pub fn available(&self) -> Result<Option<Money>> {
        if self.has_hold() {
            return Ok(None);
        }
        Ok(Some(self.cap.checked_sub(self.committed()?)?))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema_version: u32,
    sequence: u64,
    owner: String,
    cap: Money,
    max_calls: usize,
    operation: Operation,
}
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum Operation {
    Created,
    Reserve {
        call_id: String,
        task_id: String,
        amount: Money,
    },
    BeginSend {
        call_id: String,
        task_id: String,
    },
    Unknown {
        call_id: String,
        task_id: String,
    },
    Settle {
        call_id: String,
        task_id: String,
        actual: Money,
    },
    NotSent {
        call_id: String,
        task_id: String,
    },
}

fn operation_field<T, E>(value: &serde_json::Value, name: &str) -> std::result::Result<T, E>
where
    T: serde::de::DeserializeOwned,
    E: serde::de::Error,
{
    let present = value
        .get(name)
        .cloned()
        .ok_or_else(|| E::custom(format!("operation field {name} missing")))?;
    serde_json::from_value(present)
        .map_err(|_| E::custom(format!("operation field {name} invalid")))
}

/// `deny_unknown_fields` is silently ignored inside serde's internally tagged
/// enums, so a strict hand-written decoder enforces the storage schema: every
/// operation allows exactly its own keys and nothing else — no extra field
/// (credential, annotation, retry hint) can enter a persisted record.
impl<'de> serde::Deserialize<'de> for Operation {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        use serde::de::Error as _;
        let value = serde_json::Value::deserialize(deserializer)?;
        let object = value
            .as_object()
            .ok_or_else(|| D::Error::custom("operation must be an object"))?;
        let kind = object
            .get("kind")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| D::Error::custom("operation kind missing"))?;
        let (expected, operation) = match kind {
            "created" => (&["kind"][..], Operation::Created),
            "reserve" => (
                &["kind", "call_id", "task_id", "amount"][..],
                Operation::Reserve {
                    call_id: operation_field(&value, "call_id")?,
                    task_id: operation_field(&value, "task_id")?,
                    amount: operation_field(&value, "amount")?,
                },
            ),
            "begin_send" => (
                &["kind", "call_id", "task_id"][..],
                Operation::BeginSend {
                    call_id: operation_field(&value, "call_id")?,
                    task_id: operation_field(&value, "task_id")?,
                },
            ),
            "unknown" => (
                &["kind", "call_id", "task_id"][..],
                Operation::Unknown {
                    call_id: operation_field(&value, "call_id")?,
                    task_id: operation_field(&value, "task_id")?,
                },
            ),
            "settle" => (
                &["kind", "call_id", "task_id", "actual"][..],
                Operation::Settle {
                    call_id: operation_field(&value, "call_id")?,
                    task_id: operation_field(&value, "task_id")?,
                    actual: operation_field(&value, "actual")?,
                },
            ),
            "not_sent" => (
                &["kind", "call_id", "task_id"][..],
                Operation::NotSent {
                    call_id: operation_field(&value, "call_id")?,
                    task_id: operation_field(&value, "task_id")?,
                },
            ),
            other => return Err(D::Error::custom(format!("unknown operation kind {other}"))),
        };
        let keys: std::collections::BTreeSet<&str> = object.keys().map(String::as_str).collect();
        if keys.len() != expected.len() || expected.iter().any(|key| !keys.contains(key)) {
            return Err(D::Error::custom(
                "operation fields do not match its strict schema",
            ));
        }
        Ok(operation)
    }
}

fn identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
}
fn plain(path: &Path) -> Result<()> {
    if !path.is_absolute() {
        return Err(BudgetError("budget path must be absolute"));
    }
    for part in path.ancestors() {
        if let Ok(meta) = std::fs::symlink_metadata(part) {
            if meta.file_type().is_symlink() {
                return Err(BudgetError("budget storage cannot contain links"));
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err(BudgetError("budget storage cannot contain reparse points"));
                }
            }
        }
    }
    Ok(())
}
fn sync_dir(path: &Path) -> Result<()> {
    #[cfg(unix)]
    std::fs::File::open(
        path.parent()
            .ok_or(BudgetError("budget directory unavailable"))?,
    )
    .and_then(|file| file.sync_all())
    .map_err(|_| BudgetError("budget directory sync failed"))?;
    #[cfg(not(unix))]
    let _ = path; // std cannot portably flush a Windows directory.
    Ok(())
}

fn lease(path: &Path) -> Result<af_entropy::FileLease> {
    let lock = path.with_extension("budget-lock");
    plain(&lock)?;
    af_entropy::FileLease::acquire(&lock, true, "budget is busy")
        .map_err(|_| BudgetError("budget lease unavailable"))
}

/// An opaque one-time transition receipt. It is not a retry token or a provider
/// idempotency key; after a crash Sent stays a hold even if this value is lost.
#[derive(Debug)]
pub struct SendPermit {
    call_id: String,
    task_id: String,
}
impl SendPermit {
    pub fn call_id(&self) -> &str {
        &self.call_id
    }
    pub fn task_id(&self) -> &str {
        &self.task_id
    }
}

pub struct Ledger {
    path: PathBuf,
    owner: String,
    poisoned: bool,
}
impl Ledger {
    pub fn create(path: &Path, owner: &str, cap: Money, max_calls: usize) -> Result<Self> {
        plain(path)?;
        if !identity(owner) || cap.0 == 0 || !(1..=MAX_CALLS).contains(&max_calls) {
            return Err(BudgetError("invalid budget owner or bounds"));
        }
        let parent = path
            .parent()
            .ok_or(BudgetError("budget parent unavailable"))?;
        std::fs::create_dir_all(parent)
            .map_err(|_| BudgetError("budget directory creation failed"))?;
        plain(path)?;
        let _lease = lease(path)?;
        let record = Record {
            schema_version: 1,
            sequence: 1,
            owner: owner.into(),
            cap,
            max_calls,
            operation: Operation::Created,
        };
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .map_err(|_| BudgetError("budget already exists or cannot be created"))?;
        let mut raw =
            serde_json::to_vec(&record).map_err(|_| BudgetError("budget encoding failed"))?;
        raw.push(b'\n');
        file.write_all(&raw)
            .and_then(|_| file.sync_all())
            .map_err(|_| BudgetError("budget create persistence failed"))?;
        sync_dir(path)?;
        Ok(Self {
            path: path.into(),
            owner: owner.into(),
            poisoned: false,
        })
    }
    pub fn open(path: &Path, owner: &str) -> Result<Self> {
        plain(path)?;
        if !identity(owner) {
            return Err(BudgetError("invalid budget owner"));
        }
        let ledger = Self {
            path: path.into(),
            owner: owner.into(),
            poisoned: false,
        };
        ledger.snapshot()?;
        Ok(ledger)
    }
    pub fn snapshot(&self) -> Result<Snapshot> {
        if self.poisoned {
            return Err(BudgetError(
                "budget write uncertain; close and inspect durable state",
            ));
        }
        plain(&self.path)?;
        let _lease = lease(&self.path)?;
        self.load()
    }
    fn load(&self) -> Result<Snapshot> {
        let file =
            std::fs::File::open(&self.path).map_err(|_| BudgetError("budget unavailable"))?;
        if file
            .metadata()
            .map_err(|_| BudgetError("budget stat failed"))?
            .len()
            > MAX_BYTES
        {
            return Err(BudgetError("budget size limit exceeded"));
        }
        let mut reader = std::io::BufReader::new(file);
        let mut state: Option<Snapshot> = None;
        let mut total = 0;
        loop {
            let mut raw = Vec::new();
            // take() bounds allocation even if a malformed line exceeds limits.
            use std::io::Read;
            let size = reader
                .by_ref()
                .take((MAX_RECORD + 1) as u64)
                .read_until(b'\n', &mut raw)
                .map_err(|_| BudgetError("budget read failed"))?;
            if size == 0 {
                break;
            }
            total += size;
            if size > MAX_RECORD || total as u64 > MAX_BYTES || raw.last() != Some(&b'\n') {
                return Err(BudgetError(
                    "budget interrupted tail or record bound exceeded; no automatic recovery",
                ));
            }
            let row: Record = serde_json::from_slice(&raw)
                .map_err(|_| BudgetError("strict budget schema invalid"))?;
            if row.schema_version != 1
                || row.owner != self.owner
                || row.cap.0 == 0
                || !(1..=MAX_CALLS).contains(&row.max_calls)
            {
                return Err(BudgetError("budget schema, owner or bounds changed"));
            }
            if state.is_none() {
                if row.sequence != 1 || !matches!(row.operation, Operation::Created) {
                    return Err(BudgetError("missing budget creation"));
                }
                state = Some(Snapshot {
                    owner: row.owner,
                    cap: row.cap,
                    max_calls: row.max_calls,
                    calls: BTreeMap::new(),
                    sequence: 1,
                    send_checkpoint: false,
                });
            } else {
                let saved = state.as_mut().unwrap();
                if row.sequence != saved.sequence + 1
                    || row.cap != saved.cap
                    || row.max_calls != saved.max_calls
                {
                    return Err(BudgetError("budget sequence or immutable limits changed"));
                }
                apply(saved, &row.operation)?;
                saved.sequence = row.sequence;
            }
        }
        let mut state = state.ok_or(BudgetError("empty budget file"))?;
        state.send_checkpoint = self.path.with_extension("send-hold").exists();
        Ok(state)
    }
    fn transition(&mut self, operation: Operation) -> Result<()> {
        if self.poisoned {
            return Err(BudgetError(
                "budget persistence uncertain; no effects authorized",
            ));
        }
        plain(&self.path)?;
        let _lease = lease(&self.path)?;
        let mut state = self.load()?;
        apply(&mut state, &operation)?;
        let send_identity = match &operation {
            Operation::BeginSend { call_id, task_id } => Some((call_id.clone(), task_id.clone())),
            _ => None,
        };
        let settles = matches!(&operation, Operation::Settle { .. });
        let record = Record {
            schema_version: 1,
            sequence: state
                .sequence
                .checked_add(1)
                .ok_or(BudgetError("sequence overflow"))?,
            owner: self.owner.clone(),
            cap: state.cap,
            max_calls: state.max_calls,
            operation,
        };
        let mut raw =
            serde_json::to_vec(&record).map_err(|_| BudgetError("budget encoding failed"))?;
        raw.push(b'\n');
        if raw.len() > MAX_RECORD
            || std::fs::metadata(&self.path)
                .map_err(|_| BudgetError("budget stat failed"))?
                .len()
                + raw.len() as u64
                > MAX_BYTES
        {
            return Err(BudgetError("budget persistence bound exhausted"));
        }
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&self.path)
            .map_err(|_| BudgetError("budget append unavailable"))?;
        if file.write_all(&raw).and_then(|_| file.sync_all()).is_err() {
            self.poisoned = true;
            return Err(BudgetError(
                "budget append uncertain; no send/replay authorized",
            ));
        }
        if let Some((call, task)) = send_identity {
            let hold = self.path.with_extension("send-hold");
            plain(&hold)?;
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&hold)
                .map_err(|_| BudgetError("send checkpoint creation failed; no send authorized"))?;
            // No provider prompt, headers, keys, URL or raw response enters storage.
            let bytes = serde_json::to_vec(&(1u32, &self.owner, &call, &task))
                .map_err(|_| BudgetError("send checkpoint encoding failed"))?;
            file.write_all(&bytes)
                .and_then(|_| file.sync_all())
                .map_err(|_| {
                    BudgetError("send checkpoint persistence failed; no send authorized")
                })?;
            sync_dir(&hold)?;
        }
        if settles {
            let hold = self.path.with_extension("send-hold");
            if hold.exists() {
                std::fs::remove_file(&hold).map_err(|_| {
                    BudgetError("settlement checkpoint cleanup failed; hold retained")
                })?;
                sync_dir(&hold)?;
            }
        }
        Ok(())
    }
    pub fn reserve(&mut self, call: &str, task: &str, amount: Money) -> Result<()> {
        self.transition(Operation::Reserve {
            call_id: call.into(),
            task_id: task.into(),
            amount,
        })
    }
    pub fn begin_send(&mut self, call: &str, task: &str) -> Result<SendPermit> {
        self.transition(Operation::BeginSend {
            call_id: call.into(),
            task_id: task.into(),
        })?;
        Ok(SendPermit {
            call_id: call.into(),
            task_id: task.into(),
        })
    }
    pub fn mark_unknown(&mut self, call: &str, task: &str) -> Result<()> {
        self.transition(Operation::Unknown {
            call_id: call.into(),
            task_id: task.into(),
        })
    }
    pub fn settle(&mut self, call: &str, task: &str, actual: Money) -> Result<()> {
        self.transition(Operation::Settle {
            call_id: call.into(),
            task_id: task.into(),
            actual,
        })?;
        Ok(())
    }
    pub fn release_not_sent(&mut self, call: &str, task: &str) -> Result<()> {
        self.transition(Operation::NotSent {
            call_id: call.into(),
            task_id: task.into(),
        })
    }
}

fn apply(state: &mut Snapshot, operation: &Operation) -> Result<()> {
    let (call_id, task_id) = match operation {
        Operation::Reserve {
            call_id, task_id, ..
        }
        | Operation::BeginSend { call_id, task_id }
        | Operation::Unknown { call_id, task_id }
        | Operation::Settle {
            call_id, task_id, ..
        }
        | Operation::NotSent { call_id, task_id } => (call_id, task_id),
        Operation::Created => return Err(BudgetError("duplicate budget creation")),
    };
    if !identity(call_id) || !identity(task_id) {
        return Err(BudgetError("invalid call or task identity"));
    }
    if let Operation::Reserve { amount, .. } = operation {
        if state.has_hold()
            || state.calls.len() >= state.max_calls
            || state.calls.contains_key(call_id)
        {
            return Err(BudgetError(
                "hold, duplicate call or call cap blocks reservation",
            ));
        }
        if state.committed()?.checked_add(*amount)? > state.cap {
            return Err(BudgetError("reservation exceeds cap"));
        }
        state.calls.insert(
            call_id.clone(),
            Call {
                task_id: task_id.clone(),
                reserved: *amount,
                status: Status::Reserved,
                actual: None,
            },
        );
        return Ok(());
    }
    let has_hold = state.has_hold();
    let call = state
        .calls
        .get_mut(call_id)
        .ok_or(BudgetError("original reservation required"))?;
    if call.task_id != *task_id {
        return Err(BudgetError("call belongs to a different task"));
    }
    match operation {
        Operation::BeginSend { .. } if call.status == Status::Reserved && !has_hold => {
            call.status = Status::Sent
        }
        Operation::Unknown { .. } if call.status == Status::Sent => call.status = Status::Unknown,
        Operation::Settle { actual, .. } if call.status == Status::Sent => {
            call.status = Status::Settled;
            call.actual = Some(*actual);
        }
        Operation::NotSent { .. } if call.status == Status::Reserved => {
            call.status = Status::NotSent
        }
        _ => {
            return Err(BudgetError(
                "invalid transition; unknown or sent calls never replay or automatically clear",
            ))
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn path() -> PathBuf {
        let directory = std::env::temp_dir().join(format!("af-budget-{}", af_entropy::token()));
        std::fs::create_dir_all(&directory).unwrap();
        directory.join("ledger.jsonl")
    }
    #[test]
    fn exact_cap_checked_integer_money_and_owner_are_enforced() {
        assert!(Money(u64::MAX).checked_add(Money(1)).is_err());
        assert!(Money(0).checked_sub(Money(1)).is_err());
        let path = path();
        let mut ledger = Ledger::create(&path, "owner2", Money(10), 3).unwrap();
        ledger.reserve("call1", "task1", Money(10)).unwrap();
        assert!(ledger.reserve("call2", "task1", Money(1)).is_err());
        assert!(Ledger::open(&path, "different_owner").is_err());
        assert!(ledger.begin_send("call1", "wrong_task").is_err());
        assert_eq!(ledger.snapshot().unwrap().committed().unwrap(), Money(10));
    }
    #[test]
    fn after_send_crash_is_durable_and_never_replayed_or_released() {
        let path = path();
        let mut ledger = Ledger::create(&path, "owner2", Money(100), 4).unwrap();
        ledger.reserve("call1", "task1", Money(10)).unwrap();
        let permit = ledger.begin_send("call1", "task1").unwrap();
        assert_eq!(permit.call_id(), "call1");
        drop(ledger);
        let mut ledger = Ledger::open(&path, "owner2").unwrap();
        assert!(ledger.snapshot().unwrap().has_hold());
        assert!(ledger.begin_send("call1", "task1").is_err());
        assert!(ledger.release_not_sent("call1", "task1").is_err());
        assert!(ledger.reserve("call2", "task1", Money(1)).is_err());
        ledger.mark_unknown("call1", "task1").unwrap();
        assert!(ledger.settle("call1", "task1", Money(0)).is_err());
        assert!(Ledger::open(&path, "owner2")
            .unwrap()
            .snapshot()
            .unwrap()
            .has_hold());
    }
    #[test]
    fn known_actual_over_estimate_is_retained_and_cap_overrun_holds() {
        let path = path();
        let mut ledger = Ledger::create(&path, "owner2", Money(10), 4).unwrap();
        ledger.reserve("call1", "task1", Money(3)).unwrap();
        ledger.begin_send("call1", "task1").unwrap();
        ledger.settle("call1", "task1", Money(11)).unwrap();
        let state = ledger.snapshot().unwrap();
        assert_eq!(state.committed().unwrap(), Money(11));
        assert!(state.has_hold());
        assert!(ledger.reserve("call2", "task1", Money(0)).is_err());
    }
    #[test]
    fn settlement_exact_identity_and_pre_send_release_do_not_refresh_call_cap() {
        let path = path();
        let mut ledger = Ledger::create(&path, "owner2", Money(10), 2).unwrap();
        ledger.reserve("call1", "task1", Money(10)).unwrap();
        ledger.release_not_sent("call1", "task1").unwrap();
        assert!(ledger.reserve("call1", "task1", Money(1)).is_err());
        ledger.reserve("call2", "task1", Money(10)).unwrap();
        ledger.begin_send("call2", "task1").unwrap();
        ledger.settle("call2", "task1", Money(5)).unwrap();
        assert!(!ledger.snapshot().unwrap().has_hold());
        assert!(ledger.settle("call2", "task1", Money(5)).is_err());
        assert!(ledger.reserve("call3", "task1", Money(0)).is_err());
    }
    #[test]
    fn partial_line_and_unknown_fields_block_reopen_without_truncation() {
        let path = path();
        let _ = Ledger::create(&path, "owner2", Money(10), 2).unwrap();
        let original = std::fs::read(&path).unwrap();
        let mut raw = original.clone();
        raw.extend_from_slice(b"{\"unfinished\":");
        std::fs::write(&path, &raw).unwrap();
        assert!(Ledger::open(&path, "owner2").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), raw);
        let mut row: serde_json::Value = serde_json::from_slice(&original).unwrap();
        row["api_key"] = serde_json::json!("must not deserialize");
        let mut raw = serde_json::to_vec(&row).unwrap();
        raw.push(b'\n');
        std::fs::write(&path, raw).unwrap();
        assert!(Ledger::open(&path, "owner2").is_err());
    }
    #[test]
    fn uncertain_terminal_append_cannot_remove_the_send_checkpoint() {
        let path = path();
        let mut ledger = Ledger::create(&path, "owner2", Money(10), 2).unwrap();
        ledger.reserve("call1", "task1", Money(5)).unwrap();
        ledger.begin_send("call1", "task1").unwrap();
        // Model a terminal write reaching disk while the acknowledgement was
        // lost: durable checkpoint is retained independently of the log row.
        let mut row: serde_json::Value = serde_json::from_str(
            std::fs::read_to_string(&path)
                .unwrap()
                .lines()
                .last()
                .unwrap(),
        )
        .unwrap();
        row["sequence"] = serde_json::json!(4);
        row["operation"] =
            serde_json::json!({"kind":"settle","call_id":"call1","task_id":"task1","actual":4});
        let mut raw = serde_json::to_vec(&row).unwrap();
        raw.push(b'\n');
        std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(&raw)
            .unwrap();
        let mut reopened = Ledger::open(&path, "owner2").unwrap();
        assert!(reopened.snapshot().unwrap().has_hold());
        assert!(reopened.reserve("call2", "task1", Money(1)).is_err());
    }

    #[test]
    fn cooperating_owners_of_same_ledger_cannot_oversubscribe_stale_state() {
        let path = path();
        Ledger::create(&path, "owner2", Money(10), 4).unwrap();
        let gate = std::sync::Arc::new(std::sync::Barrier::new(2));
        let threads: Vec<_> = (0..2)
            .map(|i| {
                let path = path.clone();
                let gate = gate.clone();
                std::thread::spawn(move || {
                    let mut ledger = Ledger::open(&path, "owner2").unwrap();
                    gate.wait();
                    ledger
                        .reserve(&format!("call{i}"), "task1", Money(7))
                        .is_ok()
                })
            })
            .collect();
        assert_eq!(
            threads
                .into_iter()
                .map(|thread| thread.join().unwrap())
                .filter(|ok| *ok)
                .count(),
            1
        );
        assert_eq!(
            Ledger::open(&path, "owner2")
                .unwrap()
                .snapshot()
                .unwrap()
                .committed()
                .unwrap(),
            Money(7)
        );
    }

    #[test]
    fn currency_input_refuses_floats_and_storage_has_no_prompt_or_credentials_schema() {
        assert!(serde_json::from_str::<Money>("0.5").is_err());
        assert!(serde_json::from_str::<Money>("-1").is_err());
        let row = r#"{"schema_version":1,"sequence":1,"owner":"owner2","cap":10,"max_calls":2,"operation":{"kind":"created","api_key":"secret"}}"#;
        assert!(serde_json::from_str::<Record>(row).is_err());
        let exact = Money::from_usd_str("0.035931375").unwrap();
        assert_eq!(exact, Money(35_931_375));
        assert_eq!(exact.usd_string(), "0.035931375");
        assert_eq!(Money::from_usd_str("1.20").unwrap().usd_string(), "1.2");
        for invalid in [
            "-1",
            "+1",
            "01",
            "1.",
            ".1",
            "0.0000000001",
            "1e2",
            "NaN",
            "18446744074",
        ] {
            assert!(Money::from_usd_str(invalid).is_err(), "{invalid}");
        }
    }
}
