//! Immutable host-owned target-test contract (port of `check_spec.py`), used
//! by the admitted check executor. Construction binds a contract; it does not
//! attest provenance, run tests, prove sandbox availability or establish
//! independent requirement success. There is deliberately no model-facing
//! loader, shell, environment or mount API.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::collections::BTreeSet;

const INTERPRETERS: [&str; 3] = [
    "/opt/miniconda3/envs/testbed/bin/python",
    "/usr/local/bin/python",
    "/usr/bin/python3",
];

/// Exact private runtime identity (port of the codex `check_runtime.py`
/// binding core). The Docker-inspect byte binding stays host-side in Python;
/// this contract consumes the identity and its digest.
#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeIdentity {
    pub base_image_id: String,
    pub image_id: String,
    pub interpreter: String,
    pub private_prefix: String,
    pub launcher: String,
    pub python_version: (u32, u32, u32),
    pub runner: String,
}

impl RuntimeIdentity {
    pub fn validate(&self) -> Result<(), String> {
        for (name, value) in [("base_image_id", &self.base_image_id), ("image_id", &self.image_id)] {
            let digest = value.rsplit(':').next().unwrap_or_default();
            if !exact_digest(digest, 64) {
                return Err(format!("{name} must be an exact image name at SHA256 digest"));
            }
        }
        if self.interpreter.is_empty() || !self.interpreter.starts_with('/') {
            return Err("interpreter must be an absolute observed path".into());
        }
        if self.private_prefix.is_empty() || self.launcher.is_empty() {
            return Err("private runtime requires a private prefix and a launcher".into());
        }
        if self.runner != "pytest" && self.runner != "sympy-native" {
            return Err("unreviewed runner family".into());
        }
        if self.runner == "sympy-native" && self.python_version == (2, 7, 0) {
            return Err("Python2 native SymPy has no reviewed runner".into());
        }
        Ok(())
    }

    /// Eligible means the private image is distinct from its base and the
    /// private prefix/launcher are bound.
    pub fn execution_eligible(&self) -> bool {
        self.image_id != self.base_image_id
            && !self.private_prefix.is_empty()
            && !self.launcher.is_empty()
    }

    pub fn require_execution_eligible(&self) -> Result<(), String> {
        if !self.execution_eligible() {
            return Err("private runtime identity is not execution-eligible".into());
        }
        Ok(())
    }

    pub fn digest(&self) -> String {
        let document = json!({
            "base_image_id": self.base_image_id,
            "image_id": self.image_id,
            "interpreter": self.interpreter,
            "launcher": self.launcher,
            "private_prefix": self.private_prefix,
            "python_version": [self.python_version.0, self.python_version.1, self.python_version.2],
            "runner": self.runner,
        });
        let mut sorted = document;
        sort_json_in_place(&mut sorted);
        let mut hasher = Sha256::new();
        hasher.update(serde_json::to_vec(&sorted).unwrap_or_default());
        format!("{:x}", hasher.finalize())
    }
}

/// pytest-self runner identity (port of the codex `check_pytest_self.py`).
#[derive(Debug, Clone, PartialEq)]
pub struct PytestSelfIdentity {
    pub python_version: (u32, u32, u32),
    pub pytest_version: String,
    pub pytest_entrypoint: String,
    pub traceback: String,
    pub source_sha256: Vec<(String, String)>,
}

impl PytestSelfIdentity {
    fn validate(&self) -> Result<(), String> {
        let (major, minor, _) = self.python_version;
        if major != 3 || minor < 8 {
            return Err("pytest self requires Python3.8+".into());
        }
        if self.traceback != "auto" && self.traceback != "short" {
            return Err("unreviewed traceback profile".into());
        }
        if self.pytest_version.is_empty() || self.pytest_entrypoint.is_empty() {
            return Err("pytest self requires an exact pytest version and entrypoint".into());
        }
        for (path, digest) in &self.source_sha256 {
            if !valid_test_path(path) {
                return Err("invalid protected runner path".into());
            }
            if !exact_digest(digest, 64) {
                return Err("invalid protected runner digest".into());
            }
        }
        Ok(())
    }
}

