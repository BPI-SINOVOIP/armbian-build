# Amlogic S905X3 quad core 2-4GB RAM SoC eMMC GBE USB3 SPI Wifi
BOARD_NAME="Banana Pi M2Pro"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="meson-sm1"
BOARD_MAINTAINER="igorpecovnik"
INTRODUCED="2021"
BOOTCONFIG="bananapi-m2-pro_defconfig"
BOOT_FDT_FILE="amlogic/meson-sm1-bananapi-m2-pro.dtb"
KERNEL_TARGET="current,edge"
KERNEL_TEST_TARGET="current"
MODULES_BLACKLIST="simpledrm" # SimpleDRM conflicts with Panfrost
FULL_DESKTOP="yes"
SERIALCON="ttyAML0"
BOOT_LOGO="desktop"
BOOTBRANCH_BOARD="tag:v2024.07"
BOOTPATCHDIR="v2024.07"
PACKAGE_LIST_BOARD="rfkill bluetooth bluez bluez-tools gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"

function fetch_sources_tools__900_bananapi_amlogic_fip_m2pro() {
	fetch_from_repo "https://github.com/Dangku/amlogic-boot-fip" "amlogic-boot-fip" "commit:e11ae32f65219e9cba903e9744f216239b41386a"
}

function post_uboot_custom_postprocess__bpi-m2-pro() {
	uboot_g12_postprocess "$SRC"/cache/sources/amlogic-boot-fip/bananapi-m2-pro g12a
}
