"""Hash-bound trusted-host extensions and optional component loading.

Manifests contain declarations only, never Python module/import paths. Factories
are host-provided trusted Python and are not an untrusted plugin sandbox.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atomic_flow.agents.base import Tool, ToolContract


def _bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


class PluginTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict[str, Any]
    contract: dict[str, Any]

    @field_validator("contract")
    @classmethod
    def checked_contract(cls, value):
        # Canonical form includes defaults and rejects unknown declarations.
        return asdict(ToolContract(**value))


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(default=1, strict=True)
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    release: str = Field(min_length=1, max_length=64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capabilities: tuple[str, ...]
    tools: tuple[PluginTool, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def checked(self):
        if self.version != 1:
            raise ValueError("unsupported plugin manifest version")
        if (not self.capabilities or len(set(self.capabilities)) != len(self.capabilities)
                or set(self.capabilities) - {"read", "write", "execute", "interact"}):
            raise ValueError("invalid plugin capabilities")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("duplicate plugin tools")
        if any(not set(tool.contract["effects"]) <= set(self.capabilities) for tool in self.tools):
            raise ValueError("tool effects exceed plugin capabilities")
        if len(_bytes(self.model_dump(mode="json"))) > 131072:
            raise ValueError("plugin manifest exceeds 131072 bytes")
        return self

    def digest(self) -> str:
        checked = PluginManifest.model_validate(self.model_dump(mode="json"))
        return hashlib.sha256(_bytes(checked.model_dump(mode="json"))).hexdigest()


class PluginManager:
    def __init__(self):
        self._approved: dict[str, tuple[PluginManifest, str]] = {}
        self._revoked: set[str] = set()
        self._lock = threading.RLock()

    def approve(self, manifest: PluginManifest | dict, *, expected_digest: str, approved: bool = False) -> str:
        if not approved:
            raise PermissionError("plugin registration requires explicit host approval")
        checked = PluginManifest.model_validate(manifest.model_dump(mode="json") if isinstance(manifest, PluginManifest) else manifest)
        digest = checked.digest()
        if expected_digest != digest:
            raise ValueError("plugin differs from approved manifest")
        with self._lock:
            if checked.id in self._approved:
                raise ValueError("plugin ID already registered; use a new manager for an updated manifest")
            if len(self._approved) >= 32:
                raise ValueError("plugin limit reached")
            self._approved[checked.id] = (checked, digest)
        return checked.id

    def revoke(self, plugin_id: str) -> None:
        with self._lock:
            if plugin_id not in self._approved:
                raise KeyError(plugin_id)
            self._revoked.add(plugin_id)

    def inspect(self, plugin_id: str) -> dict:
        with self._lock:
            manifest, digest = self._approved[plugin_id]
            return {"manifest": copy.deepcopy(manifest.model_dump(mode="json")), "digest": digest,
                    "revoked": plugin_id in self._revoked, "trust": "trusted_host_python"}

    def _check(self, plugin_id: str, source_check) -> PluginManifest:
        with self._lock:
            manifest, digest = self._approved[plugin_id]
            if plugin_id in self._revoked or manifest.digest() != digest:
                raise PermissionError("plugin revoked or manifest changed")
            if source_check() != manifest.source_sha256:
                raise PermissionError("plugin source hash differs from approved manifest")
            return manifest

    def register_tool(self, plugin_id: str, registry, *, name: str, args_schema: type[BaseModel],
                      contract: ToolContract, factory, source_check) -> None:
        """``source_check`` is a trusted callback hashing the installed plugin bytes.

        Its declaration is host-owned; no manifest can choose a file or loader.
        Revocation/source checks are repeated even for an already loaded tool.
        """
        if not callable(factory) or not callable(source_check):
            raise TypeError("host factory and source hash callback required")
        manifest = self._check(plugin_id, source_check)
        descriptor = next((item for item in manifest.tools if item.name == name), None)
        if descriptor is None or _bytes(descriptor.input_schema) != _bytes(args_schema.model_json_schema()) or _bytes(
                descriptor.contract) != _bytes(asdict(contract)):
            raise PermissionError("tool metadata differs from approved manifest")
        manager = self

        class GuardedTool(Tool):
            def run(self, args, ctx):
                manager._check(plugin_id, source_check)
                ctx.cancel_check()
                return self.implementation.run(args, ctx)

        GuardedTool.name = name
        GuardedTool.description = descriptor.description
        GuardedTool.args_schema = args_schema
        GuardedTool.contract = contract

        def load():
            manager._check(plugin_id, source_check)
            implementation = factory()
            if (not isinstance(implementation, Tool) or implementation.name != name
                    or implementation.description != descriptor.description or implementation.host_contract() != contract
                    or _bytes(implementation.args_schema.model_json_schema()) != _bytes(descriptor.input_schema)):
                raise PermissionError("plugin factory returned different tool metadata")
            manager._check(plugin_id, source_check)
            guarded = GuardedTool()
            guarded.implementation = implementation
            guarded.result_schema = implementation.result_schema
            return guarded

        registry.register_lazy(name, descriptor.description, args_schema, contract, load)


class OptionalComponentUnavailable(RuntimeError):
    pass


class OptionalComponents:
    """Bounded host loaders, requested explicitly, initialized at most once.

    Failed initialization remains failed for this instance; no automatic import,
    download, install or retry. Re-entrant loader calls fail closed.
    """

    def __init__(self):
        self._loaders: dict[str, Any] = {}
        self._loaded: dict[str, Any] = {}
        self._failed: dict[str, str] = {}
        self._loading: set[str] = set()
        self._lock = threading.RLock()

    def register(self, name: str, loader, *, approved: bool = False) -> None:
        import re
        if not approved:
            raise PermissionError("component registration requires host approval")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name) or not callable(loader):
            raise ValueError("invalid component registration")
        with self._lock:
            if name in self._loaders or len(self._loaders) >= 32:
                raise ValueError("duplicate component or component limit reached")
            self._loaders[name] = loader

    def load(self, name: str):
        with self._lock:
            if name not in self._loaders:
                raise KeyError(name)
            if name in self._loaded:
                return self._loaded[name]
            if name in self._failed or name in self._loading:
                raise OptionalComponentUnavailable(f"optional component {name} unavailable")
            self._loading.add(name)
            try:
                value = self._loaders[name]()
            except Exception as exc:
                self._failed[name] = type(exc).__name__
                raise OptionalComponentUnavailable(f"optional component {name} initialization failed ({type(exc).__name__})") from exc
            finally:
                self._loading.remove(name)
            self._loaded[name] = value
            return value

    def diagnostics(self) -> dict:
        with self._lock:
            return {name: {"state": "failed" if name in self._failed else "loaded" if name in self._loaded else "deferred",
                           "error_type": self._failed.get(name)} for name in self._loaders}