/// Private pytest identity bound to its exact task/runtime (port of the
/// codex `check_private_pytest.py` binding core).
#[derive(Debug, Clone, PartialEq)]
pub struct PrivatePytestIdentity {
    pub task_id: String,
    pub variant: String,
    pub runtime_digest: String,
    pub python_version: (u32, u32, u32),
    pub plugin_args: Vec<String>,
    /// (test node id, declared outcome) for optional historical outcomes.
    pub optional_test_outcomes: Vec<(String, String)>,
}

pub const SYMPY_RUNNER_PATHS: [&str; 3] = ["bin/get_sympy.py", "bin/test", "sympy/utilities/runtests.py"];

fn valid_task_id(value: &str) -> bool {
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphanumeric() => {}
        _ => return false,
    }
    value.len() <= 128 && value[1..].bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn valid_path_id(value: &str) -> bool {
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphanumeric() || c == '_' => {}
        _ => return false,
    }
    value[1..].bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'/' | b'-'))
}

fn exact_digest(value: &str, size: usize) -> bool {
    value.len() == size && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn valid_test_path(value: &str) -> bool {
    if !valid_path_id(value) || !value.ends_with(".py") {
        return false;
    }
    value.split('/').all(|part| !part.is_empty() && part != "." && part != ".." && !part.starts_with('.') && !part.starts_with('-'))
}

/// At most the existing checks profile; independent oracle bounds differ.
#[derive(Debug, Clone, PartialEq)]
pub struct CheckResources {
    pub memory_bytes: u64,
    pub cpus: u32,
    pub pids: u32,
    pub tmpfs_bytes: u64,
    pub output_bytes_per_stream: u64,
}

impl Default for CheckResources {
    fn default() -> Self {
        CheckResources {
            memory_bytes: 1024 * 1024 * 1024,
            cpus: 2,
            pids: 128,
            tmpfs_bytes: 128 * 1024 * 1024,
            output_bytes_per_stream: 16 * 1024,
        }
    }
}

impl CheckResources {
    fn validate(&self) -> Result<(), String> {
        let caps = [
            ("memory_bytes", self.memory_bytes, 1024u64 * 1024 * 1024),
            ("tmpfs_bytes", self.tmpfs_bytes, 128 * 1024 * 1024),
            ("output_bytes_per_stream", self.output_bytes_per_stream, 1_000_000),
        ];
        for (name, value, maximum) in caps {
            if value == 0 || value > maximum {
                return Err(format!("{name} must be an integer between1 and{maximum}"));
            }
        }
        for (name, value, maximum) in [("cpus", self.cpus as u64, 2u64), ("pids", self.pids as u64, 128)] {
            if value == 0 || value > maximum {
                return Err(format!("{name} must be an integer between1 and{maximum}"));
            }
        }
        if self.output_bytes_per_stream < 1024 {
            return Err("output bound must be at least1024 bytes".into());
        }
        Ok(())
    }

    fn as_value(&self) -> Value {
        json!({
            "cpus": self.cpus, "memory_bytes": self.memory_bytes,
            "output_bytes_per_stream": self.output_bytes_per_stream,
            "pids": self.pids, "tmpfs_bytes": self.tmpfs_bytes,
        })
    }
}

/// Host-admitted native runner bytes and exact runtime, not an attestation.
#[derive(Debug, Clone, PartialEq)]
pub struct SymPyRunnerIdentity {
    pub python_version: (u32, u32, u32),
    pub sympy_version: String,
    /// (path, sha256) sorted exactly as SYMPY_RUNNER_PATHS.
    pub source_sha256: Vec<(String, String)>,
}

impl SymPyRunnerIdentity {
    fn validate(&self) -> Result<(), String> {
        let (major, minor, patch) = self.python_version;
        if major != 3 || !(8..=99).contains(&minor) || patch > 999 {
            return Err("native SymPy requires an exact Python3.8+ version".into());
        }
        let version_ok = !self.sympy_version.is_empty()
            && self.sympy_version.len() <= 128
            && self.sympy_version.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
            && self.sympy_version.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'+' | b'-'));
        if !version_ok {
            return Err("native SymPy requires an exact bounded package version".into());
        }
        if self.source_sha256.len() != SYMPY_RUNNER_PATHS.len()
            || self
                .source_sha256
                .iter()
                .zip(SYMPY_RUNNER_PATHS)
                .any(|((path, _), expected)| path != expected)
        {
            return Err("native SymPy requires the complete sorted runner byte inventory".into());
        }
        for (path, digest) in &self.source_sha256 {
            if path == "sympy/utilities/runtests.py" && !exact_digest(digest, 64) {
                return Err("native runner source must be an exact lowercase SHA digest".into());
            }
            if !exact_digest(digest, 64) {
                return Err("native runner source must be an exact lowercase SHA digest".into());
            }
            let _ = path;
        }
        Ok(())
    }

    fn as_value(&self) -> Value {
        json!({
            "python_version": [self.python_version.0, self.python_version.1, self.python_version.2],
            "source_sha256": self.source_sha256.iter()
                .map(|(path, digest)| json!({path.clone(): digest.clone()}))
                .collect::<Vec<_>>(),
            "sympy_version": self.sympy_version,
        })
    }
}

