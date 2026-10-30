"""Explicit on-disk state versions; migrations never execute saved actions."""
from __future__ import annotations

TASK_SCHEMA_VERSION = 2


class StateError(ValueError):
    """Saved state cannot safely be interpreted by this application."""


def migrate_task(data: object) -> dict:
    if not isinstance(data, dict):
        raise StateError("invalid task state: expected a JSON object")
    version = data.get("schema_version", 0)
    if type(version) is not int or version < 0:
        raise StateError("invalid task schema_version: expected a nonnegative integer")
    if version > TASK_SCHEMA_VERSION:
        raise StateError(f"task schema version {version} is newer than supported {TASK_SCHEMA_VERSION}; update atomic-flow")
    migrated = dict(data)
    if version < 2:
        # Older tasks never represented host review authority. Even a field
        # bearing the new name in an old file cannot grant execution permission.
        migrated["plan_approval"] = {}
        raw_steps = migrated.get("steps", [])
        if isinstance(raw_steps, list):
            steps = []
            previous = None
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    steps.append(raw_step)  # Detailed structural error in decoder.
                    continue
                step = dict(raw_step)
                if step.get("status", "pending") not in {"pending", "running", "done", "failed"}:
                    raise StateError("invalid legacy task step status")
                step["depends_on"] = [] if previous is None else [previous]
                step["blocked_reason"] = ""
                previous = step.get("index")
                steps.append(step)
            migrated["steps"] = steps
    migrated["schema_version"] = TASK_SCHEMA_VERSION
    return migrated
