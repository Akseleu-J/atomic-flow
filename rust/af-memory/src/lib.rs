//! af-memory: hybrid agent memory. Memory blocks are the Letta-style layer:
//! named, self-editable slots whose every revision is recorded with
//! provenance (author, reason, prior digest) and whose writes never carry
//! authority — they are evidence, like any model text. File memory is the
//! Manus-style layer: bounded notes under a dedicated memory directory,
//! addressed like any other file and hashed on every read.

use std::path::{Path, PathBuf};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct MemoryError(pub String);

pub type MemoryResult<T> = Result<T, MemoryError>;

fn err<T>(message: impl Into<String>) -> MemoryResult<T> {
    Err(MemoryError(message.into()))
}

fn sha_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

const MAX_BLOCK_CONTENT_BYTES: usize = 32 * 1024;
const MAX_BLOCKS: usize = 64;

/// One self-editable memory block with its revision history.
#[derive(Debug, Clone)]
pub struct MemoryBlock {
    pub id: String,
    pub title: String,
    pub content: String,
    pub revisions: Vec<Value>,
}

/// Block memory over an append-only journal: every write is journaled with
/// provenance, so memory is evidence, never authority. The journal is the
/// store of record; the in-memory blocks are its projection.
pub struct BlockMemory {
    blocks: Vec<MemoryBlock>,
}

impl BlockMemory {
    pub fn new() -> BlockMemory {
        BlockMemory { blocks: Vec::new() }
    }

    pub fn blocks(&self) -> &[MemoryBlock] {
        &self.blocks
    }

    fn find(&self, id: &str) -> MemoryResult<usize> {
        self.blocks
            .iter()
            .position(|block| block.id == id)
            .ok_or_else(|| MemoryError(format!("unknown memory block {id}")))
    }

    /// Create a block; the creation event is returned for the caller's journal.
    pub fn create(&mut self, title: &str, content: &str, author: &str, reason: &str) -> MemoryResult<Value> {
        if self.blocks.len() >= MAX_BLOCKS {
            return err("memory block limit reached");
        }
        if title.trim().is_empty() || title.len() > 128 {
            return err("block title must be bounded non-empty text");
        }
        if content.len() > MAX_BLOCK_CONTENT_BYTES {
            return err("block content exceeds its byte cap");
        }
        let id = format!("mem_{}", af_entropy::token());
        let digest = sha_hex(content.as_bytes());
        let block = MemoryBlock {
            id: id.clone(),
            title: title.to_string(),
            content: content.to_string(),
            revisions: vec![json!({
                "revision": 1, "author": author, "reason": reason,
                "content_sha256": digest, "prior_sha256": Value::Null,
            })],
        };
        self.blocks.push(block);
        Ok(json!({
            "event": "memory_block_created", "id": id, "title": title,
            "content_sha256": digest, "author": author, "reason": reason,
        }))
    }

    /// Self-edit: the model proposes; provenance records who and why. The
    /// prior digest is verified, so a stale read cannot overwrite fresh text.
    pub fn edit(
        &mut self,
        id: &str,
        new_content: &str,
        author: &str,
        reason: &str,
        known_content_sha256: &str,
    ) -> MemoryResult<Value> {
        let index = self.find(id)?;
        let current = sha_hex(self.blocks[index].content.as_bytes());
        if current != known_content_sha256 {
            return err("block changed since it was read; re-read before editing");
        }
        if new_content.len() > MAX_BLOCK_CONTENT_BYTES {
            return err("block content exceeds its byte cap");
        }
        let revision = self.blocks[index].revisions.len() as u64 + 1;
        self.blocks[index].content = new_content.to_string();
        let revision_record = json!({
            "revision": revision, "author": author, "reason": reason,
            "content_sha256": sha_hex(new_content.as_bytes()),
            "prior_sha256": current,
        });
        self.blocks[index].revisions.push(revision_record.clone());
        Ok(json!({
            "event": "memory_block_edited", "id": id, "revision": revision,
            "author": author, "reason": reason,
            "prior_sha256": current, "content_sha256": sha_hex(new_content.as_bytes()),
        }))
    }

    /// Render the blocks for the model prompt (bounded, honest: revisions
    /// count included so the model knows how contested a block is).
    pub fn render(&self, byte_budget: usize) -> Value {
        let mut used = 0usize;
        let mut rendered = Vec::new();
        for block in &self.blocks {
            let entry = json!({
                "id": block.id, "title": block.title,
                "revisions": block.revisions.len(),
                "content": block.content,
            });
            let bytes = serde_json::to_string(&entry).unwrap_or_default().len();
            if used + bytes > byte_budget {
                rendered.push(json!({"id": block.id, "omitted": true, "reason": "byte budget"}));
                continue;
            }
            used += bytes;
            rendered.push(entry);
        }
        json!({"blocks": rendered, "bytes_used": used, "byte_budget": byte_budget})
    }
}

/// File-system-as-memory: bounded notes under a dedicated memory directory.
pub struct FileMemory {
    dir: PathBuf,
}

