//! af-sandbox: explicit host-approved sandboxed workspaces for atomic-flow —
//! port of the Python draft's `workspace.py`. Root registry, root-qualified
//! read proofs with redaction, bounded previews and single-root recoverable
//! publication. Source/config/model content never creates grants.

pub mod admission;
pub mod recovery;
pub mod redact;
pub mod registry;
pub mod session;

pub use registry::{user_dir, Lease, RootRecord, SandboxRegistry, MAX_ROOTS, MAX_WRITE_PATHS};
pub use session::{manifest, SandboxSession};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SandboxError(pub String);

pub type SandboxResult<T> = Result<T, SandboxError>;

/// Same suffix set as `tree/defaults.py::SOURCE_SUFFIXES` (lowercase, with dot).
pub const SOURCE_SUFFIXES: [&str; 16] = [
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".md",
    ".yaml", ".yml", ".json",
];

/// Minimal `fnmatch` for the ignore patterns actually in use (`*`, `?`).
pub fn glob_match(pattern: &str, name: &str) -> bool {
    fn inner(p: &[u8], n: &[u8]) -> bool {
        match (p.first(), n.first()) {
            (None, None) => true,
            (Some(b'*'), _) => inner(&p[1..], n) || (!n.is_empty() && inner(p, &n[1..])),
            (Some(b'?'), Some(_)) => inner(&p[1..], &n[1..]),
            (Some(a), Some(b)) if a.eq_ignore_ascii_case(b) => inner(&p[1..], &n[1..]),
            _ => false,
        }
    }
    inner(pattern.as_bytes(), name.as_bytes())
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

pub fn sha1_hex(bytes: &[u8]) -> String {
    use sha1::{Digest, Sha1};
    let mut hasher = Sha1::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn glob_matches_ignore_patterns() {
        assert!(glob_match("*.egg-info", "pkg.egg-info"));
        assert!(glob_match("*.min.js", "app.min.js"));
        assert!(!glob_match("*.min.js", "app.js"));
        assert!(glob_match(".git", ".git"));
        assert!(!glob_match("*.egg-info", "pkg"));
    }

    #[test]
    fn identifiers_follow_the_contract() {
        use registry::valid_identifier;
        assert!(valid_identifier("default"));
        assert!(valid_identifier("root-1_A"));
        assert!(!valid_identifier("1root"));
        assert!(!valid_identifier("has space"));
        assert!(!valid_identifier(""));
    }
}
