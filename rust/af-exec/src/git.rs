//! Git-native workflow (Aider/Devin-style) on the kernel: branch-per-task,
//! one auto-commit per applied change, and a publish journey with
//! operation-scoped credentials injected by the host as an environment
//! variable name — never inline tokens. The runner is injected, so tests
//! verify exact argv and message semantics without executing git.

use serde_json::{json, Value};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct GitError(pub String);

pub type GitResult<T> = Result<T, GitError>;

/// The command runner abstraction (real host: std::process; tests: fake).
pub trait CommandRunner {
    fn run(
        &self,
        argv: &[String],
        cwd: &Path,
        extra_env: &[(&str, &str)],
    ) -> Result<(i32, String, String), String>;
}

use std::path::Path;

/// Real runner via std::process::Command.
pub struct ProcessRunner;

impl CommandRunner for ProcessRunner {
    fn run(
        &self,
        argv: &[String],
        cwd: &Path,
        extra_env: &[(&str, &str)],
    ) -> Result<(i32, String, String), String> {
        // Generic inherited authentication is not an operation-scoped grant.
        // Publication requires a separately accepted broker; never pass the
        // parent environment or arbitrary extra variables to a Git child.
        if !extra_env.is_empty() || argv.get(1).is_some_and(|v| v == "push") {
            return Err("Git credential broker is not admitted; publication refused".into());
        }
        let result = crate::process::run(argv, cwd, std::time::Duration::from_secs(30), |_| Ok(()))
            .map_err(|e| e.0)?;
        if !result.cleanup_confirmed
            || result.timed_out
            || result.stdout_truncated
            || result.stderr_truncated
            || result.error.is_some()
        {
            return Err(
                "Git process result incomplete or cleanup uncertain; do not retry effects".into(),
            );
        }
        Ok((
            result.exit_code.unwrap_or(-1),
            String::from_utf8_lossy(&result.stdout).to_string(),
            String::from_utf8_lossy(&result.stderr).to_string(),
        ))
    }
}

pub struct GitWorkflow<'a> {
    pub repo_root: &'a Path,
    pub runner: &'a dyn CommandRunner,
}

impl GitWorkflow<'_> {
    fn git(&self, argv: &[&str], extra_env: &[(&str, &str)]) -> GitResult<String> {
        let mut owned = vec!["git".to_string()];
        owned.extend(argv.iter().map(|part| part.to_string()));
        let (code, stdout, stderr) = self
            .runner
            .run(&owned, self.repo_root, extra_env)
            .map_err(GitError)?;
        if code != 0 {
            return Err(GitError(format!(
                "git {} failed ({code}): {}",
                argv.first().unwrap_or(&""),
                stderr.trim()
            )));
        }
        Ok(stdout)
    }

    /// Branch-per-task: create and switch to `af/task-<name>`; an existing
    /// branch is reused, never reset.
    pub fn ensure_task_branch(&self, task_name: &str) -> GitResult<String> {
        let safe: String = task_name
            .chars()
            .map(|character| {
                if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                    character
                } else {
                    '-'
                }
            })
            .collect();
        let branch = format!("af/task-{safe}");
        // Reuse if present; create otherwise. Failure to list refs is an error.
        let refs = self.git(&["branch", "--list", &branch], &[])?;
        if refs.trim().is_empty() {
            self.git(&["checkout", "-b", &branch], &[])?;
        } else {
            self.git(&["checkout", &branch], &[])?;
        }
        Ok(branch)
    }

    /// One auto-commit per applied change: stage exactly the changed paths
    /// (never `git add .`) and commit with the journaled message.
    pub fn auto_commit(&self, paths: &[&str], message: &str) -> GitResult<String> {
        if paths.is_empty() {
            return Err(GitError(
                "auto-commit requires at least one changed path".into(),
            ));
        }
        if message.trim().is_empty() {
            return Err(GitError("auto-commit requires a non-empty message".into()));
        }
        let literal_paths: Vec<String> = paths
            .iter()
            .map(|path| format!(":(literal){path}"))
            .collect();
        let mut add = vec!["add", "--"];
        add.extend(literal_paths.iter().map(String::as_str));
        self.git(&add, &[])?;
        // --only leaves other staged paths out of this commit.
        let mut commit = vec!["commit", "--only", "-m", message, "--"];
        commit.extend(literal_paths.iter().map(String::as_str));
        self.git(&commit, &[])?;
        let head = self.git(&["rev-parse", "--short", "HEAD"], &[])?;
        Ok(head.trim().to_string())
    }

    /// Publish journey: push the task branch using a credential environment
    /// variable name provided by the host. The token value never appears in
    /// argv, logs, or this module — the host exports it before the call.
    pub fn publish(&self, branch: &str, credential_env: &str, remote: &str) -> GitResult<Value> {
        if credential_env.trim().is_empty() {
            return Err(GitError(
                "publish requires a credential environment variable name".into(),
            ));
        }
        if std::env::var(credential_env).is_err() {
            return Err(GitError(format!(
                "credential variable {credential_env} is not exported; the host must inject it process-locally"
            )));
        }
        self.git(&["push", remote, &format!("{branch}:{branch}")], &[])?;
        Ok(json!({
            "published": true, "branch": branch, "remote": remote,
            "credential": credential_env,
            "note": "token value is process-local; it is never logged or persisted",
        }))
    }
}

