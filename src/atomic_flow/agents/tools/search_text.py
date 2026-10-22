"""Bounded literal/regex search over admitted indexed source revisions."""
from __future__ import annotations

import fnmatch
import multiprocessing
import re
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atomic_flow.activation.redact import redact
from atomic_flow.agents.base import Tool, ToolContract
from atomic_flow.agents.tools.file_paths import admitted_path


class SearchTextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=256)
    regex: bool = False
    case_sensitive: bool = True
    path_glob: str = Field(default="*", min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0, le=10000, description="Matching-line offset within this bounded scan.")
    max_results: int = Field(default=50, ge=1, le=200)
    file_offset: int = Field(default=0, ge=0, le=100000)
    max_files: int = Field(default=200, ge=1, le=1000)


def _search(files, query, regex, case_sensitive, offset, maximum):
    matcher = re.compile(query, 0 if case_sensitive else re.IGNORECASE) if regex else None
    needle = query if case_sensitive else query.casefold()
    results = []
    seen = 0
    for path, content_hash, text in files:
        for line_number, line in enumerate(text.splitlines(), 1):
            hit = bool(matcher.search(line)) if matcher else needle in (line if case_sensitive else line.casefold())
            if not hit:
                continue
            if seen >= offset:
                results.append({"path": path, "line": line_number, "content_hash": content_hash,
                                "text": line[:512], "line_truncated": len(line) > 512})
                if len(results) > maximum:
                    return results[:maximum], True
            seen += 1
    return results, False


def _regex_worker(sender, files, query, case_sensitive, offset, maximum):
    try:
        sender.send(_search(files, query, True, case_sensitive, offset, maximum))
    finally:
        sender.close()


class SearchTextTool(Tool):
    name = "search_text"
    description = ("Search indexed, nonprivate UTF-8 source by literal or regex, with line numbers and full-file hashes. "
                   "Results are bounded and redacted. Regex evaluation has a hard subprocess deadline.")
    args_schema = SearchTextArgs
    contract = ToolContract(effects=("read",), timeout_seconds=30, max_output_bytes=262144)

    def run(self, args: SearchTextArgs, ctx) -> dict[str, Any]:
        if args.regex:
            try:
                re.compile(args.query)
            except re.error as exc:
                return {"error": f"invalid regex: {exc}"}
        candidates = [path for path in sorted(ctx.file_hashes) if fnmatch.fnmatchcase(path, args.path_glob)]
        files = []
        skipped = 0
        examined = 0
        byte_count = 0
        for path in candidates[args.file_offset:args.file_offset + args.max_files]:
            ctx.cancel_check()
            examined += 1
            try:
                admitted_path(ctx, path, existing=True)
                data = ctx.read_source(path)
                if b"\x00" in data:
                    raise ValueError("binary source")
                text = data.decode("utf-8-sig")
                if byte_count + len(data) > 8_000_000:
                    examined -= 1
                    break
                byte_count += len(data)
                files.append((path, ctx.file_hashes[path], redact(text)))
            except (OSError, UnicodeError, ValueError):
                skipped += 1
        ctx.cancel_check()
        if args.regex and files:
            # Python's re engine has no cancellation API. A disposable worker
            # bounds pathological regexes instead of stalling the agent loop.
            mp = multiprocessing.get_context("spawn")
            receiver, sender = mp.Pipe(duplex=False)
            worker = mp.Process(target=_regex_worker,
                                args=(sender, files, args.query, args.case_sensitive, args.offset, args.max_results), daemon=True)
            worker.start()
            sender.close()
            try:
                deadline = time.monotonic() + 5
                while not receiver.poll(0.05):
                    ctx.cancel_check()
                    if time.monotonic() >= deadline or not worker.is_alive():
                        return {"error": "regex worker exceeded its deadline or exited; narrow the pattern/scan"}
                results, more = receiver.recv()
            finally:
                receiver.close()
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=1)
        else:
            # Limit each literal scan to one file so cancellation remains timely.
            results, more = [], False
            remaining_offset = args.offset
            for item in files:
                ctx.cancel_check()
                matches, _ = _search([item], args.query, False, args.case_sensitive, 0, 10201)
                if remaining_offset:
                    used = min(remaining_offset, len(matches))
                    remaining_offset -= used
                    matches = matches[used:]
                results.extend(matches)
                if len(results) > args.max_results:
                    more = True
                    results = results[:args.max_results]
                    break
        scan_more = args.file_offset + examined < len(candidates)
        return {"results": results, "truncated": more or scan_more, "results_truncated": more,
                "next_offset": args.offset + len(results) if more else None,
                "file_offset": args.file_offset, "next_file_offset": args.file_offset + examined if scan_more else None,
                "scanned_files": len(files), "skipped_files": skipped, "scanned_bytes": byte_count,
                "index_revision": ctx.revision}
