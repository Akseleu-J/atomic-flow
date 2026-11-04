//! af — the atomic-flow product binary (M5 slice): graph, sandbox, sessions,
//! jobs and the backend plane, wired into one native executable. Surfaces are
//! thin; all authority checks live in the crates. Sandbox mutations require
//! the explicit `--host-approved` flag: the human running the CLI is the host.

mod dashboard;
mod doctor;
mod server;
mod trusted_provider;

use clap::{Parser, Subcommand};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "af",
    version,
    about = "atomic-flow: project-aware LLM agent core"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Self-host HTTP API (headless; non-loopback bind requires --token).
    Serve {
        #[arg(long, default_value = "127.0.0.1:8787")]
        bind: String,
        #[arg(long)]
        token: Option<String>,
        /// Read the server token from this process environment variable.
        #[arg(long, conflicts_with = "token")]
        token_env: Option<String>,
    },
    /// MCP stdio server: line-delimited JSON-RPC over stdin/stdout.
    Mcp,
    /// Environment checks for the self-host kernel (read-only).
    Doctor,
    /// Structural graph operations (roadmap P01/P02 core).
    Graph {
        #[command(subcommand)]
        command: GraphCommand,
    },
    /// Host-approved sandboxed workspace operations.
    Sandbox {
        #[command(subcommand)]
        command: SandboxCommand,
    },
    /// Append-only session journal operations.
    Session {
        #[command(subcommand)]
        command: SessionCommand,
    },
    /// Durable job runner operations.
    Job {
        #[command(subcommand)]
        command: JobCommand,
    },
    /// Model backend plane smoke call.
    Backend {
        #[command(subcommand)]
        command: BackendCommand,
    },
    /// Bounded agent journey on the kernel (observe → plan → apply → verify).
    Agent {
        #[command(subcommand)]
        command: AgentCommand,
    },
    /// In-process vertical journey: build → delta → parity on a scratch root.
    SelfTest,
}

#[derive(Subcommand)]
enum AgentCommand {
    /// Run one bounded journey: full read proof, exact reviewed replacement,
    /// sandbox apply, syntax/content verification, journaled evidence.
    Journey {
        #[arg(long)]
        root_id: String,
        #[arg(long)]
        file: String,
        /// Exact reviewed replacement text (the reviewed plan; never model output).
        #[arg(long)]
        replacement: String,
        #[arg(long, default_value = "bounded kernel journey")]
        goal: String,
        #[arg(long, default_value = "journey")]
        session_id: String,
    },
}

#[derive(Subcommand)]
enum GraphCommand {
    /// Clean build over a project root, committed to the store.
    Build {
        /// Absolute path to the project root.
        #[arg(long)]
        root: PathBuf,
        /// Store file path (created if missing).
        #[arg(long)]
        store: PathBuf,
    },
    /// Print a summary of the stored graph.
    Show {
        #[arg(long)]
        store: PathBuf,
    },
}

#[derive(Subcommand)]
enum SandboxCommand {
    /// Grant a root (requires --host-approved: the human is the host).
    Grant {
        #[arg(long)]
        root_id: String,
        #[arg(long)]
        path: PathBuf,
        /// Exact write scope (relative POSIX paths).
        #[arg(long)]
        write: Vec<String>,
        #[arg(long)]
        host_approved: bool,
    },
    /// List granted roots.
    List,
    /// Inspect interrupted publication without changing files or clearing holds.
    InspectRecovery {
        #[arg(long)]
        root_id: String,
        #[arg(long)]
        changeset_id: String,
    },
    /// Restore an inspected publication. Never repeats model or tool calls.
    RecoverPublication {
        #[arg(long)]
        root_id: String,
        #[arg(long)]
        changeset_id: String,
        #[arg(long)]
        preview_digest: String,
        #[arg(long)]
        host_approved: bool,
    },
    /// Read a bounded, redacted page of root-qualified source.
    Read {
        #[arg(long)]
        root_id: String,
        #[arg(long)]
        path: String,
        #[arg(long, default_value_t = 0)]
        offset: usize,
        #[arg(long, default_value_t = 6000)]
        limit: usize,
    },
}

