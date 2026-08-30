#!/usr/bin/env python3
"""BPI-M4 Zero EMAC 映像驗證工具回歸測試。"""

from pathlib import Path
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
VERIFIER = REPO_DIR / "tools/verify-bpi-m4zero-emac-image.sh"


class M4ZeroEmacImageVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = VERIFIER.read_text(encoding="utf-8")

    def test_uses_read_only_loop_and_mount(self) -> None:
        self.assertIn("--partscan --read-only", self.script)
        self.assertIn("mount -o ro,noload", self.script)

    def test_compares_packaged_and_written_uboot(self) -> None:
        self.assertIn("u-boot-sunxi-with-spl.bin", self.script)
        self.assertIn("skip=8192", self.script)
        self.assertIn("written_sha256", self.script)
        self.assertIn("uboot_sha256", self.script)

    def test_checks_archive_stream_identity(self) -> None:
        self.assertIn('[[ -f "${archive}" ]]', self.script)
        self.assertIn('[[ -f "${image_sum}" ]]', self.script)
        self.assertIn('[[ -f "${archive_sum}" ]]', self.script)
        self.assertIn("recorded_image_sha256", self.script)
        self.assertIn("recorded_archive_sha256", self.script)
        self.assertIn('xz -t "${archive}"', self.script)
        self.assertIn('xz -dc -- "${archive}"', self.script)
        self.assertIn("decompressed_sha256", self.script)

    def test_checks_emac_acceleration_and_wifi(self) -> None:
        required = (
            "allwinner,sun50i-h618-ac300-ephy",
            "CONFIG_DWMAC_SUN8I=m",
            "CONFIG_AC300_PHY=y",
            "CONFIG_BRCMFMAC=m",
            "CONFIG_BT_HCIUART=m",
            "CONFIG_BT_HCIUART_BCM=y",
            "CONFIG_DRM_PANFROST=m",
            "CONFIG_VIDEO_SUNXI_CEDRUS=y",
            "CONFIG_CRYPTO_DEV_SUN8I_CE=m",
            "CONFIG_RTW88_8821CU=m",
            "rtw88_8821cu.ko",
            "blacklist[[:space:]]+rtw88_8821cu",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)

    def test_checks_broadcom_firmware_aliases_and_source_identity(self) -> None:
        required = (
            "brcmfmac.ko",
            "hci_uart.ko",
            "require_firmware_alias",
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.bin",
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.txt",
            "brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.clm_blob",
            "BCM4345C0.sinovoip,bpi-m4-zero-emac.hcd",
            "packages/bsp/bananapi/brcm",
            "Broadcom 韌體與倉庫來源不一致",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)

    def test_checks_cpu_thermal_cooling_policy(self) -> None:
        required = (
            "/thermal-zones/cpu-thermal",
            "cpu-trip-0",
            "cpu-trip-1",
            "60000",
            "70000",
            '"${cpu_phandle} 1 3"',
            '"${cpu_phandle} 4 ffffffff"',
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)

    def test_checks_board_files_packages_and_tools(self) -> None:
        required = (
            "sun50i-h618-bananapi-m4-zero-emac.dtb",
            "bananapi-m4-zero-emac-sdio-wifi-bt.dtbo",
            "cma=256M",
            "python3-libgpiod",
            "python3-spidev",
            "bpi-h618-hw-info",
            "bpi-h618-io-compat-install",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)


if __name__ == "__main__":
    unittest.main()
