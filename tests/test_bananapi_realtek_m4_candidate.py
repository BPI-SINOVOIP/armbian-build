from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapim4.wip"
FAMILY = ROOT / "config/sources/families/include/realtek_bpi_legacy_common.inc"
CONFIG = ROOT / "config/validation/bananapi-realtek-rtd1395-m4-legacy.json"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "F-realtek-rtd1395-m4-source-policy-20260827.md"
)
UBOOT_PATCHES = (
    ROOT / "patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0001-host-tools-use-local-libfdt-headers.patch",
    ROOT / "patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0002-uenv-use-stable-root-label.patch",
    ROOT / "patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0003-build-use-source-date-epoch.patch",
)
KERNEL_PATCHES = (
    ROOT / "patch/kernel/archive/realtek-rtd139x-bpi-4.9/0001-scripts-dtc-remove-duplicate-yylloc-definition.patch",
    ROOT / "patch/kernel/archive/realtek-rtd139x-bpi-4.9/0002-dts-identify-bananapi-m4.patch",
)
CHECKER = ROOT / "tools/check-bananapi-realtek-m4-source-policy.py"
BUILDER = ROOT / "tools/build-bananapi-realtek-m4-components.sh"
VERIFIER = ROOT / "tools/verify-bananapi-realtek-m4-components.sh"
IMAGE_BUILDER = ROOT / "tools/build-bananapi-realtek-m4-candidate.sh"
IMAGE_RUNNER = ROOT / "tools/run-bananapi-realtek-m4-candidate-isolated-cache.sh"
IMAGE_VERIFIER = ROOT / "tools/verify-bananapi-realtek-m4-candidate.sh"
COMMON_IMAGE_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"


