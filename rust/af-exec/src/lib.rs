//! af-exec: durable job runner with an unknown-outcome reconciliation latch
//! (M4 slice of the `jobs.py`/`workers.py` port). Each job's state transitions
//! are fsynced status records written before and after the observable effect:
//! a host crash between them leaves the outcome `unknown`, and the runner
//! refuses new submissions until the host explicitly reconciles every unknown.
//! Deadline/overflow are known failures only with witnessed owned cleanup.
//! Windows uses suspended spawn plus a Job Object; Unix uses a dedicated
//! process group. Trusted host execution is not filesystem/network isolation.
//! Docker has its own immutable-ID inspect/cleanup lifecycle. Exit zero never
//! constitutes independent test verification.

use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

mod docker;
pub mod driver;
pub mod durable_queue;
pub mod git;
mod process;
pub mod queue;

pub const STATUS_SCHEMA_VERSION: i64 = 1;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ExecError(pub String);

pub type ExecResult<T> = Result<T, ExecError>;

fn err<T>(message: impl Into<String>) -> ExecResult<T> {
    Err(ExecError(message.into()))
}

fn now_unix() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn is_hex32(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Does the OS still have a live process with this pid?
fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        if pid == 0 {
            return false;
        }
        // PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, STILL_ACTIVE = 259
        const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                return false;
            }
            let mut exit_code: u32 = 0;
            let ok = GetExitCodeProcess(handle, &mut exit_code);
            CloseHandle(handle);
            ok != 0 && exit_code == 259
        }
    }
    #[cfg(unix)]
    {
        // signal 0 probes existence without effect
        unsafe { kill(pid as i32, 0) == 0 }
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = pid;
        false
    }
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn OpenProcess(
        desired_access: u32,
        inherit_handle: i32,
        process_id: u32,
    ) -> *mut std::ffi::c_void;
    fn GetExitCodeProcess(handle: *mut std::ffi::c_void, exit_code: *mut u32) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
    #[allow(dead_code)]
    fn TerminateProcess(handle: *mut std::ffi::c_void, exit_code: u32) -> i32;
}

#[cfg(unix)]
extern "C" {
    fn kill(pid: i32, signal: i32) -> i32;
}

#[derive(Debug, Clone)]
pub struct JobSpec {
    pub job_id: String,
    pub argv: Vec<String>,
    pub cwd: PathBuf,
    pub timeout_secs: u64,
    /// Optional sandbox image/kernel for SandboxDriver execution.
    pub image: Option<String>,
    /// Working directory inside the sandbox (driver-dependent).
    pub sandbox_workdir: Option<String>,
}

impl JobSpec {
    pub fn new(argv: Vec<String>, cwd: PathBuf, timeout_secs: u64) -> ExecResult<JobSpec> {
        if argv.is_empty() {
            return err("job requires a non-empty argv");
        }
        if !cwd.is_absolute() || !cwd.is_dir() {
            return err("job requires an absolute existing working directory");
        }
        if timeout_secs == 0 || timeout_secs > 3600 {
            return err("job timeout must be 1-3600 seconds");
        }
        Ok(JobSpec {
            job_id: af_entropy::token(),
            argv,
            cwd,
            timeout_secs,
            image: None,
            sandbox_workdir: None,
        })
    }
}

/// Durable job store: one fsynced status record per job under `<root>/jobs/`.
pub struct JobRunner {
    root: PathBuf,
}

impl JobRunner {
    pub fn open(root: &Path) -> ExecResult<JobRunner> {
        if !root.is_absolute() {
            return err("job store root must be an absolute path");
        }
        std::fs::create_dir_all(root.join("jobs"))
            .map_err(|error| ExecError(format!("job store create failed: {error}")))?;
        Ok(JobRunner {
            root: root.to_path_buf(),
        })
    }

    fn status_path(&self, job_id: &str) -> PathBuf {
        self.root.join("jobs").join(format!("{job_id}.json"))
    }

    fn read_status(&self, job_id: &str) -> ExecResult<Value> {
        let text = std::fs::read_to_string(self.status_path(job_id))
            .map_err(|error| ExecError(format!("job status read failed: {error}")))?;
        let value: Value = serde_json::from_str(&text)
            .map_err(|error| ExecError(format!("job status is corrupt: {error}")))?;
        Ok(value)
    }

