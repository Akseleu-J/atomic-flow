//! Fixed Docker lifecycle. The daemon is trusted; receipts are observations,
//! not remote attestation and never independent test verification.
use crate::{driver, err, now_unix, process, ExecError, ExecResult, JobRunner, JobSpec};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|v| {
        v.len() == 64
            && v.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
fn cid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl JobRunner {
    fn docker_control(
        &self,
        spec: &JobSpec,
        status: &mut Value,
        label: &str,
        args: Vec<String>,
        timeout: Duration,
    ) -> ExecResult<process::Outcome> {
        let mut argv = vec!["docker".into()];
        argv.extend(args);
        let result = process::run(&argv, &spec.cwd, timeout, |_| Ok(()))?;
        status["sandbox"]["observations"][label] = json!({"exit_code":result.exit_code,"timed_out":result.timed_out,
            "stdout":String::from_utf8_lossy(&result.stdout),"stderr":String::from_utf8_lossy(&result.stderr),
            "stdout_truncated":result.stdout_truncated,"stderr_truncated":result.stderr_truncated,
            "client_cleanup_confirmed":result.cleanup_confirmed});
        self.write_status(status)?;
        Ok(result)
    }

    pub(crate) fn run_docker(&self, spec: &JobSpec) -> ExecResult<Value> {
        if !cfg!(target_os = "linux") {
            return err("accepted Docker lifecycle requires Linux host");
        }
        let image = spec
            .image
            .as_deref()
            .ok_or_else(|| ExecError("pinned Docker image required".into()))?;
        if !digest(image) && !image.rsplit_once('@').is_some_and(|(_, d)| digest(d)) {
            return err("Docker image must be immutable SHA256 identity");
        }
        let workdir = spec.sandbox_workdir.as_deref().unwrap_or("/workspace");
        if !workdir.starts_with('/') || workdir.contains("..") || workdir.contains('\0') {
            return err("plain absolute container workdir required");
        }
        let mut status = self.submit(spec)?;
        let operation = format!("af-exec-{}", spec.job_id);
        status["status"] = json!("running");
        status["verification"] = json!("not_assessed");
        status["started_at"] = json!(now_unix());
        status["sandbox"] = json!({"driver":"docker", "isolation":driver::DriverKind::Docker.isolation_statement(),
            "operation":operation,"network_none":true,"root_read_only":true,"user":"65534:65534",
            "memory_bytes":1073741824u64,"cpus":2,"pids":128,"tmpfs_bytes":134217728,
            "verification":"not_assessed","observations":{}});
        self.write_status(&status)?;
        let deadline = Instant::now() + Duration::from_secs(spec.timeout_secs);
        let mut container = None;
        let mut target_exit = None;
        let mut timed_out = false;
        let mut output_truncated = false;
        let mut target_stdout = Vec::new();
        let mut target_stderr = Vec::new();
        let mut create_attempted = false;
        let attempted = (|| -> ExecResult<()> {
            let inspected = self.docker_control(
                spec,
                &mut status,
                "image",
                vec!["image".into(), "inspect".into(), image.into()],
                Duration::from_secs(10),
            )?;
            let doc = complete_json(&inspected)?;
            let item = doc
                .get(0)
                .ok_or_else(|| ExecError("image inspection missing".into()))?;
            let id = item["Id"].as_str().unwrap_or_default();
            if !digest(id)
                || (digest(image) && image != id)
                || (!digest(image)
                    && !item["RepoDigests"]
                        .as_array()
                        .is_some_and(|a| a.iter().any(|v| v.as_str() == Some(image))))
            {
                return err("Docker image identity differs");
            }
            let id = id.to_string();
            status["sandbox"]["image_id"] = json!(id);
            create_attempted = true;
            self.write_status(&status)?; // durable ownership identity before create
            let mut args = vec![
                "create".into(),
                "--name".into(),
                operation.clone(),
                "--label".into(),
                format!("atomic.exec.operation={operation}"),
                "--init".into(),
                "--pull=never".into(),
                "--network=none".into(),
                "--read-only".into(),
                "--user=65534:65534".into(),
                "--cap-drop=ALL".into(),
                "--security-opt=no-new-privileges".into(),
                "--memory=1073741824".into(),
                "--cpus=2".into(),
                "--pids-limit=128".into(),
                "--tmpfs=/tmp:rw,noexec,nosuid,size=134217728,mode=1777".into(),
                "--workdir".into(),
                workdir.into(),
                "--env=HOME=/tmp".into(),
                "--entrypoint".into(),
                spec.argv[0].clone(),
                id.clone(),
            ];
            args.extend(spec.argv[1..].iter().cloned());
            let created =
                self.docker_control(spec, &mut status, "create", args, Duration::from_secs(10))?;
            if created.exit_code != Some(0)
                || !created.cleanup_confirmed
                || created.timed_out
                || created.stdout_truncated
            {
                return err("container creation refused or uncertain");
            }
            let identity = String::from_utf8_lossy(&created.stdout).trim().to_string();
            if !cid(&identity) {
                return err("immutable container ID missing");
            }
            container = Some(identity.clone());
            status["sandbox"]["container_id"] = json!(identity);
            self.write_status(&status)?;
            let inspect = self.docker_control(
                spec,
                &mut status,
                "before",
                vec!["inspect".into(), identity.clone()],
                Duration::from_secs(10),
            )?;
            let doc = complete_json(&inspect)?;
            if !valid_container(&doc[0], &identity, &id, &operation, workdir) {
                return err("Docker controls differ from fixed profile");
            }
            let remaining = deadline
                .checked_duration_since(Instant::now())
                .ok_or_else(|| ExecError("Docker deadline exhausted before start".into()))?;
            let run = self.docker_control(
                spec,
                &mut status,
                "target",
                vec!["start".into(), "--attach".into(), identity.clone()],
                remaining,
            )?;
            timed_out = run.timed_out;
            output_truncated = run.stdout_truncated || run.stderr_truncated;
            target_stdout = run.stdout;
            target_stderr = run.stderr;
            if timed_out || output_truncated || !run.cleanup_confirmed {
                return err("Docker target execution incomplete");
            }
            let inspected = self.docker_control(
                spec,
                &mut status,
                "after",
                vec!["inspect".into(), identity],
                Duration::from_secs(10),
            )?;
            let doc = complete_json(&inspected)?;
            let state = &doc[0]["State"];
            if state["Running"] != json!(false)
                || state["Dead"] != json!(false)
                || state["OOMKilled"] != json!(false)
            {
                return err("Docker terminal state unavailable or resource failure");
            }
            target_exit = state["ExitCode"]
                .as_i64()
                .and_then(|v| i32::try_from(v).ok());
            if target_exit.is_none() {
                return err("container exit code unavailable");
            }
            Ok(())
        })();
        // Resolve uncertain create only through the durable unique operation label.
        let cleanup = (|| -> ExecResult<bool> {
            if container.is_none() && create_attempted {
                let listed = self.docker_control(
                    spec,
                    &mut status,
                    "resolve",
                    vec![
                        "container".into(),
                        "ls".into(),
                        "-a".into(),
                        "--no-trunc".into(),
                        "--filter".into(),
                        format!("label=atomic.exec.operation={operation}"),
                        "--format".into(),
                        "{{.ID}}".into(),
                    ],
                    Duration::from_secs(10),
                )?;
                if !command_complete(&listed) {
                    return Ok(false);
                }
                let raw = String::from_utf8_lossy(&listed.stdout);
                let ids: Vec<_> = raw.lines().collect();
                if ids.len() > 1 || ids.iter().any(|v| !cid(v)) {
                    return Ok(false);
                }
                container = ids.first().map(|v| v.to_string());
            }
            let Some(identity) = container.as_ref() else {
                return Ok(!create_attempted
                    || status["sandbox"]["observations"]["resolve"]["exit_code"] == json!(0));
            };
            status["sandbox"]["container_id"] = json!(identity);
            self.write_status(&status)?;
            let removed = self.docker_control(
                spec,
                &mut status,
                "remove",
                vec!["rm".into(), "-f".into(), identity.clone()],
                Duration::from_secs(10),
            )?;
            let absent = self.docker_control(
                spec,
                &mut status,
                "absence",
                vec![
                    "container".into(),
                    "ls".into(),
                    "-a".into(),
                    "--no-trunc".into(),
                    "--filter".into(),
                    format!("id={identity}"),
                    "--format".into(),
                    "{{.ID}}".into(),
                ],
                Duration::from_secs(10),
            )?;
            Ok(command_complete(&removed) && command_complete(&absent) && absent.stdout.is_empty())
        })()
        .unwrap_or(false);
        status["status"] = json!(if !cleanup {
            "unknown"
        } else if attempted.is_ok() && target_exit == Some(0) {
            "succeeded"
        } else {
            "failed"
        });
        status["reason"] = json!(attempted.err().map(|e| e.0));
        status["exit_code"] = json!(target_exit);
        status["timed_out"] = json!(timed_out);
        status["stdout_truncated"] = json!(output_truncated);
        status["stderr_truncated"] = json!(output_truncated);
        status["cleanup_confirmed"] = json!(cleanup);
        status["sandbox"]["cleanup_confirmed"] = json!(cleanup);
        status["reconcile_required"] = json!(!cleanup);
        status["finished_at"] = json!(now_unix());
        std::fs::write(
            self.root.join("jobs").join(format!("{}.out", spec.job_id)),
            target_stdout,
        )
        .map_err(|_| ExecError("Docker stdout persistence failed; running hold retained".into()))?;
        std::fs::write(
            self.root.join("jobs").join(format!("{}.err", spec.job_id)),
            target_stderr,
        )
        .map_err(|_| ExecError("Docker stderr persistence failed; running hold retained".into()))?;
        self.write_status(&status)?;
        Ok(status)
    }
}

fn command_complete(result: &process::Outcome) -> bool {
    result.exit_code == Some(0)
        && result.cleanup_confirmed
        && !result.timed_out
        && !result.stdout_truncated
        && !result.stderr_truncated
        && result.error.is_none()
}
fn complete_json(result: &process::Outcome) -> ExecResult<Value> {
    if !command_complete(result) {
        return err("Docker control command incomplete");
    }
    serde_json::from_slice(&result.stdout)
        .map_err(|_| ExecError("Docker inspection JSON invalid".into()))
}

fn valid_container(item: &Value, cid: &str, image: &str, operation: &str, cwd: &str) -> bool {
    let h = &item["HostConfig"];
    let c = &item["Config"];
    item["Id"] == json!(cid)
        && item["Image"] == json!(image)
        && item["State"]["Running"] == json!(false)
        && c["User"] == json!("65534:65534")
        && c["WorkingDir"] == json!(cwd)
        && c["Labels"]["atomic.exec.operation"] == json!(operation)
        && (c["Volumes"].is_null() || c["Volumes"] == json!({}))
        && h["NetworkMode"] == json!("none")
        && h["ReadonlyRootfs"] == json!(true)
        && h["Privileged"] == json!(false)
        && h["Init"] == json!(true)
        && h["CapDrop"] == json!(["ALL"])
        && (h["CapAdd"].is_null() || h["CapAdd"] == json!([]))
        && h["Memory"] == json!(1073741824u64)
        && h["NanoCpus"] == json!(2000000000u64)
        && h["PidsLimit"] == json!(128)
        && ["Binds", "VolumesFrom", "Mounts"]
            .iter()
            .all(|k| h[*k].is_null() || h[*k] == json!([]))
        && h["Tmpfs"] == json!({"/tmp":"rw,noexec,nosuid,size=134217728,mode=1777"})
        && h["SecurityOpt"]
            .as_array()
            .is_some_and(|a| a.iter().any(|v| v.as_str() == Some("no-new-privileges")))
        && item["Mounts"].as_array().is_some_and(|a| {
            a.iter().all(|m| {
                m["Type"] == json!("tmpfs")
                    && m["Destination"] == json!("/tmp")
                    && m["RW"] == json!(true)
            })
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn inspected() -> Value {
        json!({"Id":"container", "Image":"image", "State":{"Running":false},
            "Config":{"User":"65534:65534","WorkingDir":"/workspace","Labels":{"atomic.exec.operation":"operation"},"Volumes":null},
            "HostConfig":{"NetworkMode":"none","ReadonlyRootfs":true,"Privileged":false,"Init":true,"CapDrop":["ALL"],"CapAdd":null,
                "Memory":1073741824u64,"NanoCpus":2000000000u64,"PidsLimit":128,"Binds":null,"VolumesFrom":null,"Mounts":null,
                "Tmpfs":{"/tmp":"rw,noexec,nosuid,size=134217728,mode=1777"},"SecurityOpt":["no-new-privileges"]},"Mounts":[]})
    }
    #[test]
    fn fixed_profile_refuses_each_missing_control_and_host_mount() {
        let item = inspected();
        assert!(valid_container(
            &item,
            "container",
            "image",
            "operation",
            "/workspace"
        ));
        for (field, value) in [
            ("Memory", json!(0)),
            ("NanoCpus", json!(0)),
            ("PidsLimit", json!(0)),
            ("Privileged", json!(true)),
            ("ReadonlyRootfs", json!(false)),
            ("NetworkMode", json!("host")),
            ("CapAdd", json!(["SYS_ADMIN"])),
            ("Init", json!(false)),
            ("Binds", json!(["/:/host:ro"])),
            ("Mounts", json!([{"Type":"bind"}])),
        ] {
            let mut bad = item.clone();
            bad["HostConfig"][field] = value;
            assert!(
                !valid_container(&bad, "container", "image", "operation", "/workspace"),
                "{field}"
            );
        }
        let mut bad = item.clone();
        bad["Config"]["User"] = json!("0:0");
        assert!(!valid_container(
            &bad,
            "container",
            "image",
            "operation",
            "/workspace"
        ));
        let mut bad = item;
        bad["Mounts"] = json!([{"Type":"volume","Destination":"/private"}]);
        assert!(!valid_container(
            &bad,
            "container",
            "image",
            "operation",
            "/workspace"
        ));
    }
    #[test]
    fn mutable_docker_tag_is_refused_before_any_effect() {
        let dir = std::env::temp_dir().join(format!("af-docker-refuse-{}", af_entropy::token()));
        let runner = JobRunner::open(&dir).unwrap();
        let mut spec = JobSpec::new(vec!["/bin/true".into()], dir.clone(), 5).unwrap();
        spec.image = Some("alpine:latest".into());
        assert!(runner.run_docker(&spec).is_err());
        assert!(runner.jobs().unwrap().is_empty());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
