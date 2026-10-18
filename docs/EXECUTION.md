# Execution and Colab validation

The default `execution.mode` is `docker`. Quality tools and matching/step pytest
all use `agents/execution.py`. Missing Docker, daemon or image never enables a local fallback.
Syntax compilation parses/compiles source without executing imports; it remains in the host process.

Build the development check image on a Docker-capable machine:

```sh
docker build -t atomic-flow-checks:local -f tools/Dockerfile.checks .
```

Only the current project/task workspace is mounted. Containers run as the nonroot
Unix caller's uid/gid (65534:65534 on a root/Windows host),
with no network, read-only root filesystem, dropped capabilities, no-new-privileges,
bounded pids/memory/CPU and a temporary /tmp. No provider variables, private agent home,
Docker socket or host-wide mounts are passed. Project bind-mount permissions still apply.
Dependencies must be installed in the image before execution; checks cannot download them.
The supplied image is a development recipe with floating Python/dependency versions;
release image digests and dependency locking remain work to do.

For trusted local development only:

```powershell
$env:ATOMIC_FLOW_EXECUTION_MODE = 'local'
```

On Linux: `export ATOMIC_FLOW_EXECUTION_MODE=local`.
This mode is **not a sandbox**: project tests can access host files and network.
Environment filtering prevents accidental credential inheritance; it cannot hide host credentials
from code with filesystem access. Automatic third-party pytest plugin loading is disabled in both modes.
Host/virtualenv test dependencies remain available in local mode.
Untrusted project config cannot set execution or task spending limits.

Output tails are bounded per stream. Commands use no shell and no stdin. Cancellation and
deadlines stop commands; POSIX process groups also clean up ordinary children after parent exit.
Windows uses taskkill /T /F: detached/orphaned descendants are a known best-effort limitation.
Hostile code can escape local process groups; use the container boundary for untrusted execution.
Container cleanup uses docker rm -f even when the client is interrupted.

`atomic task --cancel TASK_ID` writes an independent cancellation marker. It survives task JSON
saves. The runner checks it before/after model responses, during commands, before tools,
between steps and during publication. A synchronous model request already in progress is only
cancelled locally after it returns or hits the backend timeout; no provider-side cancellation or
refund is promised. Cancelled tasks currently use status `failed` with `cancelled by user` error.
An OS-held task lease excludes simultaneous runners for the same task. Manual `--recover`
should be used only after stopping its runner; it is not coordinated by this lease yet.
Publication observes cancellation and recovers already-written files using the existing journal.

## Colab handoff

Run `python tools/build_colab_bundle.py`. It produces:

- `artifacts/colab/atomic-flow-colab-oneclick.ipynb`: self-contained current source bundle.
- `artifacts/colab/atomic-flow-validation.ipynb`: version with a separate ZIP upload cell.
- `artifacts/colab/atomic-flow-working-tree.zip` and `.zip.sha256`.

The allowlisted bundle includes source, tests, metadata and validation helpers, including
uncommitted changes. It excludes .env files, .git, .venv, private agent state and credentials.
The notebook checks archive and per-file SHA256, installs `.[dev]`, runs CPU/local tests,
then attempts installation of the standard Ubuntu Docker package/daemon and a real boundary test.
No GPU, API keys, paid inference or Drive mount is needed.

Run all notebook cells on a fresh CPU runtime. Download `colab-results.zip` from the final cell.
`results.json` distinguishes CPU tests from `container=PASS/FAIL/UNAVAILABLE/IMAGE_BUILD_FAILED`.
UNAVAILABLE is **not** a successful isolation check, even if local tests pass.
There is no privileged nesting, alternate networking or security bypass if Colab cannot start Docker.
If unavailable, use a Docker-capable Linux VM for that acceptance check.

The real Docker test is opt-in (`ATOMIC_FLOW_TEST_DOCKER=1`) and checks nonroot uid,
host-file invisibility outside the workspace, read-only root and blocked external network.
Flag construction tests alone do not validate OS isolation, resource exhaustion, hostile
escape resistance or arbitrary dependency compatibility. Those remain separate acceptance work.

## Continuous integration

`.github/workflows/checks.yml` runs on pushes to main/codex branches, pull requests and manual dispatch.
Python 3.11 and 3.12 run the standard and root v3 tests. A separate Ubuntu Docker job runs
`python tools/colab_validate.py --require-docker`, including nonroot/container boundaries,
matching pytest, successful task publication, failed-edit rollback and timeout/cancellation cleanup.
Missing Docker is a CI failure, not a skipped success. Logs, tested commit, installed dependencies
and image ID are uploaded as a 14-day artifact. No model credentials or inference are needed.

Development runs locally on Windows; container acceptance runs on GitHub's Linux runner.
The current laptop has 3.4 GiB usable RAM, insufficient for Docker Desktop's published 8 GB
requirement. No local Docker/WSL installation is performed as part of this setup.
