# Allwinner A83T octa core 2Gb SoC Wifi
BOARD_NAME="Banana Pi M3"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="sun8i"
BOARD_MAINTAINER="AaronNGray"
INTRODUCED="2015"
BOOTCONFIG="Sinovoip_BPI_M3_defconfig"
BOOT_FDT_FILE="allwinner/sun8i-a83t-bananapi-m3.dtb"
OVERLAY_PREFIX="sun8i-a83t"
PACKAGE_LIST_BOARD="rfkill bluetooth bluez bluez-tools iw ethtool usbutils alsa-utils smartmontools gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"
KERNEL_TARGET="current,edge,legacy"
KERNEL_TEST_TARGET="current"
# 使用現行 Sunxi U-Boot，固定已驗證的 Linux 與 AP6212 韌體來源，並保留上游 480 MHz DRAM 設定。
# A83T 沒有延遲校準硬體，不得恢復舊版 MMC 校準修補，否則 SPL 可能發生讀取錯誤。
KERNELSOURCE_BOARD="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"
BOOTBRANCH_BOARD="commit:ece349ade2973e220f524ce59e59711cc919263f"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"

function post_family_config_branch_current__bananapim3_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}
