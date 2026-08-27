#!/usr/bin/env python3
"""Banana Pi R4 Pro 8X 內部 SD 候選政策回歸測試。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-filogic-mt7988-r4pro-current.json"
BOARD = ROOT / "config/boards/bananapir4pro.wip"
KERNEL_CONFIG = ROOT / "config/kernel/linux-filogic-current.config"
UBOOT_PATCH = ROOT / "patch/u-boot/u-boot-filogic/455-add-bpi-r4-pro-8x.patch"
POLICY_CHECKER = ROOT / "tools/check-bananapi-filogic-r4pro-policy.py"
BUILDER = ROOT / "tools/build-bananapi-filogic-r4pro-candidate.sh"
VERIFIER = ROOT / "tools/verify-bananapi-filogic-r4pro-candidate.sh"
RUNNER = ROOT / "tools/run-bananapi-filogic-r4pro-candidate-isolated-cache.sh"
GENERIC_VERIFIER = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
MT7988_FIRMWARE = ROOT / "packages/blobs/filogic/firmware/mediatek/mt7988"
POLICY_DOC = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/"
    "D-filogic-r4pro-internal-candidate-policy-20260827.md"
)


class BananaPiFilogicR4ProCandidateTests(unittest.TestCase):
    """防止 R4 Pro SD 啟動、來源、韌體與授權政策退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text(encoding="utf-8"))
        cls.policy = cls.config["boards"]["bananapir4pro"]
        cls.board_text = BOARD.read_text(encoding="utf-8")

    def test_all_six_sources_are_exactly_pinned(self) -> None:
        expected = {
            "linux_commit": "20fb2a966dcea69df6987463ae1fe1c67cff36b6",
            "firmware_commit": "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            "mt76_firmware_commit": "c5a3bd91aa735b669618610d5f0ebfa5786845a6",
            "linux_firmware_commit": "01205307636157a12c29e6a774bf83b218732050",
        }
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertEqual(self.config[field], value)
                self.assertIn(value, self.board_text)
        self.assertEqual(
            self.policy["uboot_revision"],
            "34820924edbc4ec7803eb89d9852f4b870fa760a",
        )
        self.assertEqual(
            self.policy["atf_revision"],
            "c34e37802efaea356991a0811c8fc50f8a810f5b",
        )
        self.assertIn(self.policy["uboot_revision"], self.board_text)
        self.assertIn(self.policy["atf_revision"], self.board_text)

    def test_candidate_is_internal_experimental_and_sd_only(self) -> None:
        self.assertEqual(self.config["kernel_version"], "6.19.0-rc1")
        self.assertEqual(self.config["candidate_scope"], "僅限內部驗證")
        self.assertIs(self.config["public_distribution_approved"], False)
        self.assertEqual(self.config["supported_boot_media"], ["sd"])
        self.assertEqual(
            set(self.config["excluded_boot_media"]),
            {"emmc", "spi-nand", "spi-nor", "nvme", "usb"},
        )
        self.assertEqual(
            self.policy["uboot_defconfig"],
            "mt7988a_bananapi_bpi-r4-pro-8x-sdmmc_defconfig",
        )
        self.assertEqual(
            self.policy["dtb"],
            "mediatek/mt7988a-bananapi-bpi-r4-pro-8x-sd.dtb",
        )
        self.assertNotIn("FILOGIC_BOOT_DEVICE=\"emmc\"", self.board_text)
        self.assertNotIn("FILOGIC_BOOT_DEVICE=\"snand\"", self.board_text)

    def test_uboot_uses_bootstd_and_named_redundant_environment(self) -> None:
        for value in (
            "post_config_uboot_target__bananapir4pro_sd_standard_boot",
            "scripts/config --disable CONFIG_USE_DEFAULT_ENV_FILE",
            "scripts/config --disable CONFIG_AUTOBOOT_KEYED",
            "scripts/config --disable CONFIG_ENV_IS_IN_UBI",
            "scripts/config --disable CONFIG_SUPPORT_EMMC_BOOT",
            "scripts/config --disable CONFIG_MTD_SPI_NAND",
            "scripts/config --disable CONFIG_CMD_UBI",
            "CONFIG_ENV_MMC_PARTITION ubootenv",
            "CONFIG_ENV_OFFSET 0x400000",
            "CONFIG_ENV_OFFSET_REDUND 0x440000",
            "mediatek/mt7988a-bananapi-bpi-r4-pro-8x-sd.dtb",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.board_text)
        required = self.policy["uboot_required_config_options"]
        self.assertIn("CONFIG_BOOTSTD_BOOTCOMMAND=y", required)
        self.assertIn("CONFIG_ENV_MMC_PARTITION=\"ubootenv\"", required)
        self.assertIn("# CONFIG_SUPPORT_EMMC_BOOT is not set", required)
        self.assertIn("# CONFIG_MTD_SPI_NAND is not set", required)
        self.assertIn("# CONFIG_CMD_UBI is not set", required)
        self.assertIn("# CONFIG_USE_DEFAULT_ENV_FILE is not set", required)
        for forbidden in (
            "root=/dev/fit0",
            "emmc_write_bl2=",
            "ubi_init=",
        ):
            self.assertIn(forbidden, self.policy["uboot_forbidden_binary_strings"])

    def test_gpt_payload_and_merged_dtb_contract(self) -> None:
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
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["bl2.img@17408", "u-boot.fip@6815744"],
        )
        self.assertEqual(
            self.policy["dtb_components"],
            [
                "mediatek/mt7988a-bananapi-bpi-r4-pro-8x.dtb",
                "mediatek/mt7988a-bananapi-bpi-r4-pro-sd.dtbo",
            ],
        )
        self.assertEqual(self.policy["sd_node"], "/soc/mmc@11230000")
        self.assertIn(
            "/soc/mmc@11230000:max-frequency=48000000",
            self.policy["required_uint_properties"],
        )
        self.assertEqual(self.policy["default_overlays"], [])
        self.assertEqual(self.policy["required_overlays"], [])

    def test_mt7996_and_mt7988_firmware_contract_is_complete(self) -> None:
        blobs = self.config["installed_firmware_blobs"]
        mt7996 = [path for path in blobs if "/mediatek/mt7996/" in path]
        mt7988 = [path for path in blobs if "/mediatek/mt7988/" in path]
        self.assertEqual(len(mt7996), 11)
        self.assertEqual(len(mt7988), 3)
        self.assertTrue(all(len(digest) == 64 for digest in blobs.values()))
        for path in mt7996 + mt7988:
            self.assertIn(Path(path).name, self.board_text)
        for value in (
            "mt76-firmware.LICENSE",
            "linux-firmware.LICENCE.mediatek",
            "mt7988-firmware-SOURCE.md",
        ):
            self.assertIn(value, self.board_text)

    def test_vendored_mt7988_firmware_hashes_are_fixed(self) -> None:
        expected = {
            "i2p5ge-phy-pmb.bin": "643157e984732eccad6aa5e1f80a2be82a6cbf747aac25b54c75eefeccaf8aec",
            "mt7988_wo_0.bin": "a00b95235a9baa850fe5e9c08562b54279bb5528abad207de6f2e649a8009b15",
            "mt7988_wo_1.bin": "6d9123b4e8400f93fc40cfe1adcfe67c5a2e9d7c07c168ca05f0eba739e8d39f",
            "LICENCE.mediatek": "a90d3f66704d85889945fec5525ea77622549da83aced1aac99828383f8f1805",
            "SOURCE.md": "a42e86e57a4671ea5f9c28a7bf18c62218db52eef83f28de2ce4fe204140ace3",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256(
                    (MT7988_FIRMWARE / filename).read_bytes()
                ).hexdigest()
                self.assertEqual(actual, digest)

    def test_kernel_covers_r4pro_network_storage_and_io(self) -> None:
        kernel_text = KERNEL_CONFIG.read_text(encoding="utf-8")
        combined = kernel_text + "\n" + self.board_text
        for option, value in self.config["common_kernel_options"].items():
            symbol = option.removeprefix("CONFIG_")
            with self.subTest(option=option):
                self.assertTrue(
                    f"{option}={value}" in kernel_text
                    or f"{symbol})" in self.board_text
                    or f"{symbol} " in self.board_text,
                    f"缺少核心選項 {option}={value}",
                )
        for symbol in ("MT7996E", "MEDIATEK_2P5GE_PHY", "NET_MEDIATEK_SOC_WED"):
            self.assertIn(symbol, combined)

    def test_atf_prebuilt_objects_keep_publication_blocked(self) -> None:
        self.assertEqual(
            self.config["atf_prebuilt_object_license_status"], "未釐清"
        )
        self.assertEqual(len(self.config["atf_prebuilt_objects"]), 3)
        self.assertGreaterEqual(len(self.config["public_distribution_blockers"]), 3)
        policy_text = POLICY_DOC.read_text(encoding="utf-8")
        for path, digest in self.config["atf_prebuilt_objects"].items():
            self.assertIn(path, policy_text)
            self.assertIn(digest, policy_text)
        self.assertIn("不得核准公開散布", policy_text)

    def test_policy_checker_accepts_only_the_guarded_contract(self) -> None:
        passed = subprocess.run(
            [str(POLICY_CHECKER), str(CONFIG)],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("政策檢查通過", passed.stdout)

        mutated = json.loads(json.dumps(self.config))
        mutated["public_distribution_approved"] = True
        with tempfile.TemporaryDirectory(prefix="r4pro-policy-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            rejected = subprocess.run(
                [str(POLICY_CHECKER), str(path)],
                check=False,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("不得核准公開散布", rejected.stderr)

        mutated = json.loads(json.dumps(self.config))
        mutated["boards"]["bananapir4pro"][
            "uboot_required_config_options"
        ].remove("# CONFIG_SUPPORT_EMMC_BOOT is not set")
        with tempfile.TemporaryDirectory(prefix="r4pro-policy-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            rejected = subprocess.run(
                [str(POLICY_CHECKER), str(path)],
                check=False,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("未完整停用", rejected.stderr)

    def test_official_uboot_patch_provenance_is_locked(self) -> None:
        digest = hashlib.sha256(UBOOT_PATCH.read_bytes()).hexdigest()
        self.assertEqual(digest, self.config["uboot_board_patch_local_sha256"])
        self.assertEqual(
            self.config["uboot_board_patch_source_commit"],
            "56e0e77adad258ba05782fee8f94f00d17b0b991",
        )
        patch_text = UBOOT_PATCH.read_text(encoding="utf-8")
        for value in ("<&pio 13", "<&pio 14", "&eth0", "&pio {"):
            self.assertIn(value, patch_text)

    def test_dedicated_entrypoints_are_r4pro_only(self) -> None:
        expected_config = "bananapi-filogic-mt7988-r4pro-current.json"
        expected_output = "bananapi-filogic-mt7988-r4pro-trixie-current-cli"
        for path in (BUILDER, VERIFIER):
            text = path.read_text(encoding="utf-8")
            self.assertIn(expected_config, text)
            self.assertIn(expected_output, text)
            self.assertIn('BOARDS="bananapir4pro"', text)
            self.assertIn("check-bananapi-filogic-r4pro-policy.py", text)
        runner_text = RUNNER.read_text(encoding="utf-8")
        self.assertIn(BUILDER.name, runner_text)
        self.assertIn("bananapi-filogic-r4pro-cache-overlay", runner_text)
        self.assertNotIn(GENERIC_VERIFIER.name, RUNNER.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
