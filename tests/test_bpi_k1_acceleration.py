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
