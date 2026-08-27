#!/usr/bin/env python3
"""Banana Pi R3 Mini eMMC 候選來源與驗證契約回歸測試。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7986-r3mini-current.json"
BOARD = ROOT / "config/boards/bananapir3mini.wip"
KERNEL_PATCH = (
    ROOT
    / "patch/kernel/archive/filogic-6.12/patches.armbian/mt7986a-bananapi-bpi-r3-mini-emmc.patch"
)
KERNEL_SERIES = (
    ROOT / "patch/kernel/archive/filogic-6.12/series.conf",
    ROOT / "patch/kernel/archive/filogic-6.12/series.armbian",
)
UBOOT_PATCH = ROOT / "patch/u-boot/u-boot-filogic/453-add-bpi-r3-mini-u-boot-dts.patch"
GPT = ROOT / "packages/blobs/filogic/gpt"
AIROHA = ROOT / "packages/blobs/filogic/firmware/airoha"
POLICY_CHECK = ROOT / "tools/check-bananapi-filogic-r3mini-policy.sh"
FINALIZER = ROOT / "tools/finalize-bananapi-filogic-r3mini-verification.sh"
BUILD = ROOT / "tools/build-bananapi-filogic-r3mini-candidate.sh"
RUNNER = ROOT / "tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r3mini-candidate.sh"


class BananaPiFilogicR3MiniCandidateTests(unittest.TestCase):
    """防止 R3 Mini eMMC、啟動鏈、韌體與發布政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapir3mini"]
        cls.board_text = BOARD.read_text()

    def test_sources_are_exactly_pinned(self) -> None:
        expected = {
            'KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"',
            'BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"',
            'ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'MT76_FIRMWARE_GIT_REF_BOARD="commit:c5a3bd91aa735b669618610d5f0ebfa5786845a6"',
            'LINUX_FIRMWARE_GIT_REF_BOARD="commit:01205307636157a12c29e6a774bf83b218732050"',
        }
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        self.assertEqual(
            self.config["vendor_reference_commit"],
            "9bd78779f267a21c04c5bb4d16c32e83aae8d1d3",
        )

    def test_release_gate_remains_blocked(self) -> None:
        self.assertEqual(self.config["candidate_level"], "L1 元件候選")
        self.assertTrue(self.config["component_build_completed"])
        self.assertFalse(self.config["full_rootfs_image_built"])
        self.assertFalse(self.config["public_release_authorized"])
        self.assertFalse(self.config["hardware_validation_completed"])
        self.assertEqual(self.config["release_gate"]["status"], "blocked")
        self.assertFalse(self.config["release_gate"]["full_image_built"])
        self.assertEqual(
            set(self.config["release_gate"]["required_blockers"]),
            set(self.config["public_release_blockers"]),
        )
        obj = self.config["atf_prebuilt_objects"][
            "plat/mediatek/mt7986/drivers/dram/release/dram.o"
        ]
        self.assertFalse(obj["redistribution_authorized"])
        self.assertEqual(
            obj["sha256"],
            "45acf44f2fe576991d7c0b13862cb41d1ffd37b37e1607e27ca4ddb31820fa79",
        )
        subprocess.run([str(POLICY_CHECK)], cwd=ROOT, check=True, capture_output=True)

    def test_boot_media_requires_emmc_boot0(self) -> None:
        self.assertEqual(self.policy["candidate_boot_media"], ["emmc"])
        self.assertEqual(self.policy["supported_boot_media"], [])
        self.assertIn("sd", self.policy["unsupported_boot_media"])
        contract = self.policy["boot_media_contract"]
        self.assertEqual(contract["cold_boot_source"], "emmc_boot0")
        self.assertFalse(contract["user_area_image_is_complete_cold_boot_installer"])
        self.assertFalse(contract["sd_boot_supported"])
        self.assertEqual(self.policy["emmc_user_area_target"], "/dev/mmcblk0")
        self.assertEqual(self.policy["emmc_boot0_target"], "/dev/mmcblk0boot0")
        self.assertEqual(self.policy["emmc_boot0_payload"], "bl2.img")
        self.assertTrue(self.policy["emmc_boot0_force_ro_required"])
        self.assertEqual(self.policy["emmc_boot_partition_enable"], "1 1")
        self.assertFalse(self.policy["automatic_emmc_install_authorized"])

    def test_component_evidence_locks_all_recorded_outputs(self) -> None:
        evidence = self.config["component_build_evidence"]
        self.assertEqual(
            evidence["implementation_commit"],
            "717cdc7e91231a16d80b189f43dc6819a80fd739",
        )
        self.assertEqual(len(evidence["artifacts"]), 6)
        self.assertEqual(
            evidence["artifacts"]["linux-dtb"]["sha256"],
            self.policy["dtb_sha256"],
        )
        for name, artifact in evidence["artifacts"].items():
            with self.subTest(name=name):
                self.assertGreater(artifact["size"], 0)
                self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")

    def test_gpt_payload_and_environment_contract(self) -> None:
        self.assertEqual(
            hashlib.sha256(GPT.read_bytes()).hexdigest(),
            self.policy["gpt_template_sha256"],
        )
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["bl2.img@17408", "u-boot.fip@6815744"],
        )
        self.assertEqual(
            self.policy["required_partitions"],
            [
                "1:bl2:34:8158",
                "2:ubootenv:8192:1024",
                "3:factory:9216:4096",
                "4:fip:13312:8192",
                "5:*:32768:*",
            ],
        )
        env = self.policy["uboot_environment_contract"]
        self.assertEqual(env["partition_start_sector"], 8192)
        self.assertEqual(env["partition_sector_count"], 1024)
        self.assertEqual(env["copy_size_bytes"], 0x40000)
        self.assertEqual(env["resolved_copy_offsets_bytes"], [0x400000, 0x440000])
        self.assertEqual(
            self.policy["uboot_payload_maximum_sizes"],
            ["bl2.img=4176896", "gpt=17408", "u-boot.fip=4194304"],
        )

    def test_uboot_uses_standard_boot_and_safe_emmc(self) -> None:
        for value in (
            "post_config_uboot_target__bananapir3mini_standard_boot",
            "CONFIG_BOOTSTD_BOOTCOMMAND",
            "CONFIG_BOOTMETH_EXTLINUX",
            "CONFIG_ENV_MMC_PARTITION ubootenv",
            "CONFIG_ENV_OFFSET 0x400000",
            "CONFIG_ENV_OFFSET_REDUND 0x440000",
            "mediatek/mt7986a-bananapi-bpi-r3-mini.dtb",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        patch_text = UBOOT_PATCH.read_text()
        for value in (
            "bus-width = <8>;",
            "max-frequency = <200000000>;",
            "mmc-hs200-1_8v;",
            "no-mmc-hs400;",
            "non-removable;",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch_text)
        contract = self.policy["uboot_dtb_contract"]
        self.assertEqual(contract["storage_node"], "/mmc@11230000")
        self.assertEqual(contract["bus_width"], 8)
        self.assertEqual(contract["max_frequency"], 200000000)

    def test_linux_dtb_enables_only_hs200(self) -> None:
        patch_text = KERNEL_PATCH.read_text()
        for value in (
            "bus-width = <8>;",
            "max-frequency = <200000000>;",
            "cap-mmc-highspeed;",
            "cap-mmc-hw-reset;",
            "mmc-hs200-1_8v;",
            "no-mmc-hs400;",
            "non-removable;",
            'status = "okay";',
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch_text)
        for series in KERNEL_SERIES:
            self.assertIn(KERNEL_PATCH.name, series.read_text())
        self.assertEqual(
            self.policy["dtb_sha256"],
            "5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab",
        )

    def test_network_drivers_firmware_and_licenses_are_complete(self) -> None:
        self.assertIn("opts_m+=(AIR_EN8811H_PHY)", self.board_text)
        self.assertEqual(self.config["common_kernel_options"]["CONFIG_AIR_EN8811H_PHY"], "m")
        expected = {
            "EthMD32.DSP.bin": "3e4699ec709c836d5fce7c91bc5d205beb54aea326c4b70c7050b355784cbebd",
            "EthMD32.dm.bin": "874982b88330112c376e484cdce114cf2e1476ccbb901c87f80882f127ffb90f",
            "LICENSE.airoha": "ad548ca0ffb91ec655de0f28e13089ef1cd4e0deabb2f15a9289194990e62252",
            "SOURCE.md": "06480315ef0caa8a8ddfec7b1a01f73b5b712922d4705c85eda3719a476495aa",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256((AIROHA / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)
        firmware = self.config["installed_firmware_blobs"]
        self.assertEqual(len(firmware), 14)
        self.assertIn("/lib/firmware/airoha/EthMD32.dm.bin", firmware)
        self.assertEqual(
            len([path for path in firmware if path.startswith("/lib/firmware/mediatek/")]),
            12,
        )

    def test_dedicated_entrypoints_select_only_r3mini(self) -> None:
        for path in (BUILD, RUNNER, VERIFIER):
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertIn("bananapi-filogic-mt7986-r3mini-current.json", text)
                self.assertIn(
                    "bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli",
                    text,
                )
                self.assertIn('BOARDS="bananapir3mini"', text)
        self.assertIn("bananapi-filogic-r3mini-cache-overlay", RUNNER.read_text())
        self.assertIn("check-bananapi-filogic-r3mini-policy.sh", BUILD.read_text())
        self.assertIn("check-bananapi-filogic-r3mini-policy.sh", VERIFIER.read_text())
        self.assertIn("finalize-bananapi-filogic-r3mini-verification.sh", VERIFIER.read_text())
        self.assertIn("VERIFICATION_PRE_COMPLETE_HOOK", VERIFIER.read_text())
        self.assertIn("VERIFY_ARCHIVES=yes", VERIFIER.read_text())
        self.assertIn("write_entry_state in_progress", VERIFIER.read_text())
        self.assertIn("write_entry_state failed", VERIFIER.read_text())

    def test_finalizer_enforces_payload_bounds_and_release_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            evidence = output / "UBOOT_PAYLOAD_EVIDENCE.tsv"
            evidence.write_text(
                "board\tpayload\tplacement\toffset\tsize\tsha256\n"
                "bananapir3mini\tbl2.img\timage\t17408\t200793\t"
                + "1" * 64
                + "\nbananapir3mini\tu-boot.fip\timage\t6815744\t507953\t"
                + "2" * 64
                + "\nbananapir3mini\tgpt\tpackage-only\t-\t17408\t"
                + "3" * 64
                + "\n"
            )
            status = output / "VERIFICATION_STATUS.json.partial"
            status.write_text(json.dumps({"status": "complete", "evidence_level": "L2"}))
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
            )
            result = json.loads(status.read_text())
            self.assertFalse(result["public_release_authorized"])
            self.assertFalse(result["hardware_validation_completed"])
            self.assertEqual(result["release_gate"]["status"], "blocked")
            self.assertFalse(
                result["release_gate"][
                    "emmc_user_area_image_is_complete_cold_boot_installer"
                ]
            )

            evidence.write_text(evidence.read_text().replace("\t507953\t", "\t4194305\t"))
            rejected = subprocess.run(
                [str(FINALIZER), str(status)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
