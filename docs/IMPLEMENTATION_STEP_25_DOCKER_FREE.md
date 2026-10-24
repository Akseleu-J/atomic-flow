# Package 25: Docker-free core entry path and execution boundaries

Docker is optional for installation, indexing, retrieval, CLI/SDK/API startup and
source-only questions. The execution default remains Docker. An operation that
actually runs project tests/checkers must select an available execution boundary;
absence of Docker never silently enables trusted-local execution.

This guide describes the current implementation, including remaining technical
barriers. It does not introduce a native filesystem/network sandbox.

## Minimal installation

From a reviewed atomic-flow checkout, use Python 3.11 or 3.12 and a venv:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell alternative:
# .\.venv\Scripts\Activate.ps1
python -m pip install .
atomic --help
atomic tree build --path /absolute/path/to/your/project
atomic tree status --path /absolute/path/to/your/project
```

For an exact CI-tested distribution, install the selected downloaded wheel
instead of `.`; compare its SHA-256 with its CI artifact manifest. Do not assume
the current working checkout and a previously built wheel are identical.
The package's required dependencies are Pydantic, PyYAML and pathspec. Core
installation does not require Docker SDK/CLI, Kaggle, Torch, Transformers, an
analyzer, MCP server or development test tools. The broad README developer
installation with `[api,kaggle,benchmarks,dev]` is not the minimal runtime path.

The initial index and an offline retrieval preview need no model or executor:

```sh
atomic ask "Where is the source entry point?" --project /absolute/path/to/your/project --dry-run --no-translate
```

`--no-translate` is essential to the zero-model-call dry-run contract; ordinary
asking invokes the selected provider and its normal quotas/accounting.

## A source question without local execution

Configure the chosen provider URL/model using host configuration or process-only
environment variables. For example, for an already running local OpenAI-compatible
service, replace the URL and model with that service's actual values:

```sh
atomic config set backends.openai-compatible.base_url http://127.0.0.1:8001/v1
atomic config set backends.openai-compatible.model YOUR_AVAILABLE_MODEL
atomic ask "Explain the entry point using source references" --project /absolute/path/to/your/project --backend openai-compatible --mode simple
```

These commands configure a client; they do not install or start a model server.
For a remote provider, supply its approved HTTPS URL and its credential through
the provider's documented process-only credential input, never a checked-in
project file. Configure current token prices/budget rules before live calls;
zero prices are valid only for a confirmed free route. No live provider run was
performed to validate this guide.

CLI source questions still expose the default quality-check tool. A model can
request `run_command`, which reaches the execution gate even when edits are off.
To make a source-only CLI/API deployment explicit, merge this rule into the
host-owned `~/.atomic_flow/policy.yaml` (or `ATOMIC_FLOW_HOME/policy.yaml`), preserving
any existing stricter rules:

```yaml
rules:
  - match:
      tool: run_command
    action: forbid
```

An attempted checker is denied; the system cannot report a checker result as
passed. This is a useful boundary, but a denied model tool request can still fail
that answer. SDK hosts can avoid offering execution tools at all:

```python
from pathlib import Path
from atomic_flow.agents.tools import ROLE_TOOLS
from atomic_flow.backends.router import make_backend
from atomic_flow.config import load_config
from atomic_flow.sdk import start_answer

project = Path("/absolute/path/to/your/project")
config = load_config(project)
backend = make_backend(config, "openai-compatible")
source_tools = {
    "read_node", "read_file", "search_tree", "search_text", "find_files",
    "find_callers", "read_result", "tool_catalog",
}
role_tools = {role: [tool for tool in tools if tool in source_tools]
              for role, tools in ROLE_TOOLS.items()}
handle = start_answer(config, project, backend,
                      "Explain the entry point using source references",
                      mode="simple", role_tools=role_tools)
result = handle.result(timeout=180)
print(result.model_dump())
```

This host chooses the exposed tool set. It does not grant edits, start arbitrary
executables or provision dependencies. The actual provider/model quality remains
a separate acceptance gate.

## Optional HTTP API

Inside the same reviewed checkout and venv:

```sh
python -m pip install '.[api]'
atomic up --host 127.0.0.1 --port 8000
```

The API serves `/health`, `/ready` and `/v1/answer` directly in Python; no Docker
daemon is started. A second terminal can send a source question:

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl -X POST http://127.0.0.1:8000/v1/answer -H 'Content-Type: application/json' -d '{"query":"Explain the entry point", "project":"/absolute/path/to/your/project", "backend":"openai-compatible", "mode":"simple"}'
```

