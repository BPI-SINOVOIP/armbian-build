"""小型合成 JSON 回歸；不讀現場映像，不使用任何硬體或網路工具。"""

import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location("lab_summary", TOOLS / "bpi_h618_lab_summary.py")
s = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC.loader.exec_module(s)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manifests, self.refs, entries = {}, {}, []
        for os_name in ("bookworm", "jammy", "noble", "resolute", "trixie"):
            for desktop in ("minimal", "xfce_desktop"):
                key = f"{os_name}-{desktop}"
                manifest = dict(schema="bpi-h618-customer-components-v1", board="bananapim4zeroemac",
                                os=os_name, desktop=desktop, kernel_release="6.18.49-current-sunxi64",
                                image={"path": "/不應讀取的原映像.img.xz", "raw": {"bytes": 2048, "sha256": s.progress.digest(key.encode())},
                                       "compressed": {"bytes": 100, "sha256": "c" * 64}})
                ref = self.write(f"T0-customer-components-001/{key}.json", manifest)
                self.manifests[key], self.refs[key] = manifest, ref
                entries.append(dict(os=os_name, desktop=desktop, manifest=ref))
        self.index = dict(schema="bpi-h618-customer-components-index-v1", ok=True, count=10, entries=entries)
        self.pin = self.write(s.INDEX, self.index)["sha256"]
        self.key = "bookworm-xfce_desktop"

    def write(self, name, data):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(data, ensure_ascii=False).encode()
        path.write_bytes(blob)
        return dict(path=str(path), bytes=len(blob), sha256=s.progress.digest(blob))

    def summary(self):
        return s.summarize(self.base, self.pin)

    def item(self, result=None, key=None):
        return next(i for i in (result or self.summary())["images"] if i["key"] == (key or self.key))

    def receipt(self, ok=True, time=10):
        image = self.manifests[self.key]["image"]
        return dict(schema="bpi-h618-emmc-deploy-v1", ok=ok, status="verified" if ok else "failed",
                    source=copy.deepcopy(image), finished_unix=time,
                    remote_state=dict(status="verified" if ok else "failed", readback=copy.deepcopy(image["raw"]),
                                      error=None if ok else "XZ 損壞或解碼記憶體超限"))

    def boot(self, ok=True, time=20):
        return dict(ok=ok, finished_unix=time, evidence={"components": self.refs[self.key]},
                    kernel_release_observed="6.18.49-current-sunxi64", login_observed=True,
                    original_boot_chain_verified=False)

    def smoke(self, ok=True, time=30):
        return dict(schema="bpi-h618-customer-smoke-host-v1", ok=ok, finished_unix=time,
                    components=self.refs[self.key], ssh_exitcode=0,
                    remote=dict(ok=ok, identity_verified=True, cpu={"ok": True}, file={"ok": True},
                                failed_services={"ok": True, "units": []}, failures=[], elapsed_seconds=30))

    def batch(self, number, *, stage="access", status="running", events=None, reused_stages=None, board_serial="0845"):
        case = dict(key=self.key, components_sha256=self.refs[self.key]["sha256"], image=self.manifests[self.key]["image"],
                    status=status, stage=stage, started_unix=number * 100, events=events or [])
        if reused_stages:
            case["reused_stages"] = reused_stages
        return self.write(f"T4-matrix-{number:03}/summary.json", dict(schema="bpi-h618-lab-batch-v1",
                          index_sha256=self.pin, board_serial=board_serial, cases=[case], status=status))

    def test_mixed_or_empty_t4_board_serial_rejected(self):
        self.batch(1)
        for serial in ("0846", None, "", "  ", 845):
            with self.subTest(board_serial=serial):
                self.batch(2, board_serial=serial)
                with self.assertRaisesRegex(ValueError, "板號"):
                    self.summary()

    def test_same_board_scope_does_not_assign_t3_hardware(self):
        self.batch(1, stage="boot")
        self.batch(2)
        self.write("T3-bookworm-deploy-001/receipt.json", self.receipt())
        self.write(f"T4-matrix-001/{self.key}/boot/report.json", self.boot())
        result = self.summary()
        self.assertEqual(result["hardware_scope"]["t4_board_serial"], "0845")
        self.assertIsNone(result["hardware_scope"]["unknown_hardware_id"])
        self.assertTrue(result["hardware_scope"]["counts_include_unknown_hardware"])
        stages = self.item(result)["stages"]
        self.assertIsNone(stages["deployment"]["history"][0]["hardware_id"])
        self.assertEqual(stages["boot"]["history"][0]["hardware_id"], "0845")
        self.assertFalse(result["can_skip_current_media"])

    def test_ten_fixed_images_pending_without_invented_pass(self):
        result = self.summary()
        self.assertEqual(result["counts"]["images"], 10)
        self.assertEqual(result["counts"]["deployment_pending_images"], 10)
        self.assertFalse(result["can_skip_current_media"])
        self.assertFalse(result["all_tests_passed"])
        self.assertTrue(all(not i["stages"]["boot"]["original_boot_chain_verified"] for i in result["images"]))

    def test_wrong_index_pin_rejected(self):
        with self.assertRaises(ValueError):
            s.summarize(self.base, "0" * 64)

    def test_changed_components_rejected(self):
        self.write("T0-customer-components-001/bookworm-minimal.json", {})
        with self.assertRaises(ValueError):
            self.summary()

    def test_duplicate_or_missing_index_entries_rejected(self):
        for entries in (self.index["entries"][:-1], self.index["entries"][:-1] + self.index["entries"][:1]):
            self.pin = self.write(s.INDEX, {**self.index, "entries": entries})["sha256"]
            with self.assertRaises(ValueError):
                self.summary()

    def test_attempts_merge_reuse_and_keep_lzma_and_access_failures(self):
        self.write(f"T4-matrix-001/{self.key}/deploy/receipt.json.partial", self.receipt(False))
        deploy = self.write(f"T4-matrix-002/{self.key}/deploy/receipt.json", self.receipt(time=200))
        boot = self.write(f"T4-matrix-002/{self.key}/boot/report.json", self.boot(time=210))
        self.batch(2, status="interrupted", events=[dict(stage="access", finished_unix=220, exitcode=1)])
        self.write(f"T4-matrix-002/{self.key}/access/report.json", dict(ok=False, error="SSH 主機金鑰尚未生成"))
        self.batch(3, stage="return", status="collected", reused_stages={"deploy": deploy, "boot": boot})
        self.write(f"T4-matrix-003/{self.key}/smoke/report.json.partial", self.smoke(time=320))
        self.write(f"T4-matrix-003/{self.key}/return/report.json", dict(ok=True, finished_unix=330,
                   recovery_verified=True, independent_root_ram_verified=True, strict_ssh_verified=True, sd_prefix_unchanged=True))
        result = self.summary()
        item = self.item(result)
        self.assertEqual([item["stages"][k]["status"] for k in s.STAGES], ["verified", "observed", "short_verified", "verified"])
        self.assertEqual(len(item["reused_stages"]), 2)
        self.assertEqual(len(result["failures"]), 2)
        self.assertEqual(result["failures"][0]["remote_error"], "XZ 損壞或解碼記憶體超限")
        self.assertEqual(result["failures"][1]["time_basis"], "batch_event.finished_unix")
        self.assertFalse(item["can_skip_current_media"])
        self.assertFalse(result["all_tests_passed"])

    def test_reuse_hash_mismatch_or_outside_base_rejected(self):
        good = self.write(f"T4-matrix-001/{self.key}/deploy/receipt.json", self.receipt())
        for ref in ({**good, "sha256": "0" * 64}, {**good, "path": "/不允許的目錄/receipt.json"}):
            self.batch(2, reused_stages={"deploy": ref})
            with self.assertRaises(ValueError):
                self.summary()

    def test_reuse_wrong_image_rejected(self):
        wrong = self.boot()
        wrong["evidence"]["components"] = self.refs["jammy-minimal"]
        ref = self.write(f"T4-matrix-001/{self.key}/boot/report.json", wrong)
        self.batch(2, reused_stages={"boot": ref})
        with self.assertRaises(ValueError):
            self.summary()

    def test_latest_failure_does_not_erase_historical_success(self):
        self.write(f"T4-matrix-001/{self.key}/deploy/receipt.json", self.receipt(time=100))
        self.write(f"T4-matrix-002/{self.key}/deploy/receipt.json.partial", self.receipt(False, 200))
        result = self.summary()
        stage = self.item(result)["stages"]["deployment"]
        self.assertEqual(stage["status"], "failed")
        self.assertTrue(stage["historically_succeeded"])
        self.assertFalse(result["can_skip_current_media"])

    def test_batch_only_success_is_not_stage_verification(self):
        self.batch(1, stage="boot", status="collected", events=[dict(stage="boot", exitcode=0, finished_unix=10)])
        result = self.summary()
        self.assertEqual(self.item(result)["stages"]["boot"]["status"], "incomplete")
        self.assertEqual(result["counts"]["boot_observed_images"], 0)

    def test_reused_list_alone_never_adds_deployment(self):
        self.write("T4-matrix-001/summary.json", dict(schema="bpi-h618-lab-batch-v1", index_sha256=self.pin, board_serial="0845",
                   reused=[dict(os="bookworm", desktop="minimal", raw_sha256=self.manifests["bookworm-minimal"]["image"]["raw"]["sha256"],
                                return_report_sha256="0" * 64, all_tests_passed=True)]))
        self.assertEqual(self.summary()["counts"]["deployment_verified_images"], 0)

    def test_t3_recovery_requires_hashed_reference_not_directory_guess(self):
        report = dict(ok=True, finished_unix=10, recovery_verified=True, independent_root_ram_verified=True,
                      strict_ssh_verified=True, sd_prefix_unchanged=True)
        ref = self.write("T3-bookworm-return-001/report.json", report)
        self.assertEqual(self.summary()["counts"]["recovery_verified_images"], 0)
        self.write("T4-matrix-001/summary.json", dict(schema="bpi-h618-lab-batch-v1", index_sha256=self.pin, board_serial="0845",
                   reused=[dict(os="bookworm", desktop="minimal", return_report_sha256=ref["sha256"])]))
        self.assertEqual(self.summary()["counts"]["recovery_verified_images"], 1)

    def test_services_failure_overrides_ok_flags(self):
        report = self.smoke()
        report["remote"]["failed_services"] = {"ok": False, "units": ["console-setup.service"]}
        self.write("T3-bookworm-smoke-001/report.json.partial", report)
        result = self.summary()
        self.assertEqual(result["counts"]["smoke_short_verified_images"], 0)
        self.assertEqual(result["failures"][0]["failed_services"]["units"], ["console-setup.service"])

    def test_missing_smoke_checks_are_not_true(self):
        for missing in ("identity_verified", "cpu", "file", "failed_services", "failures"):
            report = self.smoke()
            del report["remote"][missing]
            self.write("T3-bookworm-smoke-001/report.json.partial", report)
            self.assertEqual(self.summary()["counts"]["smoke_short_verified_images"], 0)

    def test_insufficient_space_is_unexecuted_condition_not_os_failure(self):
        report = self.smoke(False)
        report["remote"]["file"] = dict(ok=False, bytes_written=0, bytes_read=0)
        report["remote"]["failures"] = [dict(stage="file", reason="eMMC 暫存空間不足，須保留至少 32 MiB 餘量")]
        self.write("T3-bookworm-smoke-001/report.json.partial", report)
        result = self.summary()
        self.assertEqual(result["counts"]["failures"], 0)
        self.assertEqual(result["counts"]["conditions"], 1)
        self.assertEqual(result["conditions"][0]["file_status"], "not_tested")
        self.assertEqual(result["counts"]["stages"]["smoke"], {"blocked": 1, "pending": 9})
        self.assertFalse(result["all_tests_passed"])
        report["remote"]["failed_services"] = {"ok": False, "units": ["console-setup.service"]}
        self.write("T3-bookworm-smoke-001/report.json.partial", report)
        self.assertEqual(self.summary()["counts"]["failures"], 1)

    def test_readback_mismatch_is_not_verified(self):
        report = self.receipt()
        report["remote_state"]["readback"]["sha256"] = "0" * 64
        self.write("T3-bookworm-deploy-001/receipt.json", report)
        self.assertEqual(self.summary()["counts"]["deployment_verified_images"], 0)

    def test_different_kernel_is_not_observed_as_customer_kernel(self):
        report = self.boot()
        report["kernel_release_observed"] = "6.6.75-current-sunxi64"
        self.write("T3-bookworm-boot-001/report.json", report)
        self.assertEqual(self.summary()["counts"]["boot_observed_images"], 0)

    def test_duplicate_partial_not_double_counted(self):
        report = self.receipt()
        self.write("T3-bookworm-deploy-001/receipt.json", report)
        self.write("T3-bookworm-deploy-001/receipt.json.partial", report)
        self.assertEqual(len(self.item()["stages"]["deployment"]["history"]), 1)

    def test_in_progress_receipt_is_incomplete_not_failed(self):
        report = self.receipt(False)
        del report["finished_unix"]
        report["status"] = "started"
        report["remote_state"] = None
        self.write("T3-bookworm-deploy-001/receipt.json.partial", report)
        result = self.summary()
        self.assertEqual(result["counts"]["failures"], 0)
        self.assertEqual(self.item(result)["stages"]["deployment"]["status"], "undated")

    def test_unbound_memory_failure_retained_without_guess(self):
        self.write("T3-bookworm-memory-001/report.json", dict(ok=False, finished_unix=5, ssh_exitcode=1))
        result = self.summary()
        self.assertEqual(len(result["failures"]), 1)
        self.assertIsNone(result["failures"][0]["image"])

    def test_truncated_partial_is_reported_without_hiding_other_results(self):
        path = self.base / "T3-bookworm-smoke-001/report.json.partial"
        path.parent.mkdir()
        path.write_bytes(b'{"ok":')
        self.write("T3-bookworm-deploy-001/receipt.json", self.receipt())
        result = self.summary()
        self.assertEqual(result["counts"]["read_errors"], 1)
        self.assertEqual(result["counts"]["deployment_verified_images"], 1)
        self.assertFalse(result["snapshot_complete"])

    def test_symlink_fifo_and_oversized_reports_not_read(self):
        path = self.base / "T3-bookworm-smoke-001/report.json"
        path.parent.mkdir()
        path.symlink_to(self.base / s.INDEX)
        self.assertEqual(self.summary()["counts"]["read_errors"], 1)
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.summary()["counts"]["read_errors"], 1)
        path.unlink()
        with path.open("wb") as stream:
            stream.truncate(s.LIMIT + 1)
        self.assertEqual(self.summary()["counts"]["read_errors"], 1)

    def test_non_report_and_firstlogin_json_ignored(self):
        for name in ("T0-customer-components-001/kernel.img", "T3-bookworm-firstlogin-001/report.json",
                     "T3-bookworm-smoke-001/dmesg.json", "T4-xfce-xz-probe-001/report.json"):
            self.write(name, {"不得讀取": True})
        result = self.summary()
        self.assertEqual(result["counts"]["unbound_reports"], 0)
        self.assertEqual(result["counts"]["read_errors"], 0)

    def test_source_hash_and_host_time_traceable(self):
        report = self.receipt()
        del report["finished_unix"]
        report["finished_utc"] = "2026-09-17T00:00:00+08:00"
        report["last_progress"] = {"time_utc": "1970-01-01T00:00:00+00:00"}
        ref = self.write("T3-bookworm-deploy-001/receipt.json", report)
        record = self.item()["stages"]["deployment"]["history"][0]
        self.assertEqual(record["source"]["sha256"], ref["sha256"])
        self.assertEqual(record["time_utc"], "2026-09-16T16:00:00+00:00")
        self.assertEqual(record["time_basis"], "finished_utc")

    def test_cli_only_stdout_and_no_evidence_writes(self):
        before = {p: p.stat().st_mtime_ns for p in self.base.rglob("*")}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(s.main([str(self.base), "--index-sha256", self.pin]), 0)
        self.assertEqual(json.loads(output.getvalue())["counts"]["images"], 10)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.base.rglob("*")})


if __name__ == "__main__":
    unittest.main()
