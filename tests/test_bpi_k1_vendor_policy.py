"""以實際 dpkg diversion、小 DEB 與 uInitrd 驗證官方格式媒體政策。"""

import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bpi_k1_vendor_policy", ROOT / "tools/bpi_k1_vendor_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def policy_root(root):
    entries = {
        "var/lib/dpkg/status": "",
        "usr/lib/armbian-config/config.system.sh": 'module_partitioner() {\n true\n}\nordinary_setting() { return 0; }\n',
        "usr/lib/armbian-config/config.functions.sh": 'install_apply_partitions() {\n true\n}\ninstall_write_bootloader() {\n true\n}\n',
        "usr/lib/u-boot/platform_install.sh": 'write_uboot_platform() {\n true\n}\n',
        "etc/initramfs/post-update.d/99-uboot": (ROOT / "packages/bsp/common/etc/initramfs/post-update.d/99-uboot").read_text(),
        "etc/fstab": "# 測試媒體根系統\n",
    }
    for relative, text in entries.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class VendorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        policy_root(self.root)

    def marker(self):
        return {"source_kind": "armbian-native-rootfs", "storage": "sd"}

    @unittest.skipUnless(shutil.which("dpkg-divert"), "需要 dpkg-divert")
    def test_real_diversion_blocks_media_functions_but_preserves_general_settings(self):
        original = (self.root / "usr/lib/armbian-config/config.system.sh").read_bytes()
        record = policy.apply(self.root, self.marker(), self.root / "boot")
        current = self.root / "usr/lib/armbian-config/config.system.sh"
        self.assertEqual(current.with_name(current.name + ".standard-format").read_bytes(), original)
        result = subprocess.run(["bash", "-c", 'source "$1"; module_partitioner', "fixture", str(current)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        subprocess.run(["bash", "-c", 'source "$1"; ordinary_setting', "fixture", str(current)], check=True)
        self.assertIn("usr/lib/armbian-config/config.system.sh", record["protected_config"])
        self.assertEqual((self.root / "etc/systemd/system/armbian-resize-filesystem.service").readlink(), Path("/dev/null"))
        listing = subprocess.check_output(["dpkg-divert", "--root=" + str(self.root), "--list"], text=True)
        self.assertIn("/usr/lib/u-boot/platform_install.sh.standard-format", listing)
        self.assertFalse((self.root / "etc/bpi-k1-vendor.json").exists())

    def test_unknown_layout_fails_before_any_diversion(self):
        (self.root / "usr/lib/armbian-config/config.functions.sh").write_text("new_installer() { true; }\n")
        with self.assertRaisesRegex(ValueError, "結構未知"):
            policy.apply(self.root, self.marker(), self.root / "boot")
        self.assertFalse((self.root / "var/lib/dpkg/diversions").exists())

    @unittest.skipUnless(shutil.which("dpkg-deb"), "需要 dpkg-deb")
    def test_real_debs_allow_normal_updates_and_block_boot_packages(self):
        for name, blocked in (("ordinary-tool", False), ("linux-image-test", True), ("linux-dtb-test", True), ("linux-u-boot-test", True), ("armbian-bsp-test", True), ("armbian-config", True)):
            with self.subTest(package=name):
                package = self.root / name
                (package / "DEBIAN").mkdir(parents=True)
                (package / "DEBIAN/control").write_text(f"Package: {name}\nVersion: 1\nArchitecture: all\nMaintainer: fixture <fixture@example.invalid>\nDescription: 測試套件\n")
                deb = self.root / (name + ".deb")
                subprocess.run(["dpkg-deb", "--build", str(package), str(deb)], check=True, capture_output=True)
                if blocked:
                    with self.assertRaisesRegex(ValueError, "整套驗證"):
                        policy.apt_guard([str(deb)])
                else:
                    policy.apt_guard([str(deb)])

    def boot(self):
        boot = self.root / "boot"
        boot.mkdir()
        kernel = "6.6.36-legacy-spacemit"
        for name, data in (("vmlinuz-" + kernel, b"kernel"), ("Image", b"kernel"), ("uInitrd", b"original-initrd"), ("board.dtb", b"fixed-dtb")):
            (boot / name).write_bytes(data)
        marker = {**self.marker(), "boot_uuid": "fixture", "native_boot": policy.boot_record(boot, kernel, "/board.dtb")}
        (self.root / "etc/bpi-k1-vendor.json").write_text(json.dumps(marker))
        source = boot / ("initrd.img-" + kernel)
        source.write_bytes(gzip.compress("測試用 ramdisk".encode(), mtime=0))
        return boot, kernel, source

    @unittest.skipUnless(shutil.which("mkimage"), "需要 mkimage")
    def test_same_kernel_initramfs_rebuild_succeeds_and_changed_dtb_preserves_boot(self):
        boot, version, source = self.boot()
        with patch.object(policy, "check_boot_mount"):
            policy.update_initramfs(self.root, version, source)
        policy.verify_uinitrd(boot / "uInitrd")
        before = (boot / "uInitrd").read_bytes()
        (boot / "board.dtb").write_bytes(b"changed")
        source.write_bytes(gzip.compress(b"new-initrd", mtime=123))
        with patch.object(policy, "check_boot_mount"), self.assertRaisesRegex(ValueError, "核心或 DTB"):
            policy.update_initramfs(self.root, version, source)
        self.assertEqual((boot / "uInitrd").read_bytes(), before)
        self.assertEqual(list(boot.glob(".bpi-k1-initrd-*")), [])

    def test_unknown_kernel_version_and_mount_are_rejected_before_boot_writes(self):
        boot, version, source = self.boot()
        with self.assertRaisesRegex(ValueError, "核心版本"):
            policy.update_initramfs(self.root, "other", source)
        with patch.object(policy.subprocess, "check_output", return_value='{"filesystems":[{"target":"/","fstype":"ext4","uuid":"fixture"}]}'):
            with self.assertRaisesRegex(ValueError, "獨立 bootfs"):
                policy.update_initramfs(self.root, version, source)
        self.assertEqual((boot / "uInitrd").read_bytes(), b"original-initrd")


if __name__ == "__main__":
    unittest.main()
