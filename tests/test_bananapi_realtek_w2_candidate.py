#!/usr/bin/env python3
"""Banana Pi W2 固定來源、元件與發布邊界回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapiw2.wip"
FAMILY = (
    ROOT
    / "config/sources/families/include/realtek_bpi_legacy_common.inc"
)
CONFIG = ROOT / "config/validation/bananapi-realtek-rtd1296-w2-legacy.json"
STATUS = ROOT / "config/bananapi-optimization-status.json"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "F-realtek-rtd1296-w2-source-policy-20260827.md"
)
UBOOT_PATCHES = (
    ROOT
    / "patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy"
    / "0001-host-tools-use-local-libfdt-headers.patch",
    ROOT
    / "patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy"
    / "0003-build-use-source-date-epoch.patch",
)
KERNEL_PATCHES = (
    ROOT
    / "patch/kernel/archive/realtek-rtd129x-bpi-4.9"
    / "0001-scripts-dtc-remove-duplicate-yylloc-definition.patch",
    ROOT
    / "patch/kernel/archive/realtek-rtd129x-bpi-4.9"
    / "0002-dts-identify-bananapi-w2.patch",
)
CHECKER = ROOT / "tools/check-bananapi-realtek-w2-source-policy.py"
BUILDER = ROOT / "tools/build-bananapi-realtek-w2-components.sh"
VERIFIER = ROOT / "tools/verify-bananapi-realtek-w2-components.sh"


class BananaPiRealtekW2CandidateTests(unittest.TestCase):
    """防止 W2 固定來源、I/O 契約與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.family_text = FAMILY.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.status = json.loads(STATUS.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapiw2"]

    def test_board_pins_sources_and_storage_contract(self) -> None:
        revision = "6e6aefc35dc50b1b8231cdb03a995d088f29eb21"
        firmware = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
        for expected in (
            'BOARD_MAINTAINER="BPI-SINOVOIP"',
            'KERNEL_TARGET="legacy"',
            'BOOTCONFIG="rtd1296_sd_bananapi_defconfig"',
            'BOOTFS_TYPE="fat"',
            'ROOT_FS_LABEL="BPI-ROOT"',
            f'REALTEK_BPI_BSP_BRANCH="commit:{revision}"',
            f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{firmware}"',
            'declare -g IMAGE_PARTITION_TABLE="msdos"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_vendor_source_is_exact_and_legacy(self) -> None:
        revision = "6e6aefc35dc50b1b8231cdb03a995d088f29eb21"
        self.assertEqual(self.config["candidate_branch"], "legacy")
        self.assertEqual(self.config["linux_commit"], revision)
        self.assertEqual(self.config["uboot_commit"], revision)
        self.assertEqual(self.config["linux_ref"], f"commit:{revision}")
        self.assertEqual(self.config["uboot_ref"], f"commit:{revision}")
        self.assertFalse(self.config["atf_applicable"])

    def test_linked_prebuilt_assets_block_public_release(self) -> None:
        assets = self.config["linked_prebuilt_assets"]
        self.assertEqual(
            set(assets),
            {
                "u-boot-rtk/static_lib/libefuse.a",
                "u-boot-rtk/static_lib/libsha1_util.a",
                "u-boot-rtk/static_lib/libsecurity.a",
                "u-boot-rtk/static_lib/libkeyset.a",
            },
        )
        for path, asset in assets.items():
            with self.subTest(path=path):
                self.assertGreater(asset["size"], 0)
                self.assertEqual(len(asset["sha256"]), 64)
                self.assertFalse(asset["source_build_available"])
                self.assertFalse(asset["redistribution_license_verified"])
        self.assertFalse(self.config["public_release_allowed"])

    def test_bluecore_is_hashed_and_not_declared_rebuildable(self) -> None:
        assets = self.config["runtime_prebuilt_assets"]
        self.assertEqual(len(assets), 1)
        bluecore = assets[
            "rtk-pack/rtk/bpi-w2/configs/default/linux/bluecore.audio"
        ]
        self.assertEqual(bluecore["size"], 3969840)
        self.assertEqual(
            bluecore["sha256"],
            "59252270f05cc55cba0ddeb246bc7c6b20dab9554fa18be4e9595ea549fd9b1c",
        )
        self.assertFalse(bluecore["source_build_available"])
        self.assertFalse(bluecore["redistribution_license_verified"])

    def test_root_device_is_stable_and_not_numbered(self) -> None:
        self.assertIn('REALTEK_BPI_ROOT_LABEL="BPI-ROOT"', self.board_text)
        self.assertIn("REALTEK_BPI_ROOT_LABEL", self.family_text)
        self.assertIn("sed -i -E", self.family_text)
        builder = BUILDER.read_text(encoding="utf-8")
        self.assertIn("root=LABEL=BPI-ROOT", builder)
        self.assertNotIn("root=/dev/mmcblk0p2", builder)
        self.assertEqual(self.policy["root_filesystem_label"], "BPI-ROOT")
        self.assertEqual(self.policy["uboot_write_offset_bytes"], 40960)
        self.assertEqual(self.policy["partition_table"], "msdos")

    def test_uboot_timestamp_uses_fixed_source_epoch(self) -> None:
        patch_text = UBOOT_PATCHES[1].read_text(encoding="utf-8")
        self.assertIn("SOURCE_DATE_EPOCH", patch_text)
        self.assertIn("date -u", patch_text)
        self.assertIn("U_BOOT_TZ", patch_text)

    def test_linux_dtb_identity_is_explicit(self) -> None:
        patch_text = KERNEL_PATCHES[1].read_text(encoding="utf-8")
        self.assertIn('model = "Banana Pi BPI-W2";', patch_text)
        self.assertIn(
            'compatible = "bananapi,bpi-w2", "realtek,rtd1296";',
            patch_text,
        )
        self.assertEqual(
            self.policy["compatible"],
            ["bananapi,bpi-w2", "realtek,rtd1296"],
        )

    def test_interface_contract_covers_required_classes(self) -> None:
        for key in (
            "storage_contract",
            "network_contract",
            "usb_contract",
            "display_contract",
            "io_contract",
            "wireless_contract",
        ):
            self.assertIn(key, self.policy)
        self.assertEqual(
            self.policy["storage_contract"]["pcie_nodes"],
            ["/pcie@9804E000", "/pcie2@9803B000"],
        )
        self.assertEqual(
            len(self.policy["io_contract"]["i2c_nodes"]),
            6,
        )
        self.assertFalse(
            self.policy["io_contract"]["pin_mapping_hardware_validated"]
        )
        self.assertFalse(
            self.policy["usb_contract"]["hardware_role_validated"]
        )
        self.assertFalse(
            self.policy["display_contract"]["acceleration_validated"]
        )
        self.assertEqual(
            self.policy["display_contract"]["hdmi_rx_status"],
            "disabled",
        )

    def test_kernel_contract_includes_diagnostics_and_io(self) -> None:
        options = self.config["common_kernel_options"]
        for option in (
            "CONFIG_GPIOLIB",
            "CONFIG_GPIO_SYSFS",
            "CONFIG_I2C_RTK",
            "CONFIG_SPI_SPIDEV",
            "CONFIG_RTK_THERMAL",
            "CONFIG_RTK_WATCHDOG",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE",
            "CONFIG_R8168",
            "CONFIG_RTC_DRV_RTK",
        ):
            self.assertEqual(options[option], "y")

    def test_documentation_evidence_is_local_and_not_packaged(self) -> None:
        evidence = self.config["documentation_evidence"]
        self.assertEqual(len(evidence), 4)
        for item in evidence:
            with self.subTest(path=item["local_path"]):
                self.assertTrue(item["local_path"].startswith("/media/pi/SMCI/bpi/"))
                self.assertEqual(len(item["sha256"]), 64)
                self.assertFalse(item["included_in_candidate"])
                self.assertFalse(item["redistribution_license_verified"])

    def test_component_state_does_not_imply_hardware_or_rootfs(self) -> None:
        self.assertEqual(self.config["candidate_level"], "L1 元件候選")
        self.assertEqual(self.config["candidate_scope"], "internal-component-only")
        self.assertFalse(self.config["full_image_built"])
        self.assertFalse(self.config["hardware_validated"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        if self.config["component_build_completed"]:
            evidence = self.config["component_build_evidence"]
            self.assertEqual(
                evidence["local_evidence_root"],
                "output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy",
            )
            self.assertFalse(evidence["full_rootfs_image_built"])
            self.assertTrue(evidence["uboot_rebuild_hash_match"])
            self.assertLessEqual(evidence["work_size_kib"], 10 * 1024 * 1024)
            self.assertGreater(len(evidence["artifacts"]), 0)
            self.assertEqual(
                evidence["uboot_rebuild_sha256"],
                evidence["artifacts"]["u-boot.bin"]["sha256"],
            )
            self.assertEqual(
                self.policy["dtb_sha256"],
                evidence["artifacts"][self.policy["dtb"]]["sha256"],
            )

    def test_global_registry_records_only_component_level(self) -> None:
        evidence = self.status["evidence"]["bananapiw2"]
        self.assertEqual(evidence["level"], "L1")
        self.assertIn("未建立 rootfs 或整碟映像", evidence["basis"])
        self.assertGreaterEqual(
            len(self.status["open_findings"]["bananapiw2"]),
            3,
        )

    def test_tools_are_w2_scoped_and_avoid_lower_cache_writes(self) -> None:
        builder = BUILDER.read_text(encoding="utf-8")
        verifier = VERIFIER.read_text(encoding="utf-8")
        self.assertIn("bananapi-realtek-w2-component", builder)
        self.assertIn("output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy", builder)
        self.assertIn("output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy", verifier)
        self.assertIn("git clone --shared --no-checkout", builder)
        self.assertIn('map_asset="${asset#u-boot-rtk/}"', builder)
        self.assertIn("W2_COMPONENT_OUTPUT_DIR", verifier)
        self.assertNotIn("/cache/sources/armbian-firmware-git", builder)
        self.assertNotIn("rm -rf", builder)
        self.assertNotIn("git reset --hard", builder)
        self.assertIn("不得包含原始碼或建置樹", verifier)

    def test_policy_document_and_patches_exist(self) -> None:
        self.assertTrue(POLICY.is_file())
        for path in (*UBOOT_PATCHES, *KERNEL_PATCHES, CHECKER, BUILDER, VERIFIER):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_policy_checker_passes(self) -> None:
        result = subprocess.run(
            ["python3", str(CHECKER), str(CONFIG)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
