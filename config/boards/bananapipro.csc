# Allwinner A20 dual core 1GB RAM SoC 1xSATA GBE Wifi
BOARD_NAME="Banana Pi Pro"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="sun7i"
BOARD_MAINTAINER=""
INTRODUCED="2014"
BOOTCONFIG="Bananapro_defconfig"
PACKAGE_LIST_BOARD="gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"
KERNEL_TARGET="current,edge,legacy"
KERNEL_TEST_TARGET="current"

function post_config_uboot_target__extra_configs_for_bananapipro() {
	display_alert "$BOARD" "set dram clock" "info"
	run_host_command_logged scripts/config --set-val CONFIG_DRAM_CLK "384"
}
