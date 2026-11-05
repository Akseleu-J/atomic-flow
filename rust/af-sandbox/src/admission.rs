//! Relative path admission for sandboxed roots (port of the admitted-path
//! contract used by `agents/tools/file_paths.py` and `agents/source.py`):
//! POSIX-style relative paths only, no traversal, no link escape, confined
//! under the granted root.

use crate::registry::ensure_no_links;
use crate::{SandboxError, SandboxResult};
use std::path::{Path, PathBuf};

/// Validate and resolve a root-relative POSIX path. Refuses absolute paths,
/// backslashes, `..`, empty components, drive letters and any link component.
pub fn admit_rel(root: &Path, rel: &str) -> SandboxResult<PathBuf> {
    if rel.is_empty() {
        return Err(SandboxError(
            "path must be a non-empty relative POSIX path".into(),
        ));
    }
    if rel.contains('\\') {
        return Err(SandboxError("path must use POSIX separators".into()));
    }
    if rel.starts_with('/') {
        return Err(SandboxError("path must be relative to the root".into()));
    }
    if rel.contains(':') {
        return Err(SandboxError(
            "drive-qualified paths are not admitted".into(),
        ));
    }
    for component in rel.split('/') {
        if component.is_empty() || component == "." {
            return Err(SandboxError("path components must be non-empty".into()));
        }
        if component == ".." {
            return Err(SandboxError("path traversal is not admitted".into()));
        }
    }
    let joined = rel
        .split('/')
        .fold(root.to_path_buf(), |path, component| path.join(component));
    ensure_no_links(&joined)?;
    ensure_no_links(root)?;
    Ok(joined)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn admits_plain_relative_paths() {
        let base = std::env::temp_dir().join(format!("af-adm-{}", af_entropy::token()));
        std::fs::create_dir_all(&base).unwrap();
        assert_eq!(
            admit_rel(&base, "pkg/mod.py").unwrap(),
            base.join("pkg").join("mod.py")
        );
        assert!(admit_rel(&base, "docs/readme.md").is_ok());
        assert!(admit_rel(&base, "../secret").is_err());
        assert!(admit_rel(&base, "/abs").is_err());
        assert!(admit_rel(&base, "pkg\\mod.py").is_err());
        assert!(admit_rel(&base, "a//b").is_err());
        assert!(admit_rel(&base, "C:evil").is_err());
        assert!(admit_rel(&base, "").is_err());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[cfg(unix)]
    #[test]
    fn nested_directory_link_cannot_escape_on_posix() {
        let base = std::env::temp_dir().join(format!("af-adm-links-{}", af_entropy::token()));
        let root = base.join("root");
        let outside = base.join("outside");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        std::os::unix::fs::symlink(&outside, root.join("pkg")).unwrap();
        assert!(admit_rel(&root, "pkg/secret.rs").is_err());
        std::fs::remove_dir_all(&base).unwrap();
    }
}
