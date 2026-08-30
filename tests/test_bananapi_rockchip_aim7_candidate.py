#!/usr/bin/env python3
"""Banana Pi AIM7 RK3588 vendor 候選來源與契約回歸測試。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "config/boards/bananapiaim7.wip"
CONFIG = ROOT / "config/validation/bananapi-rockchip-rk3588-aim7-vendor.json"
LINUX_DTS = ROOT / "patch/kernel/rk35xx-vendor-6.1/dt/rk3588-bananapi-aim7.dts"
UBOOT_DTS = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/dt/rk3588-bananapi-aim7.dts"
)
UBOOT_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/bananapi-aim7-rk3588_defconfig"
)
ARMSOM_DEFCONFIG = (
    ROOT
    / "patch/u-boot/legacy/u-boot-radxa-rk35xx/defconfig/armsom-aim7-io-rk3588_defconfig"
)
BUILD = ROOT / "tools/build-bananapi-rockchip-aim7-candidate.sh"
VERIFY = ROOT / "tools/verify-bananapi-rockchip-aim7-candidate.sh"
COMPONENT_VERIFY = ROOT / "tools/verify-bananapi-rockchip-aim7-components.sh"
ISOLATED = ROOT / "tools/run-bananapi-rockchip-aim7-candidate-isolated-cache.sh"
POLICY_CHECK = ROOT / "tools/check-bananapi-rockchip-aim7-policy.py"
POLICY = (
    ROOT
    / "docs/evidence/bananapi-family-optimization/E-rockchip-aim7-source-policy-20260827.md"
)


class BananaPiRockchipAim7CandidateTests(unittest.TestCase):
    """防止 AIM7 來源、板級身分、授權與證據邊界退化。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.policy = cls.config["boards"]["bananapiaim7"]
        cls.board_text = BOARD.read_text()
        spec = importlib.util.spec_from_file_location("aim7_policy_checker", POLICY_CHECK)
        cls.policy_checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy_checker)

    def run_policy(self, data: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
        with tempfile.NamedTemporaryFile(suffix=".json") as stream:
            stream.write(json.dumps(data, ensure_ascii=False).encode())
            stream.flush()
            return subprocess.run(
                [sys.executable, str(POLICY_CHECK), stream.name],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )

    def valid_l2_config(self) -> dict[str, object]:
        promoted = json.loads(json.dumps(self.config))
        source_commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        source_tree = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        source_config = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "show",
                f"{source_commit}:{CONFIG.relative_to(ROOT).as_posix()}",
            ],
            capture_output=True,
            check=True,
        ).stdout
        source_config_sha256 = hashlib.sha256(source_config).hexdigest()
        promoted["candidate_level"] = "L2 內部軟體候選"
        promoted["candidate_scope"] = "internal-l2"
        promoted["current_evidence_level"] = "L2"
        promoted["rootfs_image_built"] = True
        promoted["full_image_built"] = True
        promoted["full_rootfs_image_built"] = True
        board = promoted["boards"]["bananapiaim7"]
        board["image_dtb_sha256"] = "3" * 64
        board["dtb_sha256"] = "3" * 64
        board["dtb_sha256_evidence_scope"] = "full-image-l2"
        board["final_kernel_config_sha256"] = "4" * 64
        board["final_uboot_config_sha256"] = "5" * 64
        board["uboot_payload_sha256"] = [
            f"idbloader.img={'6' * 64}",
            f"u-boot.itb={'7' * 64}",
        ]
        promoted["image_build_evidence"] = {
            "status": "complete",
            "evidence_level": "L2",
            "source_commit": source_commit,
            "source_tree": source_tree,
            "verifier_commit": source_commit,
            "build_validation_config_sha256": source_config_sha256,
            "verification_config_sha256": source_config_sha256,
            "candidate_matrix_sha256": "8" * 64,
            "uboot_payload_manifest_sha256": "9" * 64,
            "final_config_manifest_sha256": "a" * 64,
            "final_kernel_config_sha256": "4" * 64,
            "final_uboot_config_sha256": "5" * 64,
            "linux_dtb_sha256": "3" * 64,
            "rkbin_commit": "1d3c61008fa823936ae7a59615393f8294b64456",
            "rkbin_manifest_sha256": "d" * 64,
            "read_only_content_verified": True,
            "full_rootfs_image_built": True,
            "hardware_tested": False,
            "public_release_authorized": False,
            "image": {
                "path": "bananapiaim7/aim7.img",
                "size": 1024,
                "sha256": "b" * 64,
            },
            "archive": {
                "path": "bananapiaim7/aim7.img.xz",
                "size": 512,
                "sha256": "c" * 64,
            },
        }
        return promoted

    def write_l2_fixture(self, output: Path) -> dict[str, object]:
        candidate = self.valid_l2_config()
        evidence = candidate["image_build_evidence"]
        board = candidate["boards"]["bananapiaim7"]
        board_dir = output / "bananapiaim7"
        board_dir.mkdir(parents=True)
        image = board_dir / "aim7.img"
        archive = board_dir / "aim7.img.xz"
        image.write_bytes((b"BPI-AIM7\x00" * 128) + b"rootfs")
        with lzma.open(archive, "wb") as stream:
            stream.write(image.read_bytes())
        evidence["image"] = {
            "path": "bananapiaim7/aim7.img",
            "size": image.stat().st_size,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }
        evidence["archive"] = {
            "path": "bananapiaim7/aim7.img.xz",
            "size": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }

        rkbin_manifest = output / "RKBIN_EVIDENCE.tsv"
        rkbin_manifest.write_text(
            "path\tsha256\n"
            + "".join(
                f"{path}\t{digest}\n"
                for path, digest in sorted(candidate["rkbin_blobs"].items())
            )
        )
        payload_manifest = output / "UBOOT_PAYLOAD_EVIDENCE.tsv"
        payload_manifest.write_text(
            "board\tpayload\tplacement\toffset\tsize\tsha256\n"
            f"bananapiaim7\tidbloader.img\tboot-area\t32768\t1\t{'6' * 64}\n"
            f"bananapiaim7\tu-boot.itb\tboot-area\t8388608\t1\t{'7' * 64}\n"
        )
        config_manifest = output / "FINAL_CONFIG_EVIDENCE.tsv"
        config_manifest.write_text(
            "board\tcomponent\tpath\tsha256\n"
            f"bananapiaim7\tkernel\t/boot/config\t{'4' * 64}\n"
            f"bananapiaim7\tuboot\t/u-boot/.config\t{'5' * 64}\n"
        )
        evidence["rkbin_manifest_sha256"] = hashlib.sha256(
            rkbin_manifest.read_bytes()
        ).hexdigest()
        evidence["uboot_payload_manifest_sha256"] = hashlib.sha256(
            payload_manifest.read_bytes()
        ).hexdigest()
        evidence["final_config_manifest_sha256"] = hashlib.sha256(
            config_manifest.read_bytes()
        ).hexdigest()

        matrix = output / "CANDIDATES.tsv"
        matrix.write_text(
            "board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\t"
            "img_path\txz_path\tsource_commit\tuboot_tag\n"
            f"bananapiaim7\ttrixie\tcli\t{evidence['image']['size']}\t"
            f"{evidence['image']['sha256']}\t{evidence['archive']['size']}\t"
            f"{evidence['archive']['sha256']}\t{evidence['image']['path']}\t"
            f"{evidence['archive']['path']}\t{evidence['source_commit']}\tv2017.09\n"
        )
        evidence["candidate_matrix_sha256"] = hashlib.sha256(
            matrix.read_bytes()
        ).hexdigest()

        completion = {
            "status": "complete",
            "source_commit": evidence["source_commit"],
            "source_tree": evidence["source_tree"],
            "validation_config_sha256": evidence["build_validation_config_sha256"],
            "candidates_sha256": evidence["candidate_matrix_sha256"],
        }
        verification = {
            "status": "complete",
            "evidence_level": "L2",
            "source_commit": evidence["source_commit"],
            "source_tree": evidence["source_tree"],
            "verifier_commit": evidence["verifier_commit"],
            "build_validation_config_sha256": evidence["build_validation_config_sha256"],
            "verification_config_sha256": evidence["verification_config_sha256"],
            "candidate_matrix_sha256": evidence["candidate_matrix_sha256"],
            "uboot_payload_manifest_sha256": evidence["uboot_payload_manifest_sha256"],
            "final_config_manifest_sha256": evidence["final_config_manifest_sha256"],
            "rkbin_commit": evidence["rkbin_commit"],
            "rkbin_manifest_sha256": evidence["rkbin_manifest_sha256"],
        }
        rkbin_status = {
            "status": "complete",
            "source_commit": evidence["source_commit"],
            "rkbin_commit": evidence["rkbin_commit"],
            "validation_config_sha256": evidence["build_validation_config_sha256"],
            "manifest_sha256": evidence["rkbin_manifest_sha256"],
        }
        (output / "COMPLETION_STATUS.json").write_text(json.dumps(completion))
        (output / "VERIFICATION_STATUS.json").write_text(json.dumps(verification))
        (output / "RKBIN_STATUS.json").write_text(json.dumps(rkbin_status))

        build_parameters = (
            "BOARD=bananapiaim7 BRANCH=vendor RELEASE=trixie BUILD_DESKTOP=no "
            "BUILD_MINIMAL=yes KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=yes "
            "COMPRESS_OUTPUTIMAGE=sha,img SOURCE_DATE_EPOCH=1777288768 "
            "CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust"
        )
        metadata = {
            "source_commit": evidence["source_commit"],
            "source_tree": evidence["source_tree"],
            "validation_config_sha256": evidence["build_validation_config_sha256"],
            "source_date_epoch": "1777288768",
            "raw_size": str(evidence["image"]["size"]),
            "raw_sha256": evidence["image"]["sha256"],
            "xz_size": str(evidence["archive"]["size"]),
            "xz_sha256": evidence["archive"]["sha256"],
            "artifact_ignore_cache": "yes",
            "image_filename": image.name,
            "archive_filename": archive.name,
            "build_parameters_sha256": hashlib.sha256(
                f"{build_parameters}\n".encode()
            ).hexdigest(),
        }
        (board_dir / "artifact.metadata.txt").write_text(
            "".join(f"{key}={value}\n" for key, value in metadata.items())
        )
        return candidate

    def test_board_is_self_contained_and_vendor_only(self) -> None:
        self.assertNotIn(
            'source "${SRC}/config/boards/armsom-aim7-io.csc"',
            self.board_text,
        )
        for expected in (
            'BOARD_NAME="Banana Pi AIM7"',
            'BOARDFAMILY="rockchip-rk3588"',
            'BOOTCONFIG="bananapi-aim7-rk3588_defconfig"',
            'KERNEL_TARGET="vendor"',
            'KERNEL_TEST_TARGET="vendor"',
            'BOOT_FDT_FILE="rockchip/rk3588-bananapi-aim7.dtb"',
            'IMAGE_PARTITION_TABLE="gpt"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)

    def test_board_pins_linux_uboot_and_rkbin(self) -> None:
        for expected in (
            'BOOTBRANCH_BOARD="commit:39cd993e5d6296635438e84f4576b3a9bf76f86e"',
            'KERNELBRANCH_BOARD="commit:c6157104418d012823413c02f9222f3fe123dd25"',
            'RKBIN_GIT_REF="commit:1d3c61008fa823936ae7a59615393f8294b64456"',
            'ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/armbian/firmware"',
            'ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"',
            'DDR_BLOB="rk35/rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.20_20250926.bin"',
            'BL31_BLOB="rk35/rk3588_bl31_v1.48.elf"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.board_text)
        self.assertNotIn('BOOTBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('KERNELBRANCH_BOARD="branch:', self.board_text)
        self.assertNotIn('RKBIN_GIT_REF="branch:', self.board_text)

    def test_vendor_hook_overrides_movable_family_sources(self) -> None:
        harness = f'''
enable_extension() {{ :; }}
display_alert() {{ :; }}
SRC="{ROOT}"
BRANCH=vendor
HOSTRELEASE=jammy
source "{BOARD}"
source "{ROOT / 'config/sources/families/rockchip-rk3588.conf'}"
printf 'before_uboot=%s\n' "$BOOTBRANCH"
printf 'before_kernel=%s\n' "$KERNELBRANCH"
post_family_config_branch_vendor__bananapiaim7_pin_sources
printf 'uboot_source=%s\nuboot=%s\n' "$BOOTSOURCE" "$BOOTBRANCH"
printf 'kernel_source=%s\nkernel=%s\n' "$KERNELSOURCE" "$KERNELBRANCH"
printf 'rkbin_source=%s\nrkbin=%s\n' "$RKBIN_GIT_URL" "$RKBIN_GIT_REF"
printf 'firmware_source=%s\nfirmware=%s\n' "$ARMBIAN_FIRMWARE_GIT_SOURCE" "$ARMBIAN_FIRMWARE_GIT_REF"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("before_uboot=branch:next-dev-v2024.10", result.stdout)
        self.assertIn("before_kernel=branch:rk-6.1-rkr5.1", result.stdout)
        self.assertIn(
            "uboot=commit:39cd993e5d6296635438e84f4576b3a9bf76f86e",
            result.stdout,
        )
        self.assertIn(
            "kernel=commit:c6157104418d012823413c02f9222f3fe123dd25",
            result.stdout,
        )
        self.assertIn(
            "rkbin=commit:1d3c61008fa823936ae7a59615393f8294b64456",
            result.stdout,
        )
        self.assertIn(
            "firmware_source=https://github.com/armbian/firmware",
            result.stdout,
        )
        self.assertIn(
            "firmware=commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08",
            result.stdout,
        )

    def test_linux_and_uboot_wrappers_only_change_identity(self) -> None:
        for path in (LINUX_DTS, UBOOT_DTS):
            text = path.read_text()
            with self.subTest(path=path):
                self.assertIn('#include "rk3588-armsom-aim7-io.dts"', text)
                self.assertIn('model = "Banana Pi AIM7";', text)
                self.assertIn('"bananapi,bpi-aim7"', text)
                self.assertIn('"armsom,aim7-io"', text)
                self.assertNotIn("status =", text)
                self.assertNotIn("num-lanes", text)

    def test_dedicated_defconfig_only_changes_board_identity(self) -> None:
        expected = ARMSOM_DEFCONFIG.read_text().replace(
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-armsom-aim7-io"\n',
            'CONFIG_DEFAULT_DEVICE_TREE="rk3588-bananapi-aim7"\n'
            'CONFIG_DEFAULT_FDT_FILE="rk3588-bananapi-aim7"\n',
        )
        self.assertEqual(UBOOT_DEFCONFIG.read_text(), expected)

    def test_validation_contract_matches_fixed_sources_and_payloads(self) -> None:
        self.assertEqual(self.config["candidate_branch"], "vendor")
        self.assertEqual(self.config["kernel_family"], "rk35xx")
        self.assertEqual(
            self.config["linux_commit"],
            "c6157104418d012823413c02f9222f3fe123dd25",
        )
        self.assertEqual(
            self.config["rkbin_commit"],
            "1d3c61008fa823936ae7a59615393f8294b64456",
        )
        self.assertEqual(
            self.policy["uboot_revision"],
            "39cd993e5d6296635438e84f4576b3a9bf76f86e",
        )
        self.assertEqual(self.policy["uboot_defconfig"], UBOOT_DEFCONFIG.name)
        self.assertEqual(
            self.policy["uboot_payloads"],
            ["idbloader.img@32768", "u-boot.itb@8388608"],
        )
        self.assertEqual(self.policy["partition_start_sector"], 32768)
        self.assertEqual(self.policy["root_partition_start_sector"], 32768)
        self.assertEqual(
            self.policy["required_partitions"],
            ["1:*:32768:5330944"],
        )
        self.assertEqual(
            self.policy["required_partition_types"],
            ["1:b921b045-1df0-41c3-af44-4c6f280d3fae"],
        )
        self.assertEqual(self.policy["root_partition_label"], "armbi_root")
        self.assertEqual(self.policy["root_partition_filesystem_type"], "ext4")

    def test_rkbin_policy_hashes_blobs_and_installed_license(self) -> None:
        self.assertEqual(self.config["rkbin_license_path"], "LICENSE.TXT")
        self.assertTrue(self.config["rkbin_copy_and_distribution_grant_present"])
        self.assertFalse(self.config["rkbin_standalone_distribution_authorized"])
        self.assertFalse(self.config["rkbin_binary_modification_authorized"])
        self.assertTrue(self.config["rkbin_license_must_accompany_distribution"])
        self.assertIn("Rockchip 積體電路", self.config["rkbin_platform_constraint"])
        blobs = self.config["rkbin_blobs"]
        self.assertEqual(
            set(blobs),
            {
                "LICENSE.TXT",
                "rk35/rk3588_bl31_v1.48.elf",
                "rk35/rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.20_20250926.bin",
                "rk35/rk3588_spl_loader_v1.16.113.bin",
            },
        )
        for digest in blobs.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        installed = self.config["installed_firmware_blobs"]
        self.assertEqual(
            installed["/usr/share/doc/armbian-bsp-bananapiaim7/rkbin.LICENSE.TXT"],
            blobs["LICENSE.TXT"],
        )
        self.assertIn(
            "post_family_tweaks_bsp__bananapiaim7_rkbin_license",
            self.board_text,
        )

    def test_component_and_image_evidence_are_machine_readable(self) -> None:
        evidence = self.config["component_build_evidence"]
        image_evidence = self.config["image_build_evidence"]
        self.assertEqual(self.config["candidate_level"], "L2 內部軟體候選")
        self.assertEqual(self.config["candidate_scope"], "internal-l2")
        self.assertEqual(self.config["current_evidence_level"], "L2")
        self.assertEqual(self.config["target_evidence_level"], "L2")
        self.assertEqual(self.config["allowed_evidence_levels"], ["L1", "L2"])
        self.assertTrue(self.config["component_build_completed"])
        self.assertTrue(self.config["rootfs_image_built"])
        self.assertTrue(self.config["full_image_built"])
        self.assertTrue(self.config["full_rootfs_image_built"])
        self.assertFalse(self.config["hardware_claims_allowed"])
        self.assertFalse(self.config["public_release_allowed"])
        self.assertFalse(self.config["firmware_redistribution_audit_complete"])
        self.assertFalse(self.config["firmware_redistribution_license_verified"])
        self.assertEqual(self.config["source_date_epoch"], 1777288768)
        self.assertEqual(
            self.config["firmware_commit"],
            "f50a2a21bcdb77a562b3976930c5c6b521a1df08",
        )
        self.assertTrue(self.config["verify_firmware_source_resolution"])
        self.assertEqual(image_evidence["status"], "complete")
        self.assertEqual(image_evidence["evidence_level"], "L2")
        self.assertTrue(image_evidence["read_only_content_verified"])
        self.assertFalse(image_evidence["hardware_tested"])
        self.assertFalse(image_evidence["public_release_authorized"])
        self.assertRegex(image_evidence["image"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(image_evidence["archive"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(self.policy["image_dtb_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.policy["dtb_sha256_evidence_scope"],
            "full-image-l2",
        )
        self.assertEqual(evidence["source_date_epoch"], 1777288768)
        self.assertEqual(
            evidence["portable_manifest_sha256"],
            "164033bb5c82577eed3797bf55091a81d0945d7e5332666b55e508850ec42e96",
        )
        self.assertEqual(evidence["portable_artifact_count"], 6)
        self.assertEqual(evidence["linux_dtb_size"], 265522)
        self.assertEqual(evidence["idbloader_size"], 323584)
        self.assertEqual(evidence["uboot_spl_size"], 242776)
        self.assertEqual(evidence["uboot_dtb_size"], 10735)
        self.assertEqual(evidence["uboot_itb_size"], 1462784)
        for key, value in evidence.items():
            if key.endswith("_sha256"):
                self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_diagnostic_packages_cover_requested_interfaces(self) -> None:
        package_line = next(
            line
            for line in self.board_text.splitlines()
            if line.startswith('PACKAGE_LIST_BOARD="')
        )
        board_packages = set(package_line.split('"', 2)[1].split())
        self.assertTrue(set(self.config["common_packages"]) <= board_packages)
        for package in (
            "gpiod",
            "i2c-tools",
            "spi-tools",
            "pciutils",
            "nvme-cli",
            "libdrm-tests",
            "glmark2-es2",
            "vulkan-tools",
            "v4l-utils",
            "ffmpeg",
        ):
            with self.subTest(package=package):
                self.assertIn(package, board_packages)

    def test_kernel_contract_covers_gpu_vpu_npu_and_io(self) -> None:
        options = self.config["common_kernel_options"]
        for option in (
            "CONFIG_GPIO_CDEV",
            "CONFIG_I2C_CHARDEV",
            "CONFIG_SPI_SPIDEV",
            "CONFIG_PCIE_DW_ROCKCHIP",
            "CONFIG_DRM_ROCKCHIP",
            "CONFIG_MALI_BIFROST",
            "CONFIG_ROCKCHIP_MPP_SERVICE",
            "CONFIG_ROCKCHIP_MULTI_RGA",
            "CONFIG_ROCKCHIP_RKNPU",
            "CONFIG_USB_CONFIGFS_MASS_STORAGE",
        ):
            with self.subTest(option=option):
                self.assertEqual(options[option], "y")

        harness = f'''
opts_y=()
opts_m=()
source "{BOARD}"
custom_kernel_config__bananapiaim7_io_contract
printf '%s\n' "${{opts_y[@]}}"
'''
        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        forced = set(result.stdout.split())
        for option in (
            "GPIO_CDEV",
            "I2C_CHARDEV",
            "SPI_SPIDEV",
            "PCIE_DW_ROCKCHIP",
            "MALI_BIFROST",
            "ROCKCHIP_MPP_SERVICE",
            "ROCKCHIP_MULTI_RGA",
            "ROCKCHIP_RKNPU",
            "USB_CONFIGFS_MASS_STORAGE",
        ):
            with self.subTest(forced_option=option):
                self.assertIn(option, forced)

    def test_static_topology_records_unresolved_hardware_limits(self) -> None:
        self.assertTrue(self.policy["static_topology_only"])
        self.assertFalse(self.policy["hardware_validation_completed"])
        self.assertFalse(self.config["candidate_public_release_approved"])
        self.assertIn(
            "/pcie@fe150000:num-lanes=1",
            self.policy["required_uint_properties"],
        )
        for node in (
            "/dsi@fde20000",
            "/dsi@fde30000",
            "/spi@feb00000",
            "/spi@feb10000",
        ):
            with self.subTest(node=node):
                self.assertIn(node, self.policy["required_disabled_nodes"])
        limitations = "\n".join(self.policy["known_static_limitations"])
        self.assertIn("PCIe", limitations)
        self.assertIn("不代表", limitations)

    def test_dedicated_entrypoints_are_thin_and_aim7_only(self) -> None:
        for path in (BUILD, VERIFY):
            text = path.read_text()
            with self.subTest(path=path):
                self.assertIn("bananapi-rockchip-rk3588-aim7-vendor.json", text)
                self.assertIn("bananapi-rockchip-rk3588-aim7-trixie-vendor-cli", text)
                self.assertIn('BOARDS="bananapiaim7"', text)
                self.assertNotIn("compile.sh", text)
                self.assertIn("check-bananapi-rockchip-aim7-policy.py", text)
        build_text = BUILD.read_text()
        self.assertIn("ALLOW_INTERNAL_AIM7_CANDIDATE", build_text)
        self.assertIn("export REQUIRE_ISOLATED_CACHE=yes", build_text)
        self.assertIn("export REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", build_text)
        self.assertIn('expected_source_date_epoch="1777288768"', build_text)
        self.assertIn('MINIMUM_FREE_GIB="${MINIMUM_FREE_GIB:-80}"', build_text)
        verify_text = VERIFY.read_text()
        self.assertIn('policy_evidence_level="$(python3', verify_text)
        self.assertIn('VERIFICATION_EVIDENCE_LEVEL="${policy_evidence_level}"', verify_text)
        self.assertIn("verify-bananapi-rockchip-candidates.sh", verify_text)
        self.assertIn("write_entry_state in_progress", verify_text)
        self.assertIn("禁止沿用舊成功狀態", verify_text)
        self.assertIn("REQUIRE_BUILD_VERIFIER_IDENTITY=yes", verify_text)
        self.assertIn("REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes", verify_text)
        isolated_text = ISOLATED.read_text()
        self.assertIn("build-bananapi-rockchip-aim7-candidate.sh", isolated_text)
        self.assertIn("bananapi-rockchip-aim7-cache-overlay", isolated_text)
        self.assertIn('minimum_free_gib="${MINIMUM_FREE_GIB:-80}"', isolated_text)
        self.assertIn("minimum_free_gib >= 40", isolated_text)
        self.assertIn("ALLOW_INTERNAL_AIM7_CANDIDATE=yes", isolated_text)
        self.assertIn("REQUIRE_ISOLATED_CACHE=yes", isolated_text)
        self.assertNotIn("compile.sh", isolated_text)

    def test_direct_build_and_low_space_override_are_rejected(self) -> None:
        direct = subprocess.run(
            [str(BUILD)],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        self.assertEqual(direct.returncode, 2)
        self.assertIn("OverlayFS", direct.stderr.decode())

        low_space = subprocess.run(
            [str(ISOLATED)],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "MINIMUM_FREE_GIB": "39"},
            capture_output=True,
            check=False,
        )
        self.assertEqual(low_space.returncode, 2)
        self.assertIn("不得低於 40 GiB", low_space.stderr.decode())

        timestamp_override = subprocess.run(
            [str(BUILD)],
            cwd=ROOT,
            env={
                **os.environ,
                "ALLOW_INTERNAL_AIM7_CANDIDATE": "yes",
                "SOURCE_DATE_EPOCH": "1777288769",
            },
            capture_output=True,
            check=False,
        )
        self.assertEqual(timestamp_override.returncode, 2)
        self.assertIn("SOURCE_DATE_EPOCH 必須是", timestamp_override.stderr.decode())

        overlay_bypass = subprocess.run(
            [str(BUILD)],
            cwd=ROOT,
            env={
                **os.environ,
                "ALLOW_INTERNAL_AIM7_CANDIDATE": "yes",
                "REQUIRE_ISOLATED_CACHE": "no",
            },
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(overlay_bypass.returncode, 0)

    def test_preflight_failure_atomically_invalidates_old_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            status = output / "VERIFICATION_STATUS.json"
            status.write_text('{"status":"complete","evidence_level":"L1"}\n')
            result = subprocess.run(
                [str(VERIFY)],
                cwd=ROOT,
                env={**os.environ, "OUTPUT_DIR": str(output)},
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            failed = json.loads(status.read_text())
            self.assertEqual(failed["status"], "failed")
            self.assertIn("禁止沿用舊成功狀態", failed["detail"])

    def test_common_tools_bind_timestamp_tree_and_strict_identity(self) -> None:
        build_text = (
            ROOT / "tools/build-bananapi-sunxi-candidates.sh"
        ).read_text()
        verify_text = (
            ROOT / "tools/verify-bananapi-sunxi-candidates.sh"
        ).read_text()
        for required in (
            'source_date_epoch="$(top_field_optional source_date_epoch)"',
            'build_parameters+=" SOURCE_DATE_EPOCH=${source_date_epoch}"',
            "source_date_epoch=%s",
            "SOURCE_DATE_EPOCH 與驗證設定的固定契約不符",
        ):
            self.assertIn(required, build_text)
        for required in (
            "REQUIRE_BUILD_VERIFIER_IDENTITY",
            '"source_tree": "%s"',
            '"source_tree", "verifier_commit"',
        ):
            self.assertIn(required, verify_text)

    def test_policy_accepts_current_l2_and_rejects_label_only_demotion(self) -> None:
        accepted = self.run_policy(self.config)
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())

        demoted = json.loads(json.dumps(self.config))
        demoted["candidate_level"] = "L1 元件候選"
        rejected = self.run_policy(demoted)
        self.assertNotEqual(rejected.returncode, 0)

    def test_policy_rejects_fixed_source_or_timestamp_drift(self) -> None:
        mutations = {
            "firmware 來源漂移": (
                "firmware_source",
                "https://example.invalid/firmware",
            ),
            "firmware 引用漂移": ("firmware_ref", "branch:master"),
            "firmware 提交漂移": ("firmware_commit", "8" * 40),
            "停用 firmware 解析守門": (
                "verify_firmware_source_resolution",
                False,
            ),
            "固定時間戳漂移": ("source_date_epoch", 1777288769),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                invalid = json.loads(json.dumps(self.config))
                invalid[field] = value
                rejected = self.run_policy(invalid)
                self.assertNotEqual(rejected.returncode, 0)

    def test_policy_rejects_image_evidence_on_l1(self) -> None:
        invalid = json.loads(json.dumps(self.config))
        invalid["candidate_level"] = "L1 元件候選"
        invalid["candidate_scope"] = "internal-component-only"
        invalid["current_evidence_level"] = "L1"
        invalid["rootfs_image_built"] = False
        invalid["full_image_built"] = False
        invalid["full_rootfs_image_built"] = False
        rejected = self.run_policy(invalid)
        self.assertNotEqual(rejected.returncode, 0)

    def test_policy_rejects_well_formed_but_unbacked_internal_l2(self) -> None:
        rejected = self.run_policy(self.valid_l2_config())
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("雜湊不符", rejected.stderr.decode())

    def test_l2_policy_closes_real_files_and_rejects_evidence_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            candidate = self.write_l2_fixture(output)
            original_output = self.policy_checker.OUTPUT_DIR
            self.policy_checker.OUTPUT_DIR = output
            try:
                self.policy_checker.validate_l2_evidence(
                    candidate,
                    candidate["boards"]["bananapiaim7"],
                )

                archive = output / "bananapiaim7/aim7.img.xz"
                original_archive = archive.read_bytes()
                archive.write_bytes(original_archive + b"drift")
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_l2_evidence(
                        candidate,
                        candidate["boards"]["bananapiaim7"],
                    )
                archive.write_bytes(original_archive)

                verification_path = output / "VERIFICATION_STATUS.json"
                verification = json.loads(verification_path.read_text())
                verification["source_tree"] = "0" * 40
                verification_path.write_text(json.dumps(verification))
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_l2_evidence(
                        candidate,
                        candidate["boards"]["bananapiaim7"],
                    )

                verification["source_tree"] = candidate["image_build_evidence"][
                    "source_tree"
                ]
                verification_path.write_text(json.dumps(verification))
                (output / "RKBIN_STATUS.json").unlink()
                with self.assertRaises(SystemExit):
                    self.policy_checker.validate_l2_evidence(
                        candidate,
                        candidate["boards"]["bananapiaim7"],
                    )
            finally:
                self.policy_checker.OUTPUT_DIR = original_output

    def test_policy_rejects_incomplete_or_overclaimed_l2(self) -> None:
        mutations = {
            "缺少映像證據": lambda data: data.pop("image_build_evidence"),
            "來源與驗證提交不同": lambda data: data[
                "image_build_evidence"
            ].__setitem__("verifier_commit", "8" * 40),
            "建置與驗證契約不同": lambda data: data[
                "image_build_evidence"
            ].__setitem__("verification_config_sha256", "8" * 64),
            "候選矩陣雜湊無效": lambda data: data[
                "image_build_evidence"
            ].__setitem__("candidate_matrix_sha256", "無效"),
            "載荷清單雜湊無效": lambda data: data[
                "image_build_evidence"
            ].__setitem__("uboot_payload_manifest_sha256", "無效"),
            "最終設定清單雜湊無效": lambda data: data[
                "image_build_evidence"
            ].__setitem__("final_config_manifest_sha256", "無效"),
            "未完成唯讀驗證": lambda data: data[
                "image_build_evidence"
            ].__setitem__("read_only_content_verified", False),
            "冒充實機驗證": lambda data: data[
                "image_build_evidence"
            ].__setitem__("hardware_tested", True),
            "冒充公開發布": lambda data: data[
                "image_build_evidence"
            ].__setitem__("public_release_authorized", True),
            "映像大小無效": lambda data: data["image_build_evidence"][
                "image"
            ].__setitem__("size", 0),
            "壓縮檔路徑越界": lambda data: data["image_build_evidence"][
                "archive"
            ].__setitem__("path", "../aim7.img.xz"),
            "最終核心設定不一致": lambda data: data["boards"][
                "bananapiaim7"
            ].__setitem__("final_kernel_config_sha256", "d" * 64),
            "payload 雜湊不完整": lambda data: data["boards"][
                "bananapiaim7"
            ].__setitem__("uboot_payload_sha256", [f"idbloader.img={'6' * 64}"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                invalid = self.valid_l2_config()
                mutate(invalid)
                rejected = self.run_policy(invalid)
                self.assertNotEqual(rejected.returncode, 0)

    def test_component_verifier_preserves_evidence_boundaries(self) -> None:
        text = COMPONENT_VERIFY.read_text()
        self.assertIn("portable_manifest_sha256", text)
        self.assertIn("不得包含原始碼或建置樹", text)
        self.assertIn("不代表完整映像、實機或公開發布通過", text)

    def test_policy_rejects_hardware_and_release_overclaim(self) -> None:
        text = POLICY.read_text()
        self.assertIn("不得宣稱硬體介面已通過", text)
        self.assertIn("不得核准候選對外發布", text)
        self.assertIn("不得獨立散布或修改", text)
        self.assertIn("必須附上相同授權文件", text)
        self.assertIn("num-lanes = 1", text)
        self.assertIn("不代表使用者空間驅動", text)


if __name__ == "__main__":
    unittest.main()
