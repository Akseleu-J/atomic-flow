"""Explicit on-disk state versions; migrations never execute saved actions."""
from __future__ import annotations

TASK_SCHEMA_VERSION = 1


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
    # v0 is the existing unversioned dataclass representation. Its fields retain
    # their meaning; missing optional fields are supplied by the Task defaults.
    migrated = dict(data)
    migrated["schema_version"] = TASK_SCHEMA_VERSION
    return migrated