/// A host-admitted exact runner/image/source obligation, never a model
/// command. `task_commit` identifies the frozen task definition;
/// `base_commit` identifies target source ancestry; `source_revision`
/// identifies the actual edited snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct CheckSpec {
    pub task_id: String,
    pub task_commit: String,
    pub base_commit: String,
    pub source_revision: String,
    pub image: String,
    pub interpreter: String,
    pub runner: String,
    pub test_paths: Vec<String>,
    pub target_root: String,
    pub timeout_seconds: u32,
    pub resources: CheckResources,
    pub sympy_identity: Option<SymPyRunnerIdentity>,
    pub runtime_identity: Option<RuntimeIdentity>,
    pub pytest_self_identity: Option<PytestSelfIdentity>,
    pub private_pytest_identity: Option<PrivatePytestIdentity>,
}

fn valid_image(value: &str) -> bool {
    // [a-z0-9]+([._-][a-z0-9]+)*(:port)?(/segment)*@sha256:[0-9a-f]{64}
    let Some((reference, digest)) = value.split_once("@sha256:") else { return false };
    if !exact_digest(digest, 64) {
        return false;
    }
    let (mut segments, port) = match reference.split_once(':') {
        Some((left, right)) if right.chars().all(|c| c.is_ascii_digit()) && !right.is_empty() => {
            (left, Some(right))
        }
        _ => (reference, None),
        // A registry port plus a tag is not admitted by the Python pattern;
        // single colon is either port or nothing here.
    };
    let _ = &mut segments;
    if let Some(port) = port {
        if port.len() > 5 || port.parse::<u32>().is_err() {
            return false;
        }
    }
    segments
        .split('/')
        .all(|segment| {
            !segment.is_empty()
                && segment
                    .replace(|c: char| c == '.' || c == '_' || c == '-', "")
                    .chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        })
}

