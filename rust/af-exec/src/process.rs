//! Bounded trusted-host execution. Process ownership is lifetime control,
//! never a filesystem/network sandbox. No parent credentials are inherited.
use crate::{ExecError, ExecResult};
use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

pub(crate) const OUTPUT_CAP: usize = 1_000_000;

#[derive(Debug)]
pub(crate) struct Outcome {
    pub exit_code: Option<i32>,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    pub timed_out: bool,
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
    pub cleanup_confirmed: bool,
    pub error: Option<String>,
}

fn drain<R: Read + Send + 'static>(
    mut stream: R,
    flag: Arc<AtomicBool>,
    tx: mpsc::Sender<(Vec<u8>, bool)>,
) {
    std::thread::spawn(move || {
        let mut retained = Vec::new();
        let mut buffer = [0u8; 8192];
        let clean = loop {
            match stream.read(&mut buffer) {
                Ok(0) => break true,
                Ok(count) => {
                    let available = OUTPUT_CAP.saturating_sub(retained.len());
                    retained.extend_from_slice(&buffer[..count.min(available)]);
                    if count > available {
                        flag.store(true, Ordering::SeqCst);
                    }
                }
                Err(_) => break false,
            }
        };
        let _ = tx.send((retained, clean));
    });
}

pub(crate) fn clean_command(argv: &[String], cwd: &Path) -> ExecResult<Command> {
    if argv.is_empty() || argv[0].is_empty() || argv.iter().any(|s| s.contains('\0')) {
        return Err(ExecError("nonempty plain argv required".into()));
    }
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // PATH resolves host-reviewed executable names. No HOME, credential,
    // proxy, provider, Python/Node/Cargo injection or loader variables survive.
    for key in [
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
    ] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command.env("LANG", "C").env("LC_ALL", "C");
    platform::prepare(&mut command)?;
    Ok(command)
}

