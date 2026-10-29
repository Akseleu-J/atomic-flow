"""Source-declared historical outcomes, separate from required ordinary PASS.

Only PrivatePytestIdentity's exact reviewed Requests3362 policy can select this
builder. Required skips and new xfail/xpass nodes remain incomplete checks.
Native pytest status is retained alongside the normalized required-test exit.
"""
from __future__ import annotations

POLICY = "requests3362-public-declared-xfail-and-python3-cstringio/1"

_EVIDENCE = r'''optional_policy=identity['optional_test_outcomes']
class HistoricalEvidence(Evidence):
    def __init__(self):
        Evidence.__init__(self)
        self.optional_seen={}
        self.invalid_optional=False
    def pytest_configure(self,config):self.config=config
    def pytest_runtest_logreport(self,report):
        if report.when=='teardown':self.completed.add(report.nodeid)
        category=self.config.hook.pytest_report_teststatus(report=report)[0]
        if report.nodeid in optional_policy and category in optional_policy[report.nodeid]:
            phase='setup' if category=='skipped' else 'call'
            if report.when!=phase or report.nodeid in self.optional_seen:
                self.invalid_optional=True
            self.optional_seen[report.nodeid]={'category':category,'when':report.when}
            return
        Evidence.pytest_runtest_logreport(self,report)
evidence=HistoricalEvidence()'''

_NORMALIZE = r'''native_exit_code=code
optional_complete=(not evidence.invalid_optional and set(evidence.optional_seen)==set(optional_policy))
required_complete=(optional_complete and evidence.collection_complete and evidence.finished
    and len(evidence.completed)==evidence.collected and evidence.files==set(paths)
    and evidence.passed+evidence.call_failed+len(evidence.optional_seen)==evidence.collected
    and not evidence.collection_errors and not evidence.skipped and not evidence.xpassed
    and not evidence.setup_failed and not evidence.teardown_failed)
if code in (0,1) and required_complete:
    code=1 if evidence.call_failed else 0
protected()
receipt={'''


def _replace(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("reviewed historical outcome composition anchor changed")
    return source.replace(anchor, replacement, 1)


def outcome_source(source: str) -> str:
    source = _replace(source, "evidence=Evidence()", _EVIDENCE)
    source = _replace(source, "protected()\nreceipt={", _NORMALIZE)
    source = _replace(source, "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'", (
        "receipt.update(native_exit_code=native_exit_code,optional_outcomes=evidence.optional_seen,"
        "optional_outcome_policy=" + repr(POLICY) + ",counter_scope='required_only')\n"
        "print('\\nATOMIC_CHECK_SPEC_RECEIPT:'"))
    return _replace(source, "evidence.passed+evidence.call_failed==evidence.collected",
                    "evidence.passed+evidence.call_failed+len(evidence.optional_seen)==evidence.collected and optional_complete")


def optional_receipt_valid(value: dict, policy: tuple) -> bool:
    """Revalidate exact declared per-node categories and retain native status."""
    allowed = dict(policy)
    observed = value.get("optional_outcomes")
    if (value.get("optional_outcome_policy") != POLICY or value.get("counter_scope") != "required_only"
            or type(observed) is not dict or set(observed) != set(allowed)
            or type(value.get("native_exit_code")) is not int):
        return False
    xpassed = False
    for node, kinds in allowed.items():
        row = observed[node]
        if (type(row) is not dict or set(row) != {"category", "when"}
                or type(row["category"]) is not str or row["category"] not in kinds
                or row["when"] != ("setup" if row["category"] == "skipped" else "call")):
            return False
        xpassed |= row["category"] == "xpassed"
    return value["native_exit_code"] == (1 if value["call_failed"] or xpassed else 0)
