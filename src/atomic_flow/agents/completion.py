"""Host completion gate over final workspace, independent of final model prose."""
from __future__ import annotations

import hashlib
import json
import time

from atomic_flow.agents.diagnostics import syntax_diagnostic
from atomic_flow.agents.execution import ExecutionUnavailable, run_python
from atomic_flow.agents.source import confined_path, digest


def assess_completion(task, changeset, config, cancel_check=lambda: None) -> dict:
    report = {"schema_version": 1, "ready_to_publish": False, "checked_at": time.time(),
              "checks": [], "diagnostics": [], "tests": "not_run", "model_calls": 0}
    cancel_check()
    if not task.steps or task.current_index != len(task.steps) or not any(s.status == "done" for s in task.steps):
        report["checks"].append({"name": "completed_steps", "status": "failed"})
        return report
    unresolved = [s.index for s in task.steps if s.status not in {"done", "superseded"}]
    running = [s.index for s in task.steps if s.status == "running"]
    if running or unresolved:
        report["checks"].append({"name": "completed_steps", "status": "failed", "unresolved": unresolved})
        return report
    report["checks"].append({"name": "completed_steps", "status": "passed",
                             "superseded_by_replanning": [s.index for s in task.steps if s.status == "superseded"]})
    try:
        changeset.check_original()
        manifest = changeset.validate_workspace()
        changed = sorted(p for p in manifest.keys() | changeset.state["baseline"].keys()
                         if manifest.get(p) != changeset.state["baseline"].get(p))
        report["changed_paths"] = [{"path": p, "before_hash": changeset.state["baseline"].get(p),
                                    "after_hash": manifest.get(p)} for p in changed]
        report["workspace_manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        report["checks"].append({"name": "approved_diff_and_freshness", "status": "passed"})
        for path in changed:
            if path in manifest and path.lower().endswith(".ipynb"):
                from atomic_flow.notebooks import NotebookDocument
                notebook = NotebookDocument.load(changeset.workspace, path, expected_hash=manifest[path], private_workspace=True,
                                                 tree_ignore=config.tree.ignore, respect_gitignore=config.tree.respect_gitignore)
                if notebook.read()["missing_ids"]:
                    raise ValueError("Changed notebook needs explicit cell ID normalization")
                report["checks"].append({"name": "notebook_structure", "status": "passed", "path": path,
                                         "execution": "not_run"})
        for path in changed:
            cancel_check()
            if path not in manifest or not path.endswith((".py", ".pyi")):
                continue
            data = confined_path(changeset.workspace, path).read_bytes()
            if digest(data) != manifest[path]:
                raise ValueError("Workspace changed during final verification")
            try:
                compile(data, path, "exec", dont_inherit=True)
            except SyntaxError as exc:
                report["diagnostics"].append(syntax_diagnostic(path, data, exc))
        report["checks"].append({"name": "final_python_syntax", "status": "failed" if report["diagnostics"] else "passed",
                                 "scope": "Changed surviving Python files only"})
        if report["diagnostics"]:
            return report
        if config.task.final_check == "pytest" or any(s.verify_with == "pytest" and s.status == "done" for s in task.steps):
            result = run_python(["-m", "pytest", "-q", "--no-header", "-x"], changeset.workspace,
                                config=config.execution, timeout=180, cancel_check=cancel_check)
            report["tests"] = "passed" if result.returncode == 0 and not result.timed_out else "failed"
            report["test_exit_code"] = result.returncode
            report["test_timed_out"] = result.timed_out
            report["test_output_tail"] = (result.stdout + result.stderr)[-3000:]
            if report["tests"] != "passed":
                return report
        cancel_check()
        changeset.check_original()
        if changeset.validate_workspace() != manifest:
            raise ValueError("Workspace changed during final checks")
    except ExecutionUnavailable:
        report["tests"] = "unavailable"
        report["checks"].append({"name": "final_tests", "status": "unavailable"})
        return report
    except (OSError, ValueError) as exc:
        report["checks"].append({"name": "workspace_integrity", "status": "failed", "error_type": type(exc).__name__})
        return report
    report["ready_to_publish"] = True
    report["limits"] = "Host gates confirm declared checks and approved bytes, not semantic fulfillment of every user requirement."
    return report
