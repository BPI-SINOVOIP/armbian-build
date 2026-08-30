# Rockchip RK3568 四核心、2 GB 至 4 GB 記憶體、五埠乙太網路、eMMC、SATA、USB 3 與 PCIe
BOARD_NAME="Banana Pi R2 Pro"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="rockchip64"
BOARD_MAINTAINER="BPI-SINOVOIP"
INTRODUCED="2021"
BOOTCONFIG="bpi-r2-pro-rk3568_defconfig"
KERNEL_TARGET="current,edge"
KERNEL_TEST_TARGET="current"
FULL_DESKTOP="yes"
BOOT_LOGO="desktop"
BOOT_FDT_FILE="rockchip/rk3568-bpi-r2-pro.dtb"
BOOTBRANCH_BOARD="tag:v2026.01"
BOOTPATCHDIR="v2026.01"
SRC_EXTLINUX="yes"
SRC_CMDLINE="console=ttyS2,1500000 console=tty0"
ASOUND_STATE="asound.state.station-p2"
IMAGE_PARTITION_TABLE="gpt"

# Current 候選固定核心、U-Boot、RKBin 與韌體來源，避免可移動來源改變啟動鏈。
KERNELSOURCE_BOARD="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
KERNELBRANCH_BOARD="commit:1f99e9ab748fc5c32120de9c4eca31abfe54a4d5"
BOOTBRANCH_BOARD="commit:866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e"
RKBIN_GIT_REF="commit:46c4793ea2dcea7c8331fce9f07b5c80561a0395"
DDR_BLOB="rk35/rk3568_ddr_1560MHz_v1.21.bin"
BL31_BLOB="rk35/rk3568_bl31_v1.44.elf"
ROCKUSB_BLOB="rk35/rk356x_spl_loader_v1.21.113.bin"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"
PACKAGE_LIST_BOARD="ethtool iproute2 bridge-utils vlan iperf3 smartmontools hdparm pciutils usbutils alsa-utils gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils lm-sensors"

function post_family_config_branch_current__bananapir2pro_pin_sources() {
	declare -g KERNELSOURCE="${KERNELSOURCE_BOARD}"
	declare -g KERNELBRANCH="${KERNELBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}

function post_family_config___mainline_uboot() {
	declare -g UBOOT_TARGET_MAP="ROCKCHIP_TPL=${RKBIN_DIR}/${DDR_BLOB} BL31=$RKBIN_DIR/$BL31_BLOB spl/u-boot-spl u-boot.bin flash.bin;;idbloader.img u-boot.itb"
}