#[derive(Subcommand)]
enum SessionCommand {
    /// Create a session bound to a project path.
    Create {
        #[arg(long)]
        id: Option<String>,
        #[arg(long, default_value = ".")]
        project: PathBuf,
    },
    /// Append one event from JSON arguments.
    Append {
        #[arg(long)]
        id: String,
        #[arg(long)]
        event_type: String,
        #[arg(long)]
        payload_json: String,
        #[arg(long)]
        call_id: Option<String>,
        #[arg(long)]
        outcome: Option<String>,
    },
    /// Read the journal: committed events, pending and unresolved calls.
    Read {
        #[arg(long)]
        id: String,
    },
    /// Preserve and remove an interrupted tail.
    Recover {
        #[arg(long)]
        id: String,
    },
}

#[derive(Subcommand)]
enum JobCommand {
    /// Run one job to a terminal, witnessed state.
    Run {
        /// argv after `--`.
        #[arg(last = true)]
        argv: Vec<String>,
        #[arg(long, default_value = ".")]
        cwd: PathBuf,
        #[arg(long, default_value_t = 60)]
        timeout_secs: u64,
        #[arg(long, default_value = ".")]
        store: PathBuf,
    },
    /// List jobs with their statuses and unresolved holds.
    Status {
        #[arg(long, default_value = ".")]
        store: PathBuf,
    },
    /// Reconcile an unknown outcome (explicit host decision).
    Reconcile {
        #[arg(long)]
        store: PathBuf,
        #[arg(long)]
        job_id: String,
        #[arg(long)]
        actor: String,
        #[arg(long)]
        reason: String,
    },
}

#[derive(Subcommand)]
enum BackendCommand {
    /// Deterministic scripted call: verifies the plane without a provider.
    Ping,
    /// One real OpenAI-compatible call; requires an API key in the named env
    /// var. Unknown outcomes are reported as holds, never retried.
    Call {
        #[arg(long)]
        base_url: String,
        #[arg(long, default_value = "ATOMIC_FLOW_API_KEY")]
        api_key_env: String,
        #[arg(long, default_value = "gpt-4o-mini")]
        model: String,
        #[arg(long)]
        prompt: String,
    },
    /// Streaming call: content deltas print to stdout as they arrive; the
    /// final settled JSON (usage, finish) prints last. Same hold semantics.
    CallStream {
        #[arg(long)]
        base_url: String,
        #[arg(long, default_value = "ATOMIC_FLOW_API_KEY")]
        api_key_env: String,
        #[arg(long, default_value = "gpt-4o-mini")]
        model: String,
        #[arg(long)]
        prompt: String,
    },
}

fn main() {
    let cli = Cli::parse();
    if let Command::Serve {
        bind,
        token,
        token_env,
    } = cli.command
    {
        let token = match token_env {
            Some(name) => match std::env::var(name) {
                Ok(value)
                    if !value.is_empty()
                        && value.len() <= 512
                        && !value.chars().any(char::is_control) =>
                {
                    Some(value)
                }
                _ => {
                    eprintln!("af: server token environment variable is missing or invalid");
                    std::process::exit(1);
                }
            },
            None => token,
        };
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .map_err(|error| format!("tokio runtime failed: {error}"));
        let result = match runtime {
            Ok(runtime) => runtime.block_on(server::run(&bind, token)),
            Err(error) => Err(error),
        };
        match result {
            Ok(()) => std::process::exit(0),
            Err(error) => {
                eprintln!("af: {error}");
                std::process::exit(1);
            }
        }
    }
    if let Command::Mcp = cli.command {
        let stdin = std::io::stdin().lock();
        let mut stdout = std::io::stdout().lock();
        let tools = mcp_tools();
        let mut reader = std::io::BufReader::new(stdin);
        match af_program::mcp_stdio::run_stdio(&mut reader, &mut stdout, &tools) {
            Ok(served) => {
                eprintln!("af mcp: served {served} requests");
                std::process::exit(0);
            }
            Err(error) => {
                eprintln!("af mcp: {error}");
                std::process::exit(1);
            }
        }
    }
    let code = match run(cli.command) {
        Ok(output) => {
            println!("{output}");
            0
        }
        Err(error) => {
            eprintln!("af: {error}");
            1
        }
    };
    std::process::exit(code);
}

