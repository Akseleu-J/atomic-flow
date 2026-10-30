"""Fixed source-declared historical exceptions never waive required tests."""
import ast
import copy
from dataclasses import replace
from types import SimpleNamespace

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
                 native_exit_code=int(failed), counter_scope="required_only", optional_outcome_policy=POLICY,
                 optional_outcomes={node: {"category": "skipped" if kinds == ("skipped",) else "xfailed" if index == 0 else "xpassed",
                                          "when": "setup" if kinds == ("skipped",) else "call",
                                          "raw_nodeid": node.rsplit("::", 1)[0] + "::()::" + node.rsplit("::", 1)[1]}
                                    for index, (node, kinds) in enumerate(REQUESTS3362_OPTIONAL_OUTCOMES)})
    return bound, value


@pytest.mark.parametrize("failed", [False, True])
def test_native_expected_status_stays_distinct_from_required_pass_or_failure(failed):
    bound, value = admitted(failed=failed)
    assert requests3362_receipt(result(value), bound, NONCE) == value
    assert value["native_exit_code"] == int(failed)
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


def test_actual_generated_report_handler_maps_only_exact_native_class_collector_ids():
    from atomic_flow.check_private_outcomes import _EVIDENCE

    class Evidence:
        def __init__(self):
            self.completed, self.required = set(), []

        def pytest_runtest_logreport(self, report):
            self.required.append(report.nodeid)

    scope = {"identity": {"variant": "3362-cn", "optional_test_outcomes": dict(REQUESTS3362_OPTIONAL_OUTCOMES)}, "Evidence": Evidence}
    exec(compile(_EVIDENCE, "trusted-historical-evidence-test", "exec"), scope)
    generated = scope["evidence"]
    generated.pytest_configure(SimpleNamespace(hook=SimpleNamespace(
        pytest_report_teststatus=lambda report: (report.category, "", ""))))
    for node, kinds in REQUESTS3362_OPTIONAL_OUTCOMES:
        raw = node.rsplit("::", 1)[0] + "::()::" + node.rsplit("::", 1)[1]
        category = kinds[0]
        generated.pytest_runtest_logreport(SimpleNamespace(nodeid=raw, category=category,
                                                          when="setup" if category == "skipped" else "call"))
    assert set(generated.optional_seen) == {node for node, _ in REQUESTS3362_OPTIONAL_OUTCOMES}
    assert generated.required == [] and generated.invalid_optional is False
    # A canonical display name is not itself the admitted raw native identity.
    node = REQUESTS3362_OPTIONAL_OUTCOMES[0][0]
    generated.pytest_runtest_logreport(SimpleNamespace(nodeid=node, category="xfailed", when="call"))
    assert generated.required == [node]


@pytest.mark.parametrize("attack", ["required_skip", "required_xpass", "unknown_node", "missing_node",
                                   "wrong_phase", "changed_category", "native_exit_substitution", "native_infra_exit",
                                   "claim_all_passed", "bool_native_exit", "teardown_error", "wrong_raw_alias"])
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
    elif attack == "native_exit_substitution":
        value["native_exit_code"] = 1
    elif attack == "native_infra_exit":
        value["native_exit_code"] = 3
    elif attack == "claim_all_passed":
        value["passed"] += 3
    elif attack == "bool_native_exit":
        value["native_exit_code"] = True
    elif attack == "teardown_error":
        value["teardown_failed"] = value["failed"] = 1
    elif attack == "wrong_raw_alias":
        optional[next(iter(optional))]["raw_nodeid"] = "tests/test_requests.py::()::unknown"
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


def test_2674_exact_public_xfail_receipt_and_unexpected_outcomes():
    from test_check_private_pytest import NONCE as nonce
    from test_check_private_pytest import bound, observation, result

    from atomic_flow.check_fixture import REQUESTS2674_OPTIONAL_OUTCOMES
    from atomic_flow.check_private_pytest import private_pytest_receipt

    spec, identity = bound((2, 7, 18))
    identity = replace(identity, optional_test_outcomes=REQUESTS2674_OPTIONAL_OUTCOMES)
    spec = replace(spec, test_paths=("test_requests.py",), private_pytest_identity=identity)
    node = REQUESTS2674_OPTIONAL_OUTCOMES[0][0]
    value = observation(spec, identity, collected=3, completed=3, passed=2,
                        native_exit_code=0, counter_scope="required_only",
                        optional_outcome_policy="requests2674-public-declared-xfail/1",
                        optional_outcomes={node: {"category": "xpassed", "when": "call", "raw_nodeid": node}})
    assert private_pytest_receipt(result(value), spec, identity, nonce) == value
    for mutate in (
        lambda v: v["optional_outcomes"][node].update(raw_nodeid=node.replace("::test_", "::()::test_")),
        lambda v: v["optional_outcomes"][node].update(category="skipped"),
        lambda v: v.update(skipped=1),
        lambda v: v.update(optional_outcome_policy=POLICY),
        lambda v: v.update(native_exit_code=1),
    ):
        changed = copy.deepcopy(value)
        mutate(changed)
        assert private_pytest_receipt(result(changed), spec, identity, nonce) is None
    with pytest.raises(ValueError, match="optional outcomes"):
        replace(identity, optional_test_outcomes=((*REQUESTS2674_OPTIONAL_OUTCOMES,),))


def test_2674_generated_handler_uses_observed_python27_raw_id():
    from atomic_flow.check_fixture import REQUESTS2674_OPTIONAL_OUTCOMES
    from atomic_flow.check_private_outcomes import _EVIDENCE

    class Evidence:
        def __init__(self):
            self.completed, self.required = set(), []

        def pytest_runtest_logreport(self, report):
            self.required.append(report.nodeid)

    identity = {"variant": "2674-v2", "optional_test_outcomes": dict(REQUESTS2674_OPTIONAL_OUTCOMES)}
    scope = {"identity": identity, "Evidence": Evidence}
    exec(compile(_EVIDENCE, "trusted-2674-evidence-test", "exec"), scope)
    evidence = scope["evidence"]
    evidence.pytest_configure(SimpleNamespace(hook=SimpleNamespace(
        pytest_report_teststatus=lambda report: (report.category, "", ""))))
    node = REQUESTS2674_OPTIONAL_OUTCOMES[0][0]
    evidence.pytest_runtest_logreport(SimpleNamespace(nodeid=node, category="xpassed", when="call"))
    assert evidence.optional_seen[node]["raw_nodeid"] == node
    assert evidence.required == [] and not evidence.invalid_optional
    impostor = node.replace("::test_", "::()::test_")
    evidence.pytest_runtest_logreport(SimpleNamespace(nodeid=impostor, category="xpassed", when="call"))
    assert evidence.required == [impostor]
