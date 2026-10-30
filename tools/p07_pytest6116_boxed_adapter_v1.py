"""Prospective xdist.boxed compatibility adapter for pytest6116 diagnostics.

This is an explicitly supplied compatibility module, not part of either wheel.
It provides only the historical function imported by the public test suite. It
never registers as a pytest plugin. Real target compatibility is unverified.
"""
import marshal
import os

import py
import pytest
import pytest_forked
from _pytest import runner

EXITSTATUS_TESTEXIT = 4


def forked_run_report(item):
    """Run all phases in the fork, preserving pytest Exit and crash reports."""
    def runforked():
        try:
            reports = runner.runtestprotocol(item, log=False)
        except (KeyboardInterrupt, pytest.exit.Exception):
            os._exit(EXITSTATUS_TESTEXIT)
        return marshal.dumps([pytest_forked.serialize_report(report) for report in reports])

    result = py.process.ForkedFunc(runforked).waitfinish()
    if result.retval is not None:
        return [runner.TestReport(**data) for data in marshal.loads(result.retval)]
    if result.exitstatus == EXITSTATUS_TESTEXIT:
        pytest.exit(f"forked test item {item} raised Exit")
    return [pytest_forked.report_process_crash(item, result)]