fn run(command: Command) -> Result<String, String> {
    match command {
        Command::Serve { .. } => unreachable!("serve is handled in main"),
        Command::Mcp => unreachable!("mcp is handled in main"),
        Command::Doctor => {
            Ok(serde_json::to_string_pretty(&doctor::run_checks()).map_err(|e| e.to_string())?)
        }
        Command::SelfTest => self_test(),
        Command::Agent { command } => match command {
            AgentCommand::Journey {
                root_id,
                file,
                replacement,
                goal,
                session_id,
            } => agent_journey(root_id, file, replacement, goal, session_id),
        },
        Command::Graph { command } => match command {
            GraphCommand::Build { root, store } => graph_build(root, store),
            GraphCommand::Show { store } => graph_show(store),
        },
        Command::Sandbox { command } => match command {
            SandboxCommand::Grant {
                root_id,
                path,
                write,
                host_approved,
            } => sandbox_grant(root_id, path, write, host_approved),
            SandboxCommand::List => sandbox_list(),
            SandboxCommand::InspectRecovery {
                root_id,
                changeset_id,
            } => {
                let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
                let preview = registry
                    .inspect_recovery(&root_id, &changeset_id)
                    .map_err(|e| e.0)?;
                serde_json::to_string(&preview).map_err(|e| e.to_string())
            }
            SandboxCommand::RecoverPublication {
                root_id,
                changeset_id,
                preview_digest,
                host_approved,
            } => {
                let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
                let result = registry
                    .recover(&root_id, &changeset_id, &preview_digest, host_approved)
                    .map_err(|e| e.0)?;
                serde_json::to_string(&result).map_err(|e| e.to_string())
            }
            SandboxCommand::Read {
                root_id,
                path,
                offset,
                limit,
            } => sandbox_read(root_id, path, offset, limit),
        },
        Command::Session { command } => match command {
            SessionCommand::Create { id, project } => session_create(id, project),
            SessionCommand::Append {
                id,
                event_type,
                payload_json,
                call_id,
                outcome,
            } => session_append(id, event_type, payload_json, call_id, outcome),
            SessionCommand::Read { id } => session_read(id),
            SessionCommand::Recover { id } => session_recover(id),
        },
        Command::Job { command } => match command {
            JobCommand::Run {
                argv,
                cwd,
                timeout_secs,
                store,
            } => job_run(argv, cwd, timeout_secs, store),
            JobCommand::Status { store } => job_status(store),
            JobCommand::Reconcile {
                store,
                job_id,
                actor,
                reason,
            } => job_reconcile(store, job_id, actor, reason),
        },
        Command::Backend { command } => match command {
            BackendCommand::Ping => backend_ping(),
            BackendCommand::Call {
                base_url,
                api_key_env,
                model,
                prompt,
            } => backend_call(base_url, api_key_env, model, prompt),
            BackendCommand::CallStream {
                base_url,
                api_key_env,
                model,
                prompt,
            } => backend_call_stream(base_url, api_key_env, model, prompt),
        },
    }
}

fn graph_build(root: PathBuf, store: PathBuf) -> Result<String, String> {
    let files = af_graph::project::collect_source_files(&root)
        .map_err(|error| format!("collect failed: {error}"))?;
    let build = af_graph::project::clean_build(&files).map_err(|e| e.0)?;
    let mut graph_store = af_graph::GraphStore::open(&store).map_err(|e| e.0)?;
    let nodes = build["nodes"].as_array().cloned().unwrap_or_default();
    let edges = build["edges"].as_array().cloned().unwrap_or_default();
    let old_ids: Vec<String> = graph_store
        .nodes()
        .iter()
        .map(|node| node["id"].as_str().unwrap_or_default().to_string())
        .collect();
    let generation = graph_store
        .commit(graph_store.generation(), &nodes, &edges, &old_ids, &[])
        .map_err(|e| e.0)?;
    Ok(json!({
        "ok": true, "store": store.to_string_lossy(), "generation": generation,
        "files": build["parsed_files"], "nodes": nodes.len(), "edges": edges.len(),
        "unresolved": build["unresolved"].as_array().map(|u| u.len()).unwrap_or(0),
    })
    .to_string())
}

fn graph_show(store: PathBuf) -> Result<String, String> {
    let graph_store = af_graph::GraphStore::open(&store).map_err(|e| e.0)?;
    let kinds = count_kinds(&graph_store.nodes());
    Ok(json!({
        "ok": true, "generation": graph_store.generation(),
        "nodes": graph_store.node_count(), "edges": graph_store.edge_count(),
        "kinds": kinds,
    })
    .to_string())
}

