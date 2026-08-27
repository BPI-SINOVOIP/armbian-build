#!/usr/bin/env python3
"""Banana Pi F2S 固定來源、啟動資產與候選邊界回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapif2s.wip"
FAMILY = (
    ROOT
    / "config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc"
)
CONFIG = ROOT / "config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "F-sunplus-f2s-source-policy-20260827.md"
)
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/sunplus-sp7021-bpi-5.4"
    / "0001-dts-identify-bananapi-f2s.patch"
)
UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy"
    / "0002-dts-identify-bananapi-f2s.patch"
)
UBOOT_REPRODUCIBLE_PATCH = (
    ROOT
    / "patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy"
    / "0003-tools-quickboot-honor-source-date-epoch.patch"
)
CHECKER = ROOT / "tools/check-bananapi-sunplus-f2s-source-policy.py"
COMPONENT_BUILDER = ROOT / "tools/build-bananapi-sunplus-f2s-components.sh"
COMPONENT_VERIFIER = ROOT / "tools/verify-bananapi-sunplus-f2s-components.sh"
CANDIDATE_BUILDER = ROOT / "tools/build-bananapi-sunplus-f2s-candidate.sh"
CANDIDATE_RUNNER = (
    ROOT / "tools/run-bananapi-sunplus-f2s-candidate-isolated-cache.sh"
)
CANDIDATE_VERIFIER = ROOT / "tools/verify-bananapi-sunplus-f2s-candidate.sh"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"


class BananaPiSunplusF2SCandidateTests(unittest.TestCase):
    """防止 F2S 可重現性、授權與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board = BOARD.read_text(encoding="utf-8")
        cls.family = FAMILY.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapif2s"]

    def test_board_pins_vendor_bsp_and_storage_contract(self) -> None:
        revision = "3eee97bd8fb7582c2d9942a533647c3d78222bb5"
        for expected in (
            'BOARD_MAINTAINER="BPI-SINOVOIP"',
            'KERNEL_TARGET="legacy"',
            'KERNEL_TEST_TARGET="legacy"',
            'BOOTCONFIG="sp7021_bpi_f2s_defconfig"',
            'BOOT_FDT_FILE="sp7021-bpi-f2s.dtb"',
            f'SUNPLUS_BPI_BSP_BRANCH="commit:{revision}"',
            'declare -g IMAGE_PARTITION_TABLE="msdos"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board)

    def test_boot_assets_have_exact_hashes_and_no_release_grant(self) -> None:
        assets = self.config["source_assets"]
        self.assertEqual(len(assets), 2)
        self.assertEqual(
            assets["sp-pack/sp7021/common/bin/ISPBOOOT.BIN"]["size"],
            65536,
        )
        self.assertEqual(
            assets[
                "sp-pack/sp7021/common/bin/"
                "BPI-F2S-xboot-emmc-boot0-0k.img.gz"
            ]["uncompressed_size"],
            2097152,
        )
        for path, asset in assets.items():
            with self.subTest(path=path):
                self.assertEqual(len(asset["sha256"]), 64)
                self.assertFalse(asset["source_build_available"])
                self.assertFalse(asset["redistribution_license_verified"])
                self.assertIn(asset["sha256"], self.board)

    def test_release_and_hardware_claims_remain_blocked(self) -> None:
        self.assertTrue(self.config["full_image_built"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["hardware_validated"])
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertFalse(self.config["atf_applicable"])

    def test_complete_image_has_read_only_l2_evidence(self) -> None:
        evidence = self.config["image_build_evidence"]
        self.assertEqual(evidence["evidence_level"], "L2")
        self.assertTrue(evidence["read_only_content_verified"])
        self.assertEqual(
            evidence["source_commit"],
            "132646e1eb53644bdc4112cd7af4d9cc54502aca",
        )
        self.assertEqual(
            evidence["verifier_commit"], evidence["source_commit"]
        )
        self.assertEqual(
            evidence["image"]["sha256"],
            "08aa83f5e0f002d607214e42b1c67a0a4dc64a341f9567f047b1d1102af60dd3",
        )
        self.assertEqual(evidence["image"]["size"], 1832910848)
        self.assertEqual(
            evidence["archive"]["sha256"],
            "76c4f116512f5ffc6b39a7a90a21def1c0849d6b0b9d4fbcecc02ca391d3a736",
        )
        self.assertEqual(evidence["archive"]["size"], 357232960)

    def test_component_evidence_is_complete_but_not_a_rootfs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["source_revision"],
            "3eee97bd8fb7582c2d9942a533647c3d78222bb5",
        )
        self.assertFalse(evidence["full_rootfs_image_built"])
        self.assertTrue(evidence["uboot_rebuild_hash_match"])
        self.assertEqual(
            evidence["toolchain"]["gcc_sha256"],
            "ae824ab0542db07ea468297474f3310cdee2abf8d316220b9e3081bada1f7da3",
        )
        self.assertFalse(evidence["toolchain"]["included_in_runtime_image"])
        self.assertFalse(
            evidence["toolchain"]["redistribution_license_verified"]
        )
        self.assertEqual(
            set(evidence["artifacts"]),
            {
                "u-boot.img",
                "u-boot.bin",
                "u-boot.dtb",
                "uImage",
                "zImage",
                "sp7021-bpi-f2s.dtb",
                "linux.config",
                "linux-modules.tar.xz",
            },
        )
        for artifact in evidence["artifacts"].values():
            self.assertGreater(artifact["size"], 0)
            self.assertEqual(len(artifact["sha256"]), 64)

    def test_linux_and_uboot_license_evidence_is_separate(self) -> None:
        self.assertEqual(self.config["linux_license_path"], "linux-sp/COPYING")
        self.assertEqual(
            self.config["uboot_license_path"], "u-boot-sp/Licenses/README"
        )
        self.assertEqual(
            self.config["uboot_license_sha256"],
            "7e354ab349b7c11f1fe93639c3096bfe2bb4591659caaa712e2ee101299cf1d4",
        )
        self.assertNotEqual(
            self.config["linux_license_sha256"],
            self.config["uboot_license_sha256"],
        )

    def test_local_document_evidence_is_hashed_but_not_packaged(self) -> None:
        evidence = self.config["documentation_evidence"]
        self.assertEqual(len(evidence), 4)
        for item in evidence:
            with self.subTest(path=item["local_path"]):
                self.assertTrue(
                    item["local_path"].startswith("/media/pi/SMCI/bpi/doc/")
                )
                self.assertEqual(len(item["sha256"]), 64)
                self.assertFalse(item["included_in_candidate"])
                self.assertFalse(item["redistribution_license_verified"])

    def test_dts_patches_add_dedicated_identity(self) -> None:
        for path in (KERNEL_PATCH, UBOOT_PATCH):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn('model = "Banana Pi BPI-F2S";', text)
                self.assertIn(
                    'compatible = "sinovoip,bpi-f2s", '
                    '"sunplus,sp7021-achip";',
                    text,
                )
                self.assertIn('-\tmodel = "SP7021/CA7/BPI-F2S";', text)

    def test_quickboot_image_uses_fixed_source_timestamp(self) -> None:
        patch = UBOOT_REPRODUCIBLE_PATCH.read_text(encoding="utf-8")
        builder = COMPONENT_BUILDER.read_text(encoding="utf-8")
        self.assertIn("imagetool_get_source_date", patch)
        self.assertIn("uboot_first_sha256", builder)
        self.assertIn('"uboot_rebuild_hash_match": true', builder)

    def test_generated_uenv_uses_filesystem_uuid(self) -> None:
        self.assertIn("ROOT_PART_UUID", self.family)
        self.assertIn("root=UUID=${ROOT_PART_UUID}", self.family)
        self.assertNotIn("setenv root /dev/mmcblk1p2", self.family)
        self.assertIn("sunplus_sp7021_bpi_verify_prebuilt_boot_asset", self.family)

    def test_custom_uboot_packages_target_configuration_evidence(self) -> None:
        for required in (
            "u-boot-config-target-1",
            "u-boot-metadata-target-1.sh",
            "declare UBOOT_TARGET_MAP=",
            '"${uboottempdir}/usr/lib/u-boot/${BOOTCONFIG}"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.family)

    def test_kernel_diagnostic_options_and_packages_are_controlled(self) -> None:
        packages = set(
            next(
                line for line in self.board.splitlines()
                if line.startswith('PACKAGE_LIST_BOARD="')
            ).split('"', 2)[1].split()
        )
        self.assertTrue(set(self.config["common_packages"]) <= packages)
        for option in (
            "CONFIG_USB_CONFIGFS_MASS_STORAGE",
            "CONFIG_SUNPLUS_SP7021_THERMAL",
            "CONFIG_SUNPLUS_WATCHDOG",
        ):
            self.assertEqual(self.config["common_kernel_options"][option], "y")

    def test_boot_layout_is_machine_readable(self) -> None:
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(self.policy["uboot_payloads"], ["u-boot.img@17408"])
        self.assertEqual(self.policy["boot_partition_number"], 1)
        self.assertEqual(self.policy["root_partition_number"], 2)
        self.assertEqual(self.policy["boot_configuration"], "sunplus_uenv")
        self.assertEqual(
            self.policy["vendor_boot_directory"], "bananapi/bpi-f2s/linux"
        )
        self.assertEqual(self.policy["sd_node"], "/soc@B/mmc@sdcard")
        self.assertEqual(
            self.policy["dtb_sha256"],
            self.config["component_build_evidence"]["artifacts"]
            ["sp7021-bpi-f2s.dtb"]["sha256"],
        )

    def test_generic_verifier_handles_separate_fat_boot_read_only(self) -> None:
        text = GENERIC_VERIFIER.read_text(encoding="utf-8")
        for required in (
            "boot_partition_number",
            "root_partition_start_sector",
            "sunplus_uenv",
            'sudo mount -o ro,nosuid,nodev,noexec "${boot_partition}"',
            "root=/dev/mmcblk",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_legacy_uboot_version_fallback_stays_strict(self) -> None:
        text = GENERIC_VERIFIER.read_text(encoding="utf-8")
        for required in (
            'declare UBOOT_VERSION="0"',
            'declare UBOOT_ARTIFACT_VERSION=',
            'grep -aFq -- "U-Boot ${uboot_version}"',
            "uboot_version_fallback=yes",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_component_builder_never_builds_a_rootfs_image(self) -> None:
        text = COMPONENT_BUILDER.read_text(encoding="utf-8")
        self.assertNotIn("compile.sh build", text)
        self.assertNotIn("debootstrap", text)
        self.assertIn("uImage dtbs modules", text)
        self.assertIn('"full_rootfs_image_built": false', text)
        self.assertIn("git -C \"${source_dir}\" apply --check", text)
        self.assertIn("gzip -cd", text)
        self.assertIn("linux_license_sha256", text)
        self.assertIn("uboot_license_sha256", text)

    def test_checkers_and_entrypoints_are_valid(self) -> None:
        result = subprocess.run(
            [str(CHECKER), str(CONFIG)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (
            CHECKER,
            COMPONENT_BUILDER,
            COMPONENT_VERIFIER,
            CANDIDATE_BUILDER,
            CANDIDATE_RUNNER,
            CANDIDATE_VERIFIER,
        ):
            with self.subTest(path=path.name):
                self.assertTrue(path.stat().st_mode & 0o111)
                if path.suffix == ".sh":
                    subprocess.run(["bash", "-n", str(path)], check=True)

    def test_policy_documents_non_promotional_limits(self) -> None:
        text = POLICY.read_text(encoding="utf-8")
        for required in (
            "沒有可對應的原始碼或明確再散布授權",
            "不得把 L2 軟體證據描述成可公開發布",
            "本候選沒有驗證 eMMC `boot0` 寫入流程",
            "不得宣稱具備現代 DRM",
            "完整 Trixie minimal CLI 映像",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
