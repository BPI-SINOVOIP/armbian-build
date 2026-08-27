#!/usr/bin/env python3
"""Banana Pi CM2 搭配 R2 Pro 載板候選回歸測試。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT / "config/boards/bananapicm2.wip"
CONFIG_PATH = (
    ROOT
    / "config/validation/bananapi-rockchip-rk3568-cm2-r2pro-current.json"
)
REJECTED_KERNEL_DTS = (
    ROOT
    / "patch/kernel/archive/rockchip64-6.18/dt"
    / "rk3568-bpi-cm2-r2pro-carrier.dts"
)
REJECTED_UBOOT_PATCH = (
    ROOT
    / "patch/u-boot/v2024.01/board_bananapicm2"
    / "add-cm2-r2pro-carrier-identity.patch"
)
POLICY_PATH = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-rockchip-cm2-r2pro-source-policy-20260827.md"
)
ENTRYPOINTS = (
    ROOT / "tools/build-bananapi-rockchip-cm2-r2pro-candidate.sh",
    ROOT / "tools/verify-bananapi-rockchip-cm2-r2pro-candidate.sh",
    ROOT / "tools/run-bananapi-rockchip-cm2-r2pro-candidate-isolated-cache.sh",
)


class BananaPiRockchipCM2CandidateTests(unittest.TestCase):
    """防止模組、載板、來源、授權與驗證邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.board_text = BOARD_PATH.read_text()
        cls.config = json.loads(CONFIG_PATH.read_text())
        cls.policy = cls.config["boards"]["bananapicm2"]

    def test_identity_is_cm2_with_unverified_r2_pro_donor(self) -> None:
        self.assertIn(
            'BOARD_NAME="Banana Pi CM2（R2 Pro 軟體參考）"',
            self.board_text,
        )
        self.assertEqual(
            self.config["candidate_identity_scope"],
            "BPI-CM2 初始移植，目前僅採用 BPI-R2 Pro 軟體參考板，載板尚未確認",
        )
        self.assertFalse(self.config["generic_cm2_supported"])
        self.assertFalse(self.config["r2_pro_is_cm2_carrier_verified"])
        self.assertTrue(self.config["donor_only_contract"])
        self.assertEqual(self.policy["module"], "Banana Pi BPI-CM2")
        self.assertEqual(self.policy["carrier"], "尚未確認")
        self.assertEqual(self.policy["donor_board"], "Banana Pi BPI-R2 Pro")
        self.assertTrue(self.policy["donor_only_contract"])
        self.assertFalse(self.policy["carrier_verified"])
        self.assertFalse(self.policy["generic_module_image"])

    def test_board_explicitly_uses_r2_pro_donor(self) -> None:
        for expected in (
            'KERNEL_TARGET="current"',
            'BOOTCONFIG="bpi-r2-pro-rk3568_defconfig"',
            'BOOT_FDT_FILE="rockchip/rk3568-bpi-r2-pro.dtb"',
            'SRC_CMDLINE="console=ttyS2,1500000 console=tty0"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn("cm2-r2pro-carrier", self.board_text)

    def test_all_movable_sources_are_fixed(self) -> None:
        expected = (
            'KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"',
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            'RKBIN_GIT_REF="commit:46c4793ea2dcea7c8331fce9f07b5c80561a0395"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
        )
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        for movable in ('KERNELBRANCH_BOARD="branch:', 'BOOTBRANCH_BOARD="branch:', 'RKBIN_GIT_REF="branch:'):
            self.assertNotIn(movable, self.board_text)

    def test_current_hook_overrides_family_sources(self) -> None:
        harness = f'''
SRC="{ROOT}"
BRANCH=current
KERNELSOURCE=movable-kernel
KERNELBRANCH=branch:movable-kernel
BOOTSOURCE=movable-uboot
BOOTBRANCH=branch:movable-uboot
ARMBIAN_FIRMWARE_GIT_REF=branch:movable-firmware
source "{BOARD_PATH}"
post_family_config_branch_current__bananapicm2_r2pro_pin_sources
printf 'kernel_source=%s\\nkernel=%s\\nuboot_source=%s\\nuboot=%s\\nfirmware=%s\\n' \\
    "$KERNELSOURCE" "$KERNELBRANCH" "$BOOTSOURCE" "$BOOTBRANCH" \\
    "$ARMBIAN_FIRMWARE_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "kernel_source=https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
            result.stdout,
        )
        self.assertIn(
            "kernel=commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5",
            result.stdout,
        )
        self.assertIn("uboot_source=https://github.com/u-boot/u-boot", result.stdout)
        self.assertIn(
            "uboot=commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e",
            result.stdout,
        )

    def test_rkbin_license_and_blobs_are_gated(self) -> None:
        blobs = self.config["rkbin_blobs"]
        self.assertEqual(len(blobs), 4)
        self.assertEqual(
            blobs["LICENSE.TXT"],
            "0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c",
        )
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse(self.config["rkbin_standalone_redistribution_authorized"])
        self.assertTrue(self.config["rkbin_distribution_review_required"])
        self.assertEqual(
            self.config["installed_file_sha256"][
                "/usr/share/doc/armbian-bsp-bananapicm2/rkbin.LICENSE.TXT"
            ],
            blobs["LICENSE.TXT"],
        )
        self.assertIn(
            "post_family_tweaks_bsp__bananapicm2_r2pro_rkbin_license",
            self.board_text,
        )
        release = self.config["release_policy"]
        self.assertFalse(release["public_release_allowed"])
        self.assertFalse(release["public_redistribution_authorized"])
        self.assertTrue(release["machine_enforced"])

    def test_false_cm2_carrier_identity_is_removed(self) -> None:
        self.assertFalse(REJECTED_KERNEL_DTS.exists())
        self.assertFalse(REJECTED_UBOOT_PATCH.exists())
        self.assertEqual(self.policy["dtb"], "rockchip/rk3568-bpi-r2-pro.dtb")
        self.assertEqual(
            self.policy["model"],
            "Bananapi-R2 Pro (RK3568) DDR4 Board",
        )
        self.assertEqual(
            self.policy["compatible"],
            ["sinovoip,rk3568-bpi-r2pro", "rockchip,rk3568"],
        )
        self.assertNotIn("dtb_sha256", self.policy)

    def test_uboot_contract_keeps_donor_identity(self) -> None:
        for expected in (
            'CONFIG_DEFAULT_DEVICE_TREE="rk3568-bpi-r2-pro"',
            'CONFIG_DEFAULT_FDT_FILE="rockchip/rk3568-bpi-r2-pro.dtb"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.policy["uboot_required_config_options"])
        self.assertEqual(
            self.policy["uboot_defconfig"],
            "bpi-r2-pro-rk3568_defconfig",
        )
        self.assertEqual(
            self.policy["uboot_binary_for_string_checks"],
            "u-boot.itb",
        )
        self.assertIn(
            "Bananapi-R2 Pro (RK3568) DDR4 Board",
            self.policy["uboot_required_binary_strings"],
        )

    def test_donor_io_contract_cannot_be_hardware_evidence(self) -> None:
        self.assertEqual(self.policy["sd_node"], "/mmc@fe2b0000")
        self.assertIn("/mmc@fe310000=8", self.policy["additional_bus_widths"])
        self.assertIn(
            "/ethernet@fe2a0000/mdio/switch@1f:compatible=mediatek,mt7531",
            self.policy["required_string_properties"],
        )
        self.assertIn(
            "/usb@fcc00000:dr_mode=host",
            self.policy["required_string_properties"],
        )
        self.assertNotIn(
            "/usb@fcc00000:dr_mode=otg",
            self.policy["required_string_properties"],
        )
        hardware = self.config["hardware_evidence"]
        self.assertFalse(hardware["present"])
        self.assertFalse(hardware["donor_node_presence_is_cm2_functional_evidence"])
        self.assertEqual(hardware["validated_features"], [])

    def test_packages_cover_declared_validation_tools(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= packages)

    def test_dedicated_entrypoints_select_only_cm2_r2pro(self) -> None:
        for path in ENTRYPOINTS:
            with self.subTest(path=path.name):
                self.assertTrue(path.stat().st_mode & 0o111)
                subprocess.run(["bash", "-n", str(path)], check=True)
                text = path.read_text()
                self.assertIn("cm2-r2pro", text)
        for path in ENTRYPOINTS[:2]:
            self.assertIn('BOARDS="bananapicm2"', path.read_text())
            self.assertIn(
                "bananapi-rockchip-rk3568-cm2-r2pro-current.json",
                path.read_text(),
            )
        self.assertIn(
            "bananapi-rockchip-cm2-r2pro-donor-cache-overlay",
            ENTRYPOINTS[2].read_text(),
        )
        self.assertIn("cm2-r2pro-donor-trixie", ENTRYPOINTS[0].read_text())
        self.assertIn("cm2-r2pro-donor-trixie", ENTRYPOINTS[1].read_text())
        self.assertIn('VERIFICATION_EVIDENCE_LEVEL="L1"', ENTRYPOINTS[1].read_text())

    def test_current_evidence_is_l0_and_future_donor_is_capped_at_l1(self) -> None:
        self.assertEqual(self.config["candidate_scope"], "internal-l0")
        self.assertEqual(self.config["evidence_level"], "L0")
        self.assertFalse(self.config["component_evidence"]["accepted"])
        self.assertFalse(self.config["component_evidence"]["full_image_present"])
        text = POLICY_PATH.read_text()
        self.assertIn("目前稽核層級為內部 L0", text)
        self.assertIn("最多只能標示為內部 L1", text)
        self.assertIn("目前沒有證據證明 BPI-R2 Pro 是可安裝 BPI-CM2 的載板", text)
        self.assertIn("禁止獨立散布", text)
        self.assertIn("禁止建立公開發布候選", text)
        self.assertIn("不得只改參考板的 model 或 compatible", text)

    def test_public_release_is_machine_blocked(self) -> None:
        environment = os.environ.copy()
        environment["PUBLIC_RELEASE"] = "yes"
        for path in ENTRYPOINTS[:2]:
            with self.subTest(path=path.name):
                result = subprocess.run(
                    [str(path)],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("禁止建立公開發布候選", result.stderr)

    def test_full_donor_image_evidence_is_capped_at_l1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            output = temporary / "output"
            fake_verifier = temporary / "fake-verifier.sh"
            fake_verifier.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${OUTPUT_DIR}"
printf 'board\tidentity\tread_only_content\tevidence_level\nbananapicm2\tpass\tpass\tL2\n' >"${OUTPUT_DIR}/VERIFICATION.tsv"
printf '{"status":"complete","evidence_level":"L2"}\n' >"${OUTPUT_DIR}/VERIFICATION_STATUS.json"
""",
                encoding="utf-8",
            )
            fake_verifier.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "OUTPUT_DIR": str(output),
                    "PUBLIC_RELEASE": "no",
                    "ROCKCHIP_CANDIDATE_VERIFIER": str(fake_verifier),
                }
            )
            result = subprocess.run(
                [str(ENTRYPOINTS[1])],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            verification = (output / "VERIFICATION.tsv").read_text()
            status = json.loads(
                (output / "VERIFICATION_STATUS.json").read_text()
            )
        self.assertIn("bananapicm2\tpass\tpass\tL1", verification)
        self.assertNotIn("\tL2", verification)
        self.assertEqual(status["evidence_level"], "L1")
        self.assertEqual(status["candidate_scope"], "internal-l1-donor-only")
        self.assertTrue(status["donor_only_contract"])
        self.assertFalse(status["carrier_verified"])
        self.assertFalse(status["generic_cm2_supported"])
        self.assertFalse(status["public_release_allowed"])
        self.assertFalse(status["public_redistribution_authorized"])
        self.assertFalse(status["hardware_evidence_present"])

    def test_cm2_scripts_do_not_use_recursive_deletion(self) -> None:
        for path in ENTRYPOINTS:
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertNotIn("rm -rf", text)
                self.assertNotIn("find ", text)
        shared_runner = (
            ROOT / "tools/run-bananapi-candidates-isolated-cache.sh"
        ).read_text()
        self.assertNotIn("rm -rf", shared_runner)
        self.assertNotIn(" -delete", shared_runner)


if __name__ == "__main__":
    unittest.main()