fn count_kinds(nodes: &[Value]) -> BTreeMap<String, usize> {
    let mut kinds = BTreeMap::new();
    for node in nodes {
        let kind = node["kind"].as_str().unwrap_or_default().to_string();
        *kinds.entry(kind).or_insert(0) += 1;
    }
    kinds
}

fn self_test() -> Result<String, String> {
    use af_graph::{delta, GraphStore};
    let scratch = std::env::temp_dir().join(format!("af-selftest-{}", af_entropy::token()));
    std::fs::create_dir_all(&scratch).map_err(|e| e.to_string())?;
    let result = (|| -> Result<Value, String> {
        let lib_v1 = b"def helper():\n    return 1\n".to_vec();
        let app = b"from lib import helper\n\ndef main():\n    return helper()\n".to_vec();
        let mut files = BTreeMap::new();
        files.insert("lib.py".to_string(), lib_v1.clone());
        files.insert("app.py".to_string(), app.clone());
        let store_path = scratch.join("graph.json");
        let mut graph = GraphStore::open(&store_path).map_err(|e| e.0)?;
        let build = af_graph::project::clean_build(&files).map_err(|e| e.0)?;
        let nodes = build["nodes"].as_array().cloned().unwrap_or_default();
        let edges = build["edges"].as_array().cloned().unwrap_or_default();
        graph.commit(0, &nodes, &edges, &[], &[]).map_err(|e| e.0)?;
        // Private-body edit of lib.py: the closure must stay at parity.
        let lib_v2 = b"def helper():\n    return 1  # changed\n".to_vec();
        let digest_before = sha_hex(&lib_v1);
        let delta_plan = delta::file_delta("lib.py", Some(&lib_v2), Some(&digest_before), None)
            .map_err(|e| e.0)?;
        let report = delta::apply_delta(&mut graph, &delta_plan).map_err(|e| e.0)?;
        let mut files_after = BTreeMap::new();
        files_after.insert("lib.py".to_string(), lib_v2);
        files_after.insert("app.py".to_string(), app);
        let clean = af_graph::project::clean_build(&files_after).map_err(|e| e.0)?;
        let mut delta_ids: Vec<String> = graph
            .edges()
            .iter()
            .map(|edge| edge["id"].as_str().unwrap_or_default().to_string())
            .collect();
        let mut clean_ids: Vec<String> = clean["edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|edge| edge["id"].as_str().unwrap_or_default().to_string())
            .collect();
        // The store iterates sorted; the clean build yields insertion order.
        delta_ids.sort();
        clean_ids.sort();
        let parity = delta_ids == clean_ids;
        Ok(json!({
            "ok": parity, "delta_report": report,
            "edges_delta": delta_ids.len(), "edges_clean": clean_ids.len(),
        }))
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    let value = result?;
    if value["ok"] == json!(true) {
        Ok(format!("self-test PASS: {value}"))
    } else {
        Err(format!("self-test FAIL: {value}"))
    }
}

fn sha_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

/// Read-only kernel tools exposed over MCP. Mutating tools stay out of the
/// stdio surface on purpose: MCP clients are untrusted context until a host
/// grants more (the envelope rules apply to them as to any model text).
fn mcp_tools() -> Vec<af_program::mcp_stdio::McpTool<'static>> {
    vec![
        af_program::mcp_stdio::McpTool {
            spec: af_llm::ToolSpec {
                name: "kernel_version".into(),
                description: "atomic-flow Rust kernel version".into(),
                parameters: json!({"type": "object"}),
            },
            execute: &|_arguments| json!({"version": env!("CARGO_PKG_VERSION")}),
        },
        af_program::mcp_stdio::McpTool {
            spec: af_llm::ToolSpec {
                name: "backend_ping".into(),
                description: "scripted backend smoke (settled usage, zero spend)".into(),
                parameters: json!({"type": "object"}),
            },
            execute: &|_arguments| {
                let router = af_llm::Router::new(vec![Box::new(af_llm::ScriptedBackend::echo())]);
                let mut request = af_llm::ChatRequest::new(
                    "scripted",
                    vec![af_llm::ChatMessage::new("user", "ping")],
                );
                request.max_output_tokens = Some(8);
                match router.route(&request, &af_llm::Cancel::new()) {
                    af_llm::RouteOutcome::Completed { backend, result } => json!({
                        "ok": true, "backend": backend, "content": result.content,
                        "tokens_in": result.usage.tokens_in, "cost_usd": result.usage.cost_usd,
                    }),
                    af_llm::RouteOutcome::Hold { backend, detail } => {
                        json!({"error": format!("HOLD {backend}: {detail}")})
                    }
                    af_llm::RouteOutcome::Exhausted { refusals } => {
                        json!({"error": format!("exhausted: {refusals:?}")})
                    }
                }
            },
        },
        af_program::mcp_stdio::McpTool {
            spec: af_llm::ToolSpec {
                name: "session_read".into(),
                description: "read a session snapshot (events, pending and unresolved holds)"
                    .into(),
                parameters: json!({
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }),
            },
            execute: &|arguments| {
                let Some(id) = arguments.get("id").and_then(Value::as_str) else {
                    return json!({"error": "session_read requires id"});
                };
                let Ok(mut journal) = af_journal::SessionJournal::open(id) else {
                    return json!({"error": format!("session {id} not found")});
                };
                match journal.read() {
                    Ok(snapshot) => json!({
                        "ok": true, "session_id": snapshot.session_id,
                        "events": snapshot.events.len(),
                        "pending_call_ids": snapshot.pending_call_ids(),
                        "unresolved_call_ids": snapshot.unresolved_call_ids(),
                    }),
                    Err(error) => json!({"error": error.0}),
                }
            },
        },
    ]
}

fn agent_journey(
    root_id: String,
    file: String,
    replacement: String,
    goal: String,
    session_id: String,
) -> Result<String, String> {
    // A session bound to the operator's cwd provides the journey evidence.
    let project = std::env::current_dir().map_err(|e| e.to_string())?;
    let mut journal = af_journal::SessionJournal::create(&project, Some(&session_id), None)
        .or_else(|_| af_journal::SessionJournal::open(&session_id))
        .map_err(|e| e.0)?;
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
    let mut journey =
        af_agent::Journey::new(&registry, &mut journal, af_agent::JourneyLimits::default());
    let plan = af_agent::JourneyPlan {
        root_id,
        file,
        replacement: Some(replacement),
        goal,
    };
    let report = journey.run(&plan).map_err(|e| e.0)?;
    let value = json!({
        "ok": report.ok, "verified": report.verified,
        "goal": report.goal, "steps": report.steps,
        "refusal": report.refusal,
        "session_id": journal.session_id,
    });
    if report.ok {
        Ok(serde_json::to_string(&value).map_err(|e| e.to_string())?)
    } else {
        Err(serde_json::to_string(&value).map_err(|e| e.to_string())?)
    }
}

/// Build the guided-edit toolset for a granted root: context pack over the
/// root's graph (built on the fly from the admitted snapshot) and the default
/// check job. Server and CLI share this construction.
pub fn edit_toolset_for<'a>(
    registry: &'a af_sandbox::SandboxRegistry,
    root_id: &str,
) -> Result<af_loop::edit::EditToolset<'a>, String> {
    let root_item = registry.get(root_id).map_err(|e| e.0)?;
    let root = PathBuf::from(root_item["path"].as_str().unwrap_or_default());
    let files = af_graph::project::collect_source_files(&root)?;
    let build = af_graph::project::clean_build(&files).map_err(|e| e.0)?;
    let store_dir = af_sandbox::user_dir().join("graphs");
    std::fs::create_dir_all(&store_dir).map_err(|e| e.to_string())?;
    let document = json!({
        "schema_version": 1, "generation": 0,
        "nodes": build["nodes"], "edges": build["edges"],
    });
    std::fs::write(
        store_dir.join(format!("{root_id}.json")),
        af_graph::canonical::canonical_bytes(&document),
    )
    .map_err(|e| e.to_string())?;
    let document: Value = serde_json::from_slice(&af_graph::canonical::canonical_bytes(&document))
        .map_err(|e| e.to_string())?;
    let runner = af_exec::JobRunner::open(&af_sandbox::user_dir().join("jobs")).map_err(|e| e.0)?;
    // A requirement checker must be bound by the host. No placeholder exit-zero
    // command may be displayed as evidence that source requirements passed.
    let check_argv = Vec::new();
    af_loop::edit::EditToolset::new(registry, root_id, runner, document, check_argv, root, 4)
}

