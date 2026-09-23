"""以暫存目錄與替身驗證相機 runner 的安全界限；不接觸實機。"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools/cm6_rc3_camera_test.py"


class CameraRunnerSafetyTests(unittest.TestCase):
    def setUp(self):
        specification = importlib.util.spec_from_file_location("camera_runner_safety_subject", RUNNER_PATH)
        self.runner = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(self.runner)
        temporary = tempfile.TemporaryDirectory(prefix="cm6-camera-safety-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.runtime = self.base / "runtime"
        for path in ("usr/bin", "usr/lib", "usr/share/camera_json"):
            (self.runtime / path).mkdir(parents=True, exist_ok=True)
        (self.runtime / "usr/lib/libsdkcam.so").touch()
        calibration = self.runtime / "usr/share/camera_json" / self.runner.CALIBRATION_NAME
        calibration.write_bytes(b"calibration mock")
        self.runner.CALIBRATION_SHA256 = self.runner.digest(calibration)
        self.runner.CALIBRATION_LINK = self.base / "global-calibration"
        self.runner.FRAME_ROOT = self.base / "frames"
        self.runner.FRAME_ROOT.mkdir()
        self.runner.THERMAL_ROOT = self.base / "thermal"
        (self.runner.THERMAL_ROOT / "thermal_zone0").mkdir(parents=True)
        self.thermal = self.runner.THERMAL_ROOT / "thermal_zone0/temp"
        self.thermal.write_text("50000")
        self.runner.LOCK_FILE = self.base / "lock"
        self.frame = self.runner.FRAME_ROOT / "cpp0_output_1920x1080_s1920.nv12"
        self.executable = self.runtime / "usr/bin/cam-test"
        self.configuration = self.base / "config.json"
        self.configuration.write_text(json.dumps({"isp_node": [{"sensor_id": 0, "enable": 1}]}))
        self.runner.CONFIG_SHA256 = {self.runner.digest(self.configuration)}

        # main 會變更 umask 與中止訊號處理；每項測試均還原，避免污染其他測試。
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        for number in (signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, number, signal.getsignal(number))
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(self.runner.os, "geteuid", return_value=0))
        patches.enter_context(patch.object(self.runner, "existing_camera_processes", return_value=[]))
        patches.enter_context(patch.object(
            self.runner, "capture_kernel",
            side_effect=lambda path: path.write_text("核心紀錄替身；未讀取真實核心。\n"),
        ))
        patches.enter_context(patch.object(
            self.runner.shutil, "disk_usage",
            return_value=type("測試空間", (), {"free": 1024 * 1024 * 1024})(),
        ))
        self.set_program("sys.exit(0)")

    def set_program(self, code):
        self.executable.write_text(
            f"#!{sys.executable}\nfrom pathlib import Path\nimport time,sys\n{code}\n"
        )
        self.executable.chmod(0o700)
        self.runner.CAM_SHA256 = self.runner.digest(self.executable)

    def invoke(self, name, extra=(), configuration=None, output=None):
        output = output or self.base / name
        arguments = ["--runtime", str(self.runtime), "--config", str(configuration or self.configuration),
                     "--output", str(output), *extra]
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.runner.main(arguments)
        return result, output, time.monotonic() - started

    def test_check_only_has_no_output(self):
        """僅核對輸入，不建立輸出。"""
        result, output, _ = self.invoke("check-only", ["--check-only"])
        self.assertEqual(result, 0)
        self.assertFalse(output.exists())

    def test_unknown_configuration_is_blocked(self):
        """拒絕白名單外的配置。"""
        unknown = self.base / "unknown.json"
        unknown.write_text("{}")
        result, output, _ = self.invoke("unknown", configuration=unknown)
        self.assertEqual(result, 2)
        self.assertFalse(output.exists())

    def test_existing_evidence_is_preserved(self):
        """拒絕既有證據目錄且保留內容。"""
        existing = self.base / "existing"
        existing.mkdir()
        (existing / "keep").write_text("保留")
        result, _, _ = self.invoke("existing", output=existing)
        self.assertEqual(result, 2)
        self.assertEqual((existing / "keep").read_text(), "保留")

    def test_existing_calibration_is_preserved(self):
        """拒絕既有校正路徑且保留內容。"""
        self.runner.CALIBRATION_LINK.mkdir()
        sentinel = self.runner.CALIBRATION_LINK / "keep"
        sentinel.write_text("保留")
        result, _, _ = self.invoke("existing-calibration")
        self.assertEqual(result, 2)
        self.assertEqual(sentinel.read_text(), "保留")

    def test_existing_frame_is_preserved(self):
        """拒絕既有影格且保留原始內容。"""
        self.frame.write_text("保留")
        result, _, _ = self.invoke("existing-frame")
        self.assertEqual(result, 2)
        self.assertEqual(self.frame.read_text(), "保留")

    def test_initial_temperature_blocks_execution(self):
        """起始達 80°C 不執行相機。"""
        self.thermal.write_text("80000")
        result, output, _ = self.invoke("initial-heat")
        self.assertEqual(result, 2)
        self.assertFalse(output.exists())

    def test_success_requires_review_and_cleans_owned_link(self):
        """正常退出僅標 NEEDS_REVIEW，校驗影格並清除自建連結。"""
        self.set_program(f"Path({str(self.frame)!r}).write_bytes(b'mock-frame')")
        result, output, _ = self.invoke("success")
        report = json.loads((output / "summary.json").read_text())
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "NEEDS_REVIEW")
        self.assertEqual(report["outputs"][0]["sha256"], hashlib.sha256(b"mock-frame").hexdigest())
        self.assertFalse(self.frame.exists())
        self.assertFalse(self.runner.CALIBRATION_LINK.exists())
        self.assertEqual(report["calibration_link_cleanup"], "已移除本次連結")

    def test_sdk_failure_preserves_evidence_and_cleans_link(self):
        """SDK 失敗仍保存紀錄且清除自建連結。"""
        self.set_program("sys.exit(7)")
        result, output, _ = self.invoke("sdk-failure")
        report = json.loads((output / "summary.json").read_text())
        self.assertEqual(result, 1)
        self.assertEqual(report["returncode"], 7)
        self.assertFalse(self.runner.CALIBRATION_LINK.exists())

    def test_timeout_stops_owned_process(self):
        """時間上限停止自己的程序。"""
        self.set_program("time.sleep(20)")
        result, output, elapsed = self.invoke("timeout", ["--seconds", "1"])
        report = json.loads((output / "summary.json").read_text())
        self.assertEqual(result, 1)
        self.assertEqual(report["stop_reason"], "達時間上限")
        self.assertLess(elapsed, 7)
        self.assertFalse(self.runner.CALIBRATION_LINK.exists())

    def test_rising_temperature_stops_execution(self):
        """運行達 90°C 中止並保存證據。"""
        self.set_program(f"Path({str(self.thermal)!r}).write_text('91000')\ntime.sleep(20)")
        result, output, _ = self.invoke("rising-heat")
        report = json.loads((output / "summary.json").read_text())
        self.assertEqual(result, 1)
        self.assertEqual(report["stop_reason"], "達停止溫度")
        self.assertFalse(self.runner.CALIBRATION_LINK.exists())

    def test_excessive_duration_is_blocked(self):
        """拒絕超過 60 秒的設定。"""
        result, output, _ = self.invoke("duration", ["--seconds", "61"])
        self.assertEqual(result, 2)
        self.assertFalse(output.exists())

    def test_summary_write_failure_still_cleans_link(self):
        """摘要無法寫入仍清除自建連結。"""
        self.set_program(f"Path({str(self.frame)!r}).write_bytes(b'mock-frame')")
        original = Path.write_text

        def fail_summary(path, *args, **kwargs):
            if path.name == "summary.json":
                raise OSError("模擬空間不足")
            return original(path, *args, **kwargs)

        with patch.object(Path, "write_text", fail_summary):
            result, _, _ = self.invoke("summary-failure")
        self.assertEqual(result, 1)
        self.assertFalse(self.runner.CALIBRATION_LINK.exists())

    def test_external_replacement_is_preserved(self):
        """校正路徑遭外部取代時保留新檔並判失敗。"""
        self.set_program(
            f"Path({str(self.frame)!r}).write_bytes(b'mock-frame')\n"
            f"Path({str(self.runner.CALIBRATION_LINK)!r}).unlink()\n"
            f"Path({str(self.runner.CALIBRATION_LINK)!r}).write_text('保留外部取代檔')"
        )
        result, output, _ = self.invoke("replacement")
        report = json.loads((output / "summary.json").read_text())
        self.assertEqual(result, 1)
        self.assertEqual(self.runner.CALIBRATION_LINK.read_text(), "保留外部取代檔")
        self.assertEqual(report["calibration_link_cleanup"], "路徑已遭外部變更，保留現況")


if __name__ == "__main__":
    unittest.main()
