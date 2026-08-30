# Allwinner A20 dual core 1Gb SoC GBE WiFi 1xSATA
BOARD_NAME="Banana Pi M1+"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="sun7i"
BOARD_MAINTAINER=""
INTRODUCED="2014"
BOOTCONFIG="bananapi_m1_plus_defconfig"
PACKAGE_LIST_BOARD="rfkill iw ethtool usbutils alsa-utils gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"
KERNEL_TARGET="current,edge,legacy"
KERNEL_TEST_TARGET="current"

# Current 候選固定 Linux 與 U-Boot 來源，保留上游 M1+ 的 432 MHz DRAM 設定。
KERNELSOURCE_BOARD="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"
BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"

function post_family_config_branch_current__bananapim1plus_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
}
