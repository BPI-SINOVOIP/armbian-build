#!/usr/bin/env python3
"""BPI-M4 Zero EMAC 映像驗證工具回歸測試。"""

import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
VERIFIER = REPO_DIR / "tools/verify-bpi-m4zero-emac-image.sh"
MATRIX_VERIFIER = REPO_DIR / "tools/verify-bpi-m4zero-emac-matrix.sh"


class M4ZeroEmacImageVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = VERIFIER.read_text(encoding="utf-8")
        cls.matrix_script = MATRIX_VERIFIER.read_text(encoding="utf-8")

    def test_uses_read_only_loop_and_mount(self) -> None:
        self.assertIn("--partscan --read-only", self.script)
        self.assertIn("mount -o ro,noload", self.script)
        self.assertIn('sfdisk --verify "${image}"', self.script)
        self.assertIn('e2fsck -fn "${partition}"', self.script)

    def test_serializes_with_builder_and_uses_system_temporary_storage(self) -> None:
        for script in (self.script, self.matrix_script):
            with self.subTest(script=script[:40]):
                self.assertIn('work_dir="${WORK_DIR:-', script)
                self.assertIn('.build.lock', script)
                self.assertIn("flock", script)
                self.assertIn('system_tmp_dir="${SYSTEM_TMP_DIR:-/tmp}"', script)
        self.assertIn("BPI_M4ZERO_EMAC_LOCK_FD=9", self.matrix_script)
        self.assertIn('/proc/$$/fd/${BPI_M4ZERO_EMAC_LOCK_FD}', self.script)

    def test_matrix_verifier_waits_for_builder_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_dir = root / "output"
            work_dir = root / "work"
            output_dir.mkdir()
            work_dir.mkdir()
            lock_path = work_dir / ".build.lock"
            environment = os.environ.copy()
            environment["OUTPUT_DIR"] = str(output_dir)
            environment["WORK_DIR"] = str(work_dir)

            with lock_path.open("w", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(
                    [str(MATRIX_VERIFIER)],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    time.sleep(0.2)
                    self.assertIsNone(process.poll(), "驗證器未等待建置鎖")
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                    process.communicate(timeout=5)
                    self.assertNotEqual(process.returncode, 0)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)

    def test_matrix_rebuilds_raw_image_without_workspace_links(self) -> None:
        self.assertIn('xz -dc -- "${archive}" >"${image}"', self.matrix_script)
        self.assertIn('mktemp -d "${system_tmp_dir}/m4zero-emac-matrix.', self.matrix_script)
        self.assertIn('"${raw_sha256}"', self.matrix_script)
        self.assertIn('"${raw_size}"', self.matrix_script)
        self.assertNotIn("ln -s", self.matrix_script)
        self.assertNotIn('image="${work_dir}/${img_filename}"', self.matrix_script)

    def test_matrix_checks_public_delivery_manifests(self) -> None:
        required = (
            "IMAGE_MANIFEST.tsv",
            "BUILD_PROVENANCE.tsv",
            "SHA256SUMS",
            "DELIVERY_METADATA_SHA256SUMS",
            "公開映像清單與建置矩陣不一致",
            "SHA256SUMS 與建置矩陣不一致",
            "artifact_source_commit",
            "userpatches_sha256",
            "unrecorded",
            'cat-file -e "${artifact_source_commit}^{commit}"',
            "U-Boot 套件雜湊來源紀錄不符",
            "來源紀錄與映像清單不一致",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.matrix_script)

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
        archive_check = self.script.index('decompressed_sha256="$(xz -dc -- "${archive}"')
        optional_image_sum = self.script.index('if [[ -z "${archive_argument}" ]]')
        self.assertLess(archive_check, optional_image_sum)

    def test_checks_unique_kernel_boot_and_module_versions(self) -> None:
        required = (
            "kernel_version",
            "核心設定檔數量不是 1",
            "System.map-${kernel_version}",
            "vmlinuz-${kernel_version}",
            "initrd.img-${kernel_version}",
            "uInitrd-${kernel_version}",
            "boot.scr",
            "boot.cmd",
            "dumpimage -l",
            "dumpimage -T script",
            "dumpimage -T ramdisk",
            "boot.scr script 資料表與 boot.cmd 大小不一致",
            "boot.scr 內容與 boot.cmd 不一致",
            "Linux kernel ARM64 boot executable Image",
            "uInitrd 內容與同版 initrd.img 不一致",
            "lsinitramfs",
            "initrd 內核心模組版本與核心啟動檔不一致",
            "核心模組目錄數量不是 1",
            "modules.dep",
            "modinfo -F vermagic",
            "核心模組版本不符",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)

    def test_checks_emac_acceleration_and_wifi(self) -> None:
        required = (
            "allwinner,sun50i-h618-ac300-ephy",
            "phy-mode",
            "pinctrl-0",
            "reset-names",
            "mdio-parent-bus",
            "nvmem-cells",
            "nvmem-cell-names",
            "clock-frequency",
            '"${ccu_phandle} 1f"',
            '"${ccu_phandle} 51 ${ac300_clock_phandle}"',
            "PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PA8 PA9",
            "drive-strength",
            '"$(fdt_hex "${internal_mdio_node}" reg)" == 1',
            '"$(fdt_hex "${phy_node}" reg)" == 0',
            "pwm_specifier",
            "AC300 PWM 規格不是第 5 通道、500 ns、一般極性",
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

    def test_parses_and_checks_wifi_bluetooth_overlay(self) -> None:
        required = (
            "dtc -I dtb -O dts",
            "sinovoip,bpi-m4-zero-emac",
            "allwinner,sun50i-h618",
            "/fragment@0/__overlay__",
            "/fragment@1/__overlay__",
            "/__fixups__ mmc1",
            "/__fixups__ uart1",
            "uart1_pins",
            "uart1_rts_cts_pins",
            "brcm,bcm43540-bt",
            "host-wakeup-gpios",
            "device-wakeup-gpios",
            "shutdown-gpios",
            "max-speed",
            "vbat-supply",
            "vddio-supply",
            "/__fixups__ pio",
            "/__fixups__ reg_vcc3v3",
            "/__fixups__ reg_vcc1v8",
            "/soc/mmc@4021000",
            "bus-width",
            "non-removable",
            "keep-power-in-suspend",
            "mmc-pwrseq",
            "brcm,bcm4329-fmac",
            "mmc-pwrseq-simple",
            "post-power-on-delay-ms",
            "Wi-Fi reset GPIO 不符",
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
            "/usr/sbin/ethtool",
            "60-armbian-defaults.conf",
            "cmp -s",
            "armbian-bsp-cli-bananapim4zeroemac-current.list",
            "會與舊版 systemd 衝突的 50-default.conf",
            "bpi-h618-hw-info",
            "bpi-h618-io-compat-install",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, self.script)


if __name__ == "__main__":
    unittest.main()
