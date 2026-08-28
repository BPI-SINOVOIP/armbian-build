#!/usr/bin/env python3
"""Banana Pi W2 固定來源、元件與發布邊界回歸測試。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
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
IMAGE_BUILDER = ROOT / "tools/build-bananapi-realtek-w2-candidate.sh"
IMAGE_RUNNER = ROOT / "tools/run-bananapi-realtek-w2-candidate-isolated-cache.sh"
IMAGE_VERIFIER = ROOT / "tools/verify-bananapi-realtek-w2-candidate.sh"
COMMON_IMAGE_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"


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
            'BOOT_FS_LABEL="BPI-BOOT"',
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
        self.assertEqual(self.config["current_evidence_level"], "L2")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertTrue(self.config["verify_firmware_source_resolution"])

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

    def test_transitional_l2_state_does_not_imply_hardware_or_rootfs(self) -> None:
        self.assertEqual(self.config["candidate_level"], "L2 內部軟體候選")
        self.assertEqual(self.config["candidate_scope"], "internal-l2")
        self.assertFalse(self.config["full_image_built"])
        self.assertFalse(self.config["rootfs_image_built"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        self.assertNotIn("image_build_evidence", self.config)
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
                evidence["artifacts"][Path(self.policy["dtb"]).name]["sha256"],
            )

    def test_calibrated_final_kernel_config_is_fixed(self) -> None:
        self.assertEqual(
            self.policy["final_kernel_config_sha256"],
            "0bcd9fdd4e4dcbb1dbe5bd2702ad08171e425c8abf1f9e30e05f6fe4301ec6a3",
        )
        self.assertIsNone(self.policy["image_dtb_sha256"])
        self.assertEqual(
            self.policy["dtb_sha256_evidence_scope"],
            "component-only-l1",
        )

    def test_global_registry_stays_l1_until_formal_rebuild(self) -> None:
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

    def test_full_image_tools_are_w2_scoped_and_internal(self) -> None:
        builder = IMAGE_BUILDER.read_text(encoding="utf-8")
        runner = IMAGE_RUNNER.read_text(encoding="utf-8")
        verifier = IMAGE_VERIFIER.read_text(encoding="utf-8")
        common = COMMON_IMAGE_VERIFIER.read_text(encoding="utf-8")
        self.assertIn("bananapi-realtek-rtd1296-w2-trixie-legacy-cli", builder)
        self.assertIn("bananapi-realtek-w2-candidate-cache-overlay", builder)
        self.assertIn("ALLOW_INTERNAL_W2_CANDIDATE", builder)
        self.assertIn("PUBLIC_RELEASE=no", builder)
        self.assertIn("HARDWARE_CLAIMS=no", builder)
        self.assertIn("CACHE_LOWER", runner)
        self.assertIn("REQUIRE_BUILD_VERIFIER_IDENTITY=yes", verifier)
        self.assertIn("required_uenv_fragments", common)
        self.assertNotIn("rm -rf", builder + runner + verifier)

    def test_full_image_contract_is_strict_and_non_public(self) -> None:
        self.assertEqual(self.policy["partition_start_sector"], 8192)
        self.assertEqual(self.policy["root_partition_start_sector"], 532480)
        self.assertEqual(self.policy["boot_partition_label"], "BPI-BOOT")
        self.assertEqual(self.policy["root_partition_label"], "BPI-ROOT")
        self.assertEqual(self.policy["boot_configuration"], "realtek_bpi_uenv")
        self.assertEqual(self.policy["uboot_offset"], 40960)
        self.assertEqual(
            self.policy["vendor_boot_dtbs"],
            ["rtd-1296-bananapi-w2-2GB.dtb"],
        )
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(
            self.config["license_policy"]["opaque_payload_redistribution_verified"]
        )
        self.assertFalse(
            self.config["license_policy"]["toolchain_redistribution_verified"]
        )

    def test_policy_document_and_patches_exist(self) -> None:
        self.assertTrue(POLICY.is_file())
        for path in (
            *UBOOT_PATCHES,
            *KERNEL_PATCHES,
            CHECKER,
            BUILDER,
            VERIFIER,
            IMAGE_BUILDER,
            IMAGE_RUNNER,
            IMAGE_VERIFIER,
        ):
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

    def test_source_contract_projection_is_stable(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--print-source-contract-projection-sha256",
                str(CONFIG),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "13dcf92c40e1d19161da68adf834f45bbe56926e35782de20585bd2bbbf5335d",
        )

    def test_transitional_contract_rejects_historical_verification(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--verify-historical-image",
                str(CONFIG),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("過渡契約不能執行歷史映像重驗", result.stderr)

    def test_transitional_contract_rejects_premature_image_state(self) -> None:
        cases = {
            "rootfs 旗標提前成立": lambda data: data.__setitem__(
                "rootfs_image_built", True
            ),
            "完整 rootfs 旗標提前成立": lambda data: data.__setitem__(
                "full_rootfs_image_built", True
            ),
            "提前夾帶映像證據": lambda data: data.__setitem__(
                "image_build_evidence", {}
            ),
            "映像 DTB 提前成立": lambda data: data["boards"][
                "bananapiw2"
            ].__setitem__("image_dtb_sha256", self.policy["dtb_sha256"]),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                candidate = json.loads(CONFIG.read_text(encoding="utf-8"))
                mutate(candidate)
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", encoding="utf-8"
                ) as stream:
                    json.dump(candidate, stream, ensure_ascii=False)
                    stream.flush()
                    result = subprocess.run(
                        ["python3", str(CHECKER), stream.name],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                self.assertNotEqual(result.returncode, 0)

    def test_public_release_environment_is_rejected(self) -> None:
        environment = os.environ.copy()
        environment["PUBLIC_RELEASE"] = "yes"
        result = subprocess.run(
            ["python3", str(CHECKER), str(CONFIG)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止公開發布", result.stderr)

    def test_checker_contains_full_l2_material_binding(self) -> None:
        text = CHECKER.read_text(encoding="utf-8")
        for expected in (
            "validate_image_evidence",
            "validate_historical_image",
            "candidate_matrix_sha256",
            "verification_manifest_sha256",
            "uboot_payload_manifest_sha256",
            "final_config_manifest_sha256",
            "--verify-historical-image",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