class BananaPiRealtekM4CandidateTests(unittest.TestCase):
    """防止 M4 固定來源、介面契約與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.family_text = FAMILY.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapim4"]

    def test_board_pins_sources_and_storage_contract(self) -> None:
        revision = "25f5b88ec4ba34029f964693dc34028b26e6c67c"
        firmware = "f50a2a21bcdb77a562b3976930c5c6b521a1df08"
        for expected in (
            'BOARD_MAINTAINER="BPI-SINOVOIP"',
            'KERNEL_TARGET="legacy"',
            'BOOTCONFIG="rtd1395_bananapi_defconfig"',
            'BOOTFS_TYPE="fat"',
            'BOOT_FS_LABEL="BPI-BOOT"',
            'ROOT_FS_LABEL="BPI-ROOT"',
            'REALTEK_BPI_ROOT_LABEL="BPI-ROOT"',
            f'REALTEK_BPI_BSP_BRANCH="commit:{revision}"',
            f'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:{firmware}"',
            'declare -g IMAGE_PARTITION_TABLE="msdos"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_vendor_source_is_exact_and_legacy(self) -> None:
        revision = "25f5b88ec4ba34029f964693dc34028b26e6c67c"
        self.assertEqual(self.config["candidate_branch"], "legacy")
        self.assertEqual(self.config["linux_commit"], revision)
        self.assertEqual(self.config["uboot_commit"], revision)
        self.assertEqual(self.config["linux_ref"], f"commit:{revision}")
        self.assertEqual(self.config["uboot_ref"], f"commit:{revision}")
        self.assertFalse(self.config["atf_applicable"])

    def test_realtek_family_scope_is_explicit(self) -> None:
        inventory = self.config["realtek_family_inventory"]
        self.assertTrue(inventory["shared_legacy_include_modified"])
        self.assertEqual(inventory["legacy_boards"]["bananapim4"]["soc"], "RTD1395")
        self.assertEqual(inventory["legacy_boards"]["bananapiw2"]["soc"], "RTD1296")
        self.assertEqual(
            inventory["separate_family_boards"]["xpressreal-t3"]["soc"],
            "RTD1619B",
        )

    def test_prebuilt_assets_block_public_release(self) -> None:
        assets = self.config["conditional_unlinked_prebuilt_assets"]
        self.assertEqual(
            set(assets),
            {
                "u-boot-rtk/static_lib/libefuse.a.32",
                "u-boot-rtk/static_lib/libsha1_util.a.32",
                "u-boot-rtk/static_lib/libsecurity.a.32",
                "u-boot-rtk/static_lib/libkeyset.a.32",
            },
        )
        for asset in assets.values():
            self.assertGreater(asset["size"], 0)
            self.assertEqual(len(asset["sha256"]), 64)
            self.assertFalse(asset["source_build_available"])
            self.assertFalse(asset["included_in_candidate_binary"])
            self.assertFalse(asset["redistribution_license_verified"])
        linked = self.config["linked_unrebuilt_source_assets"]
        self.assertEqual(len(linked), 6)
        for asset in linked.values():
            self.assertTrue(asset["source_files_available"])
            self.assertFalse(asset["build_toolchain_pinned"])
            self.assertFalse(asset["rebuilt_in_candidate"])
            self.assertTrue(asset["included_in_candidate_binary"])
            self.assertFalse(asset["redistribution_license_verified"])
        self.assertFalse(self.config["public_release_allowed"])

    def test_runtime_and_excluded_assets_are_bounded(self) -> None:
        bluecore = self.config["runtime_prebuilt_assets"][
            "rtk-pack/rtk/bpi-m4/configs/default/linux/bluecore.audio"
        ]
        self.assertEqual(bluecore["size"], 4319769)
        self.assertFalse(bluecore["source_build_available"])
        self.assertFalse(bluecore["redistribution_license_verified"])
        excluded = self.config["excluded_source_assets"]
        self.assertIn("rtk-pack/rtk/bpi-m4/configs/default/linux/u-boot-bpi-m4.bin", excluded)
        self.assertIn("rtk-pack/rtk/bpi-m4/configs/default/linux/uInitrd", excluded)
        for asset in excluded.values():
            self.assertFalse(asset["included_in_candidate"])

    def test_root_label_and_timestamp_are_reproducible(self) -> None:
        root_patch = UBOOT_PATCHES[1].read_text(encoding="utf-8")
        timestamp_patch = UBOOT_PATCHES[2].read_text(encoding="utf-8")
        self.assertIn("+root=LABEL=BPI-ROOT", root_patch)
        self.assertNotIn("+root=/dev/mmcblk", root_patch)
        self.assertIn("SOURCE_DATE_EPOCH", timestamp_patch)
        self.assertIn("date -u", timestamp_patch)
        self.assertEqual(self.policy["root_filesystem_label"], "BPI-ROOT")
        self.assertEqual(self.policy["uboot_write_offset_bytes"], 40960)
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertIn('local sed_expression="s|^root=.*|root=LABEL=', self.family_text)
        self.assertIn('"${sed_expression@Q}" "${vendor_uenv@Q}"', self.family_text)

    def test_root_label_survives_logged_runner_shell_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work = Path(temporary_directory) / "path with space's quote"
            uenv = work / "rtk-pack/rtk/bpi-m4/configs/default/linux/uEnv.txt"
            uenv.parent.mkdir(parents=True)
            uenv.write_text(
                "root=/dev/mmcblk0p2 rw rootfstype=ext4 rootwait\n",
                encoding="utf-8",
            )
            probe = r'''
set -euo pipefail
REALTEK_BPI_VENDOR_BOARD=bpi-m4
REALTEK_BPI_BSP_REPO=https://example.invalid/bpi-m4.git
REALTEK_BPI_BSP_BRANCH=commit:0000000000000000000000000000000000000000
REALTEK_BPI_BSP_BOARD=bpi-m4
REALTEK_BPI_ROOT_LABEL=BPI-ROOT
BOARD=bananapim4
BRANCH=legacy
LINUXFAMILY=realtek-rtd139x-bpi
SRC=/tmp
BOOTCONFIG=rtd1395_bananapi_defconfig
CREATE_PATCHES=yes
source "$1"
display_alert() { :; }
patch_uboot_target() { :; }
exit_with_error() { return 1; }
run_host_command_logged() {
    /usr/bin/env bash -e -o pipefail -c "$*"
}
cd "$2"
build_custom_uboot__realtek_bpi_legacy_bsp
grep -Fqx 'root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait' \
    rtk-pack/rtk/bpi-m4/configs/default/linux/uEnv.txt
'''
            subprocess.run(
                ["bash", "-c", probe, "bash", str(FAMILY), str(work)],
                check=True,
                cwd=ROOT,
            )

    def test_both_dtb_variants_have_explicit_identity(self) -> None:
        patch_text = KERNEL_PATCHES[1].read_text(encoding="utf-8")
        self.assertEqual(patch_text.count('model = "Banana Pi BPI-M4";'), 2)
        self.assertEqual(
            patch_text.count('compatible = "bananapi,bpi-m4", "realtek,rtd1395";'),
            2,
        )
        self.assertEqual(self.policy["memory_variants_mib"], [1024, 2048])
        self.assertEqual(len(self.policy["dtbs"]), 2)

    def test_interface_contract_does_not_claim_hardware(self) -> None:
        for key in (
            "storage_contract",
            "network_contract",
            "usb_contract",
            "display_contract",
            "io_contract",
            "wireless_contract",
        ):
            self.assertIn(key, self.policy)
        self.assertFalse(self.policy["storage_contract"]["media_hardware_validated"])
        self.assertFalse(self.policy["usb_contract"]["hardware_role_validated"])
        self.assertFalse(self.policy["display_contract"]["kernel_gpu_driver_verified"])
        self.assertFalse(self.policy["display_contract"]["userspace_acceleration_verified"])
        self.assertFalse(self.policy["display_contract"]["video_decode_verified"])
        self.assertFalse(self.policy["io_contract"]["pin_mapping_hardware_validated"])
        self.assertFalse(self.policy["wireless_contract"]["hardware_validated"])
        self.assertEqual(
            self.policy["storage_contract"]["pcie_node"], "/pcie@98060000"
        )

    def test_kernel_contract_includes_diagnostics_and_io(self) -> None:
        options = self.config["common_kernel_options"]
        for option, expected in (
            ("CONFIG_GPIOLIB", "y"),
            ("CONFIG_I2C_RTK", "y"),
            ("CONFIG_SPI_SPIDEV", "y"),
            ("CONFIG_RTL8821CU", "m"),
            ("CONFIG_RTK_THERMAL", "y"),
            ("CONFIG_RTK_WATCHDOG", "y"),
            ("CONFIG_USB_CONFIGFS_MASS_STORAGE", "y"),
            ("CONFIG_R8168", "y"),
        ):
            self.assertEqual(options[option], expected)

    def test_documentation_evidence_is_local_and_not_packaged(self) -> None:
        self.assertEqual(len(self.config["documentation_evidence"]), 5)
        for item in self.config["documentation_evidence"]:
            self.assertTrue(item["local_path"].startswith("/media/pi/SMCI/bpi/"))
            self.assertEqual(len(item["sha256"]), 64)
            self.assertFalse(item["included_in_candidate"])
            self.assertFalse(item["redistribution_license_verified"])

    def test_l2_transition_retains_component_artifacts(self) -> None:
        self.assertFalse(self.config["full_image_built"])
        self.assertFalse(self.config["rootfs_image_built"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        self.assertFalse(self.config["hardware_validated"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertEqual(self.config["candidate_level"], "L2 內部軟體候選")
        self.assertEqual(self.config["candidate_scope"], "internal-l2")
        self.assertEqual(self.config["current_evidence_level"], "L2")
        self.assertNotIn("image_build_evidence", self.config)
        if self.config["component_build_completed"]:
            evidence = self.config["component_build_evidence"]
            self.assertEqual(
                evidence["local_evidence_root"],
                "output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy",
            )
            self.assertFalse(evidence["full_rootfs_image_built"])
            self.assertTrue(evidence["uboot_rebuild_hash_match"])
            self.assertEqual(len(evidence["artifacts"]), 9)
        else:
            self.fail("L2 過渡契約必須保留已建置的元件證據")

    def test_tools_are_m4_scoped_and_avoid_destructive_cleanup(self) -> None:
        builder = BUILDER.read_text(encoding="utf-8")
        verifier = VERIFIER.read_text(encoding="utf-8")
        self.assertIn("bananapi-realtek-m4-component", builder)
        self.assertIn("bananapi-realtek-rtd1395-m4-legacy", builder)
        self.assertIn("git clone --shared --no-checkout", builder)
        self.assertIn("M4_COMPONENT_OUTPUT_DIR", verifier)
        self.assertNotIn("rm -rf", builder)
        self.assertNotIn("git reset --hard", builder)
        self.assertNotIn("find -delete", builder)
        self.assertNotIn("bananapi-optimization-status.json", builder)
        self.assertIn("不得包含原始碼或建置樹", verifier)

    def test_full_image_tools_require_fixed_overlay_and_internal_scope(self) -> None:
        builder = IMAGE_BUILDER.read_text(encoding="utf-8")
        runner = IMAGE_RUNNER.read_text(encoding="utf-8")
        verifier = IMAGE_VERIFIER.read_text(encoding="utf-8")
        common = COMMON_IMAGE_VERIFIER.read_text(encoding="utf-8")
        self.assertIn("bananapi-realtek-rtd1395-m4-trixie-legacy-cli", builder)
        self.assertIn("bananapi-realtek-m4-candidate-cache-overlay", builder)
        self.assertIn("ALLOW_INTERNAL_M4_CANDIDATE", builder)
        self.assertIn("PUBLIC_RELEASE=no", builder)
        self.assertIn("HARDWARE_CLAIMS=no", builder)
        self.assertIn("CACHE_LOWER", runner)
        self.assertIn("REQUIRE_BUILD_VERIFIER_IDENTITY=yes", verifier)
        self.assertIn('realtek_bpi_uenv)', common)
        self.assertNotIn("git reset --hard", builder + runner + verifier)
        self.assertNotIn("rm -rf", builder + runner + verifier)

    def test_full_image_contract_is_strict_and_non_public(self) -> None:
        self.assertEqual(self.policy["partition_start_sector"], 8192)
        self.assertEqual(self.policy["root_partition_start_sector"], 532480)
        self.assertEqual(self.policy["boot_partition_label"], "BPI-BOOT")
        self.assertEqual(self.policy["root_partition_label"], "BPI-ROOT")
        self.assertEqual(self.policy["boot_configuration"], "realtek_bpi_uenv")
        self.assertEqual(self.policy["uboot_offset"], 40960)
        self.assertEqual(self.policy["vendor_boot_dtbs"], self.policy["dtbs"])
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(
            self.config["license_policy"]["opaque_payload_redistribution_verified"]
        )
        self.assertFalse(
            self.config["license_policy"]["toolchain_redistribution_verified"]
        )

    def test_policy_document_and_entrypoints_exist(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