fn sandbox_grant(
    root_id: String,
    path: PathBuf,
    write: Vec<String>,
    host_approved: bool,
) -> Result<String, String> {
    if !host_approved {
        return Err(
            "sandbox grant requires --host-approved (the human running af is the host)".into(),
        );
    }
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
    let granted = registry
        .grant(&root_id, &path, &write, true)
        .map_err(|e| e.0)?;
    Ok(json!({"ok": true, "granted": granted}).to_string())
}

fn sandbox_list() -> Result<String, String> {
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
    let roots = registry.list_roots().map_err(|e| e.0)?;
    Ok(json!({"ok": true, "roots": roots}).to_string())
}

fn sandbox_read(
    root_id: String,
    path: String,
    offset: usize,
    limit: usize,
) -> Result<String, String> {
    let registry = af_sandbox::SandboxRegistry::new("default").map_err(|e| e.0)?;
    let mut session = af_sandbox::SandboxSession::new(&registry).map_err(|e| e.0)?;
    let page = session
        .read(&root_id, &path, offset, limit, false)
        .map_err(|e| e.0)?;
    Ok(serde_json::to_string(&page).map_err(|e| e.to_string())?)
}

fn session_create(id: Option<String>, project: PathBuf) -> Result<String, String> {
    let journal =
        af_journal::SessionJournal::create(&project, id.as_deref(), None).map_err(|e| e.0)?;
    Ok(json!({"ok": true, "session_id": journal.session_id, "path": journal.path.to_string_lossy()}).to_string())
}

