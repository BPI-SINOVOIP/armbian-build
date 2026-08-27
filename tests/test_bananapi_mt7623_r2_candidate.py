#!/usr/bin/env python3
"""BPI-R2 MT7623 候選來源與守門回歸測試。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/validation/bananapi-mt7623-r2-current.json"
BOARD_PATH = ROOT / "config/boards/bananapir2.csc"
BOOT_SCRIPT = ROOT / "config/bootscripts/boot-mt7623.cmd"
UBOOT_PATCH = ROOT / "patch/u-boot/v2024.07/board_bananapir2/enable-boot-from-ext4.patch"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
SOURCE_POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "D-mt7623-r2-source-policy-20260827.md"
)


class BananaPiMT7623R2CandidateTests(unittest.TestCase):
    """驗證 R2 的固定來源、啟動載荷與安全網路預設。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text())
        cls.policy = cls.config["boards"]["bananapir2"]

    def test_sources_and_media_contract_are_fixed(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "current")
        self.assertEqual(self.config["kernel_family"], "mt7623")
        self.assertEqual(
            self.config["linux_commit"],
            "dc6160265ffc795a1832bc1424f58291d152c7bb",
        )
        self.assertEqual(
            self.policy["uboot_revision"],
            "3f772959501c99fbe5aa0b22a36efe3478d1ae1c",
        )
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(self.policy["partition_start_sector"], 8192)
        self.assertEqual(
            self.policy["dtb_sha256"],
            "55151de1694bb279e759498eb5f86253e0e90700408044c546b4310a2a81c796",
        )
        self.assertEqual(
            self.policy["uboot_payloads"],
            [
                "BPI-R2-HEAD440-0k.img@0",
                "BPI-R2-HEAD1-512b.img@512",
                "BPI-R2-preloader-2k.img@2048",
                "u-boot.bin@327680",
            ],
        )

    def test_static_boot_payload_hashes_match_repository(self) -> None:
        expected = dict(
            item.split("=", 1) for item in self.policy["uboot_payload_sha256"]
        )
        for name, digest in expected.items():
            with self.subTest(name=name):
                payload = ROOT / "packages/blobs/mt7623n" / name
                self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), digest)

    def test_boot_blob_provenance_blocks_external_release(self) -> None:
        self.assertFalse(self.config["boot_blob_redistribution_authorized"])
        self.assertEqual(
            self.config["boot_blob_source_repository"],
            "https://github.com/BPI-SINOVOIP/BPI-files.git",
        )
        expected = dict(
            item.split("=", 1) for item in self.policy["uboot_payload_sha256"]
        )
        sources = self.config["boot_blob_sources"]
        self.assertEqual(set(sources), set(expected))
        for name, metadata in sources.items():
            with self.subTest(name=name):
                self.assertTrue(metadata["source_path"].endswith(".img.gz"))
                self.assertRegex(metadata["fixed_commit"], r"^[0-9a-f]{40}$")
                self.assertEqual(metadata["decompressed_sha256"], expected[name])
        policy = SOURCE_POLICY.read_text()
        self.assertIn("不得把包含這些載荷的映像標示為可對外發布版本", policy)
        self.assertIn("boot_blob_redistribution_authorized", policy)

    def test_boot_script_uses_partition_uuid_and_correct_dtb(self) -> None:
        text = BOOT_SCRIPT.read_text()
        self.assertIn("part uuid ${devtype} ${devnum}:${mmcpart} rootuuid", text)
        self.assertIn('setenv rootdev "PARTUUID=${rootuuid}"', text)
        self.assertIn(
            'setenv fdtfile "mt7623n-bananapi-bpi-r2.dtb"', text
        )
        self.assertNotIn("mediatek/mt7623n-bananapi-bpi-r2.dtb", text)
        self.assertNotIn("/dev/mmcblk", text)

    def test_uboot_patch_has_deterministic_environment(self) -> None:
        text = UBOOT_PATCH.read_text()
        for required in (
            "+CONFIG_ENV_IS_NOWHERE=y",
            "+# CONFIG_ENV_IS_IN_MMC is not set",
            "+CONFIG_CMD_BOOTZ=y",
            "+CONFIG_CMD_EXT4=y",
            "mmcinitrdfile=boot/uInitrd",
            "boot/dtb/mt7623n-bananapi-bpi-r2.dtb",
        ):
            self.assertIn(required, text)
        self.assertNotIn("boot/dtb/mediatek/mt7623n-bananapi-bpi-r2.dtb", text)
        self.assertNotIn("#define CONFIG_BOOTCOMMAND", text)
        self.assertNotIn("index 111111111111..222222222222", text)
        self.assertIn(
            "index 4c3d90a1b7b05d128b572c608c666f0405d226fb"
            "..455d085c569ab500d10494fb1a59b15b02ce36d7",
            text,
        )
        self.assertIn(
            "index fca234a1dc71a85f4982a49db4f1ab53e30b9ed7"
            "..8a1b013d211678373861dc6b2599dae8a3bdbf35",
            text,
        )
        for header in ("@@ -32,7 +34,8 @@", "@@ -20,9 +20,27 @@", "@@ -35,8 +53,22 @@"):
            self.assertIn(header, text)

    def test_board_enables_otg_gpio_and_fixed_firmware(self) -> None:
        text = BOARD_PATH.read_text()
        for symbol in (
            "GPIO_CDEV",
            "USB_GADGET",
            "USB_MUSB_HDRC",
            "USB_MUSB_DUAL_ROLE",
            "USB_MUSB_MEDIATEK",
            "USB_ROLE_SWITCH",
        ):
            self.assertIn(symbol, text)
        self.assertIn(self.config["firmware_commit"], text)
        self.assertIn('BOOT_FDT_FILE="mt7623n-bananapi-bpi-r2.dtb"', text)
        self.assertEqual(self.policy["dtb"], "mt7623n-bananapi-bpi-r2.dtb")
        package_line = next(
            line for line in text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_network_default_separates_wan_from_lan_bridge(self) -> None:
        wan = (ROOT / "packages/bsp/mt7623/10-wan.network").read_text()
        conduit = (ROOT / "packages/bsp/mt7623/10-eth0.network").read_text()
        self.assertIn("Name=wan\n", wan)
        self.assertIn("DHCP=yes", wan)
        self.assertNotIn("Bridge=br0", wan)
        self.assertIn("LinkLocalAddressing=no", conduit)
        self.assertNotIn("DHCP=yes", conduit)

    def test_generic_verifier_checks_exact_payload_and_installed_file_hashes(self) -> None:
        text = GENERIC_VERIFIER.read_text()
        self.assertIn("uboot_payload_sha256", text)
        self.assertIn("installed_file_sha256", text)
        self.assertIn(
            'sudo sha256sum "${mount_dir}${installed_path}"',
            text,
        )
        self.assertIn("payload SHA-256 不符", text)

    def test_shell_entrypoints_are_valid(self) -> None:
        for name in (
            "build-bananapi-mt7623-r2-candidate.sh",
            "verify-bananapi-mt7623-r2-candidate.sh",
            "run-bananapi-mt7623-r2-candidate-isolated-cache.sh",
        ):
            self.assertTrue((ROOT / "tools" / name).stat().st_mode & 0o111)
            subprocess.run(
                ["bash", "-n", str(ROOT / "tools" / name)],
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
