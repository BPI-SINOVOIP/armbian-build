#!/usr/bin/env python3
"""Banana Pi M4 Super L0 donor-only 契約回歸測試。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapim4super.wip"
CONFIG = (
    ROOT
    / "config/validation/bananapi-rockchip-rk3568-m4super-vendor.json"
)
SOURCE_POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-m4super-source-policy-20260827.md"
)
COMPONENT_NOTE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-m4super-component-evidence-20260827.md"
)
CHECKER = ROOT / "tools/check-bananapi-rockchip-m4super-source-policy.py"
PATCHING_CONFIG = ROOT / "patch/kernel/rk35xx-vendor-6.1/0000.patching_config.yaml"
REMOVED_PATHS = (
    ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3568-bananapi-m4-super.dts",
    ROOT / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3568-bananapi-m4-super.dts",
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig"
    / "bananapi-m4-super-rk3568_defconfig",
    ROOT
    / "patch/kernel/rk35xx-vendor-6.1/board_bananapim4super"
    / "0001-add-bpi-m4-super-overlays-to-makefile.patch",
    ROOT
    / "patch/kernel/rk35xx-vendor-6.1/overlay"
    / "rk35xx-bpi-m4-super-i2c3-m1.dts",
    ROOT
    / "patch/kernel/rk35xx-vendor-6.1/overlay"
    / "rk35xx-bpi-m4-super-i2c5-m0.dts",
    ROOT
    / "patch/kernel/rk35xx-vendor-6.1/overlay"
    / "rk35xx-bpi-m4-super-spi2-m0-spidev.dts",
    ROOT / "tools/build-bananapi-rockchip-m4super-candidate.sh",
    ROOT / "tools/verify-bananapi-rockchip-m4super-candidate.sh",
    ROOT / "tools/run-bananapi-rockchip-m4super-candidate-isolated-cache.sh",
)


class BananaPiRockchipM4SuperCandidateTests(unittest.TestCase):
    """防止 donor 參考再次被升格成板級、產物或硬體聲明。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.board = cls.config["boards"]["bananapim4super"]

    def run_checker(self, config: dict[str, object]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "contract.json"
            path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return subprocess.run(
                ["python3", str(CHECKER), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_board_keeps_sige3_donor_identity(self) -> None:
        for expected in (
            'BOARD_NAME="Banana Pi M4 Super（ArmSoM Sige3 donor-only）"',
            'KERNEL_TEST_TARGET="vendor"',
            'BOOTCONFIG="armsom-sige3-rk3568_defconfig"',
            'BOOT_FDT_FILE="rockchip/rk3568-armsom-sige3.dtb"',
            'M4SUPER_EVIDENCE_LEVEL="L0"',
            'M4SUPER_DONOR_ONLY="yes"',
            'M4SUPER_DONOR_BOARD="ArmSoM Sige3"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn("bananapi-m4-super-rk3568_defconfig", self.board_text)
        self.assertNotIn("rk3568-bananapi-m4-super.dtb", self.board_text)
        self.assertNotIn("PACKAGE_LIST_BOARD=", self.board_text)

    def test_all_donor_sources_are_fixed(self) -> None:
        for expected in (
            'KERNELBRANCH_BOARD="commit:c6157104418d012823413c02f9222f3fe123dd25"',
            'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
            'RKBIN_GIT_REF="commit:1d3c61008fa823936ae7a59615393f8294b64456"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_vendor_hook_overrides_movable_family_sources(self) -> None:
        harness = f'''
BRANCH=vendor
KERNELSOURCE=可變核心
KERNELBRANCH=branch:可變核心
BOOTSOURCE=可變啟動器
BOOTBRANCH=branch:可變啟動器
RKBIN_GIT_URL=https://example.invalid/rkbin
RKBIN_GIT_REF=branch:可變二進位
ARMBIAN_FIRMWARE_GIT_SOURCE=https://example.invalid/firmware
ARMBIAN_FIRMWARE_GIT_REF=branch:可變韌體
source "{BOARD}"
post_family_config_branch_vendor__bananapim4super_pin_sources
printf 'kernel=%s\nuboot=%s\nrkbin=%s\nfirmware=%s\n' \
    "$KERNELBRANCH" "$BOOTBRANCH" "$RKBIN_GIT_REF" "$ARMBIAN_FIRMWARE_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel=commit:c6157104418d012823413c02f9222f3fe123dd25", result.stdout)
        self.assertIn("uboot=commit:39cd993e5d6296635438e84f4576b3a9bf76f86e", result.stdout)
        self.assertIn("rkbin=commit:1d3c61008fa823936ae7a59615393f8294b64456", result.stdout)
        self.assertIn("firmware=commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08", result.stdout)

    def test_contract_is_l0_and_blocks_all_elevated_claims(self) -> None:
        self.assertEqual(self.config["evidence_level"], "L0")
        self.assertEqual(self.config["allowed_evidence_levels"], ["L0"])
        self.assertEqual(self.config["contract_scope"], "donor_only")
        self.assertTrue(self.config["donor_only"])
        for key in (
            "donor_hardware_equivalence_verified",
            "component_build_completed",
            "full_image_built",
            "hardware_validated",
            "public_release_allowed",
        ):
            with self.subTest(key=key):
                self.assertFalse(self.config[key])
        self.assertEqual(self.board["donor_board"], "ArmSoM Sige3")
        self.assertEqual(self.board["donor_dtb"], "rockchip/rk3568-armsom-sige3.dtb")
        self.assertEqual(self.board["donor_uboot_defconfig"], "armsom-sige3-rk3568_defconfig")
        self.assertIsNone(self.board["candidate_dtb"])
        self.assertIsNone(self.board["candidate_uboot_defconfig"])
        self.assertEqual(self.board["candidate_overlays"], [])

    def test_wireless_and_pcie_conflicts_are_unresolved(self) -> None:
        conflicts = self.config["hardware_identity_conflicts"]
        wireless = conflicts["wireless_module"]
        self.assertEqual(wireless["official_value"], "SYN43752")
        self.assertEqual(wireless["donor_dts_value"], "ap6275s")
        self.assertEqual(wireless["donor_normalized_value"], "AP6275S")
        self.assertFalse(wireless["resolved"])
        pcie = conflicts["pcie_lane_count"]
        self.assertEqual(
            pcie["official_page_values"],
            ["硬體規格表：PCIe 3.0 x1", "同頁產品比較表：PCIe 3.0 x2"],
        )
        self.assertFalse(pcie["resolved"])

    def test_unverified_board_files_and_build_entrypoints_are_absent(self) -> None:
        for path in REMOVED_PATHS:
            with self.subTest(path=path):
                self.assertFalse(path.exists(), f"未驗證檔案仍存在：{path}")
        patching_text = PATCHING_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("overlay-directories:", patching_text)

    def test_contract_contains_no_artifact_digest(self) -> None:
        text = CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("sha256", text.lower())
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", text))
        self.assertEqual(
            self.config["artifact_evidence"],
            {
                "component_outputs_recorded": False,
                "full_image_outputs_recorded": False,
                "artifact_hashes_recorded": False,
            },
        )

    def test_policy_checker_accepts_only_current_l0_contract(self) -> None:
        result = self.run_checker(self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("L0 donor-only 契約與發布邊界通過", result.stdout)

        invalid_cases = {
            "證據升級": ("evidence_level", "L2"),
            "硬體等效": ("donor_hardware_equivalence_verified", True),
            "元件完成": ("component_build_completed", True),
            "完整映像": ("full_image_built", True),
        }
        for name, (key, value) in invalid_cases.items():
            with self.subTest(case=name):
                modified = copy.deepcopy(self.config)
                modified[key] = value
                rejected = self.run_checker(modified)
                self.assertNotEqual(rejected.returncode, 0)

        modified = copy.deepcopy(self.config)
        modified["artifact_sha256"] = "a" * 64
        rejected = self.run_checker(modified)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("不得記錄產物雜湊欄位", rejected.stderr)

    def test_documents_withdraw_component_and_hardware_claims(self) -> None:
        source_text = SOURCE_POLICY.read_text(encoding="utf-8")
        component_text = COMPONENT_NOTE.read_text(encoding="utf-8")
        for required in (
            "L0 donor-only 研究入口",
            "SYN43752",
            "AP6275S",
            "PCIe 3.0 x1",
            "PCIe 3.0 x2",
            "撤回未經原理圖",
            "不是建置或硬體證據",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source_text)
        for required in (
            "全部大小、雜湊、可重現建置及元件通過聲明已撤回",
            "component_build_completed=false",
            "完整 Armbian 映像或壓縮檔",
            "donor 直接建置結果不得改名",
        ):
            with self.subTest(required=required):
                self.assertIn(required, component_text)
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", component_text))

    def test_board_and_checker_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(BOARD)], check=True)
        compile(
            CHECKER.read_text(encoding="utf-8"),
            str(CHECKER),
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
