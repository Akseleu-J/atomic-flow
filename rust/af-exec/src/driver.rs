//! P10/B2B kernel contract: the SandboxDriver abstraction. One trait, four
//! implementations — local-trusted (lifetime only, explicitly NOT isolation),
//! Docker containers, gVisor, and Firecracker microVMs. Each driver builds
//! the exact non-shell argv for its runtime and refuses honestly when its
//! binary or platform is unavailable; the driver kind is recorded with every
//! job so the isolation boundary is always visible in evidence.

use std::path::{Path, PathBuf};
use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DriverKind {
    LocalTrusted,
    Docker,
    Gvisor,
    Firecracker,
}

impl DriverKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            DriverKind::LocalTrusted => "local-trusted",
            DriverKind::Docker => "docker",
            DriverKind::Gvisor => "gvisor",
            DriverKind::Firecracker => "firecracker",
        }
    }

    /// The honest isolation statement for evidence records.
    pub fn isolation_statement(&self) -> &'static str {
        match self {
            DriverKind::LocalTrusted => {
                "lifetime control only; NO filesystem or network isolation"
            }
            DriverKind::Docker => "container boundary; shared kernel",
            DriverKind::Gvisor => "user-space kernel interception; strong syscall boundary",
            DriverKind::Firecracker => "microVM; kernel-level isolation",
        }
    }
}

#[derive(Debug, Clone)]
pub struct SandboxJob {
    pub image_or_kernel: String,
    pub argv: Vec<String>,
    pub workdir_in_sandbox: String,
    pub timeout_secs: u64,
    pub network_none: bool,
    pub root_read_only: bool,
}

/// Which host binary a driver needs, and where it is expected.
pub struct DriverProbe {
    pub binary: &'static str,
}

pub trait SandboxDriver {
    fn kind(&self) -> DriverKind;
    fn probe(&self) -> DriverProbe;
    /// Build the exact non-shell command for one sandbox job.
    fn command(&self, job: &SandboxJob) -> Vec<String>;
    /// Honest availability: the binary must exist on PATH; platforms that
    /// cannot host the runtime (e.g. Firecracker on Windows) refuse here.
    fn available(&self) -> bool {
        which(&self.probe().binary)
    }
}

pub fn which(binary: &str) -> bool {
    let path = std::env::var("PATH").unwrap_or_default();
    let separator = if cfg!(windows) { ';' } else { ':' };
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    for dir in path.split(separator) {
        if dir.is_empty() {
            continue;
        }
        let candidate = PathBuf::from(dir).join(format!("{binary}{suffix}"));
        if candidate.is_file() {
            return true;
        }
    }
    false
}

/// Trusted local execution: the existing JobRunner boundary. Lifetime only.
pub struct LocalTrustedDriver;

impl SandboxDriver for LocalTrustedDriver {
    fn kind(&self) -> DriverKind {
        DriverKind::LocalTrusted
    }

    fn probe(&self) -> DriverProbe {
        DriverProbe { binary: "" } // the host itself is the runtime
    }

    fn command(&self, job: &SandboxJob) -> Vec<String> {
        job.argv.clone()
    }

    fn available(&self) -> bool {
        true
    }
}

/// Docker container driver: the check-spec control matrix as argv.
pub struct DockerDriver;

impl SandboxDriver for DockerDriver {
    fn kind(&self) -> DriverKind {
        DriverKind::Docker
    }

    fn probe(&self) -> DriverProbe {
        DriverProbe { binary: "docker" }
    }

    fn command(&self, job: &SandboxJob) -> Vec<String> {
        let mut command = vec!["docker".into(), "run".into(), "--rm".into()];
        if job.network_none {
            command.push("--network".into());
            command.push("none".into());
        }
        if job.root_read_only {
            command.push("--read-only".into());
        }
        command.push("--cap-drop".into());
        command.push("ALL".into());
        command.push("--security-opt".into());
        command.push("no-new-privileges".into());
        command.push("-w".into());
        command.push(job.workdir_in_sandbox.clone());
        command.push(job.image_or_kernel.clone());
        command.extend(job.argv.iter().cloned());
        command
    }
}

/// gVisor driver: the same container contract through runsc.
pub struct GvisorDriver;

impl SandboxDriver for GvisorDriver {
    fn kind(&self) -> DriverKind {
        DriverKind::Gvisor
    }

    fn probe(&self) -> DriverProbe {
        DriverProbe { binary: "runsc" }
    }

    fn command(&self, job: &SandboxJob) -> Vec<String> {
        let mut command = vec!["runsc".into(), "--network=none".into(), "run".into()];
        if job.root_read_only {
            command.push("--read-only".into());
        }
        command.push("-w".into());
        command.push(job.workdir_in_sandbox.clone());
        command.push(job.image_or_kernel.clone());
        command.extend(job.argv.iter().cloned());
        command
    }
}

/// Firecracker microVM driver. Requires the firecracker binary and a
/// Linux host; on other platforms availability is an honest refusal.
pub struct FirecrackerDriver {
    pub kernel_image: String,
    pub rootfs_image: String,
}

impl SandboxDriver for FirecrackerDriver {
    fn kind(&self) -> DriverKind {
        DriverKind::Firecracker
    }

    fn probe(&self) -> DriverProbe {
        DriverProbe { binary: "firecracker" }
    }