pub(crate) fn run<F>(
    argv: &[String],
    cwd: &Path,
    timeout: Duration,
    on_spawn: F,
) -> ExecResult<Outcome>
where
    F: FnOnce(u32) -> ExecResult<()>,
{
    let deadline = Instant::now() + timeout;
    let mut command = clean_command(argv, cwd)?;
    let mut ownership = platform::Owner::new()?;
    let mut child = command
        .spawn()
        .map_err(|_| ExecError("host process spawn failed".into()))?;
    // Windows is suspended until durable PID admission and Job assignment.
    // Unix child starts in a new group; failed checkpoint always kills that group.
    let admitted = ownership
        .attach(&mut child)
        .and_then(|_| on_spawn(child.id()));
    if let Err(error) = admitted {
        let _ = ownership.terminate(&mut child);
        return Err(ExecError(format!(
            "spawn checkpoint refused; owned cleanup attempted: {}",
            error.0
        )));
    }
    if let Err(error) = ownership.resume(&mut child) {
        let _ = ownership.terminate(&mut child);
        return Err(error);
    }
    let out_overflow = Arc::new(AtomicBool::new(false));
    let err_overflow = Arc::new(AtomicBool::new(false));
    let (out_tx, out_rx) = mpsc::channel();
    let (err_tx, err_rx) = mpsc::channel();
    drain(
        child
            .stdout
            .take()
            .ok_or_else(|| ExecError("missing stdout pipe".into()))?,
        out_overflow.clone(),
        out_tx,
    );
    drain(
        child
            .stderr
            .take()
            .ok_or_else(|| ExecError("missing stderr pipe".into()))?,
        err_overflow.clone(),
        err_tx,
    );
    let mut timed_out = false;
    let mut error = None;
    let exit = loop {
        if Instant::now() >= deadline {
            timed_out = true;
            break None;
        }
        match child.try_wait() {
            Ok(Some(exit)) => break Some(exit),
            Err(_) => {
                error = Some("process wait uncertain".into());
                break None;
            }
            Ok(None) => {}
        }
        if Instant::now() >= deadline {
            timed_out = true;
            break None;
        }
        if out_overflow.load(Ordering::SeqCst) || err_overflow.load(Ordering::SeqCst) {
            break None;
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    // Even a witnessed parent exit cannot prove descendant cleanup.
    let cleanup_confirmed = ownership.terminate(&mut child);
    let exit_code = exit
        .and_then(|e| e.code())
        .or_else(|| child.try_wait().ok().flatten().and_then(|e| e.code()));
    let out = out_rx.recv_timeout(Duration::from_secs(2));
    let err = err_rx.recv_timeout(Duration::from_secs(2));
    let streams_complete =
        out.as_ref().is_ok_and(|(_, clean)| *clean) && err.as_ref().is_ok_and(|(_, clean)| *clean);
    if !streams_complete {
        error = Some("owned output drains did not complete".into());
    }
    Ok(Outcome {
        exit_code,
        stdout: out.map(|v| v.0).unwrap_or_default(),
        stderr: err.map(|v| v.0).unwrap_or_default(),
        timed_out,
        stdout_truncated: out_overflow.load(Ordering::SeqCst),
        stderr_truncated: err_overflow.load(Ordering::SeqCst),
        cleanup_confirmed: cleanup_confirmed && streams_complete,
        error,
    })
}

#[cfg(unix)]
mod platform {
    use super::*;
    use std::os::unix::process::CommandExt;
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    pub fn prepare(command: &mut Command) -> ExecResult<()> {
        command.process_group(0);
        Ok(())
    }
    pub struct Owner {
        group: Option<i32>,
    }
    impl Owner {
        pub fn new() -> ExecResult<Self> {
            Ok(Self { group: None })
        }
        pub fn attach(&mut self, child: &mut std::process::Child) -> ExecResult<()> {
            self.group = Some(child.id() as i32);
            Ok(())
        }
        pub fn resume(&self, _: &mut std::process::Child) -> ExecResult<()> {
            Ok(())
        }
        pub fn terminate(&self, child: &mut std::process::Child) -> bool {
            let Some(group) = self.group else {
                return false;
            };
            unsafe {
                kill(-group, 9);
            }
            let until = Instant::now() + Duration::from_secs(2);
            loop {
                let parent = child.try_wait().ok().flatten().is_some();
                let absent = unsafe { kill(-group, 0) != 0 };
                if parent && absent {
                    return true;
                }
                if Instant::now() >= until {
                    return false;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }
    impl Drop for Owner {
        fn drop(&mut self) {
            if let Some(group) = self.group {
                unsafe {
                    kill(-group, 9);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root() -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!("af-owned-test-{}", af_entropy::token()));
        std::fs::create_dir(&p).unwrap();
        p
    }
    fn shell(script: &str) -> Vec<String> {
        #[cfg(windows)]
        {
            vec!["cmd".into(), "/C".into(), script.into()]
        }
        #[cfg(unix)]
        {
            vec!["sh".into(), "-c".into(), script.into()]
        }
    }
    #[test]
    fn inherited_provider_environment_never_reaches_child() {
        let p = root();
        let key = "AF_EXEC_TEST_PROVIDER_SECRET";
        let previous = std::env::var_os(key);
        std::env::set_var(key, "synthetic-never-inherited");
        #[cfg(windows)]
        let code = "if defined AF_EXEC_TEST_PROVIDER_SECRET (echo inherited) else (echo clean)";
        #[cfg(unix)]
        let code =
            "if [ -n \"$AF_EXEC_TEST_PROVIDER_SECRET\" ]; then echo inherited; else echo clean; fi";
        let result = run(&shell(code), &p, Duration::from_secs(5), |_| Ok(())).unwrap();
        match previous {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
        assert_eq!(String::from_utf8_lossy(&result.stdout).trim(), "clean");
        assert!(result.cleanup_confirmed);
        std::fs::remove_dir_all(p).unwrap();
    }
    #[test]
    fn output_overflow_is_bounded_and_stops_owned_processes() {
        let p = root();
        #[cfg(windows)] let code = "for /L %i in (1,1,40000) do @echo 012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789";
        #[cfg(unix)]
        let code = "head -c 3000000 /dev/zero";
        let result = run(&shell(code), &p, Duration::from_secs(15), |_| Ok(())).unwrap();
        assert!(result.stdout_truncated);
        assert!(result.stdout.len() <= OUTPUT_CAP && result.stderr.len() <= OUTPUT_CAP);
        assert!(result.cleanup_confirmed);
        std::fs::remove_dir_all(p).unwrap();
    }
    #[test]
    fn parent_exit_does_not_leave_a_descendant_running() {
        let p = root();
        #[cfg(windows)] let code = "start \"\" /b cmd /c \"ping -n 4 127.0.0.1 >nul & echo escaped>marker.txt\" & exit /b 0";
        #[cfg(unix)]
        let code = "(sleep 2; echo escaped > marker.txt) & exit 0";
        let result = run(&shell(code), &p, Duration::from_secs(5), |_| Ok(())).unwrap();
        // An unreapable orphan group is conservatively unknown, never success.
        if cfg!(windows) {
            assert!(result.cleanup_confirmed, "{result:?}");
        }
        std::thread::sleep(Duration::from_millis(3500));
        assert!(!p.join("marker.txt").exists());
        std::fs::remove_dir_all(p).unwrap();
    }
    #[test]
    fn failed_spawn_checkpoint_stops_the_owned_tree() {
        let p = root();
        #[cfg(windows)]
        let code = "ping -n 4 127.0.0.1 >nul & echo escaped>marker.txt";
        #[cfg(unix)]
        let code = "sleep 2; echo escaped > marker.txt";
        let result = run(&shell(code), &p, Duration::from_secs(5), |_| {
            Err(ExecError("injected durable checkpoint failure".into()))
        });
        assert!(result.is_err());
        std::thread::sleep(Duration::from_millis(2500));
        assert!(!p.join("marker.txt").exists());
        std::fs::remove_dir_all(p).unwrap();
    }
}

#[cfg(windows)]
#[path = "process_windows.rs"]
mod platform;

#[cfg(not(any(unix, windows)))]
mod platform {
    use super::*;
    pub fn prepare(_: &mut Command) -> ExecResult<()> {
        Err(ExecError(
            "owned process lifetime unavailable on this platform".into(),
        ))
    }
    pub struct Owner;
    impl Owner {
        pub fn new() -> ExecResult<Self> {
            Err(ExecError("owned process lifetime unavailable".into()))
        }
        pub fn attach(&mut self, _: &mut std::process::Child) -> ExecResult<()> {
            unreachable!()
        }
        pub fn resume(&self, _: &mut std::process::Child) -> ExecResult<()> {
            unreachable!()
        }
        pub fn terminate(&self, _: &mut std::process::Child) -> bool {
            false
        }
    }
}