/// Commit message builder from a journey report (stable, evidence-first).
pub fn commit_message(goal: &str, files: &[&str], verified: bool) -> String {
    let files_part = files.join(", ");
    let status = if verified { "verified" } else { "unverified" };
    format!("af: {goal}\n\nfiles: {files_part}\nchecks: {status}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::BTreeMap;
    use std::path::Path;

    type RecordedCall = (Vec<String>, BTreeMap<String, String>);

    struct FakeRunner {
        calls: RefCell<Vec<RecordedCall>>,
        fail_on: RefCell<BTreeMap<String, i32>>,
    }

    impl FakeRunner {
        fn new() -> FakeRunner {
            FakeRunner {
                calls: RefCell::new(Vec::new()),
                fail_on: RefCell::new(BTreeMap::new()),
            }
        }

        fn fail_when(&self, first_arg: &str, code: i32) {
            self.fail_on
                .borrow_mut()
                .insert(first_arg.to_string(), code);
        }
    }

    impl CommandRunner for FakeRunner {
        fn run(
            &self,
            argv: &[String],
            _cwd: &Path,
            extra_env: &[(&str, &str)],
        ) -> Result<(i32, String, String), String> {
            let env: BTreeMap<String, String> = extra_env
                .iter()
                .map(|(key, value)| (key.to_string(), value.to_string()))
                .collect();
            self.calls.borrow_mut().push((argv.to_vec(), env));
            let fail_map = self.fail_on.borrow();
            let code = fail_map
                .get(&argv[0])
                .or_else(|| argv.get(1).and_then(|sub| fail_map.get(sub)))
                .copied()
                .unwrap_or(0);
            drop(fail_map);
            Ok((code, String::new(), "simulated stderr".into()))
        }
    }

    fn workflow<'a>(runner: &'a FakeRunner) -> GitWorkflow<'a> {
        GitWorkflow {
            repo_root: Path::new("/repo"),
            runner,
        }
    }

    #[test]
    fn branch_per_task_creates_then_reuses() {
        let runner = FakeRunner::new();
        let git = workflow(&runner);
        let branch = git.ensure_task_branch("fix scheduler!").unwrap();
        assert_eq!(branch, "af/task-fix-scheduler-");
        let calls = runner.calls.borrow();
        assert_eq!(
            calls[0].0,
            vec!["git", "branch", "--list", "af/task-fix-scheduler-"]
        );
        assert_eq!(
            calls[1].0,
            vec!["git", "checkout", "-b", "af/task-fix-scheduler-"]
        );
    }

    #[test]
    fn auto_commit_stages_exact_paths_and_never_add_dot() {
        let runner = FakeRunner::new();
        let git = workflow(&runner);
        let head = git
            .auto_commit(&["src/lib.py", "src/app.py"], "af: bump helper")
            .unwrap();
        assert_eq!(head, ""); // fake runner returns empty stdout
        let calls = runner.calls.borrow();
        assert_eq!(calls[0].0[0..3].to_vec(), vec!["git", "add", "--"]);
        assert!(calls
            .iter()
            .all(|(argv, _)| !argv.contains(&".".to_string())
                || argv[0] != "git"
                || argv[1] != "add"));
        assert_eq!(
            calls[1].0,
            vec![
                "git",
                "commit",
                "--only",
                "-m",
                "af: bump helper",
                "--",
                ":(literal)src/lib.py",
                ":(literal)src/app.py"
            ]
        );
        assert_eq!(calls[2].0, vec!["git", "rev-parse", "--short", "HEAD"]);
        assert!(git.auto_commit(&[], "x").is_err());
        assert!(git.auto_commit(&["a.py"], "  ").is_err());
    }

    #[test]
    fn real_commit_preserves_unrelated_index_entries() {
        let root = std::env::temp_dir().join(format!("af-git-scope-{}", af_entropy::token()));
        std::fs::create_dir(&root).unwrap();
        struct Temp(std::path::PathBuf);
        impl Drop for Temp {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let _temp = Temp(root.clone());
        let git = GitWorkflow {
            repo_root: &root,
            runner: &ProcessRunner,
        };
        git.git(&["init", "--quiet"], &[]).unwrap();
        for (key, value) in [
            ("user.name", "Fixture"),
            ("user.email", "fixture@example.invalid"),
            ("commit.gpgsign", "false"),
        ] {
            git.git(&["config", "--local", key, value], &[]).unwrap();
        }
        for path in ["change[1].txt", "change1.txt", "unrelated.txt"] {
            std::fs::write(root.join(path), "before\n").unwrap();
        }
        git.git(&["add", "--all"], &[]).unwrap();
        git.git(&["commit", "-m", "baseline"], &[]).unwrap();
        for path in ["change[1].txt", "change1.txt", "unrelated.txt"] {
            std::fs::write(root.join(path), "after\n").unwrap();
        }
        git.git(&["add", "--", "unrelated.txt"], &[]).unwrap();
        git.auto_commit(&["change[1].txt"], "reviewed change")
            .unwrap();
        assert_eq!(
            git.git(
                &["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
                &[]
            )
            .unwrap()
            .trim(),
            "change[1].txt"
        );
        assert_eq!(
            git.git(&["diff", "--cached", "--name-only"], &[])
                .unwrap()
                .trim(),
            "unrelated.txt"
        );
    }

    #[test]
    fn publish_requires_exported_credential_and_never_inlines_it() {
        let runner = FakeRunner::new();
        let git = workflow(&runner);
        let error = git
            .publish("af/task-x", "AF_DEFINITELY_MISSING_TOKEN", "origin")
            .unwrap_err();
        assert!(error.0.contains("not exported"));
        // With the variable exported the push runs with the env *name* only.
        std::env::set_var("AF_TEST_PUBLISH_TOKEN", "secret-value");
        let report = git
            .publish("af/task-x", "AF_TEST_PUBLISH_TOKEN", "origin")
            .unwrap();
        assert_eq!(report["published"], json!(true));
        assert_eq!(report["credential"], json!("AF_TEST_PUBLISH_TOKEN"));
        let calls = runner.calls.borrow();
        let push = calls
            .iter()
            .find(|(argv, _)| argv.len() > 1 && argv[0] == "git" && argv[1] == "push")
            .unwrap();
        assert_eq!(push.0, vec!["git", "push", "origin", "af/task-x:af/task-x"]);
        assert!(
            !serde_json::to_string(&push.0)
                .unwrap()
                .contains("secret-value"),
            "the token value must never appear in argv"
        );
        std::env::remove_var("AF_TEST_PUBLISH_TOKEN");
    }

    #[test]
    fn failing_git_is_an_error_with_stderr() {
        let runner = FakeRunner::new();
        runner.fail_when("commit", 1);
        let git = workflow(&runner);
        let error = git.auto_commit(&["a.py"], "message").unwrap_err();
        assert!(error.0.contains("git commit failed (1)"));
    }

    #[test]
    fn commit_message_is_evidence_first() {
        let message = commit_message("bump helper", &["src/lib.py"], true);
        assert!(message.starts_with("af: bump helper"));
        assert!(message.contains("files: src/lib.py"));
        assert!(message.contains("checks: verified"));
    }

    #[test]
    fn real_runner_refuses_implicit_credentials_without_spawning_git() {
        let runner = ProcessRunner;
        let cwd = std::env::temp_dir();
        assert!(runner
            .run(&["git".into(), "push".into()], &cwd, &[])
            .is_err());
        assert!(runner
            .run(
                &["git".into(), "status".into()],
                &cwd,
                &[("GH_TOKEN", "synthetic")]
            )
            .is_err());
    }
}
