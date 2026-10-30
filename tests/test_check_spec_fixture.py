"""Private check metadata is sealed without enabling unfinished fixture dispatch."""
from dataclasses import replace

import pytest
from test_check_private_pytest import NONCE, bound

from atomic_flow.agents.execution import ExecutionUnavailable
from atomic_flow.check_execution import checker_args
from atomic_flow.check_fixture import FixtureIdentity
from atomic_flow.check_private_pytest import private_pytest_args


def fixture(spec, identity):
    return FixtureIdentity(identity.task_id, identity.variant, identity.runtime_digest,
        spec.image, "2be37770e20e5379d6e1cbed7b67808308a7903fefb3ca70726c70b9280ecfde",
        identity.helper_sha256,
        (("/opt/p07-function-fixture/cacert.pem", "a" * 64),
         ("/opt/p07-function-fixture/cert.pem", "b" * 64)), "c" * 64, "d" * 64)


def test_optional_private_fields_preserve_existing_contract_bytes():
    spec, _ = bound()
    assert "private_pytest_identity" not in spec.as_dict()
    assert "fixture_identity" not in spec.as_dict()
    assert replace(spec, private_pytest_identity=None, fixture_identity=None).canonical_bytes() == spec.canonical_bytes()


def test_exact_private_profile_binds_argv_and_static_fixture_without_enabling_execution():
    original, identity = bound()
    spec = replace(original, private_pytest_identity=identity)
    spec = replace(spec, fixture_identity=fixture(spec, identity))
    assert spec.argv == (spec.interpreter, "-m", "pytest", *identity.plugin_args, *spec.test_paths)
    assert "--no-header" not in spec.argv and "-x" not in spec.argv
    assert spec.as_dict()["fixture_identity"] == spec.fixture_identity.as_dict()
    assert spec.digest != original.digest
    with pytest.raises(ExecutionUnavailable, match="not implemented"):
        checker_args(spec, NONCE)


@pytest.mark.parametrize("change", ["task", "runtime", "runner", "type"])
def test_private_identity_cannot_substitute_task_runtime_runner_or_plain_mapping(change):
    spec, identity = bound()
    values = {"private_pytest_identity": identity}
    if change == "task":
        values["task_id"] = "psf__requests-2317"
    elif change == "runtime":
        values["runtime_identity"] = replace(spec.runtime_identity, inventory_sha256="e" * 64)
    elif change == "runner":
        values["runner"] = "pytest-self"
    else:
        values["private_pytest_identity"] = identity.as_dict()
    with pytest.raises(ValueError):
        replace(spec, **values)


@pytest.mark.parametrize("change", ["task", "runtime", "image", "type", "missing_pytest"])
def test_fixture_must_match_the_complete_private_spec(change):
    original, identity = bound()
    spec = replace(original, private_pytest_identity=identity)
    bound_fixture = fixture(spec, identity)
    if change == "task":
        bound_fixture = replace(bound_fixture, task_id="psf__requests-2317")
    elif change == "runtime":
        bound_fixture = replace(bound_fixture, runtime_digest="e" * 64)
    elif change == "image":
        bound_fixture = replace(bound_fixture, image_id="sha256:" + "e" * 64)
    elif change == "type":
        bound_fixture = bound_fixture.as_dict()
    elif change == "missing_pytest":
        spec = original
    with pytest.raises(ValueError, match="fixture identity"):
        replace(spec, fixture_identity=bound_fixture)


def test_builder_cannot_override_an_identity_already_sealed_in_spec():
    spec, identity = bound()
    bound_spec = replace(spec, private_pytest_identity=identity)
    changed = replace(identity, installed_files=tuple((path, "9" * 64) for path, _ in identity.installed_files))
    with pytest.raises(ValueError, match="binding"):
        private_pytest_args(bound_spec, changed, NONCE)