impl CheckSpec {
    pub fn validate(&self) -> Result<(), String> {
        if !valid_task_id(&self.task_id) {
            return Err("task identity must be a bounded plain identifier".into());
        }
        if !exact_digest(&self.task_commit, 40) {
            return Err("task_commit must be an exact lowercase SHA digest".into());
        }
        if !exact_digest(&self.base_commit, 40) {
            return Err("base_commit must be an exact lowercase SHA digest".into());
        }
        if !exact_digest(&self.source_revision, 64) {
            return Err("source_revision must be an exact lowercase SHA digest".into());
        }
        // Private runtime binding: when a runtime identity is present, the
        // image, interpreter and runner must match it exactly; otherwise the
        // standalone image/interpreter admission rules apply.
        let runtime = self.runtime_identity.clone();
        if let Some(identity) = &runtime {
            identity.validate()?;
            identity.require_execution_eligible()?;
            if self.image != identity.image_id
                || self.interpreter != identity.interpreter
                || self.runner != identity.runner
            {
                return Err(
                    "image, interpreter and runner must match the exact private runtime identity".into(),
                );
            }
        } else {
            if self.image.len() > 512 || !valid_image(&self.image) {
                return Err("image must be an exact image reference at sha256 digest".into());
            }
            if !INTERPRETERS.contains(&self.interpreter.as_str()) {
                return Err("interpreter must be an admitted absolute image path".into());
            }
        }
        if self.runner != "pytest" && self.runner != "sympy-native" && self.runner != "pytest-self" {
            return Err("unreviewed test runner".into());
        }
        if self.target_root != "/workspace" && self.target_root != "/testbed" {
            return Err("unreviewed target root".into());
        }
        if self.test_paths.is_empty() || self.test_paths.len() > 256 {
            return Err("host test obligation must be an immutable bounded tuple".into());
        }
        for path in &self.test_paths {
            if !valid_test_path(path) {
                return Err("test paths must be confined ordinary relative Python files".into());
            }
        }
        if self.test_paths.iter().collect::<BTreeSet<_>>().len() != self.test_paths.len() {
            return Err("duplicate test obligations are not admitted".into());
        }
        if self.runner == "sympy-native" && self.test_paths.iter().any(|path| !path.starts_with("sympy/")) {
            return Err("native SymPy obligations must be under sympy/".into());
        }
        if self.sympy_identity.is_some() && self.runner != "sympy-native" {
            return Err("native SymPy identity belongs only to a native runner specification".into());
        }
        if let Some(identity) = &self.sympy_identity {
            identity.validate()?;
            // Privately bound Python3.6.15 native SymPy requires an eligible
            // runtime identity with the same version.
            if identity.python_version == (3, 6, 15) {
                let Some(runtime) = &runtime else {
                    return Err("Python3.6.15 native SymPy requires an eligible private runtime identity".into());
                };
                if identity.python_version != runtime.python_version {
                    return Err("native SymPy Python version differs from the private runtime identity".into());
                }
            }
        }
        if let Some(self_identity) = &self.pytest_self_identity {
            self_identity.validate()?;
            if self.runner != "pytest-self" {
                return Err("pytest self identity belongs only to a pytest self runner specification".into());
            }
            if let Some(runtime) = &runtime {
                if self_identity.python_version != runtime.python_version {
                    return Err("pytest self Python version differs from the private runtime identity".into());
                }
            }
        }
        if let Some(private) = &self.private_pytest_identity {
            let Some(runtime) = &runtime else {
                return Err("private pytest identity requires its exact private runtime".into());
            };
            if self.runner != "pytest"
                || private.task_id != self.task_id
                || private.runtime_digest != runtime.digest()
                || private.python_version != runtime.python_version
            {
                return Err("private pytest identity differs from its exact task/runtime/runner".into());
            }
            let declared_files: BTreeSet<&str> = private
                .optional_test_outcomes
                .iter()
                .filter_map(|(node, _)| node.split("::").next())
                .collect();
            let allowed: BTreeSet<&str> = self.test_paths.iter().map(String::as_str).collect();
            if !declared_files.is_subset(&allowed) {
                return Err("declared optional outcomes require their complete ordinary test files".into());
            }
        }
        if self.timeout_seconds == 0 || self.timeout_seconds > 180 {
            return Err("timeout_seconds must be an integer between1 and180".into());
        }
        self.resources.validate()?;
        Ok(())
    }

