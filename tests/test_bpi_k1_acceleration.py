"""K1 加速配套的跨板、簽章、ABI 與根系統失敗邊界回歸。"""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_k1_acceleration", REPO / "tools/bpi_k1_acceleration.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class AccelerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = MOD.load_lock(MOD.DEFAULT_LOCK)

    def file(self, name, content):
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def baseline(self):
        self.file("usr/lib/os-release", 'ID=ubuntu\nVERSION_CODENAME=noble\n')
        (self.root / "etc").mkdir()
        (self.root / "etc/os-release").symlink_to("/usr/lib/os-release")
        self.file("etc/armbian-release", "BOARD=bananapicm6\n")
        self.file("var/lib/dpkg/status", "Package: libc6\nStatus: install ok installed\nArchitecture: riscv64\nVersion: 2.39-0ubuntu8\n")
        self.file("boot/Image", "Rogue_DDK_Linux_WS rogueddk 23.2@6460340\n")
        self.file("boot/config-6.6.36-legacy-spacemit", "CONFIG_POWERVR_ROGUE=y\nCONFIG_DRM_SPACEMIT=y\nCONFIG_VIDEO_LINLON_K1X=m\nCONFIG_SPACEMIT_TCM=y\n")

    def test_absolute_symlink_uses_candidate_root(self):
        self.baseline()
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "base")
        self.assertTrue(result["passed"], result["errors"])
        self.assertFalse(result["hardware_verified"])
        self.assertTrue(result["pending_dependencies"])

    def test_aliases_require_explicit_exact_board_and_physical_pairing(self):
        self.baseline()
        for name, target in MOD.targets.load_registry()["targets"].items():
            with self.subTest(alias=name):
                physical = target["board"]
                profile = self.lock["profiles"][physical]
                self.file("etc/armbian-release", "BOARD=" + name + "\n")
                self.file("boot/Image", profile["kernel_pvr"])
                self.file("boot/config-" + profile["kernel_release"],
                          "\n".join(k + "=y" for k in self.lock["required_kernel_options"]) + "\n")
                good = MOD.preflight(self.lock, physical, self.root, "base", name)
                self.assertTrue(good["passed"], good["errors"])
                self.assertEqual(good["armbian_board"], name)
                old_entry = MOD.preflight(self.lock, physical, self.root, "base")
                self.assertIn("Armbian 板型與加速配套不同", old_entry["errors"])
                other_medium = name.replace("vendor-sd", "titan-emmc") if target["storage"] == "sd" else name.replace("titan-emmc", "vendor-sd")
                mismatch = MOD.preflight(self.lock, physical, self.root, "base", other_medium)
                self.assertIn("Armbian 板型與加速配套不同", mismatch["errors"])
                wrong_physical = "bpi-f3" if physical == "bpi-cm6" else "bpi-cm6"
                mismatch = MOD.preflight(self.lock, wrong_physical, self.root, "base", name)
                self.assertIn("Armbian 板型與加速配套不同", mismatch["errors"])

    def test_duplicate_board_cannot_hide_cross_board_identity(self):
        self.baseline()
        self.file("etc/armbian-release", "BOARD=bananapicm6\nBOARD=bananapif3\n")
        self.assertIn("Armbian 板型與加速配套不同", MOD.preflight(self.lock, "bpi-cm6", self.root, "base")["errors"])

    def test_board_assignment_rejects_suffixes_and_duplicate_exports(self):
        for value in ('BOARD="bananapicm6"-unknown\n',
                      'BOARD=bananapicm6\nexport BOARD=bananapif3\n',
                      'BOARD=bananapicm6#other\n', 'BOARD=$(false)\n'):
            with self.subTest(value=value):
                self.assertIsNone(MOD.armbian_board_name(value))
        self.assertEqual(MOD.armbian_board_name('BOARD="bananapicm6" # 板型\n'), 'bananapicm6')

    def test_cross_board_kernel_is_rejected(self):
        self.baseline()
        result = MOD.preflight(self.lock, "bpi-f3", self.root, "base")
        self.assertFalse(result["passed"])
        self.assertTrue(any("DDK" in error for error in result["errors"]))
        self.assertTrue(any("板型" in error for error in result["errors"]))

    def test_second_incompatible_kernel_is_rejected(self):
        self.baseline()
        self.file("boot/vmlinuz-6.18.37", "24.2@6603887")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "base")
        self.assertFalse(result["passed"])

    def test_bianbu_os_identity_is_rejected(self):
        self.baseline()
        self.file("usr/lib/os-release", "ID=bianbu\nVERSION_CODENAME=noble\n")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "base")
        self.assertIn("根系統必須保留 Ubuntu Noble 身分", result["errors"])

    def test_wrong_architecture_is_rejected(self):
        self.baseline()
        self.file("var/lib/dpkg/status", "Package: libc6\nStatus: install ok installed\nArchitecture: arm64\nVersion: 2.39\n")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "base")
        self.assertIn("根系統 libc6 架構必須是 riscv64", result["errors"])

    def test_missing_vpu_kernel_option_is_rejected(self):
        self.baseline()
        self.file("boot/config-6.6.36-legacy-spacemit", "CONFIG_POWERVR_ROGUE=y\nCONFIG_DRM_SPACEMIT=y\n")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "base")
        self.assertTrue(any("CONFIG_VIDEO_LINLON_K1X" in error for error in result["errors"]))

    def test_installed_stage_cannot_pass_without_runtime_and_session(self):
        self.baseline()
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertFalse(result["passed"])
        self.assertTrue(any("套件版本" in error for error in result["errors"]))
        self.assertTrue(any("Wayland" in error for error in result["errors"]))

    def desktop_fixture(self, packages, session="gnome.desktop"):
        self.baseline()
        # 此組測試隔離桌面守門；既有測試另驗證固定 BSP 與相依條件。
        self.lock["profiles"]["bpi-cm6"]["packages"] = []
        status = self.root / "var/lib/dpkg/status"
        with status.open("a") as stream:
            for name in packages:
                stream.write(f"\nPackage: {name}\nStatus: install ok installed\nVersion: 1\n")
                program = self.file(MOD.GNOME_DESKTOP_COMPONENTS[name], "#!/bin/sh\nexit 0\n")
                program.chmod(0o755)
        for name in ("/usr/lib/libspacemit_ep.so.1.2.2", "/usr/lib/libonnxruntime.so.1.18.1",
                     "/usr/lib/libspacemit_mpp.so.0.0.15", "/usr/lib/libpvr_dri_support.so",
                     "/etc/vulkan/icd.d/powervr_icd.json", "/lib/firmware/linlon-v52_v76-80-2/h264dec.fwb",
                     "/lib/firmware/linlon-v52_v76-80-2/hevcdec.fwb"):
            self.file(name, "測試資料\n")
        self.file("/usr/share/wayland-sessions/" + session, "[Desktop Entry]\n")
        self.file("/etc/environment", "MUTTER_DEBUG_DISABLE_HW_CURSORS=1\n")

    def test_rc3_session_without_settings_and_input_method_is_rejected(self):
        self.desktop_fixture(("gnome-session", "gnome-shell", "gdm3", "gnome-terminal", "nautilus"))
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertFalse(result["passed"])
        for name in ("gnome-control-center", "ibus"):
            self.assertIn("GNOME 桌面必要套件未完成安裝：" + name, result["errors"])
        self.assertFalse(any("缺少 Wayland" in error for error in result["errors"]))

    def test_rc4_screensaver_cannot_be_satisfied_by_only_libgjs(self):
        self.desktop_fixture(name for name in MOD.GNOME_DESKTOP_COMPONENTS if name != "gjs")
        with (self.root / "var/lib/dpkg/status").open("a") as stream:
            stream.write("\nPackage: libgjs0g\nStatus: install ok installed\nVersion: 1.80.2-1build2\n")
        self.file("/usr/share/dbus-1/services/org.gnome.ScreenSaver.service",
                  "[D-BUS Service]\nName=org.gnome.ScreenSaver\n"
                  "Exec=/usr/bin/gjs -m /usr/share/gnome-shell/org.gnome.ScreenSaver\n")
        self.file("/usr/share/gnome-shell/org.gnome.ScreenSaver", "// 測試用啟動腳本\n")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertFalse(result["passed"])
        self.assertEqual(result["errors"], ["GNOME 桌面必要套件未完成安裝：gjs",
                                           "GNOME 桌面必要程式缺失或不可執行：/usr/bin/gjs"])
        self.assertFalse(result["hardware_verified"])

    def test_complete_gnome_components_pass_for_both_board_profiles(self):
        self.desktop_fixture(MOD.GNOME_DESKTOP_COMPONENTS)
        for board, armbian, ddk in (("bpi-cm6", "bananapicm6", "23.2@6460340"),
                                    ("bpi-f3", "bananapif3", "24.2@6603887")):
            with self.subTest(board=board):
                self.lock["profiles"][board]["packages"] = []
                self.file("etc/armbian-release", "BOARD=" + armbian + "\n")
                self.file("boot/Image", ddk)
                if board == "bpi-f3":
                    (self.root / "etc/environment").unlink()
                result = MOD.preflight(self.lock, board, self.root, "installed")
                self.assertTrue(result["passed"], result["errors"])
                self.assertFalse(result["hardware_verified"])

    def test_installed_package_does_not_hide_missing_or_nonexecutable_program(self):
        self.desktop_fixture(MOD.GNOME_DESKTOP_COMPONENTS)
        (self.root / "usr/bin/gnome-control-center").unlink()
        (self.root / "usr/bin/gjs").unlink()
        (self.root / "usr/bin/ibus-daemon").chmod(0o644)
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertFalse(result["passed"])
        for name in ("gnome-control-center", "gjs", "ibus-daemon"):
            self.assertIn("GNOME 桌面必要程式缺失或不可執行：/usr/bin/" + name, result["errors"])

    def test_gnome_marker_cannot_be_satisfied_by_unrelated_session(self):
        self.desktop_fixture((), session="other.desktop")
        self.file("etc/bpi-k1-vendor.json", json.dumps({"desktop": "gnome-wayland"}))
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertIn("GNOME 桌面必要套件未完成安裝：gnome-shell", result["errors"])
        self.assertIn("缺少 GNOME Wayland 桌面工作階段", result["errors"])

    def test_invalid_desktop_marker_is_reported_as_preflight_failure(self):
        self.desktop_fixture(MOD.GNOME_DESKTOP_COMPONENTS)
        self.file("etc/bpi-k1-vendor.json", "{")
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertFalse(result["passed"])
        self.assertIn("官方格式根系統標記無法解析：/etc/bpi-k1-vendor.json", result["errors"])

    def test_other_desktop_or_profile_does_not_require_gnome_components(self):
        self.desktop_fixture((), session="other.desktop")
        (self.root / "etc/environment").unlink()
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertTrue(result["passed"], result["errors"])
        self.lock["profiles"]["bpi-cm6"].pop("desktop_protocol")
        self.file("etc/bpi-k1-vendor.json", json.dumps({"desktop": "gnome-wayland"}))
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertTrue(result["passed"], result["errors"])

    def test_cm6_gnome_rejects_missing_disabled_or_conflicting_cursor_setting(self):
        self.desktop_fixture(MOD.GNOME_DESKTOP_COMPONENTS)
        path = self.root / "etc/environment"
        for text in (None, "MUTTER_DEBUG_DISABLE_HW_CURSORS=0\n",
                     "MUTTER_DEBUG_DISABLE_HW_CURSORS=1\nMUTTER_DEBUG_DISABLE_HW_CURSORS=0\n",
                     'MUTTER_DEBUG_DISABLE_HW_CURSORS="1\n'):
            with self.subTest(environment=text):
                if text is None:
                    path.unlink()
                else:
                    path.write_text(text)
                result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
                self.assertFalse(result["passed"])
                self.assertTrue(any("GNOME 板級相容設定缺失或衝突" in error for error in result["errors"]))
                self.assertFalse(result["hardware_verified"])

    def test_cm6_cursor_setting_accepts_quotes_and_comments_without_hardware_claim(self):
        self.desktop_fixture(MOD.GNOME_DESKTOP_COMPONENTS)
        path = self.file("etc/environment", '# MUTTER_DEBUG_DISABLE_HW_CURSORS=0\nMUTTER_DEBUG_DISABLE_HW_CURSORS="1" # 相容設定\n')
        original = path.read_bytes()
        result = MOD.preflight(self.lock, "bpi-cm6", self.root, "installed")
        self.assertTrue(result["passed"], result["errors"])
        self.assertFalse(result["hardware_verified"])
        self.assertEqual(path.read_bytes(), original)

    def test_python_upper_bound_and_alternative_dependencies(self):
        available = {"python3": "3.13.1", "python3-minimal": "3.12.3", "libopencl1": "2.2"}
        missing = MOD.dependency_missing("python3 (<< 3.13), python3:any | python3-minimal:any, ocl-icd-libopencl1 | libopencl1", available)
        self.assertEqual(missing, ["python3 (<< 3.13)"])

    def test_virtual_packages_are_resolved_without_fake_versions(self):
        versions = MOD.package_versions([{"Package": "gir1.2-glib-2.0", "Version": "2.80.0", "Provides": "gir1.2-gio-2.0 (= 2.80.0), gir1.2-gobject-2.0"}])
        self.assertEqual(MOD.dependency_missing("gir1.2-gio-2.0 (>= 2.79), gir1.2-gobject-2.0", versions), [])
        self.assertEqual(MOD.dependency_missing("gir1.2-gobject-2.0 (>= 2.79)", versions), ["gir1.2-gobject-2.0 (>= 2.79)"])

    def test_partial_or_modified_cache_is_never_reused(self):
        cached = self.file("package.deb", "損壞套件")
        with patch.object(MOD.urllib.request, "urlopen") as request:
            with self.assertRaisesRegex(MOD.AuditError, "快取雜湊"):
                MOD.obtain("https://archive.spacemit.com/bianbu/test", cached, "0" * 64)
            request.assert_not_called()

    def test_unsigned_index_rejected_before_packages_are_used(self):
        lock = copy.deepcopy(self.lock)
        key = lock["profiles"]["bpi-cm6"]["packages"][0]
        source_id = lock["packages"][key]["index"]
        unsigned = self.file(f"cache/indices/{source_id}/InRelease", "未簽章資料\n")
        lock["sources"][source_id]["inrelease_sha256"] = MOD.sha256(unsigned.read_bytes())
        shutil.copyfile(MOD.DEFAULT_LOCK.parent / lock["keyring"], self.root / lock["keyring"])
        with self.assertRaisesRegex(MOD.AuditError, "官方索引簽章"):
            MOD.verify_sources(lock, self.root / "lock.json", self.root / "cache", [key])

    def test_path_traversal_in_package_lock_is_rejected(self):
        key = next(iter(self.lock["packages"]))
        self.lock["packages"][key]["Filename"] = "pool/../../outside.deb"
        path = self.file("lock.json", json.dumps(self.lock))
        with self.assertRaisesRegex(MOD.AuditError, "套件路徑"):
            MOD.load_lock(path)

    def test_signed_but_incompatible_gpu_bundle_is_rejected(self):
        packages = self.lock["profiles"]["bpi-cm6"]["packages"]
        packages[packages.index("img-gpu-powervr=23.2-6460340bb2")] = "img-gpu-powervr=24.2-6603887bb8"
        path = self.file("lock.json", json.dumps(self.lock))
        with self.assertRaisesRegex(MOD.AuditError, "核心 DDK 不相容"):
            MOD.load_lock(path)

    def test_symlink_loop_does_not_escape_to_host(self):
        (self.root / "a").symlink_to("/b")
        (self.root / "b").symlink_to("/a")
        with self.assertRaisesRegex(MOD.AuditError, "符號連結形成循環"):
            MOD.rooted_path(self.root, "/a")

    def test_real_lock_has_separate_gpu_versions_and_shared_ai(self):
        f3 = self.lock["profiles"]["bpi-f3"]["packages"]
        cm6 = self.lock["profiles"]["bpi-cm6"]["packages"]
        self.assertIn("img-gpu-powervr=24.2-6603887bb8", f3)
        self.assertNotIn("img-gpu-powervr=24.2-6603887bb8", cm6)
        self.assertIn("img-gpu-powervr=23.2-6460340bb2", cm6)
        self.assertIn("python3-spacemit-ort=1.2.2", set(f3) & set(cm6))


if __name__ == "__main__":
    unittest.main()
