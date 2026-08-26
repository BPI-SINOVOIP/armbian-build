# MediaTek MT7988A 四核心 Cortex-A73 網路板卡
BOARD_NAME="Banana Pi R4"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="filogic"
BOARD_MAINTAINER=""
INTRODUCED="2024"
KERNEL_TARGET="current"
KERNEL_TEST_TARGET="current"
BOOTCONFIG="mt7988a_bananapi_bpi-r4-sdmmc_defconfig"
BOOT_FDT_FILE="mediatek/mt7988a-bananapi-bpi-r4-sd.dtb"
SRC_EXTLINUX="yes"
SRC_CMDLINE="console=ttyS0,115200n1 earlyprintk loglevel=8 initcall_debug=0 swiotlb=512 cgroup_enable cgroup_memory=1 init=/sbin/init"
HAS_VIDEO_OUTPUT="no"

# Current 候選固定完整啟動鏈與韌體來源，避免可移動分支改變映像內容。
KERNELSOURCE_BOARD="https://github.com/frank-w/BPI-Router-Linux.git"
KERNELBRANCH_BOARD="commit:4a4506842b77b597f11e7fc53be1dcdbdc97eea9"
BOOTBRANCH_BOARD="commit:34820924edbc4ec7803eb89d9852f4b870fa760a"
ATFBRANCH_BOARD="commit:c34e37802efaea356991a0811c8fc50f8a810f5b"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"
MT76_FIRMWARE_GIT_SOURCE_BOARD="https://github.com/openwrt/mt76.git"
MT76_FIRMWARE_GIT_REF_BOARD="commit:c5a3bd91aa735b669618610d5f0ebfa5786845a6"
PACKAGE_LIST_BOARD="ethtool iproute2 bridge-utils vlan iperf3 nftables tcpdump smartmontools hdparm pciutils nvme-cli usbutils iw rfkill wireless-regdb gpiod i2c-tools python3-libgpiod python3-spidev lm-sensors"

function post_family_config_branch_current__bananapir4_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
	declare -g ATFBRANCH="${ATFBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}

function post_config_uboot_target__bananapir4_standard_boot() {
	display_alert "U-Boot ${BOARD}" "啟用 extlinux 標準自動開機並修正 R4 DTB 路徑" "info"
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
	  mediatek/mt7988a-bananapi-bpi-r4-sd.dtb
}

# MT7996 韌體固定由 mt76 提交收入 BSP；MT7988 WED 保留倉庫內固定輸入並分開驗證。
function post_family_tweaks_bsp__bananapir4_network_firmware() {
	local firmware_commit="${MT76_FIRMWARE_GIT_REF_BOARD#commit:}"
	local firmware_source firmware_file
	fetch_from_repo "${MT76_FIRMWARE_GIT_SOURCE_BOARD}" "mt76-firmware" \
	  "${MT76_FIRMWARE_GIT_REF_BOARD}" "yes"
	firmware_source="${SRC}/cache/sources/mt76-firmware/${firmware_commit}/firmware"
	for firmware_file in \
		mt7996_dsp.bin mt7996_eeprom.bin mt7996_eeprom_2i5i6i.bin \
		mt7996_eeprom_233.bin mt7996_eeprom_233_2i5i6i.bin \
		mt7996_rom_patch.bin mt7996_rom_patch_233.bin \
		mt7996_wa.bin mt7996_wa_233.bin \
		mt7996_wm.bin mt7996_wm_233.bin; do
		run_host_command_logged install -Dm0644 \
		  "${firmware_source}/mt7996/${firmware_file}" \
		  "${destination}/lib/firmware/mediatek/mt7996/${firmware_file}"
	done
	for firmware_file in mt7988_wo_0.bin mt7988_wo_1.bin; do
		run_host_command_logged install -Dm0644 \
		  "${SRC}/packages/blobs/filogic/firmware/mediatek/mt7988/${firmware_file}" \
		  "${destination}/lib/firmware/mediatek/mt7988/${firmware_file}"
	done
	run_host_command_logged install -Dm0644 "${firmware_source}/LICENSE" \
	  "${destination}/usr/share/doc/armbian-bsp-${BOARD}/mt76-firmware.LICENSE"
}