    /// Exact non-shell argv; native execution additionally requires host
    /// runner identity.
    pub fn argv(&self) -> Vec<String> {
        if self.runner == "pytest" {
            if let Some(private) = &self.private_pytest_identity {
                let mut argv = vec![self.interpreter.clone(), "-m".into(), "pytest".into()];
                argv.extend(private.plugin_args.iter().cloned());
                argv.extend(self.test_paths.iter().cloned());
                return argv;
            }
            let mut argv = vec![
                self.interpreter.clone(),
                "-m".into(),
                "pytest".into(),
                "-q".into(),
                "--no-header".into(),
                "-x".into(),
            ];
            argv.extend(self.test_paths.iter().cloned());
            argv
        } else if self.runner == "pytest-self" {
            let mut argv = vec![self.interpreter.clone(), "-m".into(), "pytest".into(), "-rA".into()];
            if self
                .pytest_self_identity
                .as_ref()
                .is_some_and(|identity| identity.traceback == "short")
            {
                argv.push("--tb=short".into());
            }
            argv.extend(self.test_paths.iter().cloned());
            argv
        } else {
            let mut argv = vec![self.interpreter.clone(), "bin/test".into(), "-C".into(), "--verbose".into()];
            argv.extend(self.test_paths.iter().cloned());
            argv
        }
    }

    pub fn as_value(&self) -> Value {
        let mut value = Map::new();
        value.insert("schema_version".into(), json!(1));
        value.insert("kind".into(), json!("host_target_test_spec"));
        value.insert("task_id".into(), json!(self.task_id));
        value.insert("task_commit".into(), json!(self.task_commit));
        value.insert("base_commit".into(), json!(self.base_commit));
        value.insert("source_revision".into(), json!(self.source_revision));
        value.insert("image".into(), json!(self.image));
        value.insert("interpreter".into(), json!(self.interpreter));
        value.insert("runner".into(), json!(self.runner));
        value.insert("test_paths".into(), json!(self.test_paths));
        value.insert("target_root".into(), json!(self.target_root));
        value.insert("timeout_seconds".into(), json!(self.timeout_seconds));
        value.insert("resources".into(), self.resources.as_value());
        value.insert("argv".into(), json!(self.argv()));
        value.insert(
            "controls".into(),
            json!({
                "network": "none", "root_read_only": true, "cap_drop": ["ALL"],
                "no_new_privileges": true, "non_root": true, "automatic_pull": false,
                "provider_environment": false,
            }),
        );
        if let Some(identity) = &self.sympy_identity {
            value.insert("sympy_identity".into(), identity.as_value());
        }
        Value::Object(value)
    }

    /// Canonical bytes: sorted compact JSON (parity with the Python contract).
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut sorted = self.as_value();
        sort_json_in_place(&mut sorted);
        serde_json::to_vec(&sorted).expect("canonical spec is valid JSON")
    }

    pub fn digest(&self) -> String {
        let mut hasher = Sha256::new();
        hasher.update(self.canonical_bytes());
        format!("{:x}", hasher.finalize())
    }
}

