"""Fixed source-declared historical exceptions never waive required tests."""
import ast
import copy
from dataclasses import replace

import pytest
from test_check_requests3362 import NONCE, receipt, result, spec

from atomic_flow.check_fixture import REQUESTS3362_OPTIONAL_OUTCOMES
from atomic_flow.check_private_outcomes import POLICY
from atomic_flow.check_requests3362 import requests3362_args, requests3362_receipt


def admitted(*, failed=False):
    original = spec()
    identity = replace(original.private_pytest_identity, optional_test_outcomes=REQUESTS3362_OPTIONAL_OUTCOMES)
    bound = replace(original, private_pytest_identity=identity, test_paths=("tests/test_requests.py", "tests/test_utils.py"))
    value = receipt(bound, failed=failed)
    value.update(collected=value["collected"] + 3, completed=value["completed"] + 3,
                 native_exit_code=1, counter_scope="required_only", optional_outcome_policy=POLICY,
                 optional_outcomes={node: {"category": "skipped" if kinds == ("skipped",) else "xfailed" if index == 0 else "xpassed",
                                          "when": "setup" if kinds == ("skipped",) else "call"}
                                    for index, (node, kinds) in enumerate(REQUESTS3362_OPTIONAL_OUTCOMES)})
    return bound, value


@pytest.mark.parametrize("failed", [False, True])
def test_native_expected_status_stays_distinct_from_required_pass_or_failure(failed):
    bound, value = admitted(failed=failed)
    assert requests3362_receipt(result(value), bound, NONCE) == value
    assert value["native_exit_code"] == 1
    assert value["exit_code"] == int(failed)
    assert value["skipped"] == value["xpassed"] == 0  # Required tests only.
    assert {x["category"] for x in value["optional_outcomes"].values()} == {"skipped", "xfailed", "xpassed"}


def test_optional_bootstrap_and_wrapped_fixture_composition_keep_exact_python36_grammar():
    bound, _ = admitted()
    source = requests3362_args(bound, NONCE)[1]
    ast.parse(source, feature_version=(3, 6))
    assert "native_exit_code=code" in source
    assert "optional_complete" in source
    assert "getfixturedefs(name,item.nodeid)" in source


@pytest.mark.parametrize("attack", ["required_skip", "required_xpass", "unknown_node", "missing_node",
                                   "wrong_phase", "changed_category", "native_exit_hidden", "native_infra_exit",
                                   "claim_all_passed", "bool_native_exit", "teardown_error"])
def test_unreviewed_exceptions_or_infrastructure_cannot_satisfy_required_obligation(attack):
    bound, value = admitted()
    value = copy.deepcopy(value)
    optional = value["optional_outcomes"]
    if attack == "required_skip":
        value["skipped"] = 1
    elif attack == "required_xpass":
        value["xpassed"] = 1
    elif attack == "unknown_node":
        optional["test_p07_private_fixture.py::test_required_production_value"] = {"category": "skipped", "when": "setup"}
    elif attack == "missing_node":
        optional.pop(next(iter(optional)))
    elif attack == "wrong_phase":
        optional[next(iter(optional))]["when"] = "teardown"
    elif attack == "changed_category":
        optional[next(iter(optional))]["category"] = "skipped"
    elif attack == "native_exit_hidden":
        value["native_exit_code"] = 0
    elif attack == "native_infra_exit":
        value["native_exit_code"] = 3
    elif attack == "claim_all_passed":
        value["passed"] += 3
    elif attack == "bool_native_exit":
        value["native_exit_code"] = True
    elif attack == "teardown_error":
        value["teardown_failed"] = value["failed"] = 1
    assert requests3362_receipt(result(value), bound, NONCE) is None


def test_optional_policy_cannot_expand_to_new_nodes_or_other_requests_cohorts():
    bound, _ = admitted()
    identity = bound.private_pytest_identity
    with pytest.raises(ValueError, match="optional outcomes"):
        replace(identity, optional_test_outcomes=(*REQUESTS3362_OPTIONAL_OUTCOMES,
                                                ("tests/test_new.py::test_required", ("skipped",))))
    with pytest.raises(ValueError, match="optional outcomes"):
        replace(identity, variant="function36-v3", task_id="psf__requests-1963")
    with pytest.raises(ValueError, match="complete ordinary test files"):
        replace(bound, test_paths=("tests/test_requests.py",))
