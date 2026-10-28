"""Pure packet projection tests; no corpus gold imports or filesystem execution."""
import hashlib
import json
from collections.abc import Mapping

import pytest

from tools.p07_public_task_packet import (
    MAX_FILES,
    MAX_PROBLEM_BYTES,
    SELECTION_POLICY_VERSION,
    canonical_bytes,
    packet_sha256,
    prepare_public_task_packet,
    prepare_with_receipt,
)

TASK = {"instance_id": "sympy__sympy-20212", "repo": "sympy/sympy", "base_commit": "a" * 40,
        "problem_statement": "Public issue: preserve expression behavior for this input."}
RUNTIME = {"framework_sha": "b" * 40, "workflow_sha": "c" * 40, "source_profile_sha256": "d" * 64}


def packet(task=None, manifest=None, **changes):
    return prepare_public_task_packet(TASK if task is None else task,
                                     {"sympy/core/expr.py": "e" * 64} if manifest is None else manifest,
                                     **(RUNTIME | changes))


class PublicProjectionOnly(Mapping):
    """Fail if the helper iterates or requests any non-public task value."""

    def __getitem__(self, name):
        if name not in TASK:
            raise AssertionError("forbidden field inspected: " + name)
        return TASK[name]

    def __iter__(self):
        raise AssertionError("full corpus row iteration is forbidden")

    def __len__(self):
        raise AssertionError("full corpus row length is irrelevant")


class Forbidden:
    def __repr__(self):
        raise AssertionError("forbidden value rendered")

    def __str__(self):
        raise AssertionError("forbidden value rendered")


def test_projection_never_iterates_private_row_or_reads_forbidden_fields():
    assert packet(PublicProjectionOnly()) == packet()
    private = {**TASK, "patch": Forbidden(), "test_patch": Forbidden(), "source_path": Forbidden(),
               "hints_text": Forbidden(), "FAIL_TO_PASS": Forbidden(), "PASS_TO_PASS": Forbidden(),
               "calibration": Forbidden(), "credentials": Forbidden()}
    assert packet(private) == packet()


def test_gold_secret_and_hint_mutations_cannot_change_packet_or_digest():
    original = packet()
    for key in ("patch", "test_patch", "source_path", "hints", "FAIL_TO_PASS", "PASS_TO_PASS",
                "calibration", "gold_changed_paths", "api_key"):
        for value in (None, "different hidden value", {"nested": [Forbidden()]}):
            changed = packet({**TASK, key: value})
            assert canonical_bytes(changed) == canonical_bytes(original)
            assert packet_sha256(changed) == packet_sha256(original)


def test_complete_scope_uses_only_public_base_policy_and_explicit_protection():
    manifest = {path: "e" * 64 for path in [
        "sympy/core/expr.py", "sympy/core/basic.py", "sympy/core/types.pyi",
        "sympy/tests/test_issue.py", "sympy/core/tests/helper.py",
        "sympy/testing/helpers.py", "sympy/conftest.py", "sympy/core/test_example.py",
        "sympy/core/example_test.py", "sympy/core/example_tests.pyi",
        "sympy/core/.hidden.py", "sympy/core/setup.py", "sympy/core/conf.py",
        "sympy/core/pytest.ini", "sympy/core/pyproject.toml", "sympy/bin/run.py",
        "sympy/vendor/library.py", "sympy/examples/example.py", "setup.py",
        "tests/test_sample.py", ".github/workflows/check.yml", "README.md",
    ]}
    result = packet(manifest=manifest, protected_paths=("sympy/core/basic.py",))
    assert result["writable_paths"] == ["sympy/core/expr.py", "sympy/core/types.pyi"]
    assert set(result["source_manifest"]) == set(manifest)
    assert result["protected_paths"] == ["sympy/core/basic.py"]
    assert result["selection_policy"]["version"] == SELECTION_POLICY_VERSION
    assert result["selection_policy"]["existing_files_only"] is True
    assert result["authority"] == "data_only_requires_separate_host_review"
    assert "runner" not in result and "source_path" not in result["task"]


@pytest.mark.parametrize("repo,instance,path", [
    ("psf/requests", "psf__requests-863", "requests/sessions.py"),
    ("pytest-dev/pytest", "pytest-dev__pytest-6116", "src/_pytest/python.py"),
    ("pytest-dev/pytest", "pytest-dev__pytest-7168", "_pytest/main.py"),
    ("pytest-dev/pytest", "pytest-dev__pytest-7168", "pytest.py"),
    ("pytest-dev/pytest", "pytest-dev__pytest-7168", "src/pytest/__init__.py"),
    ("pylint-dev/pylint", "pylint-dev__pylint-6506", "pylint/checkers/base.py"),
    ("sphinx-doc/sphinx", "sphinx-doc__sphinx-1234", "sphinx/application.py"),
    ("sympy/sympy", "sympy__sympy-20212", "sympy/core/expr.py"),
])
def test_explicit_repository_families_and_layouts(repo, instance, path):
    result = packet({**TASK, "repo": repo, "instance_id": instance}, {path: "e" * 64})
    assert result["writable_paths"] == [path]