fn sort_json_in_place(value: &mut Value) {    match value {
        Value::Object(map) => {
            let keys: Vec<String> = map.keys().cloned().collect();
            let sorted: BTreeMap<String, Value> = keys
                .into_iter()
                .map(|key| {
                    let mut item = map[&key].clone();
                    sort_json_in_place(&mut item);
                    (key, item)
                })
                .collect();
            *map = sorted.into_iter().collect();
        }
        Value::Array(items) => {
            for item in items {
                sort_json_in_place(item);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> CheckSpec {
        CheckSpec {
            task_id: "sympy__sympy-21614".into(),
            task_commit: "a".repeat(40),
            base_commit: "b".repeat(40),
            source_revision: "c".repeat(64),
            image: "p07-oracle@sha256:".to_string() + &"d".repeat(64),
            interpreter: "/usr/local/bin/python".into(),
            runner: "pytest".into(),
            test_paths: vec!["tests/test_basic.py".into()],
            target_root: "/workspace".into(),
            timeout_seconds: 180,
            resources: CheckResources::default(),
            sympy_identity: None,
            runtime_identity: None,
            pytest_self_identity: None,
            private_pytest_identity: None,
        }
    }

    #[test]
    fn valid_spec_and_exact_argv() {
        let spec = spec();
        spec.validate().expect("valid spec");
        assert_eq!(
            spec.argv(),
            vec!["/usr/local/bin/python", "-m", "pytest", "-q", "--no-header", "-x", "tests/test_basic.py"]
        );
    }

    #[test]
    fn unreviewed_runners_interpreters_and_roots_refuse() {
        let mut bad = spec();
        bad.runner = "shell".into();
        assert!(bad.validate().unwrap_err().contains("unreviewed test runner"));
        let mut bad = spec();
        bad.interpreter = "/bin/sh".into();
        assert!(bad.validate().unwrap_err().contains("interpreter"));
        let mut bad = spec();
        bad.target_root = "/".into();
        assert!(bad.validate().unwrap_err().contains("target root"));
        let mut bad = spec();
        bad.timeout_seconds = 181;
        assert!(bad.validate().unwrap_err().contains("timeout_seconds"));
    }

    #[test]
    fn image_requires_digest_and_lowercase() {
        let mut bad = spec();
        bad.image = "p07-oracle:latest".into();
        assert!(bad.validate().unwrap_err().contains("image"));
        let mut bad = spec();
        bad.image = format!("p07-oracle@sha256:{}", "D".repeat(64));
        assert!(bad.validate().unwrap_err().contains("image"));
    }

    #[test]
    fn test_paths_are_confined_and_unique() {
        let mut bad = spec();
        bad.test_paths = vec!["../escape.py".into()];
        assert!(bad.validate().unwrap_err().contains("confined"));
        let mut bad = spec();
        bad.test_paths = vec!["tests/a.py".into(), "tests/a.py".into()];
        assert!(bad.validate().unwrap_err().contains("duplicate"));
        let mut bad = spec();
        bad.test_paths = (0..257).map(|i| format!("tests/t{i}.py")).collect();
        assert!(bad.validate().unwrap_err().contains("bounded tuple"));
    }

    #[test]
    fn sympy_native_binds_to_sympy_paths_and_identity() {
        let mut bad = spec();
        bad.runner = "sympy-native".into();
        bad.interpreter = "/opt/miniconda3/envs/testbed/bin/python".into();
        assert!(bad.validate().unwrap_err().contains("under sympy/"));
        let mut native = spec();
        native.runner = "sympy-native".into();
        native.interpreter = "/opt/miniconda3/envs/testbed/bin/python".into();
        native.test_paths = vec!["sympy/core/tests/test_assumptions.py".into()];
        native.sympy_identity = Some(SymPyRunnerIdentity {
            python_version: (3, 9, 18),
            sympy_version: "1.6.2".into(),
            source_sha256: SYMPY_RUNNER_PATHS
                .iter()
                .map(|path| (path.to_string(), "e".repeat(64)))
                .collect(),
        });
        native.validate().expect("native spec valid");
        assert_eq!(
            native.argv(),
            vec!["/opt/miniconda3/envs/testbed/bin/python", "bin/test", "-C", "--verbose", "sympy/core/tests/test_assumptions.py"]
        );
        // Identity belongs only to the native runner.
        let mut wrong = spec();
        wrong.sympy_identity = native.sympy_identity.clone();
        assert!(wrong.validate().unwrap_err().contains("belongs only"));
    }

    #[test]
    fn canonical_digest_is_stable_and_field_order_independent() {
        let a = spec().digest();
        let mut reordered = spec();
        reordered.test_paths = vec!["tests/test_basic.py".into()];
        let b = reordered.digest();
        assert_eq!(a, b);
        let mut different = spec();
        different.timeout_seconds = 120;
        assert_ne!(a, different.digest());
    }

    #[test]
    fn pytest_self_runner_and_profile() {
        let mut native = spec();
        native.runner = "pytest-self".into();
        native.test_paths = vec!["tests/test_self.py".into()];
        native.validate().expect("pytest-self valid");
        assert_eq!(
            native.argv(),
            vec!["/usr/local/bin/python", "-m", "pytest", "-rA", "tests/test_self.py"]
        );
        native.pytest_self_identity = Some(PytestSelfIdentity {
            python_version: (3, 11, 0),
            pytest_version: "8.3.0".into(),
            pytest_entrypoint: "/usr/local/bin/pytest".into(),
            traceback: "short".into(),
            source_sha256: vec![("bin/pytest_self.py".into(), "e".repeat(64))],
        });
        native.validate().expect("identity valid");
        assert!(native.argv().contains(&"--tb=short".to_string()));
        // Identity belongs only to the pytest-self runner.
        let mut wrong = spec();
        wrong.pytest_self_identity = native.pytest_self_identity;
        assert!(wrong.validate().unwrap_err().contains("belongs only"));
    }

    #[test]
    fn private_runtime_binding_is_exact() {
        let mut bound = spec();
        bound.image = format!("sha256:{}", "1".repeat(64));
        bound.runtime_identity = Some(RuntimeIdentity {
            base_image_id: format!("sha256:{}", "2".repeat(64)),
            image_id: format!("sha256:{}", "1".repeat(64)),
            interpreter: "/usr/local/bin/python".into(),
            private_prefix: "/opt/private".into(),
            launcher: "/opt/private/launch".into(),
            python_version: (3, 11, 6),
            runner: "pytest".into(),
        });
        bound.validate().expect("bound spec valid");
        // Matching image/interpreter/runner is mandatory.
        let mut drifted = bound.clone();
        drifted.image = format!("sha256:{}", "3".repeat(64));
        assert!(drifted
            .validate()
            .unwrap_err()
            .contains("must match the exact private runtime identity"));
        // Ineligible identity (image equals base) is refused.
        let mut ineligible = bound;
        ineligible.runtime_identity.as_mut().unwrap().image_id =
            ineligible.runtime_identity.as_ref().unwrap().base_image_id.clone();
        assert!(ineligible.validate().unwrap_err().contains("execution-eligible"));
    }

    #[test]
    fn private_pytest_identity_binds_task_runtime_and_paths() {
        let runtime = RuntimeIdentity {
            base_image_id: format!("sha256:{}", "2".repeat(64)),
            image_id: format!("sha256:{}", "1".repeat(64)),
            interpreter: "/usr/local/bin/python".into(),
            private_prefix: "/opt/private".into(),
            launcher: "/opt/private/launch".into(),
            python_version: (3, 11, 6),
            runner: "pytest".into(),
        };
        let mut bound = spec();
        bound.image = runtime.image_id.clone();
        bound.runtime_identity = Some(runtime);
        bound.private_pytest_identity = Some(PrivatePytestIdentity {
            task_id: bound.task_id.clone(),
            variant: "2674-v2".into(),
            runtime_digest: bound.runtime_identity.as_ref().unwrap().digest(),
            python_version: (3, 11, 6),
            plugin_args: vec!["-p".into(), "no:cacheprovider".into()],
            optional_test_outcomes: vec![("tests/test_basic.py::test_a".into(), "skip".into())],
        });
        bound.validate().expect("bound private pytest valid");
        assert!(bound.argv().contains(&"no:cacheprovider".to_string()));
        // Optional outcomes outside the ordinary test files are refused.
        let mut drifting = bound.clone();
        drifting.private_pytest_identity.as_mut().unwrap().optional_test_outcomes =
            vec![("tests/other.py::test_x".into(), "skip".into())];
        assert!(drifting
            .validate()
            .unwrap_err()
            .contains("require their complete ordinary test files"));
        // Digest drift is refused.
        let mut digest_drift = bound;
        digest_drift.private_pytest_identity.as_mut().unwrap().runtime_digest = "f".repeat(64);
        assert!(digest_drift
            .validate()
            .unwrap_err()
            .contains("differs from its exact task/runtime/runner"));
    }
}