    fn write_status(&self, status: &Value) -> ExecResult<()> {
        let job_id = status["job_id"].as_str().unwrap_or_default().to_string();
        if !is_hex32(&job_id) {
            return err("invalid job id");
        }
        let path = self.status_path(&job_id);
        let temp = self
            .root
            .join("jobs")
            .join(format!("{job_id}.{}.tmp", af_entropy::token()));
        {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)
                .map_err(|error| ExecError(format!("job status temp failed: {error}")))?;
            file.write_all(
                &serde_json::to_vec_pretty(status).map_err(|e| ExecError(format!("{e}")))?,
            )
            .and_then(|_| file.write_all(b"\n"))
            .and_then(|_| file.flush())
            .and_then(|_| file.sync_all())
            .map_err(|error| ExecError(format!("job status fsync failed: {error}")))?;
        }
        std::fs::rename(&temp, &path).map_err(|_| ExecError("job status replace failed".into()))?;
        #[cfg(unix)]
        std::fs::File::open(self.root.join("jobs"))
            .and_then(|f| f.sync_all())
            .map_err(|_| ExecError("job status directory fsync failed".into()))?;
        Ok(())
    }

    /// Mark the intent durably before any observable effect.
    pub fn submit(&self, spec: &JobSpec) -> ExecResult<Value> {
        if !is_hex32(&spec.job_id)
            || spec.argv.is_empty()
            || spec.argv[0].is_empty()
            || spec.argv.iter().any(|v| v.contains('\0'))
            || !spec.cwd.is_absolute()
            || !spec.cwd.is_dir()
            || spec.timeout_secs == 0
            || spec.timeout_secs > 3600
        {
            return err("invalid job identity, argv, cwd or timeout");
        }
        if self.status_path(&spec.job_id).exists() {
            return err("duplicate job id");
        }
        if self.unresolved_unknown_ids()?.is_empty() {
            // Latch check happened; record submitted state.
        } else {
            return err("unknown job outcome holds the runner; reconcile first");
        }
        let status = json!({
            "schema_version": STATUS_SCHEMA_VERSION,
            "job_id": spec.job_id,
            "status": "submitted",
            "argv": spec.argv,
            "cwd": spec.cwd.to_string_lossy(),
            "timeout_secs": spec.timeout_secs,
            "created_at": now_unix(),
        });
        self.write_status(&status)?;
        Ok(status)
    }

    /// Trusted host process execution with owned lifetime and bounded drains.
    /// A successful command is diagnostic execution, never test verification.
    pub fn run(&self, spec: &JobSpec) -> ExecResult<Value> {
        self.run_recorded(spec, None)
    }

    fn run_recorded(&self, spec: &JobSpec, sandbox: Option<Value>) -> ExecResult<Value> {
        let mut status = self.submit(spec)?;
        status["status"] = json!("running");
        status["started_at"] = json!(now_unix());
        status["verification"] = json!("not_assessed");
        status["isolation"] =
            json!("trusted host; owned lifetime only, NO filesystem or network isolation");
        if let Some(evidence) = sandbox {
            status["sandbox"] = evidence;
        }
        self.write_status(&status)?;
        let outcome = process::run(
            &spec.argv,
            &spec.cwd,
            Duration::from_secs(spec.timeout_secs),
            |pid| {
                status["pid"] = json!(pid);
                self.write_status(&status)
            },
        );
        match outcome {
            Ok(result) => {
                status["exit_code"] = json!(result.exit_code);
                status["timed_out"] = json!(result.timed_out);
                status["stdout_truncated"] = json!(result.stdout_truncated);
                status["stderr_truncated"] = json!(result.stderr_truncated);
                status["cleanup_confirmed"] = json!(result.cleanup_confirmed);
                status["reason"] = json!(result.error);
                let complete = result.cleanup_confirmed && result.error.is_none();
                status["status"] = json!(if !complete {
                    "unknown"
                } else if result.exit_code == Some(0)
                    && !result.timed_out
                    && !result.stdout_truncated
                    && !result.stderr_truncated
                {
                    "succeeded"
                } else {
                    "failed"
                });
                status["reconcile_required"] = json!(!complete);
                // Output files are bounded by the drain, never post-hoc truncation.
                for (suffix, bytes) in [("out", result.stdout), ("err", result.stderr)] {
                    let path = self
                        .root
                        .join("jobs")
                        .join(format!("{}.{}", spec.job_id, suffix));
                    let mut file = std::fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(path)
                        .map_err(|_| {
                            ExecError(
                                "owned output persistence failed; running checkpoint retained"
                                    .into(),
                            )
                        })?;
                    file.write_all(&bytes)
                        .and_then(|_| file.sync_all())
                        .map_err(|_| {
                            ExecError(
                                "owned output fsync failed; running checkpoint retained".into(),
                            )
                        })?;
                }
            }
            Err(error) => {
                // Spawn may have occurred: persist a hold rather than guessing zero effects.
                let never_spawned = error.0 == "host process spawn failed";
                status["status"] = json!(if never_spawned { "failed" } else { "unknown" });
                status["reconcile_required"] = json!(!never_spawned);
                status["cleanup_confirmed"] = json!(never_spawned);
                status["reason"] = json!(error.0);
            }
        }
        status["finished_at"] = json!(now_unix());
        self.write_status(&status)?;
        Ok(status)
    }

    /// Only accepted host lifecycle implementations execute sandbox jobs.
    pub fn run_with_driver(
        &self,
        spec: &JobSpec,
        driver: &dyn driver::SandboxDriver,
    ) -> ExecResult<Value> {
        match driver.kind() {
            driver::DriverKind::LocalTrusted => {
                let mut status = self.run(spec)?;
                status["sandbox"] = json!({"driver":"local-trusted", "isolation":"NO filesystem or network isolation",
                    "cleanup_confirmed":status["cleanup_confirmed"], "verification":"not_assessed"});
                self.write_status(&status)?;
                Ok(status)
            }
            driver::DriverKind::Docker => self.run_docker(spec),
            _ => err("sandbox lifecycle has no accepted inspect/cleanup implementation; execution refused"),
        }
    }

    /// All job ids with their current persisted status.
    pub fn jobs(&self) -> ExecResult<BTreeMap<String, Value>> {
        let mut result = BTreeMap::new();
        let entries = std::fs::read_dir(self.root.join("jobs"))
            .map_err(|error| ExecError(format!("job store list failed: {error}")))?;
        for entry in entries.filter_map(|e| e.ok()) {
            let path = entry.path();
            let name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default()
                .to_string();
            if let Some(job_id) = name.strip_suffix(".json") {
                if !is_hex32(job_id) {
                    continue;
                }
                let text = std::fs::read_to_string(&path)
                    .map_err(|error| ExecError(format!("job status read failed: {error}")))?;
                let value: Value = serde_json::from_str(&text)
                    .map_err(|error| ExecError(format!("job status is corrupt: {error}")))?;
                result.insert(job_id.to_string(), value);
            }
        }
        Ok(result)
    }

    /// Persisted running or unknown jobs hold admission. A reusable PID cannot
    /// establish that a previous effect is complete, even if it is alive.
    pub fn unresolved_unknown_ids(&self) -> ExecResult<Vec<String>> {
        let mut unknown = Vec::new();
        for (job_id, status) in self.jobs()? {
            if status["status"] == json!("running") {
                unknown.push(job_id.clone());
            }
            if status["status"] == json!("unknown") {
                unknown.push(job_id);
            }
        }
        Ok(unknown)
    }

    /// Host decision: mark a provably-dead running job as unknown outcome.
    /// Returns the reconciled status.
    pub fn mark_unknown(&self, job_id: &str) -> ExecResult<Value> {
        let mut status = self.read_status(job_id)?;
        if status["status"] != json!("running") {
            return err("only running jobs can become unknown outcomes");
        }
        let pid = status.get("pid").and_then(Value::as_u64).unwrap_or(0) as u32;
        if process_alive(pid) {
            return err("job process is still alive; outcome is not unknown yet");
        }
        status["status"] = json!("unknown");
        status["reconcile_required"] = json!(true);
        let value = status;
        self.write_status(&value)?;
        Ok(value)
    }

    /// Host reconciliation closes an unknown with explicit evidence. This is
    /// the only path that releases the runner's latch — never a retry.
    pub fn reconcile(
        &self,
        job_id: &str,
        actor: &str,
        reason: &str,
        evidence: &Value,
    ) -> ExecResult<Value> {
        if actor.trim().is_empty() || reason.trim().is_empty() {
            return err("reconciliation requires actor and reason");
        }
        let mut status = self.read_status(job_id)?;
        if status["status"] != json!("unknown") {
            return err("only unknown outcomes can be reconciled");
        }
        status["status"] = json!("reconciled");
        status["reconcile_required"] = json!(false);
        status["reconciliation"] = json!({
            "actor": actor,
            "reason": reason,
            "evidence": evidence,
            "at": now_unix(),
        });
        let value = status;
        self.write_status(&value)?;
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root() -> PathBuf {
        let root = std::env::temp_dir().join(format!("af-exec-{}", af_entropy::token()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    fn echo_job(root: &Path, code: i32) -> (JobRunner, JobSpec) {
        let runner = JobRunner::open(root).unwrap();
        #[cfg(windows)]
        let argv = vec!["cmd".to_string(), "/C".to_string(), format!("exit {code}")];
        #[cfg(not(windows))]
        let argv = vec!["sh".to_string(), "-c".to_string(), format!("exit {code}")];
        let spec = JobSpec::new(argv, root.to_path_buf(), 30).unwrap();
        (runner, spec)
    }

    #[test]
    fn successful_job_reaches_succeeded() {
        let root = temp_root();
        let (runner, spec) = echo_job(&root, 0);
        let status = runner.run(&spec).unwrap();
        assert_eq!(status["status"], json!("succeeded"));
        assert_eq!(status["exit_code"], json!(0));
        let jobs = runner.jobs().unwrap();
        assert_eq!(jobs[&spec.job_id]["status"], json!("succeeded"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn failing_exit_is_witnessed_failure_not_unknown() {
        let root = temp_root();
        let (runner, spec) = echo_job(&root, 3);
        let status = runner.run(&spec).unwrap();
        assert_eq!(status["status"], json!("failed"));
        assert_eq!(status["exit_code"], json!(3));
        assert!(runner.unresolved_unknown_ids().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn timeout_kill_is_a_witnessed_failure() {
        let root = temp_root();
        let runner = JobRunner::open(&root).unwrap();
        #[cfg(windows)]
        let argv = vec![
            "cmd".to_string(),
            "/C".to_string(),
            "ping -n 30 127.0.0.1 >nul".to_string(),
        ];
        #[cfg(not(windows))]
        let argv = vec!["sleep".to_string(), "30".to_string()];
        let spec = JobSpec::new(argv, root.clone(), 1).unwrap();
        let status = runner.run(&spec).unwrap();
        assert_eq!(status["status"], json!("failed"));
        assert_eq!(status["timed_out"], json!(true));
        assert_eq!(status["cleanup_confirmed"], json!(true));
        assert!(runner.unresolved_unknown_ids().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn dead_running_job_becomes_unknown_and_latches_the_runner() {
        let root = temp_root();
        let runner = JobRunner::open(&root).unwrap();
        // Simulate a host crash: a running record with a pid that is gone.
        let job_id = af_entropy::token();
        let record = json!({
            "schema_version": STATUS_SCHEMA_VERSION,
            "job_id": job_id,
            "status": "running",
            "argv": ["whatever"],
            "pid": 4_000_000,
            "started_at": now_unix(),
        });
        let path = root.join("jobs").join(format!("{job_id}.json"));
        std::fs::write(&path, serde_json::to_vec_pretty(&record).unwrap()).unwrap();
        // The runner cannot testify: reconcile marks unknown.
        runner.mark_unknown(&job_id).unwrap();
        assert_eq!(
            runner.read_status(&job_id).unwrap()["status"],
            json!("unknown")
        );
        // Latch: no new submissions while an unknown is unresolved.
        let (runner2, spec) = echo_job(&root, 0);
        let error = runner2.submit(&spec).unwrap_err();
        assert!(error.0.contains("unknown job outcome holds the runner"));
        // Explicit host reconciliation releases the latch; no replay happened.
        let reconciled = runner
            .reconcile(
                &job_id,
                "host",
                "verified the child never produced effects",
                &json!({"checked": true}),
            )
            .unwrap();
        assert_eq!(reconciled["status"], json!("reconciled"));
        assert!(runner.unresolved_unknown_ids().unwrap().is_empty());
        let status = runner2.run(&spec).unwrap();
        assert_eq!(status["status"], json!("succeeded"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn alive_running_job_holds_admission_without_guessing_completion() {
        let root = temp_root();
        let runner = JobRunner::open(&root).unwrap();
        let job_id = af_entropy::token();
        let record = json!({
            "schema_version": STATUS_SCHEMA_VERSION,
            "job_id": job_id,
            "status": "running",
            "pid": std::process::id(),
            "started_at": now_unix(),
        });
        let path = root.join("jobs").join(format!("{job_id}.json"));
        std::fs::write(&path, serde_json::to_vec_pretty(&record).unwrap()).unwrap();
        let error = runner.mark_unknown(&job_id).unwrap_err();
        assert!(error.0.contains("still alive"));
        assert_eq!(
            runner.unresolved_unknown_ids().unwrap(),
            vec![job_id.clone()]
        );
        let (_, next) = echo_job(&root, 0);
        assert!(runner.submit(&next).is_err());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn duplicate_and_malformed_jobs_refused() {
        let root = temp_root();
        let (runner, spec) = echo_job(&root, 0);
        runner.run(&spec).unwrap();
        let error = runner.submit(&spec).unwrap_err();
        assert!(error.0.contains("duplicate job id"));
        let bad = JobSpec::new(vec![], root.clone(), 5).unwrap_err();
        assert!(bad.0.contains("non-empty argv"));
        let bad = JobSpec::new(vec!["x".into()], root.clone(), 0).unwrap_err();
        assert!(bad.0.contains("timeout"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn spawn_failure_is_recorded_failed() {
        let root = temp_root();
        let runner = JobRunner::open(&root).unwrap();
        let spec = JobSpec::new(
            vec!["definitely-not-a-real-binary-9f8a2c".to_string()],
            root.clone(),
            10,
        )
        .unwrap();
        let status = runner.run(&spec).unwrap();
        assert_eq!(status["status"], json!("failed"));
        assert!(status["reason"].as_str().unwrap().contains("spawn failed"));
        let _ = std::fs::remove_dir_all(&root);
    }
}
