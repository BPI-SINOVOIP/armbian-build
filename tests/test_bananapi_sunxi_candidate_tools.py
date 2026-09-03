#!/usr/bin/env python3
"""Banana Pi Sunxi 候選映像工具回歸測試。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/validation/bananapi-sunxi-a20-current.json"
H3_CONFIG = ROOT / "config/validation/bananapi-sunxi-h3-current.json"
H2PLUS_CONFIG = ROOT / "config/validation/bananapi-sunxi-h2plus-current.json"
M1PLUS_CONFIG = ROOT / "config/validation/bananapi-sunxi-a20-m1plus-current.json"
R40_CONFIG = ROOT / "config/validation/bananapi-sunxi-r40-current.json"
R40_6204_CONFIG = ROOT / "config/validation/bananapi-sunxi-r40-6204-legacy.json"
A31S_CONFIG = ROOT / "config/validation/bananapi-sunxi-a31s-current.json"
A33_CONFIG = ROOT / "config/validation/bananapi-sunxi-a33-current.json"
A83T_CONFIG = ROOT / "config/validation/bananapi-sunxi-a83t-current.json"
A64_CONFIG = ROOT / "config/validation/bananapi-sunxi-a64-current.json"
BUILD_SCRIPT = ROOT / "tools/build-bananapi-sunxi-candidates.sh"
VERIFY_SCRIPT = ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
ISOLATED_RUNNER = ROOT / "tools/run-bananapi-candidates-isolated-cache.sh"
ATF_COMPILER = ROOT / "lib/functions/compilation/atf.sh"
CRUST_COMPILER = ROOT / "lib/functions/compilation/crust.sh"
UBOOT_COMPILER = ROOT / "lib/functions/compilation/uboot.sh"
EXPECTED_BOARDS = {"bananapi", "bananapipro"}


class BananaPiSunxiCandidateToolTests(unittest.TestCase):
    """驗證 A20 代表板來源、映像同一性與開機區守門。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())

    def test_validation_config_has_exact_board_set(self) -> None:
        self.assertEqual(self.config["schema_version"], 1)
        self.assertEqual(set(self.config["boards"]), EXPECTED_BOARDS)
        for board, policy in self.config["boards"].items():
            with self.subTest(board=board):
                self.assertEqual(policy["family"], "sun7i")
                self.assertEqual(policy["uboot_tag"], "v2024.01")
                self.assertEqual(policy["uboot_offset"], 8192)
                self.assertEqual(policy["overlay_prefix"], "sun7i-a20")
                self.assertEqual(policy["sd_bus_width"], 4)
                self.assertIn("/display-engine", policy["required_status_nodes"])
                self.assertNotIn("/soc/display-engine", policy["required_status_nodes"])

    def test_board_images_include_standard_io_tools(self) -> None:
        required = set(self.config["common_packages"])
        for board in EXPECTED_BOARDS:
            suffix = "conf" if board == "bananapi" else "csc"
            text = (ROOT / f"config/boards/{board}.{suffix}").read_text()
            package_line = next(
                line for line in text.splitlines()
                if line.startswith('PACKAGE_LIST_BOARD="')
            )
            packages = set(package_line.split('"', 2)[1].split())
            self.assertTrue(required <= packages)

    def test_generic_verifier_accepts_installed_virtual_package_provider(self) -> None:
        verifier = VERIFY_SCRIPT.read_text()
        function_start = verifier.index("package_installed() {")
        function_end = verifier.index("\n}\n\nvalidate_boot_area()", function_start) + 3
        package_function = verifier[function_start:function_end]
        status = """Package: glmark2-es2-x11
Status: install ok installed
Provides: glmark2-es2,
 glmark2-es2-versioned (= 2023.01)

Package: ignored-provider
Status: deinstall ok config-files
Provides: unavailable-virtual
"""
        with tempfile.TemporaryDirectory() as temporary:
            status_path = Path(temporary) / "var/lib/dpkg/status"
            status_path.parent.mkdir(parents=True)
            status_path.write_text(status)
            for package, expected_status in (
                ("glmark2-es2-x11", 0),
                ("glmark2-es2", 0),
                ("glmark2-es2-versioned", 0),
                ("unavailable-virtual", 1),
                ("missing-package", 1),
            ):
                with self.subTest(package=package):
                    result = subprocess.run(
                        [
                            "bash",
                            "-c",
                            package_function + '\npackage_installed "$1" "$2"',
                            "package-test",
                            temporary,
                            package,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, expected_status, result.stderr)

    def test_h3_reference_policy_covers_wireless_and_header_io(self) -> None:
        config = json.loads(H3_CONFIG.read_text())
        self.assertEqual(set(config["boards"]), {"bananapim2plus"})
        policy = config["boards"]["bananapim2plus"]
        self.assertEqual(policy["family"], "sun8i")
        self.assertEqual(policy["sd_node"], "/soc/mmc@1c0f000")
        self.assertEqual(policy["default_overlays"], ["analog-codec"])
        self.assertTrue({"i2c0", "pwm", "spi-spidev", "uart2"} <= set(policy["required_overlays"]))
        board_text = (ROOT / "config/boards/bananapim2plus.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(set(config["common_packages"]) <= set(package_line.split('"', 2)[1].split()))

        for version in ("6.18", "7.1"):
            overlay_dir = ROOT / f"patch/kernel/archive/sunxi-{version}/overlay_32"
            makefile = (overlay_dir / "Makefile").read_text()
            for overlay in policy["required_overlays"]:
                filename = f"{policy['overlay_prefix']}-{overlay}"
                with self.subTest(version=version, overlay=overlay):
                    self.assertTrue((overlay_dir / f"{filename}.dtso").is_file())
                    self.assertIn(f"{filename}.dtbo", makefile)

    def test_h2plus_policy_covers_storage_wireless_and_header_io(self) -> None:
        config = json.loads(H2PLUS_CONFIG.read_text())
        self.assertEqual(set(config["boards"]), {"bananapim2zero", "bananapip2zero"})
        overlay_dir = ROOT / "patch/kernel/archive/sunxi-6.18/overlay_32"
        for board, policy in config["boards"].items():
            with self.subTest(board=board):
                self.assertEqual(policy["family"], "sun8i")
                self.assertEqual(policy["uboot_tag"], "v2026.07")
                self.assertEqual(policy["sd_bus_width"], 4)
                self.assertIn("/soc/mmc@1c10000=4", policy["additional_bus_widths"])
                board_text = next((ROOT / "config/boards").glob(f"{board}.*")).read_text()
                package_line = next(
                    line for line in board_text.splitlines()
                    if line.startswith('PACKAGE_LIST_BOARD="')
                )
                self.assertTrue(
                    set(config["common_packages"])
                    <= set(package_line.split('"', 2)[1].split())
                )
                for overlay in policy["required_overlays"]:
                    self.assertTrue(
                        (overlay_dir / f"{policy['overlay_prefix']}-{overlay}.dtso").is_file()
                    )
        self.assertIn(
            "/soc/mmc@1c11000=8",
            config["boards"]["bananapip2zero"]["additional_bus_widths"],
        )

        p2_zero_patch = (
            ROOT
            / "patch/u-boot/v2026.07-sunxi/board_bananapip2zero"
            / "0001-sunxi-add-bananapi-p2-zero.patch"
        )
        self.assertTrue(p2_zero_patch.is_file())
        patch_text = p2_zero_patch.read_text()
        self.assertIn("configs/bananapi_p2_zero_defconfig", patch_text)
        self.assertIn("sun8i-h2-plus-bananapi-p2-zero.dts", patch_text)

    def test_m1plus_policy_pins_sources_wireless_and_header_io(self) -> None:
        config = json.loads(M1PLUS_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(config["kernel_family"], "sunxi")
        self.assertEqual(
            config["linux_commit"],
            "1f99e9ab748fc5c32120de9c4eca31abfe54a4d5",
        )
        self.assertEqual(set(config["boards"]), {"bananapim1plus"})
        policy = config["boards"]["bananapim1plus"]
        self.assertEqual(policy["family"], "sun7i")
        self.assertEqual(policy["sd_node"], "/soc/mmc@1c0f000")
        self.assertIn("/soc/mmc@1c12000=4", policy["additional_bus_widths"])
        self.assertEqual(
            policy["uboot_revision"],
            "866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e",
        )
        self.assertEqual(
            set(config["installed_firmware_blobs"]),
            {
                "/lib/firmware/brcm/brcmfmac43362-sdio.bin",
                "/lib/firmware/brcm/brcmfmac43362-sdio.txt",
            },
        )
        self.assertTrue(
            {"can", "i2c2", "i2c3", "i2s0", "spi-spidev", "uart7"}
            <= set(policy["required_overlays"])
        )
        board_text = (ROOT / "config/boards/bananapim1plus.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )
        self.assertIn(
            'KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"',
            board_text,
        )
        self.assertIn(
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            board_text,
        )
        self.assertNotIn("CONFIG_DRAM_CLK", board_text)

        for version in ("6.18", "7.1"):
            overlay_dir = ROOT / f"patch/kernel/archive/sunxi-{version}/overlay_32"
            makefile = (overlay_dir / "Makefile").read_text()
            for overlay in policy["required_overlays"]:
                filename = f"{policy['overlay_prefix']}-{overlay}"
                with self.subTest(version=version, overlay=overlay):
                    self.assertTrue((overlay_dir / f"{filename}.dtso").is_file())
                    self.assertIn(f"{filename}.dtbo", makefile)

    def test_r40_policy_pins_sources_wireless_storage_and_header_io(self) -> None:
        config = json.loads(R40_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(config["kernel_family"], "sunxi")
        self.assertEqual(
            config["linux_commit"],
            "1f99e9ab748fc5c32120de9c4eca31abfe54a4d5",
        )
        self.assertEqual(
            config["firmware_commit"],
            "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        )
        self.assertEqual(
            set(config["boards"]),
            {"bananapim2berry", "bananapim2ultra"},
        )
        self.assertEqual(
            set(config["installed_firmware_blobs"]),
            {
                "/lib/firmware/brcm/brcmfmac43430-sdio.bin",
                "/lib/firmware/brcm/brcmfmac43430-sdio.txt",
                "/lib/firmware/brcm/brcmfmac43430-sdio.clm_blob",
                "/lib/firmware/brcm/BCM43430A1.hcd",
            },
        )
        required_overlays = {
            "i2c2",
            "i2c3",
            "spi-spidev0",
            "spi-spidev1",
            "uart2",
            "uart4",
            "uart5",
            "uart7",
        }
        for board, policy in config["boards"].items():
            with self.subTest(board=board):
                self.assertEqual(policy["family"], "sun8i")
                self.assertEqual(policy["overlay_prefix"], "sun8i-r40")
                self.assertEqual(policy["sd_node"], "/soc/mmc@1c0f000")
                self.assertEqual(policy["sd_bus_width"], 4)
                self.assertEqual(set(policy["required_overlays"]), required_overlays)
                self.assertEqual(
                    policy["uboot_revision"],
                    "866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e",
                )
                board_text = next((ROOT / "config/boards").glob(f"{board}.*")).read_text()
                package_line = next(
                    line for line in board_text.splitlines()
                    if line.startswith('PACKAGE_LIST_BOARD="')
                )
                self.assertTrue(
                    set(config["common_packages"])
                    <= set(package_line.split('"', 2)[1].split())
                )
                self.assertIn(
                    'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
                    board_text,
                )
                self.assertNotIn("CONFIG_DRAM_CLK", board_text)

        berry = config["boards"]["bananapim2berry"]
        ultra = config["boards"]["bananapim2ultra"]
        self.assertEqual(
            berry["dtb"],
            "allwinner/sun8i-v40-bananapi-m2-berry.dtb",
        )
        self.assertEqual(
            ultra["dtb"],
            "allwinner/sun8i-r40-bananapi-m2-ultra.dtb",
        )
        self.assertNotIn("/soc/mmc@1c11000=8", berry["additional_bus_widths"])
        self.assertIn("/soc/mmc@1c11000=8", ultra["additional_bus_widths"])

        for version in ("6.18", "7.1"):
            overlay_dir = ROOT / f"patch/kernel/archive/sunxi-{version}/overlay_32"
            makefile = (overlay_dir / "Makefile").read_text()
            for overlay in required_overlays:
                filename = f"sun8i-r40-{overlay}"
                with self.subTest(version=version, overlay=overlay):
                    self.assertTrue((overlay_dir / f"{filename}.dtso").is_file())
                    self.assertIn(f"{filename}.dtbo", makefile)

    def test_6204_legacy_policy_covers_industrial_io_and_boot_layout(self) -> None:
        config = json.loads(R40_6204_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "legacy")
        self.assertEqual(config["kernel_family"], "sunxi")
        self.assertEqual(
            config["linux_commit"],
            "2538fbeff8a94ee2b54eb09d92209e24a1e650d4",
        )
        self.assertEqual(set(config["boards"]), {"bananapi6204"})
        policy = config["boards"]["bananapi6204"]
        self.assertEqual(policy["partition_table"], "msdos")
        self.assertEqual(policy["partition_start_sector"], 8192)
        self.assertEqual(
            policy["dtb_sha256"],
            "266250249d06a6d217b20d13663b33af6b831d69b9dcd77247d1ea58e5554e11",
        )
        self.assertEqual(policy["uboot_target_index"], 1)
        self.assertEqual(policy["sd_node"], "/soc/mmc@1c0f000")
        self.assertTrue(
            {"/soc/mmc@1c10000=4", "/soc/mmc@1c11000=8"}
            <= set(policy["additional_bus_widths"])
        )
        self.assertTrue(
            {
                "/soc/spi@1c06000/can@1",
                "/soc/phy@1c13400",
                "/soc/serial@1c28000",
                "/soc/serial@1c28c00",
                "/soc/serial@1c29000",
                "/soc/serial@1c29400",
                "/soc/serial@1c29c00",
            }
            <= set(policy["required_status_nodes"])
        )
        board_text = (ROOT / "config/boards/bananapi6204.wip").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )

    def test_6204_patch_keeps_the_complete_dts_hunk(self) -> None:
        patch = (
            ROOT
            / "patch/kernel/archive/sunxi-6.12/patches.armbian/arm-dts-sun8i-r40-add-bpi-6204.patch"
        ).read_text()
        hunk = patch.split("@@ -0,0 +1,378 @@\n", 1)[1].split("-- \n", 1)[0]
        additions = [line for line in hunk.splitlines() if line.startswith("+")]
        self.assertEqual(len(additions), 378)
        self.assertIn('+\tpinctrl-0 = <&spi1_pi_pins>, <&spi1_cs1_pi_pin>;', patch)
        self.assertIn("+&uart7 {", patch)
        self.assertIn("+&usbphy {", patch)

    def test_partition_table_gate_normalizes_dos_alias(self) -> None:
        text = VERIFY_SCRIPT.read_text()
        self.assertIn("normalize_partition_table", text)
        self.assertIn("dos | msdos) printf 'msdos\\n'", text)

    def test_verifier_supports_bounded_evidence_level_override(self) -> None:
        text = VERIFY_SCRIPT.read_text()
        self.assertIn(
            'verification_evidence_level="${VERIFICATION_EVIDENCE_LEVEL:-L2}"',
            text,
        )
        self.assertIn("L1 | L2)", text)
        self.assertIn('"${board}" "${verification_evidence_level}"', text)

    def test_builder_supports_bounded_board_output_name_override(self) -> None:
        text = BUILD_SCRIPT.read_text()
        self.assertIn(
            'output_image_prefix_effective="${output_image_prefix:-Armbian-*_}"',
            text,
        )
        self.assertIn(
            'output_image_board_token_effective="${output_image_board_token:-${board}}"',
            text,
        )
        self.assertIn('-iname "${output_image_glob}"', text)
        self.assertIn(
            '"${output_image_prefix_effective}" =~ ^[[:alnum:]._*+-]+$', text
        )
        self.assertIn(
            '"${output_image_board_token_effective}" =~ ^[[:alnum:].+-]+$', text
        )

    def test_a31s_policy_limits_claims_to_mainline_dtb(self) -> None:
        config = json.loads(A31S_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(config["kernel_family"], "sunxi")
        self.assertEqual(set(config["boards"]), {"bananapim2"})
        self.assertEqual(
            config["firmware_commit"],
            "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        )
        self.assertEqual(
            set(config["installed_firmware_blobs"]),
            {
                "/lib/firmware/brcm/brcmfmac43362-sdio.bin",
                "/lib/firmware/brcm/brcmfmac43362-sdio.txt",
            },
        )
        policy = config["boards"]["bananapim2"]
        self.assertEqual(policy["family"], "sun6i")
        self.assertEqual(
            policy["dtb"],
            "allwinner/sun6i-a31s-sinovoip-bpi-m2.dtb",
        )
        self.assertEqual(policy["overlay_prefix"], "sun6i-a31s")
        self.assertEqual(policy["required_overlays"], [])
        self.assertIn("/soc/mmc@1c11000=4", policy["additional_bus_widths"])
        self.assertNotIn("CONFIG_USB_GADGET", config["common_kernel_options"])
        self.assertNotIn("CONFIG_DRM_LIMA", config["common_kernel_options"])

        board_text = (ROOT / "config/boards/bananapim2.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )
        self.assertIn(
            'KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"',
            board_text,
        )
        self.assertIn(
            'BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"',
            board_text,
        )
        self.assertNotIn("CONFIG_DRAM_CLK", board_text)

    def test_a33_policy_covers_storage_otg_wireless_and_acceleration(self) -> None:
        config = json.loads(A33_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(set(config["boards"]), {"bananapim2magic"})
        policy = config["boards"]["bananapim2magic"]
        self.assertEqual(policy["dtb"], "allwinner/sun8i-r16-bananapi-m2m.dtb")
        self.assertEqual(policy["overlay_prefix"], "sun8i-a33")
        self.assertEqual(policy["required_overlays"], [])
        self.assertTrue(
            {"/soc/mmc@1c10000=4", "/soc/mmc@1c11000=8"}
            <= set(policy["additional_bus_widths"])
        )
        self.assertTrue(
            {
                "/soc/crypto-engine@1c15000",
                "/soc/gpu@1c40000",
                "/soc/video-codec@1c0e000",
            }
            <= set(policy["required_present_nodes"])
        )
        self.assertNotIn("/display-engine", policy["required_status_nodes"])
        self.assertEqual(
            set(config["installed_firmware_blobs"]),
            {
                "/lib/firmware/brcm/brcmfmac43430-sdio.bin",
                "/lib/firmware/brcm/brcmfmac43430-sdio.txt",
                "/lib/firmware/brcm/brcmfmac43430-sdio.clm_blob",
                "/lib/firmware/brcm/BCM43430A1.hcd",
            },
        )

        board_text = (ROOT / "config/boards/bananapim2magic.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )
        self.assertIn(
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            board_text,
        )
        self.assertNotIn("CONFIG_DRAM_CLK", board_text)

    def test_a83t_policy_covers_m3_storage_network_hdmi_and_video(self) -> None:
        config = json.loads(A83T_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(set(config["boards"]), {"bananapim3"})
        policy = config["boards"]["bananapim3"]
        self.assertEqual(policy["dtb"], "allwinner/sun8i-a83t-bananapi-m3.dtb")
        self.assertEqual(policy["overlay_prefix"], "sun8i-a83t")
        self.assertEqual(policy["required_overlays"], [])
        self.assertTrue(
            {"/soc/mmc@1c10000=4", "/soc/mmc@1c11000=8"}
            <= set(policy["additional_bus_widths"])
        )
        self.assertTrue(
            {
                "/display-engine",
                "/soc/ethernet@1c30000",
                "/soc/hdmi@1ee0000",
            }
            <= set(policy["required_status_nodes"])
        )
        self.assertIn(
            "/soc/video-codec@01c0e000",
            policy["required_present_nodes"],
        )
        self.assertNotIn("/soc/gpu@1c40000", policy["required_present_nodes"])
        self.assertEqual(
            set(config["installed_firmware_blobs"]),
            {
                "/lib/firmware/brcm/brcmfmac43430-sdio.bin",
                "/lib/firmware/brcm/brcmfmac43430-sdio.txt",
                "/lib/firmware/brcm/brcmfmac43430-sdio.clm_blob",
                "/lib/firmware/brcm/BCM43430A1.hcd",
            },
        )

        board_text = (ROOT / "config/boards/bananapim3.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )
        self.assertNotIn("BOOTPATCHDIR", board_text)
        self.assertEqual(policy["uboot_tag"], "v2026.07")
        self.assertEqual(
            policy["uboot_revision"],
            "ece349ade2973e220f524ce59e59711cc919263f",
        )
        self.assertIn(
            'BOOTBRANCH_BOARD="commit:ece349ade2973e220f524ce59e59711cc919263f"',
            board_text,
        )
        self.assertIn(
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            board_text,
        )
        self.assertNotIn("CONFIG_DRAM_CLK", board_text)

    def test_a64_policy_covers_full_firmware_chain_and_m64_io(self) -> None:
        config = json.loads(A64_CONFIG.read_text())
        self.assertEqual(config["candidate_branch"], "current")
        self.assertEqual(config["kernel_family"], "sunxi64")
        self.assertEqual(set(config["boards"]), {"bananapim64"})
        policy = config["boards"]["bananapim64"]
        self.assertEqual(policy["dtb"], "allwinner/sun50i-a64-bananapi-m64.dtb")
        self.assertEqual(policy["overlay_prefix"], "sun50i-a64")
        self.assertTrue(
            {"i2c1", "spi-spidev", "uart4", "w1-gpio"}
            <= set(policy["required_overlays"])
        )
        self.assertNotIn("uart1", policy["required_overlays"])
        self.assertTrue(
            {"atf_git_source", "atf_git_ref", "atf_revision",
             "crust_git_source", "crust_git_ref", "crust_revision"}
            <= set(policy)
        )
        self.assertEqual(
            policy["atf_revision"],
            "c2a0e7080d64d69940be4ad0ff6578501f3cbf9e",
        )
        self.assertEqual(
            policy["crust_revision"],
            "ffe9f1ac9c675e6e67db9084bd19fbdeffd8e162",
        )
        self.assertIn(
            "/soc/usb@1c19000:dr_mode=otg",
            policy["required_string_properties"],
        )
        self.assertTrue(
            {"/soc/crypto@1c15000", "/soc/gpu@1c40000",
             "/soc/video-codec@1c0e000"}
            <= set(policy["required_present_nodes"])
        )

        board_text = (ROOT / "config/boards/bananapim64.csc").read_text()
        package_line = next(
            line for line in board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        self.assertTrue(
            set(config["common_packages"])
            <= set(package_line.split('"', 2)[1].split())
        )
        self.assertNotIn("CONFIG_DRAM_CLK", board_text)

    def test_build_tool_records_reproducibility_evidence(self) -> None:
        text = BUILD_SCRIPT.read_text()
        for required in (
            "status --porcelain --untracked-files=all",
            "validate_default_userpatches",
            "cache 不是 OverlayFS",
            "CANDIDATE_LOCK_FILE",
            "source_commit",
            "source_tree",
            "validation_config_sha256",
            "build_parameters_sha256",
            "decompressed_sha256",
            "ARTIFACT_IGNORE_CACHE",
            "CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust",
            "hard_minimum_free_gib=40",
            "candidates_sha256",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_l2_verifier_closes_commit_archive_and_status_boundaries(self) -> None:
        text = VERIFY_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "L2 驗證不得停用 XZ 串流同一性檢查",
            'candidate_source_commit}" == "${verifier_commit',
            'build_validation_config_sha256}" == "${verification_config_sha256',
            "candidates_sha256",
            "candidate_matrix_sha256",
            "write_verification_state in_progress",
            "write_verification_state failed",
            "禁止沿用舊成功狀態",
            "VERIFICATION_PRE_COMPLETE_HOOK",
            "VERIFICATION_EXTRA_STATUS_JSON",
            "VERIFICATION_DEFER_STATUS_PROMOTION",
            "assert_verifier_identity",
            "驗證期間來源 HEAD 已改變",
            "驗證期間來源 tree 已改變",
            "驗證期間來源工作樹已改變",
            "驗證期間 validation 已改變",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_final_identity_guard_rechecks_validation_atomically(self) -> None:
        text = VERIFY_SCRIPT.read_text(encoding="utf-8")
        start = text.index("assert_verifier_identity() {")
        end = text.index("\n}\n", start) + 3
        helper = text[start:end]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            repository = temporary / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "source").write_text("固定來源\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "source"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(repository), "-c", "user.name=測試",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "固定來源",
                ],
                check=True,
            )
            validation = temporary / "validation.json"
            validation.write_text('{"固定":true}\n', encoding="utf-8")
            script = (
                'fail() { echo "$*" >&2; exit 1; }\n'
                + helper
                + '\nrepo_dir="$1"\nvalidation_config="$2"\n'
                + 'verifier_commit="$(git -C "$repo_dir" rev-parse HEAD)"\n'
                + 'verifier_tree="$(git -C "$repo_dir" rev-parse HEAD^{tree})"\n'
                + 'verification_config_sha256="$(sha256sum "$validation_config" | cut -d" " -f1)"\n'
                + 'printf \'{"固定":false}\\n\' >"$validation_config"\n'
                + "assert_verifier_identity\n"
            )
            rejected = subprocess.run(
                ["bash", "-c", script, "原子核對", str(repository), str(validation)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("validation 已改變", rejected.stderr)

    def test_verifier_supports_m6_partition_boot_and_overlap_contracts(self) -> None:
        text = VERIFY_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "required_partition_types",
            "boot_partition_label",
            "root_partition_label",
            "root_partition_filesystem_type",
            "separate_fat_armbian_env",
            "rootdev=UUID=${root_uuid}",
            "dumpimage -T script -p 0",
            "boot_script_source_sha256",
            "payload_overlap_policy",
            "payload_write_order",
            "image-controlled-overlap",
            "先寫 payload 前段",
            "後寫 payload 不符",
            "先寫 payload 尾段",
            "uboot_payload_sizes",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertIn("lsblk -dnro RO", text)

    def test_verifier_replaces_stale_success_when_required_input_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory)
            status = output / "VERIFICATION_STATUS.json"
            status.write_text(
                json.dumps({"status": "complete", "evidence_level": "L2"}),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output)
            result = subprocess.run(
                [str(VERIFY_SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(status.read_text(encoding="utf-8"))["status"], "failed")

    def test_l2_runtime_gate_rejects_disabled_archive_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as directory:
            output = Path(directory)
            (output / "CANDIDATES.tsv").write_text("不完整候選\n", encoding="utf-8")
            (output / "COMPLETION_STATUS.json").write_text(
                json.dumps({"status": "complete"}), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment.update({"OUTPUT_DIR": str(output), "VERIFY_ARCHIVES": "no"})
            result = subprocess.run(
                [str(VERIFY_SCRIPT)],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("不得停用 XZ", result.stderr)
            status = json.loads((output / "VERIFICATION_STATUS.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")

    def test_optional_firmware_and_kernel_module_gates_are_machine_checked(self) -> None:
        build_text = BUILD_SCRIPT.read_text()
        verify_text = VERIFY_SCRIPT.read_text()
        for required in (
            "verify_firmware_source_resolution",
            "firmware_git_source",
            "firmware_git_ref",
            "firmware_revision",
            "validate_firmware_source_log",
            "firmware_runtime_sources_sha256",
            "validate_firmware_runtime_sources_log",
            "Fetching SHA1 of 'commit'",
            "armbian-firmware-git ${firmware_revision}",
        ):
            with self.subTest(required=required):
                self.assertIn(required, build_text)
                self.assertIn(required, verify_text)
        self.assertIn("required_kernel_module_paths", verify_text)
        self.assertIn("module_matches", verify_text)

    def test_top_level_json_booleans_are_normalized_for_shell_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.json"
            config_path.write_text(
                json.dumps({"enabled": True, "disabled": False}),
                encoding="utf-8",
            )
            for script in (BUILD_SCRIPT, VERIFY_SCRIPT):
                text = script.read_text()
                start = text.index("top_field_optional() {")
                end = text.index("\n}\n\n", start) + 3
                helper = text[start:end]
                for key, expected in (("enabled", "true"), ("disabled", "false")):
                    with self.subTest(script=script.name, key=key):
                        result = subprocess.run(
                            [
                                "bash",
                                "-c",
                                'validation_config="$1"\n' + helper
                                + '\ntop_field_optional "$2"',
                                "boolean-test",
                                str(config_path),
                                key,
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.strip(), expected)

    def test_64_bit_sunxi_firmware_chain_is_traceable(self) -> None:
        build_text = BUILD_SCRIPT.read_text()
        verify_text = VERIFY_SCRIPT.read_text()
        atf_text = ATF_COMPILER.read_text()
        crust_text = CRUST_COMPILER.read_text()
        uboot_text = UBOOT_COMPILER.read_text()

        self.assertIn('declare -g ATF_GIT_REVISION="${atf_git_head}"', atf_text)
        self.assertIn('declare -g CRUST_GIT_REVISION="${crust_git_head}"', crust_text)
        for component in ("ATF", "CRUST"):
            with self.subTest(component=component):
                self.assertIn(f"UBOOT_{component}_GIT_SOURCE", uboot_text)
                self.assertIn(f"UBOOT_{component}_GIT_BRANCH", uboot_text)
                self.assertIn(f"UBOOT_{component}_GIT_REVISION", uboot_text)
        for field in (
            "atf_git_source",
            "atf_git_ref",
            "atf_revision",
            "crust_git_source",
            "crust_git_ref",
            "crust_revision",
        ):
            with self.subTest(field=field):
                self.assertIn(field, build_text)
                self.assertIn(field, verify_text)

    def test_verifier_checks_sunxi_boot_layout_read_only(self) -> None:
        text = VERIFY_SCRIPT.read_text()
        for required in (
            "--read-only",
            "mount -o ro,noload",
            "uboot_offset",
            'cmp --silent --ignore-initial="0:${offset}"',
            "u-boot-sunxi-with-spl.bin",
            "fdtfile=",
            "dtb_basename",
            "overlay_prefix=",
            "overlay_directory",
            "required_overlays",
            "additional_bus_widths",
            "sd_bus_width",
            "required_status_nodes",
            "required_present_nodes",
            "common_kernel_options",
            "candidate_source_commit",
            "verifier_commit",
            "build_validation_config_sha256",
            "verification_config_sha256",
            "kernel_family",
            "xz -dc",
            "分割區數量不符",
            "final_kernel_config_sha256",
            "final_uboot_config_sha256",
            "FINAL_CONFIG_EVIDENCE.tsv",
            "uboot_target_make_forbidden",
            "forbidden_packaged_assets",
            "唯一核心設定內容",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text if required != "u-boot-sunxi-with-spl.bin" else CONFIG.read_text())

    def test_isolated_runner_accepts_a_family_builder(self) -> None:
        text = ISOLATED_RUNNER.read_text()
        self.assertIn("CANDIDATE_BUILDER", text)
        self.assertIn('"${candidate_builder}" "$@"', text)

    def test_build_tool_rejects_non_reference_release(self) -> None:
        environment = os.environ.copy()
        environment["RELEASE"] = "jammy"
        with tempfile.TemporaryDirectory() as output_dir:
            environment["OUTPUT_DIR"] = output_dir
            result = subprocess.run(
                ["bash", str(BUILD_SCRIPT)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("只接受 RELEASE=trixie", result.stderr)


if __name__ == "__main__":
    unittest.main()
