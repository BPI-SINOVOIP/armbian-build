"""核對固定來源、限定改動及原生相機快取；不編譯、不下載、不操作硬體。"""
import copy
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_bpi_cm6_camera as CAMERA
import prepare_bpi_k1_vendor_rootfs as COMMON
import bpi_k1_native as NATIVE


class CameraSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = CAMERA.load_lock()

    def test_fixed_mode2_matches_two_sensors_and_cpp(self):
        value = json.loads((CAMERA.CONFIG / self.lock["config"]["path"]).read_text())
        CAMERA.mode2_config(value)
        for field, wrong in (("sensor_id", 1), ("sensor_work_mode", 0), ("bit_depth", 12), ("in_width", 3864)):
            with self.subTest(field=field):
                changed = copy.deepcopy(value)
                changed["isp_node"][1][field] = wrong
                with self.assertRaises(ValueError):
                    CAMERA.mode2_config(changed)

    def test_portable_inputs_have_no_private_host_tree_or_absolute_tool_paths(self):
        serialized = json.dumps(self.lock)
        self.assertNotIn("/home/build-worker/", serialized)
        self.assertNotIn("/usr/include/", serialized)
        self.assertNotIn("trees", self.lock["toolchain"])
        for path in self.lock["toolchain"]["commands"].values():
            self.assertFalse(Path(path).is_absolute())
            self.assertNotIn("..", Path(path).parts)
        for item in self.lock["inputs"]:
            self.assertEqual(Path(item["path"]).name, item["path"])
            self.assertTrue(item["url"].startswith(("https://archive.ubuntu.com/", "https://ports.ubuntu.com/", "https://archive.spacemit.com/")))

    def test_refuses_other_board_before_any_inputs_or_output(self):
        with mock.patch.object(CAMERA, "load_lock") as load:
            with self.assertRaises(ValueError):
                CAMERA.build(self.root, self.root / "output", board="bpi-f3")
            load.assert_not_called()
        self.assertFalse((self.root / "output").exists())

    def test_input_mutation_symlink_and_escape_are_rejected(self):
        path = self.root / "payload"
        path.write_bytes(b"original")
        item = {"path": "payload", "bytes": path.stat().st_size, "sha256": CAMERA.digest(path)}
        CAMERA.verify_inputs(self.root, {"inputs": [item]})
        extra = self.root / "unlocked.h"
        extra.write_bytes(b"shadow")
        with self.assertRaises(ValueError):
            CAMERA.verify_inputs(self.root, {"inputs": [item]})
        extra.unlink()
        path.write_bytes(b"modified")
        with self.assertRaises(ValueError):
            CAMERA.verify_inputs(self.root, {"inputs": [item]})
        path.unlink()
        path.symlink_to("/etc/hosts")
        with self.assertRaises(ValueError):
            CAMERA.verify_inputs(self.root, {"inputs": [item]})
        with self.assertRaises(ValueError):
            CAMERA.child(self.root, "../elsewhere")

    def test_fetch_only_accepts_fixed_bytes_and_never_overwrites_bad_cache(self):
        source = self.root / "source"
        source.write_bytes(b"fixed")
        item = {"path": "item.deb", "bytes": 5, "sha256": CAMERA.digest(source), "url": "https://example.invalid/item.deb"}
        cache = self.root / "cache"
        with mock.patch.object(CAMERA.urllib.request, "urlopen", return_value=io.BytesIO(b"fixed")):
            CAMERA.obtain_inputs(cache, {"inputs": [item]})
        (cache / item["path"]).write_bytes(b"wrong")
        with mock.patch.object(CAMERA.urllib.request, "urlopen") as request:
            with self.assertRaises(ValueError):
                CAMERA.obtain_inputs(cache, {"inputs": [item]})
            request.assert_not_called()

    def test_only_library_profile_and_provenance_can_change(self):
        before = {"usr/lib/libsdkcam.so": 1, "usr/lib/libcam_sensors.so": 2}
        after = {"usr/lib/libsdkcam.so": 3, "usr/lib/libcam_sensors.so": 2,
                 self.lock["config"]["installed_path"]: 4, "usr/share/doc/k1x-cam/cm6-source.json": 5}
        self.assertEqual(len(CAMERA.validate_payload_changes(before, after, self.lock)), 3)
        after["usr/lib/libcam_sensors.so"] = 6
        with self.assertRaises(ValueError):
            CAMERA.validate_payload_changes(before, after, self.lock)

    def test_source_rejects_wrong_archive_before_extract(self):
        wrong = self.root / "wrong.tar.xz"
        wrong.write_bytes(b"not a source")
        with self.assertRaises(ValueError):
            CAMERA.extract_source(wrong, self.root / "source", self.lock)
        self.assertFalse((self.root / "source").exists())

    def test_compiler_cannot_consume_unlocked_host_header(self):
        source = self.root / "source"
        source.mkdir()
        header = source / "fixed.h"
        header.write_text("int value;\n")
        dependency = self.root / "output.d"
        dependency.write_text("output.o: " + str(header) + "\n")
        self.assertEqual(len(CAMERA.verify_dependencies([dependency], source, self.root / "target", self.root / "tools", self.root)), 1)
        dependency.write_text("output.o: /etc/hosts\n")
        with self.assertRaisesRegex(ValueError, "標頭"):
            CAMERA.verify_dependencies([dependency], source, self.root / "target", self.root / "tools", self.root)

    def test_manifest_rejects_changed_builder_and_false_stop_claim(self):
        record = {"schema_version": 1, "board": "bpi-cm6", "status": "complete",
                  "source_lock_sha256": CAMERA.digest(CAMERA.LOCK),
                  "builder_sha256": CAMERA.digest(CAMERA.__file__), "patches": self.lock["patches"],
                  "inputs": {item["path"]: item["sha256"] for item in self.lock["inputs"]},
                  "streamoff_fixed": True, "stop_order_patch_applied": True, "hardware_validation": "pending"}
        manifest = self.root / CAMERA.PACKAGE_MANIFEST
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "硬體驗證"):
            CAMERA.package_records(self.root)
        record["streamoff_fixed"] = False
        record["builder_sha256"] = "0" * 64
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "來源建置證據"):
            CAMERA.package_records(self.root)

    def test_patch_series_rejects_reorder_and_broken_source_chain(self):
        lock = copy.deepcopy(self.lock)
        lock["patches"].reverse()
        with self.assertRaisesRegex(ValueError, "順序"):
            CAMERA.validate_patch_series(lock)
        lock = copy.deepcopy(self.lock)
        lock["patches"][1]["before_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "雜湊鏈"):
            CAMERA.validate_patch_series(lock)

    def test_common_records_preserve_official_fallback(self):
        records = COMMON.camera_package_records(self.root)
        for name, value in COMMON.CM6_CAMERA_PACKAGES.items():
            self.assertEqual({key: records[name][key] for key in value}, value)
        with self.assertRaises(ValueError):
            COMMON.camera_packages("bpi-f3", self.root)

    def test_native_only_cm6_camera_binds_source_builder_and_profile(self):
        args = ("legacy", "noble", "gnome", "minimal", "sd", "20260927-rc9")
        cm6 = NATIVE.configuration("bananapicm6", *args, camera="dual-imx415")
        plain = NATIVE.configuration("bananapicm6", *args, camera="none")
        for name in ("tools/build_bpi_cm6_camera.py", "config/spacemit-k1-camera/source-lock.json",
                     "config/spacemit-k1-camera/cm6-dual-imx415-mode2.json"):
            self.assertIn(name, cm6["asset_inputs"])
            self.assertIn(name, cm6["build_inputs"])
            self.assertNotIn(name, plain["asset_inputs"])
        self.assertIn(NATIVE.CAMERA_LOCK, cm6["source_locks"])
        self.assertNotIn(NATIVE.CAMERA_LOCK, plain["source_locks"])

    def test_locked_source_patch_series_keeps_single_and_covers_both_stop_branches(self):
        # 來源快取為選配；有明確固定檔案時才做真實補丁核對，絕不編譯。
        cache = ROOT / "cache/bpi-cm6-camera-debs"
        archive = cache / self.lock["source"]["path"]
        if not archive.is_file():
            self.skipTest("尚未準備固定來源快取")
        source = self.root / "source"
        before = CAMERA.extract_source(archive, source, self.lock)
        single_prefix = (source / self.lock["patches"][0]["target"]).read_text().split("static int online_test_viisp_streamOff(", 1)[0]
        def run(argv, cwd):
            return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True)
        after = CAMERA.apply_patch(source, before, self.lock, run)
        self.assertEqual([name for name in before if before[name] != after[name]], [self.lock["patches"][0]["target"]])
        text = (source / self.lock["patches"][0]["target"]).read_text()
        self.assertIn("config->ispFeConfig[0].sensorId, config->ispFeConfig[0].sensorWorkMode", text)
        self.assertIn("config->ispFeConfig[1].sensorId, config->ispFeConfig[1].sensorWorkMode", text)
        self.assertEqual(text.split("static int dual_pipeline_viisp_streamOff(", 1)[0], single_prefix)
        helper = text.split("static int dual_pipeline_viisp_streamOff(", 1)[1].split("\nint dual_pipeline_online_test", 1)[0]
        calls = [line.strip() for line in helper.splitlines() if line.strip().endswith(";") and not line.strip().startswith("return")]
        self.assertEqual(calls, [
            "viisp_vi_online_streamOff(pipeline0Id);", "viisp_vi_online_streamOff(pipeline1Id);",
            "testSensorStop(sensor0Handle);", "viisp_isp_streamOff(firmware0Id);",
            "cpp_stop(pipeline0Id);", "test_buffer_reset(pipeline0Id);",
            "testSensorStop(sensor1Handle);", "viisp_isp_streamOff(firmware1Id);",
            "cpp_stop(pipeline1Id);", "test_buffer_reset(pipeline1Id);",
        ])
        self.assertEqual(text.count("dual_pipeline_viisp_streamOff(sensor0Handle, pipeline0Id, firmware0Id,"), 2)
        dual = text.split("int dual_pipeline_online_test(", 1)[1]
        autorun, manual = dual.split("    } else {", 1)
        for branch in (autorun, manual):
            self.assertEqual(branch.count("dual_pipeline_viisp_streamOff(sensor0Handle, pipeline0Id, firmware0Id,"), 1)
        self.assertNotIn("online_test_viisp_streamOff(", text)


if __name__ == "__main__":
    unittest.main()
