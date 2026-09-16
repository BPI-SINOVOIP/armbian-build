"""純資料回歸；不接觸現場 ledger、報告或映像。"""

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("lab_progress", TOOLS / "bpi_h618_lab_progress.py")
p = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC.loader.exec_module(p)


def blob(value):
    return json.dumps(value, sort_keys=True).encode()


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.boots = {role: p.digest(role.encode()) for role in p.ROLES}
        self.manifest = {"schema": "bpi-h618-customer-components-v1", "board": "bananapim4zeroemac",
                         "image": {"raw": {"sha256": "a" * 64}}, "hardware_validated": False,
                         "files": {role: {"sha256": sha} for role, sha in self.boots.items()},
                         "preflight": {"source_verified": True, "legacy_initrd_verified": True, "mmc_support_verified": True}}
        self.ledger, self.key = self.register()
        self.ledger = p.begin_attempt(self.ledger, self.key, "T3-001")

    def register(self, **kwargs):
        data = blob(self.manifest)
        args = dict(board_model="bananapim4zero", hardware_id="0845", boot_sha256=self.boots, test_version="T3-v1")
        args.update(kwargs)
        return p.register(p.new_ledger(), data, p.digest(data), **args)

    def report(self, stage="deployment", status="passed", **kwargs):
        data = dict(work_key=self.key, attempt_id=self.ledger["items"][self.key]["attempt_id"], stage=stage, status=status)
        if stage == "services":
            data["failed_services"] = []
        data.update(kwargs)
        return blob(data)

    def record(self, stage="deployment", status="passed", **kwargs):
        data = self.report(stage, status, **kwargs)
        self.ledger = p.record_report(self.ledger, self.key, data, p.digest(data), "evidence/report.json")

    def state(self):
        return p.summarize(self.ledger)[self.key]["state"]

    def test_workkey_stable_and_order_independent(self):
        self.assertEqual(self.register(boot_sha256=dict(reversed(list(self.boots.items()))))[1], self.key)

    def test_workkey_changes_for_each_identity_field(self):
        identity = self.ledger["items"][self.key]["identity"]
        for field, value in (("board_model", "other"), ("hardware_id", "0846"), ("image_raw_sha256", "b" * 64),
                             ("test_version", "T3-v2"), ("boot_sha256", {**self.boots, "dtb": "c" * 64})):
            with self.subTest(field=field):
                self.assertNotEqual(p.work_key({**identity, field: value}), self.key)

    def test_450_workkeys_use_only_memory(self):
        identity = self.ledger["items"][self.key]["identity"]
        self.assertEqual(len({p.work_key({**identity, "hardware_id": str(i)}) for i in range(450)}), 450)

    def test_image_and_physical_board_not_conflated(self):
        item = self.ledger["items"][self.key]
        self.assertEqual(item["artifact_board"], "bananapim4zeroemac")
        self.assertEqual(item["identity"]["board_model"], "bananapim4zero")

    def test_modified_ram_dtb_gets_new_key(self):
        self.assertNotEqual(self.register(boot_sha256={**self.boots, "dtb": "b" * 64})[1], self.key)

    def test_shared_kernel_is_rejected(self):
        with self.assertRaises(p.safe.ArtifactError):
            self.register(boot_sha256={**self.boots, "kernel": "b" * 64})

    def test_missing_preflight_is_not_true(self):
        del self.manifest["preflight"]["source_verified"]
        with self.assertRaises(p.safe.ArtifactError):
            self.register()

    def test_unknown_checks_are_pending(self):
        self.assertEqual(self.state(), "pending_deployment")
        item = self.ledger["items"][self.key]
        self.assertTrue(all(not p.can_skip(item, stage) for stage in p.STAGES))

    def test_stage_progress_does_not_imply_whole_pass(self):
        self.record("deployment")
        self.assertEqual(self.state(), "deployment_verified")
        self.record("customer_kernel")
        self.assertEqual(self.state(), "customer_kernel_observed")
        self.record("identity")
        self.assertEqual(self.state(), "incomplete")

    def test_report_hash_and_attempt_binding(self):
        data = self.report()
        with self.assertRaises(p.safe.ArtifactError):
            p.record_report(self.ledger, self.key, data, "f" * 64, "report.json")
        for change in ({"work_key": "f" * 64}, {"attempt_id": "T3-other"}, {"status": True}, {"stage": "unknown"}):
            with self.subTest(change=change), self.assertRaises(p.safe.ArtifactError):
                self.record(**change)

    def test_exact_report_is_idempotent_and_skippable(self):
        self.record("mem")
        original = copy.deepcopy(self.ledger)
        self.record("mem")
        self.assertEqual(self.ledger, original)
        self.assertTrue(p.can_skip(self.ledger["items"][self.key], "mem"))

    def test_failure_cannot_be_overwritten(self):
        self.record("mem", "failed")
        with self.assertRaises(p.safe.ArtifactError):
            self.record("mem", "passed")
        self.assertEqual(self.state(), "failed")

    def test_failure_retained_after_new_attempt(self):
        self.record("mem", "failed")
        self.ledger = p.begin_attempt(self.ledger, self.key, "T3-002")
        self.assertFalse(p.can_skip(self.ledger["items"][self.key], "mem"))
        for stage in p.STAGES:
            self.record(stage)
        self.assertEqual(self.state(), "passed_after_retry")
        self.ledger["items"][self.key]["history"][0]["state"] = "passed"
        self.assertEqual(self.state(), "passed_after_retry")

    def test_failed_services_block_pass(self):
        with self.assertRaises(p.safe.ArtifactError):
            self.record("services", failed_services=["example.service"])
        self.record("services", "failed", failed_services=["example.service"])
        self.assertEqual(self.state(), "failed")

    def test_services_must_be_explicit_even_if_other_checks_pass(self):
        for stage in p.STAGES[:-1]:
            self.record(stage)
        self.assertEqual(self.state(), "incomplete")
        with self.assertRaises(p.safe.ArtifactError):
            self.record("services", failed_services=None)
        self.record("services")
        self.assertEqual(self.state(), "passed")

    def test_not_applicable_is_neither_pass_nor_skip(self):
        self.record("network", "not_applicable")
        self.assertFalse(p.can_skip(self.ledger["items"][self.key], "network"))
        self.assertNotEqual(self.state(), "passed")

    def test_roundtrip_retains_resume_and_detects_tampering(self):
        self.record("deployment")
        restored = p.loads(p.dumps(self.ledger))
        self.assertTrue(p.can_skip(restored["items"][self.key], "deployment"))
        restored["items"][self.key]["checks"]["deployment"]["json"] += " "
        with self.assertRaises(p.safe.ArtifactError):
            p.summarize(restored)

    def test_updates_do_not_mutate_input(self):
        original = copy.deepcopy(self.ledger)
        p.begin_attempt(self.ledger, self.key, "T3-002")
        self.assertEqual(self.ledger, original)

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        for data in (b"{\"ok\":true,\"ok\":false}", b"{\"value\":NaN}"):
            with self.subTest(data=data), self.assertRaises(p.safe.ArtifactError):
                p.decoded(data)


if __name__ == "__main__":
    unittest.main()