On Windows use `curl.exe` or PowerShell `Invoke-RestMethod` with the same JSON.
The host policy above also governs API checker calls. `/ready` confirms the API's
own admission/ownership state; it does not attest provider reachability, analyzer
quality, Docker image availability or a project's test environment. Non-loopback
binding additionally requires the existing server key and project-root controls;
this guide keeps the service on loopback.

## Checks, tests and untrusted project execution

| Operation | Docker on the user's client | Current boundary |
| --- | --- | --- |
| Install/import/index/retrieval | No | Source parsing/admission; no project imports for indexing |
| Source-only CLI/SDK/API answer | No | Provider plus source tools; exclude/deny checker tools explicitly |
| Python syntax compilation | No | Parse/compile only; does not execute project imports |
| Quality tools/tests, default profile | Yes if run locally | Optional Docker executor; refuses missing engine/image |
| Quality tools/tests, trusted local profile | No | Explicit host execution with filesystem/network access |
| Remote worker SDK submission | No | Trusted remote Linux host runs pinned-image Docker worker |
| Untrusted native execution without Docker | Unsupported | No verified native filesystem/network sandbox exists |

For a trusted local project only, explicitly select the existing local mode:

```powershell
$env:ATOMIC_FLOW_EXECUTION_MODE = 'local'
atomic job start --project C:\absolute\project --mode local -- -m pytest -q
```

POSIX equivalent: `export ATOMIC_FLOW_EXECUTION_MODE=local`. The test environment
must already contain pytest and the project's reviewed dependencies. This mode
is not isolation: the executed code can reach host files/network. Windows checks
and the approved LSP/MCP clients use owned Job Objects for process lifetime;
POSIX uses process groups. Neither provides native filesystem/network isolation.
The older taskkill-only description in `EXECUTION.md` predates the owned Windows
implementation; use the current source and package-23 evidence for that scope.

For untrusted execution, retain the Docker executor on a suitable host or use the
existing explicitly configured remote SDK worker. `sdk.connect_worker` and
`remote.build_bundle` support credential-scoped, hash-bound submission and
observation. This is an SDK host integration, not an automatic fallback: the
current TaskRunner `run_python` checker path does not transparently switch to a
remote worker. The remote worker currently still requires Linux/Docker; a
Docker-free client is not a Docker-free backend deployment. No newly deployed
external worker is claimed by this guide.

## Evidence, support and remaining work

The installed-release workflow selects Ubuntu 24.04, Windows 2022 and macOS 14,
Python 3.11/3.12, core/API combinations. Only terminal successful exact-source
matrix artifacts establish tested wheel support. Package 23's local real
pylsp/MCP staged journey passed on Windows, but it used inherited core tool-venv
dependencies and does not replace that clean matrix. Current terminal CI status
belongs in the coordinating implementation report, not a timeless claim here.

This pass verified CLI, SDK and tree imports in a process whose Popen constructor
was replaced with a fail-on-use sentinel. Imports passed, no child process was
launched and no `docker` Python module was imported. Static packaging inspection
confirmed no Docker dependency. This does not claim every possible tool path
avoids execution; the table above states the actual boundary.

Remaining technical barriers are specific:

1. Default answerer/critic catalogs include `run_command`; CLI/API need host
   policy configuration to guarantee no checker launch. SDK can constrain its
   catalog directly. A dedicated core-only capability profile is not implemented.
2. `atomic doctor --json` currently aggregates executor availability into overall
   health. Missing optional Docker can make the report unhealthy even when all
   requested indexing/source operations work. Treat its individual checks as
   scoped diagnostics, not a mandatory startup dependency.
3. Native isolated execution is not implemented, and the remote executor is not
   wired as the default task-checker adapter. Docker is optional at the client
   entry point but remains the implemented isolated execution backend.
4. Test dependencies need host/image provisioning, and the development Docker
   image recipe remains distinct from a reproducible signed release image.

The guide adds no UI work, automatic local fallback or new isolation claims.
