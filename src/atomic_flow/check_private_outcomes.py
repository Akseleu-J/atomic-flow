"""Source-declared historical outcomes, separate from required ordinary PASS.

Only PrivatePytestIdentity's exact reviewed Requests3362 or Requests2674 policy can select this
builder. Required skips and new xfail/xpass nodes remain incomplete checks.
Native pytest exit status stays unchanged; required counters are separate from
the explicitly recorded optional categories.
"""
from __future__ import annotations

POLICY = "requests3362-public-declared-xfail-and-python3-cstringio/1"

_EVIDENCE = r'''optional_policy=identity['optional_test_outcomes']
optional_raw={(parts[0]+'::'+parts[1]+'::()::'+parts[2] if identity['variant']=='3362-cn' else node):node
              for node in optional_policy for parts in [node.split('::')]}
class HistoricalEvidence(Evidence):
    def __init__(self):
        Evidence.__init__(self)
        self.optional_seen={}
        self.invalid_optional=False
    def pytest_configure(self,config):self.config=config
    def pytest_runtest_logreport(self,report):
        if report.when=='teardown':self.completed.add(report.nodeid)
        category=self.config.hook.pytest_report_teststatus(report=report)[0]
        node=optional_raw.get(report.nodeid)
        if node is not None and category in optional_policy[node]:
            phase='setup' if category=='skipped' else 'call'
            if report.when!=phase or node in self.optional_seen:
                self.invalid_optional=True
            self.optional_seen[node]={'category':category,'when':report.when,'raw_nodeid':report.nodeid}
            return
        Evidence.pytest_runtest_logreport(self,report)
evidence=HistoricalEvidence()'''

_NATIVE_STATUS = r'''native_exit_code=code
optional_complete=(not evidence.invalid_optional and set(evidence.optional_seen)==set(optional_policy))
protected()
receipt={'''


def _replace(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("reviewed historical outcome composition anchor changed")
    return source.replace(anchor, replacement, 1)


def outcome_source(source: str) -> str:
    source = _replace(source, "evidence=Evidence()", _EVIDENCE)
    source = _replace(source, "protected()\nreceipt={", _NATIVE_STATUS)
    source = _replace(source, "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'", (
        "receipt.update(native_exit_code=native_exit_code,optional_outcomes=evidence.optional_seen,"
        "optional_outcome_policy=identity['optional_outcome_policy'],counter_scope='required_only')\n"
        "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'"))
    return _replace(source, "evidence.passed+evidence.call_failed==evidence.collected",
                    "evidence.passed+evidence.call_failed+len(evidence.optional_seen)==evidence.collected and optional_complete")


def optional_receipt_valid(value: dict, policy: tuple, *, variant: str = "3362-cn") -> bool:
    """Revalidate exact declared per-node categories and retain native status."""
    allowed = dict(policy)
    expected_policy = {"3362-cn": POLICY, "2674-v2": "requests2674-public-declared-xfail/1"}.get(variant)
    if expected_policy is None:
        return False
    observed = value.get("optional_outcomes")
    if (value.get("optional_outcome_policy") != expected_policy or value.get("counter_scope") != "required_only"
            or type(observed) is not dict or set(observed) != set(allowed)
            or type(value.get("native_exit_code")) is not int):
        return False
    for node, kinds in allowed.items():
        row = observed[node]
        parts = node.split("::")
        raw_node = parts[0] + "::" + parts[1] + "::()::" + parts[2] if variant == "3362-cn" else node
        if (type(row) is not dict or set(row) != {"category", "when", "raw_nodeid"}
                or row["raw_nodeid"] != raw_node
                or type(row["category"]) is not str or row["category"] not in kinds
                or row["when"] != ("setup" if row["category"] == "skipped" else "call")):
            return False
    # The reviewed pytest2.8 non-strict XPASS does not itself fail the native
    # session. Never infer a native failure just from its displayed category,
    # and never turn an unexplained native failure into ordinary success.
    return value["native_exit_code"] == value["exit_code"] == (1 if value["call_failed"] else 0)
