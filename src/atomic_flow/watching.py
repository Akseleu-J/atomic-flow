"""Portable bounded source polling. A watch event never grants read/write proof.

Content hashes, not timestamps, are authoritative. No background process is
started; the host chooses safe polling boundaries or the explicit watch CLI.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from atomic_flow.agents.source import SourceConflict, confined_path, digest
from atomic_flow.agents.tools.file_paths import admitted_path
from atomic_flow.tree.builder import _Ignore
from atomic_flow.tree.defaults import DEFAULT_IGNORE


class WatchConflict(SourceConflict):
    pass


@dataclass(frozen=True)
class SourceChange:
    sequence: int
    paths: tuple[str, ...]
    fingerprint: str
    ready: bool

    def observation(self):
        return {"version": 1, "sequence": self.sequence, "paths": list(self.paths[:128]),
                "omitted_paths": max(0, len(self.paths) - 128), "fingerprint": self.fingerprint,
                "ready": self.ready, "trust": "data_only", "read_evidence": False}


class SourceWatcher:
    def __init__(self, project: Path, *, interval=0.25, debounce=0.2, max_files=10000, max_entries=20000,
                 max_bytes=64 * 1024 * 1024, clock=time.monotonic, cancel_check: Callable = lambda: None,
                 tree_ignore=None, respect_gitignore=True, private_workspace=False):
        if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in (interval, debounce)):
            raise ValueError("invalid watch timing")
        if not 0.1 <= interval <= 10 or not 0 <= debounce <= 5:
            raise ValueError("watch interval/debounce outside bounded range")
        if type(max_files) is not int or not 1 <= max_files <= 10000 or type(max_bytes) is not int or not 1024 <= max_bytes <= 64 * 1024 * 1024:
            raise ValueError("invalid watch source limits")
        if type(max_entries) is not int or not 1 <= max_entries <= 20000:
            raise ValueError("invalid watch directory-entry limit")
        self.project = Path(project).expanduser().absolute()
        self._check_root()
        if not self.project.is_dir():
            raise WatchConflict("watch project must be a directory")
        self.interval, self.debounce, self.clock = interval, debounce, clock
        self.max_files, self.max_bytes, self.cancel_check = max_files, max_bytes, cancel_check
        self.max_entries = max_entries
        self.tree_ignore = list(DEFAULT_IGNORE) + list(tree_ignore or [])
        self.respect_gitignore, self.private_workspace = respect_gitignore, private_workspace
        self._observed = self._scan()
        self._baseline = self._observed.copy()
        self._pending: set[str] = set()
        self._sequence = 0
        self._last_poll = self._last_change = clock()

    def _check_root(self):
        if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (self.project, *self.project.parents)):
            raise WatchConflict("watch root must not contain links")

    def _scan(self):
        self._check_root()
        self.cancel_check()
        ctx = SimpleNamespace(project_root=self.project, tree_ignore=self.tree_ignore,
                              respect_gitignore=self.respect_gitignore, private_workspace=self.private_workspace)
        manifest, total = {}, 0
        # The ignore policy itself changes the source universe. Watch only its
        # hash, never its text, and reject link-based redirection.
        ignore_file = confined_path(self.project, ".gitignore")
        if self.respect_gitignore and ignore_file.is_file():
            with ignore_file.open("rb") as handle:
                raw = handle.read(1_000_001)
            if len(raw) > 1_000_000:
                raise WatchConflict("watch ignore policy exceeds byte limit")
            manifest[".gitignore"] = digest(raw)
            total += len(raw)
            if total > self.max_bytes:
                raise WatchConflict("watch source byte limit exceeded")
        skip = _Ignore(self.project, self.tree_ignore, self.respect_gitignore)
        started = self.clock()
        pending, entries = [self.project], 0
        while pending:
            self.cancel_check()
            if self.clock() - started > 5:
                raise WatchConflict("source watch scan deadline exceeded")
            folder = pending.pop()
            with os.scandir(folder) as stream:
                for item in stream:
                    entries += 1
                    self.cancel_check()
                    if entries > self.max_entries or self.clock() - started > 5:
                        raise WatchConflict("source watch entry/deadline limit exceeded")
                    rel = Path(item.path).relative_to(self.project).as_posix()
                    if item.is_dir(follow_symlinks=False):
                        if not skip(rel, True):
                            pending.append(confined_path(self.project, rel))
                        continue
                    if rel == ".gitignore":
                        continue
                    try:
                        path = admitted_path(ctx, rel, existing=True, notebook=rel.lower().endswith(".ipynb"))
                    except SourceConflict:
                        continue
                    with path.open("rb") as handle:
                        raw = handle.read(1_000_001)
                    if len(raw) > 1_000_000:
                        raise WatchConflict("watched file grew beyond byte limit")
                    total += len(raw)
                    if len(manifest) >= self.max_files or total > self.max_bytes:
                        raise WatchConflict("watch source count/byte limit exceeded")
                    manifest[rel] = digest(raw)
        self.cancel_check()
        return manifest

    @staticmethod
    def _fingerprint(manifest):
        return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def poll(self, *, force=False) -> SourceChange | None:
        self.cancel_check()
        now = self.clock()
        if not force and now - self._last_poll < self.interval:
            return self._event(now)
        current = self._scan()
        self._last_poll = now
        changed = {path for path in current.keys() | self._observed.keys() if current.get(path) != self._observed.get(path)}
        if changed:
            if len(self._pending | changed) > self.max_entries:
                raise WatchConflict("watch pending change count exceeded; start a fresh watcher")
            self._pending.update(changed)
            self._last_change = now
            self._sequence += 1
        self._observed = current
        return self._event(now)

    def _event(self, now):
        if not self._pending:
            return None
        return SourceChange(self._sequence, tuple(sorted(self._pending)), self._fingerprint(self._observed), now - self._last_change >= self.debounce)

    def acknowledge(self, event: SourceChange):
        if not isinstance(event, SourceChange) or not event.ready or event.sequence != self._sequence:
            raise WatchConflict("watch acknowledgement is stale or not ready")
        current = self._scan()
        if self._fingerprint(current) != event.fingerprint or current != self._observed:
            self.poll(force=True)
            raise WatchConflict("source changed during refresh; watch event remains unacknowledged")
        self._baseline = current.copy()
        self._pending.clear()

    def reconcile_graph(self, store, event: SourceChange | None = None):
        """Host-explicit graph refresh, followed by freshness-checked event ack.

        The store is Python-only; polling also sees other source resources.
        Passing no event performs a dropped-event/recovery content audit.
        """
        from atomic_flow.graph.ingest import ingest_source_changes
        if event is not None and (not isinstance(event, SourceChange) or not event.ready
                                  or event.sequence != self._sequence):
            raise WatchConflict("watch graph refresh requires the current ready event")
        report = ingest_source_changes(store, self.project, use_git=(self.project / ".git").exists(),
                                       event=event, reason="watch event reconciliation" if event else "dropped-event recovery audit",
                                       cancel_check=self.cancel_check)
        if event is not None:
            self.acknowledge(event)
        return report
