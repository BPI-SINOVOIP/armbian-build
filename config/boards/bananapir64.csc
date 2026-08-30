# MediaTek MT7622A 雙核心 Cortex-A53 網路板卡
BOARD_NAME="Banana Pi R64"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="filogic"
BOARD_MAINTAINER=""
INTRODUCED="2019"
KERNEL_TARGET="current"
KERNEL_TEST_TARGET="current"
BOOTCONFIG="mt7622_bananapi_bpi-r64-sdmmc_defconfig"
BOOT_FDT_FILE="mediatek/mt7622-bananapi-bpi-r64.dtb"
SRC_EXTLINUX="yes"
SRC_CMDLINE="console=ttyS0,115200n1 earlyprintk loglevel=8 initcall_debug=0 cgroup_enable=memory"
HAS_VIDEO_OUTPUT="no"

# Current 候選固定完整啟動鏈與韌體來源，避免可移動分支改變映像內容。
KERNELSOURCE_BOARD="https://github.com/frank-w/BPI-Router-Linux.git"
KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"
BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"
ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"
LINUX_FIRMWARE_GIT_SOURCE_BOARD="https://gitlab.com/kernel-firmware/linux-firmware.git"
LINUX_FIRMWARE_GIT_REF_BOARD="commit:01205307636157a12c29e6a774bf83b218732050"
PACKAGE_LIST_BOARD="ethtool iproute2 bridge-utils vlan iperf3 nftables tcpdump smartmontools hdparm pciutils nvme-cli usbutils iw rfkill wireless-regdb gpiod i2c-tools python3-libgpiod python3-spidev lm-sensors"

declare -g FILOGIC_SOC="mt7622"
declare -g FILOGIC_BOOT_DEVICE="sdmmc"
declare -g FILOGIC_FIP_NAME="u-boot_sdmmc.fip"

function post_family_config_branch_current__bananapir64_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
	declare -g ATFBRANCH="${ATFBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}

function post_config_uboot_target__bananapir64_standard_boot() {
	display_alert "U-Boot ${BOARD}" "啟用 extlinux 標準自動開機並保護 R64 環境分割區" "info"
	run_host_command_logged scripts/config --enable CONFIG_AUTOBOOT
	run_host_command_logged scripts/config --disable CONFIG_AUTOBOOT_KEYED
	run_host_command_logged scripts/config --disable CONFIG_AUTOBOOT_MENU_SHOW
	run_host_command_logged scripts/config --enable CONFIG_BOOTSTD
	run_host_command_logged scripts/config --enable CONFIG_BOOTSTD_FULL
	run_host_command_logged scripts/config --enable CONFIG_BOOTSTD_DEFAULTS
	run_host_command_logged scripts/config --enable CONFIG_BOOTSTD_BOOTCOMMAND
	run_host_command_logged scripts/config --enable CONFIG_BOOTMETH_EXTLINUX
	run_host_command_logged scripts/config --enable CONFIG_CMD_BOOTFLOW
	run_host_command_logged scripts/config --enable CONFIG_CMD_BOOTFLOW_FULL
	run_host_command_logged scripts/config --enable CONFIG_FS_EXT4
	run_host_command_logged scripts/config --enable CONFIG_CMD_EXT4
	run_host_command_logged scripts/config --set-str CONFIG_DEFAULT_FDT_FILE \
	  mediatek/mt7622-bananapi-bpi-r64.dtb
	run_host_command_logged scripts/config --disable CONFIG_ENV_IS_NOWHERE
	run_host_command_logged scripts/config --enable CONFIG_ENV_IS_IN_MMC
	run_host_command_logged scripts/config --enable CONFIG_SYS_REDUNDAND_ENVIRONMENT
	run_host_command_logged scripts/config --enable CONFIG_USE_ENV_MMC_PARTITION
	run_host_command_logged scripts/config --set-str CONFIG_ENV_MMC_PARTITION ubootenv
	run_host_command_logged scripts/config --set-val CONFIG_ENV_SIZE 0x40000
	run_host_command_logged scripts/config --set-val CONFIG_ENV_OFFSET 0x400000
	run_host_command_logged scripts/config --set-val CONFIG_ENV_OFFSET_REDUND 0x440000
	run_host_command_logged scripts/config --set-val CONFIG_SYS_MMC_ENV_DEV 0
}

# 三個 MT7622 執行期韌體固定由可追溯的 Linux 韌體提交收入 BSP。
function post_family_tweaks_bsp__bananapir64_network_firmware() {
	local firmware_file
	local firmware_source="${SRC}/packages/blobs/filogic/firmware/mediatek/mt7622"
	local mt7988_source="${SRC}/packages/blobs/filogic/firmware/mediatek/mt7988"
	display_alert "MT7622 網路與藍牙韌體來源" \
	  "${LINUX_FIRMWARE_GIT_SOURCE_BOARD} ${LINUX_FIRMWARE_GIT_REF_BOARD}" "info"
	for firmware_file in \
		mt7622pr2h.bin mt7622_n9.bin mt7622_rom_patch.bin \
		mt7981_wo.bin mt7986_wo_0.bin mt7986_wo_1.bin; do
		run_host_command_logged install -Dm0644 \
		  "${firmware_source}/${firmware_file}" \
		  "${destination}/lib/firmware/mediatek/${firmware_file}"
	done
	for firmware_file in i2p5ge-phy-pmb.bin mt7988_wo_0.bin mt7988_wo_1.bin; do
		run_host_command_logged install -Dm0644 \
		  "${mt7988_source}/${firmware_file}" \
		  "${destination}/lib/firmware/mediatek/mt7988/${firmware_file}"
	done
	run_host_command_logged install -Dm0644 \
	  "${firmware_source}/LICENCE.mediatek" \
	  "${destination}/usr/share/doc/armbian-bsp-${BOARD}/linux-firmware.LICENCE.mediatek"
	run_host_command_logged install -Dm0644 \
	  "${firmware_source}/SOURCE.md" \
	  "${destination}/usr/share/doc/armbian-bsp-${BOARD}/mt7622-firmware-SOURCE.md"
	run_host_command_logged install -Dm0644 \
	  "${mt7988_source}/SOURCE.md" \
	  "${destination}/usr/share/doc/armbian-bsp-${BOARD}/mt7988-firmware-SOURCE.md"
}
