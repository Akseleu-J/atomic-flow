"""Read-only pinned-source walkthrough; never import or execute the target repo."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from atomic_flow.graph.enrichment import SemanticBudget, enrich


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("walkthrough output already exists; preserve original evidence")
    revision = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", args.commit + "^{commit}"]).decode().strip()
    if revision != args.commit or len(revision) != 40:
        raise ValueError("provide a full immutable commit SHA")
    all_paths = subprocess.check_output(["git", "-C", str(args.repo), "ls-tree", "-r", "--name-only", revision]).decode().splitlines()
    if not args.path or any(path not in all_paths for path in args.path):
        raise ValueError("select explicit paths present at the pinned revision")
    files = {path: subprocess.check_output(["git", "-C", str(args.repo), "show", revision + ":" + path]) for path in args.path}

    def analyzer(request):
        line, first = next((index, text) for index, text in enumerate(
            request["source"].splitlines(keepends=True), 1) if text.strip())
        return {"summary": "Scripted source walkthrough fragment for " + request["path"],
                "tags": ["source-walkthrough"], "evidence": [{"node_id": "file:" + request["path"],
                "start_line": line, "end_line": line, "quote": first}]}

    cache = {}
    budget = SemanticBudget(len(files))
    identity = dict(extractor="python-ast/1", prompt="walkthrough/1", model="scripted", config="explicit-pinned-paths", language="en")
    result = enrich(files, project_scope=str(args.repo.resolve()) + "::" + revision, identity=identity,
                    analyzer=analyzer, budget=budget, cache=cache)
    repeat = enrich(files, project_scope=str(args.repo.resolve()) + "::" + revision, identity=identity,
                    analyzer=analyzer, budget=budget, cache=cache)
    assert repeat["calls"] == 0
    result.update(repository=str(args.repo), pinned_commit=revision, selected_paths=sorted(files),
                  omitted_by_explicit_selection=sorted(set(all_paths) - files.keys()),
                  scope="bounded selected-source walkthrough; no full-repository semantic coverage claim",
                  repeat_calls=repeat["calls"], new_provider_calls=0,
                  source_sha256={p: hashlib.sha256(raw).hexdigest() for p, raw in files.items()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pinned_commit": revision, "selected": len(files), "omitted": len(result["omitted_by_explicit_selection"]),
                      "first_calls": result["calls"], "repeat_calls": 0, "semantic_correctness": "not_assessed"}))


if __name__ == "__main__":
    main()
