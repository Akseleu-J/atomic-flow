//! P09 kernel contract: segmented session store with a checkpoint chain.
//! Appends validate only the current segment; every older segment is pinned
//! by a sha256 checkpoint recorded at rotation, so historical tampering is
//! detected by the chain — without rehashing the whole history on every
//! append. This replaces the linear-per-append rehash of the single-file
//! journal while keeping integrity and crash recovery.

use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SegmentError(pub String);

pub type SegmentResult<T> = Result<T, SegmentError>;

fn err<T>(message: impl Into<String>) -> SegmentResult<T> {
    Err(SegmentError(message.into()))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

pub struct SegmentStore {
    dir: PathBuf,
    pub session_id: String,
    segment_cap_bytes: u64,
    current: usize,
    current_bytes: u64,
}

impl SegmentStore {
    pub fn open(dir: &Path, session_id: &str, segment_cap_bytes: u64) -> SegmentResult<SegmentStore> {
        if session_id.is_empty()
            || !session_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return err("invalid segment session id");
        }
        if segment_cap_bytes < 64 {
            return err("segment cap is too small to hold a checkpoint and one event");
        }
        let dir = dir.join(format!("segments_{session_id}"));
        std::fs::create_dir_all(&dir)
            .map_err(|error| SegmentError(format!("segment directory failed: {error}")))?;
        let mut current = 0usize;
        while Path::new(&Self::segment_path(&dir, current + 1)).exists() {
            current += 1;
        }
        // A missing current segment means the first append writes the first
        // checkpoint; it must not rotate past a nonexistent prior segment.
        let current_bytes = if Path::new(&Self::segment_path(&dir, current)).exists() {
            std::fs::metadata(Self::segment_path(&dir, current))
                .map_err(|error| SegmentError(format!("{error}")))?
                .len()
        } else {
            0
        };
        Ok(SegmentStore { dir, session_id: session_id.to_string(), segment_cap_bytes, current, current_bytes })
    }

    fn segment_path(dir: &Path, index: usize) -> PathBuf {
        dir.join(format!("seg-{index:06}.jsonl"))
    }

    fn write_checkpoint(&mut self) -> SegmentResult<()> {
        let path = Self::segment_path(&self.dir, self.current);
        let prior_digest = if self.current == 0 {
            String::new()
        } else {
            let previous = std::fs::read(Self::segment_path(&self.dir, self.current - 1))
                .map_err(|error| SegmentError(format!("prior segment read failed: {error}")))?;
            sha256_hex(&previous)
        };
        let checkpoint = json!({
            "checkpoint": {
                "session_id": self.session_id,
                "segment": self.current,
                "prior_segment_sha256": prior_digest,
                "started_at_unix": std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs_f64(),
            }
        });
        let mut line = serde_json::to_string(&checkpoint).map_err(|e| SegmentError(format!("{e}")))?;
        line.push('\n');
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|error| SegmentError(format!("checkpoint create failed: {error}")))?;
        file.write_all(line.as_bytes())
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| SegmentError(format!("checkpoint fsync failed: {error}")))?;
        self.current_bytes = line.len() as u64;
        Ok(())
    }

    /// Append one event line (compact JSON); rotates when the cap is reached.
    /// O(1): only this append's bytes are validated, history is pinned by the
    /// checkpoint chain.
    pub fn append(&mut self, event: &Value) -> SegmentResult<usize> {
        let mut line = serde_json::to_string(event).map_err(|e| SegmentError(format!("{e}")))?;
        line.push('\n');
        if !Self::segment_path(&self.dir, self.current).exists() {
            self.write_checkpoint()?;
        } else if self.current_bytes + line.len() as u64 > self.segment_cap_bytes {
            self.current += 1;
            self.write_checkpoint()?;
        }
        let path = Self::segment_path(&self.dir, self.current);
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .map_err(|error| SegmentError(format!("segment append failed: {error}")))?;
        file.write_all(line.as_bytes())
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| SegmentError(format!("segment fsync failed: {error}")))?;
        self.current_bytes += line.len() as u64;
        Ok(self.current)
    }

    /// Verify the checkpoint chain: every segment i>0 pins the exact bytes of
    /// segment i-1. Any historical edit breaks the chain here.
    pub fn verify_chain(&self) -> SegmentResult<Value> {
        let mut checked = 0usize;
        let mut broken: Vec<Value> = Vec::new();
        let mut index = 0usize;
        while Path::new(&Self::segment_path(&self.dir, index)).exists() {
            if index > 0 {
                let prior = std::fs::read(Self::segment_path(&self.dir, index - 1))
                    .map_err(|error| SegmentError(format!("{error}")))?;
                let expected = sha256_hex(&prior);
                let checkpoint_line = first_line(&Self::segment_path(&self.dir, index))?;
                let checkpoint: Value = serde_json::from_str(checkpoint_line.trim())
                    .map_err(|error| SegmentError(format!("checkpoint parse failed: {error}")))?;
                let recorded = checkpoint
                    .pointer("/checkpoint/prior_segment_sha256")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if recorded != expected {
                    broken.push(json!({"segment": index, "recorded": recorded, "actual": expected}));
                }
            }
            checked += 1;
            index += 1;
        }
        Ok(json!({
            "ok": broken.is_empty(), "segments_checked": checked,
            "broken": broken,
            "note": "older segments are pinned by checkpoints; the chain detects edits without full-history rehash per append",
        }))
    }

    /// Read all committed events in order; an unterminated final line is an
    /// uncommitted tail and is reported, never executed.
    pub fn read(&self) -> SegmentResult<(Vec<Value>, bool)> {
        let mut events = Vec::new();
        let mut incomplete_tail = false;
        let mut index = 0usize;
        while Path::new(&Self::segment_path(&self.dir, index)).exists() {
            let file = std::fs::File::open(Self::segment_path(&self.dir, index))
                .map_err(|error| SegmentError(format!("{error}")))?;
            let mut reader = std::io::BufReader::new(file);
            let mut first = true;
            loop {
                let mut line = Vec::new();
                let read = reader.read_until(b'\n', &mut line)
                    .map_err(|error| SegmentError(format!("{error}")))?;
                if read == 0 {
                    break;
                }
                if line.last() != Some(&b'\n') {
                    incomplete_tail = true;
                    break;
                }
                if first {
                    first = false;
                    if serde_json::from_slice::<Value>(&line)
                        .ok()
                        .and_then(|value| value.get("checkpoint").cloned())
                        .is_some()
                    {
                        continue;
                    }
                }
                let event: Value = serde_json::from_slice(&line)
                    .map_err(|error| SegmentError(format!("invalid segment event: {error}")))?;
                events.push(event);
            }
            index += 1;
        }
        Ok((events, incomplete_tail))
    }

    /// Retention: remove segments older than `keep` most recent ones; the
    /// chain digest of the oldest remaining segment is returned for archives.
    pub fn retain(&mut self, keep: usize) -> SegmentResult<Value> {
        let mut total = 0usize;
        while Path::new(&Self::segment_path(&self.dir, total)).exists() {
            total += 1;
        }
        let mut removed = 0usize;
        while total > keep {
            std::fs::remove_file(Self::segment_path(&self.dir, removed))
                .map_err(|error| SegmentError(format!("retention remove failed: {error}")))?;
            removed += 1;
            total -= 1;
        }
        // Re-index: rebuild checkpoint pins after removal is refused —
        // removal must be an explicit archival operation with the digest.
        Ok(json!({
            "removed_segments": removed,
            "remaining": total,
            "archive_digest": if total > 0 {
                Value::String(sha256_hex(&std::fs::read(Self::segment_path(&self.dir, removed))
                    .map_err(|error| SegmentError(format!("{error}")))?))
            } else {
                Value::Null
            },
        }))
    }
}