fn session_append(
    id: String,
    event_type: String,
    payload_json: String,
    call_id: Option<String>,
    outcome: Option<String>,
) -> Result<String, String> {
    let payload: Value = serde_json::from_str(&payload_json)
        .map_err(|error| format!("payload_json is not valid JSON: {error}"))?;
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|e| e.0)?;
    let event = journal
        .append(
            &event_type,
            &payload,
            call_id.as_deref(),
            outcome.as_deref(),
            None,
        )
        .map_err(|e| e.0)?;
    Ok(serde_json::to_string(&event).map_err(|e| e.to_string())?)
}

fn session_read(id: String) -> Result<String, String> {
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|e| e.0)?;
    let snapshot = journal.read().map_err(|e| e.0)?;
    Ok(json!({
        "ok": true, "session_id": snapshot.session_id,
        "events": snapshot.events.len(),
        "incomplete_tail": snapshot.incomplete_tail,
        "tail_bytes": snapshot.tail_bytes,
        "pending_call_ids": snapshot.pending_call_ids(),
        "unresolved_call_ids": snapshot.unresolved_call_ids(),
    })
    .to_string())
}

fn session_recover(id: String) -> Result<String, String> {
    let mut journal = af_journal::SessionJournal::open(&id).map_err(|e| e.0)?;
    match journal.recover_tail().map_err(|e| e.0)? {
        Some(backup) => Ok(json!({"ok": true, "backup": backup.to_string_lossy()}).to_string()),
        None => Ok(json!({"ok": true, "backup": null}).to_string()),
    }
}

fn job_run(
    argv: Vec<String>,
    cwd: PathBuf,
    timeout_secs: u64,
    store: PathBuf,
) -> Result<String, String> {
    let runner = af_exec::JobRunner::open(&store).map_err(|e| e.0)?;
    let spec = af_exec::JobSpec::new(argv, cwd, timeout_secs).map_err(|e| e.0)?;
    let status = runner.run(&spec).map_err(|e| e.0)?;
    Ok(serde_json::to_string(&status).map_err(|e| e.to_string())?)
}

fn job_status(store: PathBuf) -> Result<String, String> {
    let runner = af_exec::JobRunner::open(&store).map_err(|e| e.0)?;
    let jobs = runner.jobs().map_err(|e| e.0)?;
    let unknown = runner.unresolved_unknown_ids().map_err(|e| e.0)?;
    Ok(json!({
        "ok": true, "jobs": jobs.values().collect::<Vec<_>>(),
        "unresolved_unknown_ids": unknown,
    })
    .to_string())
}

