#!/usr/bin/env python3
"""K1 唯讀蒐證工具的失敗邊界與原始證據回歸。"""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/collect_bpi_k1_runtime.py"
SPEC = importlib.util.spec_from_file_location("collect_bpi_k1_runtime", MODULE_PATH)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def collector(self, timeout=1):
        return runtime.Collector(self.base / "evidence", timeout)

    def test_llvmpipe_is_not_hardware_pass(self):
        result = runtime.gpu_observation({"glxinfo": "OpenGL renderer string: llvmpipe (LLVM 18)"}, True)
        self.assertEqual(result["status"], "software_renderer_observed")
        self.assertFalse(result["validated"])

    def test_lavapipe_and_mixed_devices_are_explicit(self):
        result = runtime.gpu_observation({"vulkaninfo": "deviceName = PowerVR BXE\ndriverName = lavapipe"}, False)
        self.assertEqual(result["status"], "software_renderer_observed")
        self.assertEqual(len(result["hardware_renderer_lines"]), 1)
        self.assertEqual(result["display_session"], "not_available")

    def test_gpu_device_presence_does_not_prove_renderer(self):
        result = runtime.gpu_observation({"drm_info": "driver: pvr; /dev/dri/renderD128"}, True)
        self.assertEqual(result["status"], "renderer_not_identified")

    def test_hardware_renderer_is_still_unverified(self):
        result = runtime.gpu_observation({"eglinfo": "OpenGL ES renderer: PowerVR Rogue"}, True)
        self.assertEqual(result["status"], "hardware_renderer_reported_unverified")
        self.assertFalse(result["validated"])

    def test_missing_command_preserves_empty_stream_hashes(self):
        collector = self.collector()
        with mock.patch.object(runtime.shutil, "which", return_value=None):
            result = collector.command("missing", ["missing-tool"])
        self.assertEqual(result["status"], "command_not_installed")
        self.assertEqual(result["stdout"]["sha256"], hashlib.sha256(b"").hexdigest())

    def test_timeout_preserves_partial_stdout_and_stderr(self):
        collector = self.collector(timeout=0.2)
        result = collector.command("timeout", [sys.executable, "-I", "-c", "import sys,time; print('partial', flush=True); print('diagnostic', file=sys.stderr, flush=True); time.sleep(10)"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(collector.content("timeout"), "partial\n")
        stderr = collector.output / result["stderr"]["path"]
        self.assertEqual(stderr.read_bytes(), b"diagnostic\n")
        self.assertEqual(result["stderr"]["sha256"], hashlib.sha256(stderr.read_bytes()).hexdigest())

    def test_command_failure_is_not_success(self):
        collector = self.collector()
        result = collector.command("failure", [sys.executable, "-I", "-c", "raise SystemExit(3)"])
        self.assertEqual(result["status"], "command_failed")
        self.assertEqual(result["exit_code"], 3)

    def test_no_display_skips_x11_without_launch(self):
        collector = self.collector()
        with mock.patch.object(runtime.subprocess, "Popen") as popen:
            result = collector.command("glxinfo", ["glxinfo", "-B"], "no_x11_display_session")
        popen.assert_not_called()
        self.assertEqual(result["status"], "skipped")

    def test_shell_metacharacters_remain_literal_arguments(self):
        collector = self.collector()
        payload = "$(touch should-not-exist); `false`"
        result = collector.command("literal", [sys.executable, "-I", "-c", "import sys; print(sys.argv[1])", payload])
        self.assertEqual(result["status"], "collected")
        self.assertEqual(collector.content("literal").strip(), payload)

    def test_existing_evidence_is_never_overwritten(self):
        collector = self.collector()
        original = collector.output / "report.json"
        original.write_text("既有證據", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            runtime.Collector(collector.output)
        self.assertEqual(original.read_text(encoding="utf-8"), "既有證據")

    def test_snapshot_preserves_device_tree_nuls(self):
        source = self.base / "compatible"
        source.write_bytes(b"bananapi,bpi-f3\x00spacemit,k1\x00")
        collector = self.collector()
        result = collector.snapshot("dt_compatible", source)
        copied = collector.output / result["artifact"]["path"]
        self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_profile_rejects_device_file(self):
        result = self.collector().snapshot("profile", Path("/dev/null"))
        self.assertEqual(result["status"], "not_regular_file")

    def test_snapshot_has_size_limit(self):
        source = self.base / "large.json"
        source.write_bytes(b"12345")
        collector = self.collector()
        with mock.patch.object(runtime, "MAX_INPUT_BYTES", 4):
            result = collector.snapshot("large", source)
        self.assertEqual(result["status"], "input_too_large")
        self.assertNotIn("artifact", result)

    def test_invalid_and_unrecognized_profiles_do_not_pass(self):
        self.assertEqual(runtime.profile_observation(b"{broken")["status"], "invalid_json")
        self.assertEqual(runtime.profile_observation(b'{"provider":"SpaceMITExecutionProvider"}')["status"], "unsupported_profile_shape")
        result = runtime.profile_observation(b'[{"name":"SpaceMITExecutionProvider"}]')
        self.assertEqual(result["status"], "execution_not_identified")

    def test_profile_records_actual_node_provider_and_cpu_events(self):
        profile = [
            {"cat": "Node", "dur": 10, "args": {"provider": "SpaceMITExecutionProvider"}},
            {"cat": "Node", "dur": 20, "args": {"provider": "CPUExecutionProvider"}},
            {"cat": "Session", "dur": 30, "args": {"provider": "SpaceMITExecutionProvider"}},
            {"cat": "Node", "dur": 0, "args": {"provider": "SpaceMITExecutionProvider"}},
        ]
        result = runtime.profile_observation(json.dumps({"traceEvents": profile}).encode())
        self.assertEqual(result["status"], "spacemit_execution_reported_unverified")
        self.assertEqual(result["cpu_node_event_fraction"], 0.5)
        self.assertEqual(result["providers"]["SpaceMITExecutionProvider"]["node_events"], 1)
        self.assertFalse(result["validated"])

    def test_cpu_only_profile_exposes_missing_acceleration(self):
        profile = [{"cat": "Node", "dur": 1, "args": {"provider": "CPUExecutionProvider"}}]
        result = runtime.profile_observation(json.dumps(profile).encode())
        self.assertEqual(result["status"], "spacemit_execution_not_observed")
        self.assertEqual(result["cpu_node_event_fraction"], 1.0)

    def test_malformed_profile_duration_cannot_crash_collection(self):
        profile = [{"cat": "Node", "dur": 10 ** 500, "args": {"provider": "SpaceMITExecutionProvider"}}]
        result = runtime.profile_observation(json.dumps(profile).encode())
        self.assertEqual(result["status"], "execution_not_identified")

    def test_complete_collection_cannot_promote_inventory_to_validation(self):
        collector = self.collector()
        samples = {
            "dt_model": "Banana Pi BPI-F3\x00",
            "dt_compatible": "bananapi,bpi-f3\x00spacemit,k1\x00",
            "packages": "img-gpu-powervr\t24.2\triscv64\tinstalled\nunrelated\t1\triscv64\tinstalled\n",
            "vulkaninfo": "deviceName = PowerVR Rogue",
            "ai_providers": json.dumps({"available_providers": ["SpaceMITExecutionProvider", "CPUExecutionProvider"]}),
        }

        def fake_command(key, argv, reason=None):
            record = {"status": "skipped" if reason else "collected"}
            collector.records[key] = record
            return record

        with mock.patch.object(collector, "snapshot"), \
             mock.patch.object(collector, "command", side_effect=fake_command), \
             mock.patch.object(collector, "content", side_effect=lambda key: samples.get(key, "")), \
             mock.patch.object(runtime.Path, "glob", return_value=[]), \
             mock.patch.dict(runtime.os.environ, {}, clear=True):
            report = collector.collect([])
        self.assertEqual(report["board"]["compatible"], ["bananapi,bpi-f3", "spacemit,k1"])
        self.assertEqual(len(report["selected_packages"]), 1)
        self.assertEqual(report["ai"]["status"], "spacemit_provider_available_unverified")
        self.assertEqual(report["gpu"]["status"], "hardware_renderer_reported_unverified")
        self.assertEqual(report["records"]["glxinfo"]["status"], "skipped")
        self.assertFalse(report["hardware_validation_passed"])
        self.assertFalse(report["ai"]["validated"])
        self.assertFalse(report["gpu"]["validated"])

    def test_invalid_timeout_is_rejected_before_output_creation(self):
        for value in (0, -1, 121, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.Collector(self.base / "invalid", value)
        self.assertFalse((self.base / "invalid").exists())


if __name__ == "__main__":
    unittest.main()