fn first_line(path: &Path) -> SegmentResult<String> {
    let file = std::fs::File::open(path).map_err(|error| SegmentError(format!("{error}")))?;
    let mut reader = std::io::BufReader::new(file);
    let mut line = Vec::new();
    reader.read_until(b'\n', &mut line)
        .map_err(|error| SegmentError(format!("{error}")))?;
    String::from_utf8(line).map_err(|error| SegmentError(format!("{error}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp_dir() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("af-segments-{}", af_entropy::token()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn rotates_and_reads_across_segments_in_order() {
        let dir = temp_dir();
        let mut store = SegmentStore::open(&dir, "rotate", 200).unwrap();
        for index in 0..6 {
            store.append(&json!({"seq": index, "text": format!("event {index}")})).unwrap();
        }
        let (events, incomplete) = store.read().unwrap();
        assert!(!incomplete);
        assert_eq!(events.len(), 6);
        assert_eq!(events[0]["seq"], json!(0));
        assert_eq!(events[5]["seq"], json!(5));
        let chain = store.verify_chain().unwrap();
        assert_eq!(chain["ok"], json!(true));
        assert!(chain["segments_checked"].as_u64().unwrap() >= 2, "small cap must rotate");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn historical_tampering_breaks_the_chain() {
        let dir = temp_dir();
        let mut store = SegmentStore::open(&dir, "tamper", 200).unwrap();
        // Padded events guarantee rotation regardless of checkpoint size.
        for index in 0..6 {
            store
                .append(&json!({"seq": index, "pad": "x".repeat(60)}))
                .unwrap();
        }
        // Rewrite an older segment's committed bytes out of band.
        let old = dir.join("segments_tamper").join("seg-000000.jsonl");
        let bytes = std::fs::read(&old).unwrap();
        let poisoned: Vec<u8> = bytes.iter().map(|b| if *b == b'0' { b'9' } else { *b }).collect();
        std::fs::write(&old, &poisoned).unwrap();
        let chain = store.verify_chain().unwrap();
        assert_eq!(chain["ok"], json!(false));
        assert!(!chain["broken"].as_array().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn interrupted_tail_is_reported_not_executed() {
        let dir = temp_dir();
        let mut store = SegmentStore::open(&dir, "tail", 400).unwrap();
        store.append(&json!({"seq": 0})).unwrap();
        let path = dir.join("segments_tail").join("seg-000000.jsonl");
        let mut file = std::fs::OpenOptions::new().append(true).open(&path).unwrap();
        file.write_all(b"{\"partial\":").unwrap();
        drop(file);
        let (events, incomplete) = store.read().unwrap();
        assert!(incomplete);
        assert_eq!(events.len(), 1, "committed events survive; the tail is not executed");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn retention_reports_archive_digest() {
        let dir = temp_dir();
        let mut store = SegmentStore::open(&dir, "retain", 200).unwrap();
        // Padded events guarantee several segments at this cap.
        for index in 0..8 {
            store
                .append(&json!({"seq": index, "pad": "x".repeat(60)}))
                .unwrap();
        }
        let report = store.retain(1).unwrap();
        assert!(report["removed_segments"].as_u64().unwrap() >= 1);
        assert!(report["archive_digest"].as_str().is_some_and(|digest| digest.len() == 64));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