impl FileMemory {
    pub fn open(dir: &Path) -> MemoryResult<FileMemory> {
        std::fs::create_dir_all(dir)
            .map_err(|error| MemoryError(format!("memory directory failed: {error}")))?;
        Ok(FileMemory { dir: dir.to_path_buf() })
    }

    fn note_path(&self, name: &str) -> MemoryResult<PathBuf> {
        if name.is_empty()
            || name.len() > 96
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || b == b'.')
            || name.contains("..")
        {
            return err("memory note name must be a bounded safe file name");
        }
        Ok(self.dir.join(name))
    }

    /// Write (create or replace) a note; returns its digest for CAS reads.
    pub fn write(&self, name: &str, content: &str) -> MemoryResult<Value> {
        let path = self.note_path(name)?;
        if content.len() > MAX_BLOCK_CONTENT_BYTES {
            return err("memory note exceeds its byte cap");
        }
        std::fs::write(&path, content.as_bytes())
            .map_err(|error| MemoryError(format!("memory note write failed: {error}")))?;
        Ok(json!({"name": name, "sha256": sha_hex(content.as_bytes()), "bytes": content.len()}))
    }

    /// Read a note with its digest; missing notes are explicit, not empty.
    pub fn read(&self, name: &str) -> MemoryResult<Value> {
        let path = self.note_path(name)?;
        let bytes = std::fs::read(&path).map_err(|error| MemoryError(format!("memory note read failed: {error}")))?;
        Ok(json!({
            "name": name,
            "content": String::from_utf8_lossy(&bytes),
            "sha256": sha_hex(&bytes),
        }))
    }

    pub fn list(&self) -> Value {
        let mut names: Vec<String> = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&self.dir) {
            for entry in entries.filter_map(|entry| entry.ok()) {
                if let Some(name) = entry.file_name().to_str() {
                    names.push(name.to_string());
                }
            }
        }
        names.sort();
        json!({"notes": names})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_record_provenance_and_refuse_stale_edits() {
        let mut memory = BlockMemory::new();
        let created = memory.create("user profile", "prefers rust", "agent", "initial intake").unwrap();
        let id = created["id"].as_str().unwrap().to_string();
        let digest = created["content_sha256"].as_str().unwrap().to_string();
        let edited = memory
            .edit(&id, "prefers rust and zero-CT", "agent", "user correction", &digest)
            .unwrap();
        assert_eq!(edited["revision"], json!(2));
        assert_eq!(edited["prior_sha256"], json!(digest));
        // Stale digest cannot overwrite: re-read first.
        let error = memory.edit(&id, "overwritten", "agent", "stale write", &digest).unwrap_err();
        assert!(error.0.contains("changed since it was read"));
        // Provenance chain is complete.
        let block = &memory.blocks()[0];
        assert_eq!(block.revisions.len(), 2);
        assert_eq!(block.revisions[0]["author"], json!("agent"));
        assert_eq!(block.revisions[1]["reason"], json!("user correction"));
    }

    #[test]
    fn block_limits_and_render_budget() {
        let mut memory = BlockMemory::new();
        let created = memory.create("big", &"x".repeat(MAX_BLOCK_CONTENT_BYTES + 1), "agent", "t").unwrap_err();
        assert!(created.0.contains("byte cap"));
        for index in 0..MAX_BLOCKS {
            memory.create(&format!("block {index}"), "content", "agent", "fill").unwrap();
        }
        assert!(memory.create("one more", "c", "agent", "t").is_err(), "block limit enforced");
        let render = memory.render(300);
        assert!(render["blocks"].as_array().unwrap().iter().any(|block| block["omitted"] == json!(true)));
    }

    #[test]
    fn file_memory_is_cas_addressed_and_explicit_about_missing() {
        let dir = std::env::temp_dir().join(format!("af-fmem-{}", af_entropy::token()));
        let memory = FileMemory::open(&dir).unwrap();
        let written = memory.write("task-context.md", "# context\n- item 1\n").unwrap();
        let read = memory.read("task-context.md").unwrap();
        assert_eq!(written["sha256"], read["sha256"]);
        assert!(read["content"].as_str().unwrap().contains("item 1"));
        let error = memory.read("missing.md").unwrap_err();
        assert!(error.0.contains("read failed"));
        // Unsafe names refused.
        assert!(memory.write("../escape.md", "x").is_err());
        assert!(memory.write("a/b.md", "x").is_err());
        let listing = memory.list();
        assert!(listing["notes"].as_array().unwrap().contains(&json!("task-context.md")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn hybrid_layers_are_independent() {
        // The two layers never alias: block edits never touch file notes.
        let mut blocks = BlockMemory::new();
        let dir = std::env::temp_dir().join(format!("af-hmem-{}", af_entropy::token()));
        let files = FileMemory::open(&dir).unwrap();
        let created = blocks.create("state", "v1", "agent", "t").unwrap();
        files.write("state.md", "file-v1").unwrap();
        let digest = created["content_sha256"].as_str().unwrap().to_string();
        blocks.edit(created["id"].as_str().unwrap(), "v2", "agent", "t", &digest).unwrap();
        assert_eq!(files.read("state.md").unwrap()["content"], json!("file-v1"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
