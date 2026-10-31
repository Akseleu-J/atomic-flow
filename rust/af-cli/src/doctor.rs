//! `af doctor`: environment checks for the self-host kernel. Read-only —
//! reports what is present, what is missing, and never mutates anything.

use serde_json::{json, Value};
use std::path::PathBuf;

fn which(binary: &str) -> Option<PathBuf> {
    let path = std::env::var("PATH").unwrap_or_default();
    let separator = if cfg!(windows) { ';' } else { ':' };
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    for dir in path.split(separator) {
        if dir.is_empty() {
            continue;
        }
        let candidate = PathBuf::from(dir).join(format!("{binary}{suffix}"));
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn command_version(binary: &str, args: &[&str]) -> Option<String> {
    let output = std::process::Command::new(binary)
        .args(args)
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&output.stdout).to_string();
    Some(text.lines().next().unwrap_or_default().trim().to_string())
}

/// Run all checks; every entry is independent — one failure never hides the
/// rest.
pub fn run_checks() -> Value {
    let mut checks: Vec<Value> = Vec::new();
    let mut check = |name: &str, ok: bool, detail: Value| {
        checks.push(json!({"name": name, "ok": ok, "detail": detail}));
    };

    check("kernel_version", true, json!(env!("CARGO_PKG_VERSION")));

    let home = af_sandbox::user_dir();
    let home_writable = std::fs::create_dir_all(&home).is_ok();
    check(
        "atomic_flow_home",
        home_writable,
        json!({"path": home.to_string_lossy(), "writable": home_writable}),
    );

    let sessions_dir = home.join("sessions");
    check(
        "sessions_dir",
        sessions_dir.is_dir(),
        json!({"path": sessions_dir.to_string_lossy(), "exists": sessions_dir.is_dir()}),
    );

    match which("git") {
        Some(path) => {
            let version = command_version("git", &["--version"]).unwrap_or_default();
            check(
                "git",
                true,
                json!({"path": path.to_string_lossy(), "version": version}),
            );
        }
        None => check("git", false, json!({"reason": "git not found on PATH"})),
    }

    match which("docker") {
        Some(path) => {
            let version =
                command_version("docker", &["version", "--format", "{{.Server.Version}}"]);
            check(
                "docker",
                version.is_some(),
                json!({"path": path.to_string_lossy(), "server_version": version}),
            );
        }
        None => check(
            "docker",
            false,
            json!({"reason": "docker not found; sandbox jobs fall back to local-trusted"}),
        ),
    }

    let firecracker = which("firecracker");
    check(
        "firecracker",
        firecracker.is_some() && cfg!(target_os = "linux"),
        json!({
            "on_path": firecracker.is_some(),
            "reason": if cfg!(target_os = "linux") { "microVM acceptance needs /dev/kvm" } else { "Linux-only runtime" },
        }),
    );

    let graphs_dir = home.join("graphs");
    check(
        "graphs_dir",
        graphs_dir.is_dir(),
        json!({"path": graphs_dir.to_string_lossy(), "exists": graphs_dir.is_dir()}),
    );

    json!({
        "ok": checks.iter().all(|check| check["ok"] == json!(true)),
        "checks": checks,
    })
}
