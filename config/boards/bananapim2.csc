# A31s quad core 1Gb SoC WiFi
BOARD_NAME="Banana Pi M2"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="sun6i"
BOARD_MAINTAINER=""
INTRODUCED="2015"
BOOTCONFIG="Sinovoip_BPI_M2_defconfig"
OVERLAY_PREFIX="sun6i-a31s"
PACKAGE_LIST_BOARD="rfkill iw ethtool usbutils gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"
KERNEL_TARGET="current,legacy"
KERNEL_TEST_TARGET="current"

# Current 候選固定 Linux、U-Boot 與 AP6210 韌體來源；保留上游 432 MHz DRAM 設定。
KERNELSOURCE_BOARD="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"
BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"

function post_family_config_branch_current__bananapim2_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}