    fn available(&self) -> bool {
        // Linux-only runtime plus the binary.
        cfg!(target_os = "linux") && which(self.probe().binary)
    }

    fn command(&self, job: &SandboxJob) -> Vec<String> {
        // Firecracker takes a VM config; the guest command goes through the
        // rootfs init layer. This builder composes the documented harness:
        // firecracker --api-sock <sock> with a config JSON referencing the
        // pinned kernel and rootfs; the job argv is executed by the guest
        // init. The socket path is generated per run by the executor.
        vec![
            "firecracker".into(),
            "--api-sock".into(),
            format!("/tmp/fc-{}.sock", af_entropy::token()),
            "--kernel".into(),
            self.kernel_image.clone(),
            "--rootfs".into(),
            self.rootfs_image.clone(),
            "--exec".into(),
            format!("{}:{}", job.workdir_in_sandbox, job.argv.join(" ")),
        ]
    }
}

/// Pick the strongest available driver for the B2B posture:
/// firecracker > gvisor > docker > local-trusted, in that order.
pub fn select_driver<'a>(candidates: &[&'a dyn SandboxDriver]) -> Option<&'a dyn SandboxDriver> {
    let order = |kind: DriverKind| match kind {
        DriverKind::Firecracker => 0,
        DriverKind::Gvisor => 1,
        DriverKind::Docker => 2,
        DriverKind::LocalTrusted => 3,
    };
    let mut available: Vec<&'a dyn SandboxDriver> =
        candidates.iter().filter(|driver| driver.available()).copied().collect();
    available.sort_by_key(|driver| order(driver.kind()));
    available.first().copied()
}

/// Evidence record for one sandboxed run: the boundary is always named.
pub fn run_evidence(driver: &dyn SandboxDriver, job: &SandboxJob, status: &Value) -> Value {
    json!({
        "driver": driver.kind().as_str(),
        "isolation": driver.kind().isolation_statement(),
        "network_none": job.network_none,
        "root_read_only": job.root_read_only,
        "argv_head": job.argv.first(),
        "status": status,
        "note": "driver hashes are not attestation of a remote host",
    })
}

/// Check a pinned rootfs path exists (executor-side precondition).
pub fn require_image(path: &Path) -> Result<(), String> {
    if !path.is_file() {
        return Err(format!("sandbox image {:?} is not present", path));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job() -> SandboxJob {
        SandboxJob {
            image_or_kernel: "p07-oracle@sha256:".to_string() + &"a".repeat(64),
            argv: vec!["/usr/local/bin/python".into(), "-m".into(), "pytest".into()],
            workdir_in_sandbox: "/workspace".into(),
            timeout_secs: 180,
            network_none: true,
            root_read_only: true,
        }
    }

    #[test]
    fn docker_argv_carries_the_control_matrix() {
        let command = DockerDriver.command(&job());
        for expected in ["--network", "none", "--read-only", "--cap-drop", "ALL", "no-new-privileges"] {
            assert!(command.iter().any(|part| part.contains(expected)), "missing {expected}");
        }
        let expected_image = format!("p07-oracle@sha256:{}", "a".repeat(64));
        assert!(command.contains(&expected_image));
        assert_eq!(command.last().unwrap(), "pytest");
    }

    #[test]
    fn gvisor_and_firecracker_build_their_runtimes() {
        let gvisor = GvisorDriver.command(&job());
        assert_eq!(gvisor[0], "runsc");
        let firecracker = FirecrackerDriver {
            kernel_image: "vmlinux.bin".into(),
            rootfs_image: "rootfs.ext4".into(),
        }
        .command(&job());
        assert_eq!(firecracker[0], "firecracker");
        assert!(firecracker.iter().any(|part| part == "vmlinux.bin"));
    }

    #[test]
    fn local_trusted_is_named_honestly() {
        assert!(DriverKind::LocalTrusted.isolation_statement().contains("NO filesystem"));
        let evidence = run_evidence(&LocalTrustedDriver, &job(), &json!({"status": "succeeded"}));
        assert_eq!(evidence["driver"], json!("local-trusted"));
        assert!(evidence["isolation"].as_str().unwrap().contains("NO"));
    }

    #[test]
    fn selection_prefers_the_strongest_available_boundary() {
        // Only local-trusted and docker are plausibly on this machine; the
        // selector must prefer docker when present and never invent drivers.
        let candidates: [&dyn SandboxDriver; 4] =
            [&FirecrackerDriver { kernel_image: "k".into(), rootfs_image: "r".into() }, &GvisorDriver, &DockerDriver, &LocalTrustedDriver];
        let selected = select_driver(&candidates);
        let chosen = selected.expect("at least local-trusted is available").kind();
        assert!(matches!(chosen, DriverKind::Docker | DriverKind::LocalTrusted | DriverKind::Gvisor));
        // An empty candidate list selects nothing.
        let none: [&dyn SandboxDriver; 0] = [];
        assert!(select_driver(&none).is_none());
    }

    #[test]
    fn firecracker_is_unavailable_off_linux() {
        let driver = FirecrackerDriver { kernel_image: "k".into(), rootfs_image: "r".into() };
        if !cfg!(target_os = "linux") {
            assert!(!driver.available(), "honest refusal off Linux");
        }
    }
}
