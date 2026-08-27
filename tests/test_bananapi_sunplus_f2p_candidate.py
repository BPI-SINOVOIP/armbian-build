#!/usr/bin/env python3
"""Banana Pi F2P 固定來源、SD-only 與發布限制回歸測試。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapif2p.wip"
F2S_BOARD = ROOT / "config/boards/bananapif2s.wip"
FAMILY = (
    ROOT
    / "config/sources/families/include"
    / "sunplus_sp7021_bpi_legacy_common.inc"
)
CONFIG = ROOT / "config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-sunplus-f2p-source-policy-20260827.md"
)
BUILD_EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-sunplus-f2p-component-build-20260827.md"
)
COMPONENT_BUILD = ROOT / "tools/build-bananapi-sunplus-f2p-components.sh"
COMPONENT_VERIFY = ROOT / "tools/verify-bananapi-sunplus-f2p-components.sh"
IMAGE_BUILD = ROOT / "tools/build-bananapi-sunplus-f2p-candidate.sh"
IMAGE_ISOLATED = (
    ROOT / "tools/run-bananapi-sunplus-f2p-candidate-isolated-cache.sh"
)
IMAGE_VERIFY = ROOT / "tools/verify-bananapi-sunplus-f2p-candidate.sh"
GENERIC_VERIFY = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
SOURCE_POLICY = ROOT / "tools/check-bananapi-sunplus-f2p-source-policy.py"


class BananaPiSunplusF2PCandidateTests(unittest.TestCase):
    """防止 F2P 誤用 F2S 啟動資產或越過證據邊界。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapif2p"]
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.family_text = FAMILY.read_text(encoding="utf-8")

    def test_board_remains_wip_and_is_sd_only(self) -> None:
        self.assertEqual(BOARD.suffix, ".wip")
        for expected in (
            'BOARD_NAME="Banana Pi F2P"',
            'BOARDFAMILY="sunplus-sp7021-bpi"',
            'KERNEL_TARGET="legacy"',
            'BOOTCONFIG="sp7021_bpi_f2p_defconfig"',
            'BOOT_FDT_FILE="sp7021-bpi-f2p.dtb"',
            'SUNPLUS_BPI_EMMC_XBOOT_ASSET=""',
            'SUNPLUS_BPI_SD_XBOOT_ASSET="sp-pack/sp7021/common/bin/ISPBOOOT.BIN"',
            'SUNPLUS_BPI_CANDIDATE_MEDIA="sd-only"',
            'SUNPLUS_BPI_PUBLIC_RELEASE_ALLOWED="no"',
            'SUNPLUS_BPI_HARDWARE_CLAIMS_ALLOWED="no"',
            'declare -g IMAGE_PARTITION_TABLE="msdos"',
            "USB_CONFIGFS_MASS_STORAGE",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_bsp_source_is_fixed_to_exact_commit(self) -> None:
        self.assertIn(
            'SUNPLUS_BPI_BSP_BRANCH="commit:'
            '3eee97bd8fb7582c2d9942a533647c3d78222bb5"',
            self.board_text,
        )
        self.assertNotIn('SUNPLUS_BPI_BSP_BRANCH="branch:', self.board_text)
        self.assertEqual(
            self.config["source_commits"]["bsp"]["revision"],
            "3eee97bd8fb7582c2d9942a533647c3d78222bb5",
        )

    def test_f2p_does_not_package_f2s_emmc_xboot(self) -> None:
        foreign_name = "BPI-F2S-xboot-emmc-boot0-0k.img.gz"
        self.assertNotIn(foreign_name, self.board_text)
        self.assertNotIn(foreign_name, self.family_text)
        self.assertIn(foreign_name, F2S_BOARD.read_text(encoding="utf-8"))
        self.assertTrue(
            any(
                Path(path).name == foreign_name
                for path in self.config["excluded_foreign_board_assets"]
            )
        )
        self.assertIn(foreign_name, self.policy["forbidden_packaged_assets"])
        self.assertFalse(self.policy["emmc_install_allowed"])

    def test_target_map_is_board_explicit(self) -> None:
        harness = f'''
SRC="{ROOT}"
BRANCH=legacy
BOARD=bananapif2p
source "{BOARD}"
source "{ROOT / 'config/sources/families/sunplus-sp7021-bpi.conf'}"
printf 'f2p=%s\n' "$UBOOT_TARGET_MAP"
unset SUNPLUS_BPI_EMMC_XBOOT_ASSET
BOARD=bananapif2s
source "{F2S_BOARD}"
source "{ROOT / 'config/sources/families/sunplus-sp7021-bpi.conf'}"
printf 'f2s=%s\n' "$UBOOT_TARGET_MAP"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        f2p_line, f2s_line = result.stdout.splitlines()
        self.assertNotIn("BPI-F2S-xboot", f2p_line)
        self.assertIn("ISPBOOOT.BIN", f2p_line)
        self.assertIn("BPI-F2S-xboot-emmc-boot0-0k.img.gz", f2s_line)

    def test_validation_contract_keeps_release_closed(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "legacy")
        self.assertEqual(self.config["candidate_level"], "L1 元件候選")
        self.assertEqual(self.config["candidate_scope"], "internal-sd-only")
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["rootfs_image_built"])
        self.assertFalse(self.config["full_image_built"])
        self.assertEqual(self.policy["candidate_boot_media"], ["microSD"])
        self.assertEqual(self.policy["supported_boot_media"], [])
        self.assertFalse(self.config["firmware_redistribution_license_verified"])
        self.assertFalse(
            self.config["toolchain"]["separate_redistribution_audit_complete"]
        )
        self.assertFalse(self.config["trusted_firmware_a"]["applicable"])

    def test_firmware_and_dtb_hashes_are_explicit(self) -> None:
        hashes = [
            *self.config["firmware_blobs"].values(),
            *self.config["excluded_foreign_board_assets"].values(),
            self.policy["dtb_sha256"],
            self.policy["uboot_dtb_sha256"],
        ]
        for digest in hashes:
            with self.subTest(digest=digest):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_packages_and_kernel_contract_cover_only_static_capability(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)
        options = self.config["common_kernel_options"]
        for key in (
            "CONFIG_SOC_SP7021",
            "CONFIG_I2C_SUNPLUS",
            "CONFIG_SPI_SUNPLUS_SP7021",
            "CONFIG_SP_EMMC",
            "CONFIG_SP_SDV2",
            "CONFIG_USB_GADGET_SP7021",
            "CONFIG_VIDEO_SP_HDMITX",
        ):
            with self.subTest(key=key):
                self.assertIn(key, options)
        self.assertIn(
            "/soc@B/spi@sp_spi_controller0",
            self.policy["required_disabled_nodes"],
        )

    def test_component_tools_never_build_rootfs(self) -> None:
        build_text = COMPONENT_BUILD.read_text(encoding="utf-8")
        verify_text = COMPONENT_VERIFY.read_text(encoding="utf-8")
        self.assertNotIn("compile.sh", build_text)
        self.assertNotIn("make linux", build_text)
        self.assertNotIn("make pack", build_text)
        self.assertIn("uImage dtbs modules", build_text)
        self.assertIn("rootfs_image_built: false", build_text)
        self.assertIn("rootfs_image_built", verify_text)
        self.assertIn("output/components/2026.08/bananapi-sunplus-f2p-legacy", build_text)
        self.assertIn("linux-modules.tar", build_text)
        self.assertIn("component_build_evidence", verify_text)
        self.assertNotIn('git -C "${source_dir}"', verify_text)

    def test_component_evidence_locks_portable_outputs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["implementation_commit"],
            "a35a6652cc745ec156a1190e315664d6b415d212",
        )
        self.assertEqual(len(evidence["artifacts"]), 6)
        self.assertEqual(
            evidence["artifacts"]["linux_dtb"]["sha256"],
            self.policy["dtb_sha256"],
        )
        for name, artifact in evidence["artifacts"].items():
            with self.subTest(name=name):
                self.assertGreater(artifact["size"], 0)
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")

    def test_full_image_entrypoint_is_internal_and_overlay_isolated(self) -> None:
        build_text = IMAGE_BUILD.read_text(encoding="utf-8")
        isolated_text = IMAGE_ISOLATED.read_text(encoding="utf-8")
        self.assertIn("ALLOW_INTERNAL_F2P_SD_CANDIDATE", build_text)
        self.assertIn("build-bananapi-sunxi-candidates.sh", build_text)
        self.assertIn("check-bananapi-sunplus-f2p-source-policy.py", build_text)
        self.assertIn('MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-40}"', build_text)
        self.assertIn('expected_source_date_epoch="1609074838"', build_text)
        self.assertNotIn("compile.sh", build_text)
        self.assertIn("run-bananapi-candidates-isolated-cache.sh", isolated_text)
        self.assertIn("bananapi-sunplus-f2p-cache-overlay", isolated_text)

    def test_image_verifier_is_read_only_and_rejects_foreign_asset(self) -> None:
        wrapper = IMAGE_VERIFY.read_text(encoding="utf-8")
        generic = GENERIC_VERIFY.read_text(encoding="utf-8")
        self.assertIn("verify-bananapi-sunxi-candidates.sh", wrapper)
        self.assertIn("check-bananapi-sunplus-f2p-source-policy.py", wrapper)
        self.assertIn("write_entry_state failed", wrapper)
        self.assertIn("VERIFICATION_EVIDENCE_LEVEL=L2", wrapper)
        self.assertIn("VERIFY_ARCHIVES=yes", wrapper)
        self.assertIn("losetup --find --show --partscan --read-only", generic)
        self.assertIn('mount -o ro,noload,nosuid,nodev,noexec', generic)
        self.assertIn("forbidden_packaged_assets", generic)

    def test_full_image_contract_is_exact_and_sd_only(self) -> None:
        self.assertEqual(self.policy["partition_table"], "msdos")
        self.assertEqual(
            self.policy["required_partitions"],
            ["1:*:8192:*", "2:*:*:*"],
        )
        self.assertEqual(self.policy["boot_partition_number"], 1)
        self.assertEqual(self.policy["root_partition_number"], 2)
        self.assertEqual(self.policy["boot_configuration"], "sunplus_uenv")
        self.assertEqual(self.policy["uboot_payloads"], ["u-boot.img@17408"])
        self.assertEqual(
            self.policy["uboot_package_only_payloads"], ["ISPBOOOT.BIN"]
        )
        self.assertIn(
            "BPI-F2S-xboot-emmc-boot0-0k.img.gz",
            self.policy["forbidden_packaged_assets"],
        )
        self.assertEqual(self.policy["sd_bus_width"], 4)

    def test_source_policy_accepts_current_l1_state(self) -> None:
        result = subprocess.run(
            ["python3", str(SOURCE_POLICY), str(CONFIG)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_policy_rejects_label_only_l2(self) -> None:
        mutated = json.loads(CONFIG.read_text(encoding="utf-8"))
        mutated["candidate_level"] = "L2 內部軟體候選"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8"
        ) as stream:
            json.dump(mutated, stream, ensure_ascii=False)
            stream.flush()
            result = subprocess.run(
                ["python3", str(SOURCE_POLICY), stream.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)

    def test_policy_and_build_evidence_state_limits(self) -> None:
        policy_text = POLICY.read_text(encoding="utf-8")
        evidence_text = BUILD_EVIDENCE.read_text(encoding="utf-8")
        for expected in (
            "內部使用、SD-only、可追溯的 L1 元件候選",
            "禁止使用 `BPI-F2S-xboot-emmc-boot0-0k.img.gz`",
            "不宣稱可開機或介面可用",
            "尚未完成獨立再散布授權稽核",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, policy_text)
        self.assertIn("沒有建立完整 rootfs 映像", evidence_text)
        self.assertIn("不支持", evidence_text)


if __name__ == "__main__":
    unittest.main()
