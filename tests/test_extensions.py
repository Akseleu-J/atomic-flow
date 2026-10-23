import concurrent.futures

import pytest
from pydantic import BaseModel, ConfigDict

from atomic_flow.agents.base import Tool, ToolContract, ToolRegistry
from atomic_flow.agents.context import ToolContext
from atomic_flow.agents.loop import execute_tool
from atomic_flow.agents.policy import PolicyEngine, Rule
from atomic_flow.extensions import OptionalComponents, OptionalComponentUnavailable, PluginManager, PluginManifest


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class Echo(Tool):
    name = "extension_echo"
    description = "Echo host-selected data"
    args_schema = Args
    contract = ToolContract()

    def run(self, args, ctx):
        return {"value": args.value}


def manifest(**overrides):
    from dataclasses import asdict
    return PluginManifest.model_validate({"id": "test_plugin", "release": "1.0", "source_sha256": "a" * 64,
                                          "capabilities": ["read"], "tools": [{"name": Echo.name,
                                          "description": Echo.description, "input_schema": Args.model_json_schema(),
                                          "contract": asdict(Echo.contract)}], **overrides})


def test_plugin_lazy_role_gate_source_drift_and_cached_revocation(tmp_path):
    manager, registry = PluginManager(), ToolRegistry()
    definition = manifest()
    manager.approve(definition, expected_digest=definition.digest(), approved=True)
    loaded = []
    source = ["a" * 64]

    def factory():
        loaded.append(True)
        return Echo()

    manager.register_tool(definition.id, registry, name=Echo.name, args_schema=Args, contract=Echo.contract,
                          factory=factory, source_check=lambda: source[0])
    ctx = ToolContext(tmp_path, {}, policy=PolicyEngine(host_rules=[Rule(Echo.name, "auto")]))
    result, _ = execute_tool({"tool": Echo.name, "args": {"value": "yes"}}, registry, ctx, allowed_tools=[])
    assert result["outcome"] == "denied" and not loaded
    source[0] = "b" * 64
    result, _ = execute_tool({"tool": Echo.name, "args": {"value": "yes"}}, registry, ctx, allowed_tools=[Echo.name])
    assert result["outcome"] == "error" and not loaded
    source[0] = "a" * 64
    result, _ = execute_tool({"tool": Echo.name, "args": {"value": "yes"}}, registry, ctx, allowed_tools=[Echo.name])
    assert result["value"] == "yes" and loaded == [True]
    manager.revoke(definition.id)
    result, _ = execute_tool({"tool": Echo.name, "args": {"value": "yes"}}, registry, ctx, allowed_tools=[Echo.name])
    assert result["outcome"] == "error" and "revoked" in result["error"]
    assert loaded == [True]


def test_plugin_manifest_permissions_approval_metadata_and_isolation():
    manager = PluginManager()
    definition = manifest()
    with pytest.raises(PermissionError):
        manager.approve(definition, expected_digest=definition.digest())
    with pytest.raises(ValueError):
        manager.approve(definition, expected_digest="0" * 64, approved=True)
    with pytest.raises(ValueError):
        manifest(capabilities=["write"])
    with pytest.raises(ValueError):
        manifest(module="arbitrary.import")
    manager.approve(definition, expected_digest=definition.digest(), approved=True)
    definition.tools[0].input_schema["fake"] = True
    assert "fake" not in manager.inspect(definition.id)["manifest"]["tools"][0]["input_schema"]
    with pytest.raises(PermissionError):
        manager.register_tool(definition.id, ToolRegistry(), name=Echo.name, args_schema=Args,
                              contract=ToolContract(effects=("write",)), factory=Echo, source_check=lambda: "a" * 64)


def test_factory_mismatch_rejected():
    definition = manifest()
    manager, registry = PluginManager(), ToolRegistry()
    manager.approve(definition, expected_digest=definition.digest(), approved=True)
    manager.register_tool(definition.id, registry, name=Echo.name, args_schema=Args, contract=Echo.contract,
                          factory=lambda: object(), source_check=lambda: "a" * 64)
    with pytest.raises(PermissionError, match="different tool metadata"):
        registry.get(Echo.name)


def test_optional_components_deferred_concurrent_once_and_failure():
    components = OptionalComponents()
    with pytest.raises(PermissionError):
        components.register("encoder", lambda: object())
    calls, value = [], object()

    def load():
        calls.append(True)
        return value

    components.register("encoder", load, approved=True)
    assert components.diagnostics()["encoder"]["state"] == "deferred" and not calls
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: components.load("encoder"), range(8)))
    assert all(item is value for item in results) and calls == [True]
    failures = []

    def missing():
        failures.append(True)
        raise ImportError("SECRET details")

    components.register("optional_api", missing, approved=True)
    for _ in range(2):
        with pytest.raises(OptionalComponentUnavailable) as exc:
            components.load("optional_api")
        assert "SECRET" not in str(exc.value)
    assert failures == [True]
    assert components.diagnostics()["optional_api"] == {"state": "failed", "error_type": "ImportError"}


def test_optional_recursive_load_fails_closed():
    components = OptionalComponents()
    components.register("self", lambda: components.load("self"), approved=True)
    with pytest.raises(OptionalComponentUnavailable):
        components.load("self")
    assert components.diagnostics()["self"]["state"] == "failed"