fn job_reconcile(
    store: PathBuf,
    job_id: String,
    actor: String,
    reason: String,
) -> Result<String, String> {
    let runner = af_exec::JobRunner::open(&store).map_err(|e| e.0)?;
    if runner.unresolved_unknown_ids().map_err(|e| e.0)?.is_empty() {
        return Err("no unknown outcomes to reconcile".into());
    }
    let reconciled = runner
        .reconcile(
            &job_id,
            &actor,
            &reason,
            &json!({"via": "af cli", "evidence": af_entropy::token64()}),
        )
        .map_err(|e| e.0)?;
    Ok(serde_json::to_string(&reconciled).map_err(|e| e.to_string())?)
}

fn backend_ping() -> Result<String, String> {
    let router = af_llm::Router::new(vec![Box::new(af_llm::ScriptedBackend::echo())]);
    let mut request =
        af_llm::ChatRequest::new("scripted", vec![af_llm::ChatMessage::new("user", "ping")]);
    request.max_output_tokens = Some(8);
    match router.route(&request, &af_llm::Cancel::new()) {
        af_llm::RouteOutcome::Completed { backend, result } => Ok(json!({
            "ok": true, "backend": backend, "content": result.content,
            "cost_usd": result.usage.cost_usd,
        })
        .to_string()),
        af_llm::RouteOutcome::Hold { backend, detail } => Err(format!("HOLD {backend}: {detail}")),
        af_llm::RouteOutcome::Exhausted { refusals } => Err(format!("exhausted: {refusals:?}")),
    }
}

fn backend_call(
    base_url: String,
    api_key_env: String,
    model: String,
    prompt: String,
) -> Result<String, String> {
    let router = af_llm::Router::new(vec![Box::new(af_llm::OpenAiBackend::new(
        "openai",
        &base_url,
        &api_key_env,
    ))]);
    let mut request =
        af_llm::ChatRequest::new(&model, vec![af_llm::ChatMessage::new("user", &prompt)]);
    request.max_output_tokens = Some(256);
    match router.route(&request, &af_llm::Cancel::new()) {
        af_llm::RouteOutcome::Completed { backend, result } => Ok(json!({
            "ok": true, "backend": backend,
            "content_chars": result.content.chars().count(),
            "tokens_in": result.usage.tokens_in, "tokens_out": result.usage.tokens_out,
            "truncated": result.truncated,
        })
        .to_string()),
        af_llm::RouteOutcome::Hold { backend, detail } => Err(format!(
            "HOLD {backend}: {detail} (unknown outcomes are never retried)"
        )),
        af_llm::RouteOutcome::Exhausted { refusals } => Err(format!("exhausted: {refusals:?}")),
    }
}

fn backend_call_stream(
    base_url: String,
    api_key_env: String,
    model: String,
    prompt: String,
) -> Result<String, String> {
    let backend = af_llm::OpenAiBackend::new("openai", &base_url, &api_key_env);
    let mut request =
        af_llm::ChatRequest::new(&model, vec![af_llm::ChatMessage::new("user", &prompt)]);
    request.max_output_tokens = Some(256);
    let mut deltas = 0usize;
    // Deltas stream to stdout live; the settled summary prints last so a
    // consumer splitting on the final line gets clean JSON.
    let outcome = backend.chat_stream(&request, &af_llm::Cancel::new(), &mut |delta| {
        print!("{delta}");
        deltas += 1;
        let _ = std::io::Write::flush(&mut std::io::stdout());
    });
    println!();
    match outcome {
        af_llm::BackendOutcome::Completed(result) => Ok(json!({
            "ok": true, "deltas": deltas,
            "content_chars": result.content.chars().count(),
            "tokens_in": result.usage.tokens_in, "tokens_out": result.usage.tokens_out,
            "truncated": result.truncated,
        })
        .to_string()),
        af_llm::BackendOutcome::Refused { sent, reason } => {
            Err(format!("REFUSED (sent={sent}): {reason}"))
        }
        af_llm::BackendOutcome::Unknown { sent, detail } => Err(format!(
            "HOLD (sent={sent}): {detail} (unknown outcomes are never retried)"
        )),
    }
}

// af-agents is exercised through its own tests; the binary re-exports nothing
// from it yet. Keep the dependency explicit for the authority-plane wiring
// that lands with the task loop.
#[allow(unused_imports)]
use af_agents as _agents_touch;