def test_order_independent_manifest_protection_and_stable_canonical_receipt():
    one = packet(manifest={"sympy/z.py": "e" * 64, "sympy/a.py": "f" * 64},
                 protected_paths=("sympy/z.py", "absent_anchor.py"))
    two = packet(manifest={"sympy/a.py": "f" * 64, "sympy/z.py": "e" * 64},
                 protected_paths=("absent_anchor.py", "sympy/z.py", "sympy/z.py"))
    assert canonical_bytes(one) == canonical_bytes(two)
    raw = canonical_bytes(one)
    assert json.loads(raw) == one
    assert packet_sha256(one) == hashlib.sha256(raw).hexdigest()
    receipt = prepare_with_receipt(TASK, {"sympy/core/expr.py": "e" * 64}, **RUNTIME)
    assert receipt["packet"] == packet()
    assert receipt["packet_sha256"] == packet_sha256(receipt["packet"])
    assert receipt["canonical_bytes"] == len(canonical_bytes(receipt["packet"]))


@pytest.mark.parametrize("field,value", [
    ("repo", "attacker/sympy"), ("repo", "Sympy/sympy"), ("repo", "sympy/sympy/../other"),
    ("instance_id", "pytest-dev__pytest-20212"), ("instance_id", "sympy__sympy-0"),
    ("base_commit", "A" * 40), ("base_commit", "main"),
    ("problem_statement", ""), ("problem_statement", "  "),
    pytest.param("problem_statement", "x" * (MAX_PROBLEM_BYTES + 1), id="problem-over-byte-bound"), ("problem_statement", "\x00input"),
    ("problem_statement", "\ud800"), ("problem_statement", {"text": "not plain"}),
])
def test_rejects_unknown_repository_identity_or_problem(field, value):
    with pytest.raises(ValueError):
        packet({**TASK, field: value})


@pytest.mark.parametrize("path", [
    "../sympy/expr.py", "/sympy/expr.py", "sympy//expr.py", "sympy/./expr.py",
    "sympy/../expr.py", "sympy\\expr.py", "C:/sympy/expr.py",
    "sympy/-expr.py", "sympy/expr.py\n", "sympy/é.py",
    pytest.param("sympy/" + "x" * 512, id="path-over-length-bound"),
])
def test_rejects_unsafe_inventory_paths_even_when_not_writable(path):
    with pytest.raises(ValueError):
        packet(manifest={path: "e" * 64, "sympy/core/expr.py": "f" * 64})


@pytest.mark.parametrize("manifest", [
    {}, [], {"sympy/a.py": "A" * 64}, {"sympy/a.py": "e" * 63},
    {"sympy/a.py": {"sha256": "e" * 64, "type": "symlink"}},
    {"sympy/a.py": None}, {"tests/test_a.py": "e" * 64},
    {"other/production.py": "e" * 64},
])
def test_rejects_invalid_or_empty_production_inventory(manifest):
    with pytest.raises(ValueError):
        packet(manifest=manifest)


def test_rejects_oversized_inventory_and_mutable_or_escaping_protection():
    with pytest.raises(ValueError):
        packet(manifest={f"sympy/f{i}.py": "e" * 64 for i in range(MAX_FILES + 1)})
    with pytest.raises(ValueError):
        packet(protected_paths=["sympy/a.py"])
    with pytest.raises(ValueError):
        packet(protected_paths=("../escape.py",))
    with pytest.raises(ValueError):
        packet(protected_paths=("sympy/core/expr.py",))


@pytest.mark.parametrize("field,value", [
    ("framework_sha", "main"), ("workflow_sha", "a" * 39),
    ("source_profile_sha256", "A" * 64), ("source_profile_sha256", None),
])
def test_runtime_workflow_and_profile_are_exact_bindings(field, value):
    with pytest.raises(ValueError):
        packet(**{field: value})


def test_public_source_runtime_or_protection_changes_are_bound():
    base = packet()
    changed = [
        packet({**TASK, "problem_statement": "A different public issue."}),
        packet({**TASK, "base_commit": "f" * 40}),
        packet(manifest={"sympy/core/expr.py": "f" * 64}),
        packet(framework_sha="e" * 40), packet(workflow_sha="f" * 40),
        packet(source_profile_sha256="f" * 64),
        packet(protected_paths=("absent_anchor.py",)),
    ]
    assert all(packet_sha256(value) != packet_sha256(base) for value in changed)
