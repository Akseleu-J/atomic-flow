"""Offline verifier protocol tests; fixtures are NOT real Docker evidence."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import surfaces_evidence as evidence


IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
TERMINAL = "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 42 filtered out; finished in 0.01s"


def protocol_fixture():
    observation = {
        "exit_code": 0, "client_cleanup_confirmed": True,
        "timed_out": False, "stdout_truncated": False,
        "stderr_truncated": False, "stdout": "", "stderr": "",
    }
    observations = {
        name: copy.deepcopy(observation)
        for name in ("image", "create", "before", "target", "after", "remove", "absence")
    }
    observations["target"]["stdout"] = "sandbox-live\n"
    return {
        "status": "succeeded", "exit_code": 0, "cleanup_confirmed": True,
        "verification": "not_assessed", "reconcile_required": False,
        "timed_out": False, "stdout_truncated": False, "stderr_truncated": False,
        "sandbox": {
            "driver": "docker", "network_none": True, "root_read_only": True,
            "cleanup_confirmed": True, "verification": "not_assessed",
            "isolation": "container with shared kernel", "image_id": IMAGE,
            "container_id": CONTAINER, "observations": observations,
        },
    }


class ReceiptVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name)
        (self.destination / "image-identity.json").write_text(
            json.dumps({"image_id": IMAGE}), encoding="utf-8"
        )

    def log(self, status=None, terminal=TERMINAL):
        if status is None:
            status = protocol_fixture()
        # Real libtest can prefix the marker with the test name on this line.
        return "test driver::tests::real_docker_acceptance ... AF_DOCKER_RECEIPT=" + json.dumps(status) + "\nok\n" + terminal + "\n"

    def write_log(self, value):
        (self.destination / "acceptance.log").write_text(value, encoding="utf-8")

    def refuse_before_host_query(self, log):
        self.write_log(log)
        with patch.object(evidence, "docker") as host:
            with self.assertRaises(SystemExit):
                evidence.verify(self.destination)
            host.assert_not_called()
        self.assertFalse((self.destination / "receipt-validation.json").exists())

    def test_valid_protocol_fixture_retains_not_assessed(self):
        self.write_log(self.log())
        with patch.object(evidence, "docker", return_value="c" * 64 + "\n") as host:
            evidence.verify(self.destination)
            host.assert_called_once_with("container", "ls", "-a", "--no-trunc", "--format", "{{.ID}}")
        result = json.loads((self.destination / "receipt-validation.json").read_text())
        self.assertIs(result["receipt_valid"], True)
        self.assertEqual(result["verification"], "not_assessed")
        retained = json.loads((self.destination / "receipt.json").read_text())
        self.assertEqual(retained, protocol_fixture())

    def test_missing_or_duplicate_receipt_is_refused(self):
        for log in (TERMINAL, self.log() + self.log()):
            with self.subTest(log=log[:40]):
                self.refuse_before_host_query(log)

    def test_zero_executed_or_failed_or_ignored_test_is_refused(self):
        for terminal in (
            TERMINAL.replace("1 passed", "0 passed"),
            TERMINAL.replace("0 failed", "1 failed"),
            TERMINAL.replace("0 ignored", "1 ignored"),
            TERMINAL.replace("test result: ok", "test result: FAILED"),
            "",
        ):
            with self.subTest(terminal=terminal):
                self.refuse_before_host_query(self.log(terminal=terminal))

    def test_other_boundary_or_image_cannot_be_accepted(self):
        for field, value in (("driver", "local-trusted"), ("image_id", "sha256:" + "d" * 64)):
            with self.subTest(field=field):
                status = protocol_fixture()
                status["sandbox"][field] = value
                self.refuse_before_host_query(self.log(status))

    def test_missing_live_target_witness_is_refused(self):
        status = protocol_fixture()
        status["sandbox"]["observations"]["target"]["stdout"] = ""
        self.refuse_before_host_query(self.log(status))

    def test_cleanup_or_absence_uncertainty_is_refused(self):
        variants = []
        status = protocol_fixture()
        status["cleanup_confirmed"] = False
        variants.append(status)
        status = protocol_fixture()
        status["sandbox"]["cleanup_confirmed"] = False
        variants.append(status)
        status = protocol_fixture()
        status["sandbox"]["observations"]["absence"]["stdout"] = CONTAINER + "\n"
        variants.append(status)
        status = protocol_fixture()
        status["sandbox"]["observations"]["remove"]["exit_code"] = 1
        variants.append(status)
        for status in variants:
            with self.subTest(status=status):
                self.refuse_before_host_query(self.log(status))

    def test_semantic_verification_cannot_be_invented(self):
        status = protocol_fixture()
        status["verification"] = "verified"
        self.refuse_before_host_query(self.log(status))

    def test_host_still_lists_container(self):
        self.write_log(self.log())
        with patch.object(evidence, "docker", return_value=CONTAINER + "\n"):
            with self.assertRaisesRegex(SystemExit, "remains on host"):
                evidence.verify(self.destination)
        self.assertFalse((self.destination / "receipt-validation.json").exists())

    def test_host_command_failure_is_not_container_absence(self):
        self.write_log(self.log())
        with patch.object(evidence, "docker", side_effect=subprocess.CalledProcessError(1, ["docker"])):
            with self.assertRaises(subprocess.CalledProcessError):
                evidence.verify(self.destination)
        self.assertFalse((self.destination / "receipt-validation.json").exists())


if __name__ == "__main__":
    unittest.main()
