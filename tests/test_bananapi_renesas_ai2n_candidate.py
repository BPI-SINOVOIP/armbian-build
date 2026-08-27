#!/usr/bin/env python3
"""Banana Pi AI2N 固定來源、發布邊界與候選契約回歸測試。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bpi-ai2n.conf"
FAMILY = ROOT / "config/sources/families/renesas-rzv2n-bpi.conf"
KERNEL_CONFIG = ROOT / "config/kernel/linux-renesas-rzv2n-bpi-ai2n.config"
VALIDATION = (
    ROOT / "config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json"
)
SOURCE_VERIFIER = ROOT / "tools/verify-bananapi-renesas-ai2n-sources.sh"
BUILDER = ROOT / "tools/build-bananapi-renesas-ai2n-candidate.sh"
VERIFIER = ROOT / "tools/verify-bananapi-renesas-ai2n-candidate.sh"
RUNNER = ROOT / "tools/run-bananapi-renesas-ai2n-candidate-isolated-cache.sh"
SOURCE_PREPARER = ROOT / "tools/prepare-bananapi-renesas-ai2n-overlay-sources.sh"
UBOOT_COMPAT_EXTENSION = ROOT / "extensions/uboot-binman-fix-pkg-resources.sh"
MAIN_CONFIG = ROOT / "lib/functions/configuration/main-config.sh"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-renesas-ai2n-source-policy-20260827.md"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BananaPiRenesasAi2nCandidateTests(unittest.TestCase):
    """防止 AI2N 來源、載荷、授權與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(VALIDATION.read_text(encoding="utf-8"))
        cls.board = cls.config["boards"]["bpi-ai2n"]
        cls.board_text = BOARD.read_text(encoding="utf-8")
        cls.family_text = FAMILY.read_text(encoding="utf-8")

    def test_board_has_dedicated_packages_and_legacy_contract(self) -> None:
        for expected in (
            'BOARDFAMILY="renesas-rzv2n-bpi"',
            'KERNEL_TARGET="legacy"',
            'KERNEL_TEST_TARGET="legacy"',
            'BOOTCONFIG="bananapi_ai2n_defconfig"',
            'BOOT_FDT_FILE="renesas/bananapi-ai2n.dtb"',
            'OVERLAY_PREFIX="bpi-ai2n"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        for package in self.config["common_packages"]:
            with self.subTest(package=package):
                self.assertIn(package, self.board_text)

    def test_all_moving_sources_are_fixed(self) -> None:
        expected = {
            "linux": "48c742429129c095045823c204209bb2a92fb5b4",
            "atf": "a011da37865c7649db48efc29b18b36cf87e4bb3",
            "uboot": "8aec7f20bcf5555d7d219c2bad295b4a627b6521",
        }
        for component, revision in expected.items():
            with self.subTest(component=component):
                self.assertEqual(
                    self.config["source_commits"][component]["revision"], revision
                )
                self.assertRegex(revision, r"^[0-9a-f]{40}$")
                self.assertIn(f"commit:{revision}", self.family_text)
        self.assertEqual(self.config["linux_commit"], expected["linux"])
        self.assertEqual(self.board["uboot_revision"], expected["uboot"])
        self.assertEqual(self.board["atf_revision"], expected["atf"])

    def test_packaging_tools_are_built_from_fixed_atf_source(self) -> None:
        self.assertNotIn(
            "packages/blobs/bpi-renesas/tools", self.family_text
        )
        for expected in (
            "tools/renesas/rz_boot_param",
            "tools/renesas/bptool",
            "tools/fiptool/fiptool",
            '"${fiptool}" create',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.family_text)
        self.assertEqual(
            set(self.config["source_built_packaging_tools"]),
            {"bptool", "fiptool"},
        )
        for path, digest in self.config["unused_prebuilt_packaging_tools"].items():
            with self.subTest(path=path):
                self.assertEqual(sha256(ROOT / path), digest)

    def test_packaging_temporary_file_is_worktree_scoped(self) -> None:
        self.assertNotIn("/tmp/bp.bin", self.family_text)
        self.assertIn('mktemp "${PWD}/${pack_out}/.bp.XXXXXX.bin"', self.family_text)
        self.assertIn('unlink "${bp_file}"', self.family_text)

    def test_uboot_python_compatibility_uses_framework_extension(self) -> None:
        text = UBOOT_COMPAT_EXTENSION.read_text(encoding="utf-8")
        self.assertIn("importlib_resources.files", text)
        self.assertIn("pkg_resources.resource_string", text)
        self.assertIn(
            'enable_extension "uboot-binman-fix-pkg-resources"',
            MAIN_CONFIG.read_text(encoding="utf-8"),
        )

    def test_proprietary_assets_are_hash_locked(self) -> None:
        assets = self.config["proprietary_assets"]
        self.assertEqual(len(assets), 9)
        for relative, expected in assets.items():
            with self.subTest(relative=relative):
                self.assertRegex(expected, r"^[0-9a-f]{64}$")
                self.assertEqual(sha256(ROOT / relative), expected)
        installed = {
            **self.config["installed_firmware_blobs"],
            **self.config["installed_file_sha256"],
        }
        self.assertEqual(set(installed.values()), set(assets.values()))

    def test_public_release_and_hardware_claims_are_blocked(self) -> None:
        policy = self.config["release_policy"]
        hardware = self.config["hardware_evidence"]
        self.assertFalse(policy["public_release_allowed"])
        self.assertFalse(policy["public_redistribution_authorized"])
        self.assertTrue(policy["machine_enforced"])
        self.assertGreaterEqual(len(policy["block_reasons"]), 3)
        self.assertFalse(hardware["present"])
        self.assertFalse(hardware["node_presence_is_functional_evidence"])
        self.assertEqual(hardware["validated_features"], [])
        self.assertEqual(self.config["candidate_scope"], "internal-l0")
        self.assertEqual(self.config["evidence_level"], "L0")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertEqual(self.config["allowed_evidence_levels"], ["L0", "L2"])

    def test_policy_only_gate_refuses_public_release(self) -> None:
        environment = os.environ.copy()
        environment.update({"POLICY_ONLY": "yes", "PUBLIC_RELEASE": "yes"})
        result = subprocess.run(
            [str(SOURCE_VERIFIER)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止建立公開發布候選", result.stderr)

    def test_policy_only_gate_allows_internal_candidate(self) -> None:
        environment = os.environ.copy()
        environment.update({"POLICY_ONLY": "yes", "PUBLIC_RELEASE": "no"})
        result = subprocess.run(
            [str(SOURCE_VERIFIER)],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("發布政策守門通過", result.stdout)

    def test_public_gate_requires_redistribution_authorization(self) -> None:
        config = json.loads(VALIDATION.read_text(encoding="utf-8"))
        config["release_policy"]["public_release_allowed"] = True
        config["release_policy"]["public_redistribution_authorized"] = False
        with tempfile.TemporaryDirectory() as directory:
            validation = Path(directory) / "validation.json"
            validation.write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "POLICY_ONLY": "yes",
                    "PUBLIC_RELEASE": "yes",
                    "VALIDATION_CONFIG": str(validation),
                }
            )
            result = subprocess.run(
                [str(SOURCE_VERIFIER)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止建立公開發布候選", result.stderr)

    def test_kernel_configuration_matches_candidate_contract(self) -> None:
        kernel_text = KERNEL_CONFIG.read_text(encoding="utf-8")
        for option, value in self.config["common_kernel_options"].items():
            with self.subTest(option=option):
                self.assertIn(f"{option}={value}", kernel_text)

    def test_uboot_payload_and_boot_area_are_explicit(self) -> None:
        self.assertEqual(self.board["output_image_prefix"], "Bananapi-Armbian_*_")
        self.assertEqual(self.board["output_image_board_token"], "Bpi-ai2n")
        self.assertEqual(
            self.board["uboot_payloads"],
            ["bl2_bp_sd.bin@512", "fip.bin@393216"],
        )
        self.assertEqual(self.board["uboot_package_only_payloads"], ["u-boot.bin"])
        self.assertEqual(self.board["partition_table"], "msdos")
        self.assertEqual(self.board["partition_start_sector"], 8192)
        self.assertEqual(self.board["logical_sector_size"], 512)
        self.assertEqual(self.board["uboot_defconfig"], "bananapi_ai2n_defconfig")
        self.assertIn(
            "CONFIG_CMD_USB_MASS_STORAGE=y",
            self.board["uboot_required_config_options"],
        )
        self.assertIn(
            "CONFIG_USB_FUNCTION_MASS_STORAGE=y",
            self.board["uboot_required_config_options"],
        )

    def test_dtb_contract_does_not_overstate_functionality(self) -> None:
        self.assertEqual(
            self.board["dtb_sha256"],
            "51b9c6f78e88ceb61d44a56f2507a71da94bb8245d1f6f163f0ff97f306814de",
        )
        self.assertEqual(self.board["sd_bus_width"], 4)
        self.assertEqual(self.board["additional_bus_widths"], ["/soc/mmc@15c00000=8"])
        self.assertEqual(len(self.board["required_overlays"]), 18)
        self.assertEqual(self.board["default_overlays"], [])
        for node in (
            "/soc/drp1@17000000",
            "/soc/drpai@16800000",
            "/soc/pcie@13400000",
            "/soc/usb@15850000",
        ):
            self.assertIn(node, self.board["required_status_nodes"])
        for node in (
            "/sound",
            "/soc/isp@16080000",
            "/soc/spi@12800000",
            "/soc/i2c@14400800",
        ):
            self.assertIn(node, self.board["required_disabled_nodes"])
        self.assertNotIn("/sound", self.board["required_present_nodes"])

    def test_source_tree_binary_inventory_is_explicit(self) -> None:
        inventory = self.config["source_tree_binary_inventory"]
        self.assertEqual(inventory["uboot"], {})
        self.assertEqual(len(inventory["atf"]), 6)
        self.assertEqual(len(inventory["linux"]), 2)
        self.assertTrue(
            all(not item["used_by_ai2n_build"] for item in inventory["atf"].values())
        )
        self.assertTrue(
            all(item["used_by_ai2n_build"] for item in inventory["linux"].values())
        )

    def test_dedicated_entrypoints_are_isolated(self) -> None:
        for path in (BUILDER, VERIFIER, RUNNER):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(
                    "bananapi-renesas-rzv2n-ai2n-legacy.json", text
                )
                self.assertIn(
                    "bananapi-renesas-rzv2n-ai2n-trixie-legacy-cli", text
                )
        self.assertIn("bananapi-renesas-ai2n-cache-overlay", RUNNER.read_text())
        self.assertIn('BOARDS="bpi-ai2n"', BUILDER.read_text())
        self.assertIn('BOARDS="bpi-ai2n"', VERIFIER.read_text())
        self.assertIn('VERIFICATION_EVIDENCE_LEVEL="L2"', VERIFIER.read_text())
        generic_builder = (
            ROOT / "tools/build-bananapi-sunxi-candidates.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'output_image_glob="${output_image_prefix_effective}'
            '${output_image_board_token_effective}_${release}_${branch}_*.img"',
            generic_builder,
        )
        self.assertIn(str(SOURCE_PREPARER.relative_to(ROOT)), BUILDER.read_text())
        self.assertTrue(SOURCE_PREPARER.stat().st_mode & 0o111)

    def test_policy_records_component_and_hardware_boundaries(self) -> None:
        text = POLICY.read_text(encoding="utf-8")
        for expected in (
            "目前只能登錄為內部 L0 來源／元件契約",
            "通過後才可標示為內部 L2",
            "禁止建立公開發布候選",
            "不能以節點存在或核心選項開啟取代",
            "OpenSSL 3.0",
            "PUBLIC_RELEASE=yes",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_l2_requires_complete_read_only_verifier(self) -> None:
        verifier = VERIFIER.read_text(encoding="utf-8")
        self.assertLess(
            verifier.index('"${generic_verifier}"'),
            verifier.index('status["evidence_level"] = "L2"'),
        )
        self.assertIn('status["public_release_allowed"] = False', verifier)
        self.assertIn('status["hardware_evidence_present"] = False', verifier)
        for path in (
            VALIDATION,
            SOURCE_VERIFIER,
            SOURCE_PREPARER,
            BUILDER,
            VERIFIER,
            RUNNER,
            POLICY,
        ):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("rm -rf", text)

    def test_overlay_source_preparer_cannot_touch_lower_cache(self) -> None:
        text = SOURCE_PREPARER.read_text(encoding="utf-8")
        for required in (
            "mountpoint -q",
            '== overlay',
            "checkout-index --force --",
            "ls-files --others --exclude-standard -z",
            "ls-files --others --ignored --exclude-standard -z",
            "status --porcelain --untracked-files=all",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("/media/pi/SMCI/armbian/bpi-v26.2.1/cache", text)


if __name__ == "__main__":
    unittest.main()
