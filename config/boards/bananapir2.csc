# Mediatek MT7623n quad core 2GB mPci 2xSATA 2xUSB3.0 5xGBE
BOARD_NAME="Banana Pi R2"
BOARD_VENDOR="sinovoip"
BOARDFAMILY="mt7623"
BOARD_MAINTAINER="BPI-SINOVOIP"
INTRODUCED="2019"
KERNEL_TARGET="current,edge"
KERNEL_TEST_TARGET="current,edge"
BOOTCONFIG="mt7623n_bpir2_defconfig"
BOOT_FDT_FILE="mediatek/mt7623n-bananapi-bpi-r2"
HAS_VIDEO_OUTPUT="yes"
IMAGE_PARTITION_TABLE="msdos"
PACKAGE_LIST_BOARD="alsa-utils bridge-utils ethtool gpiod hdparm i2c-tools iperf3 iproute2 iw lm-sensors nftables pciutils python3-libgpiod python3-spidev rfkill smartmontools tcpdump usbutils v4l-utils vlan wireless-regdb"
ARMBIAN_FIRMWARE_GIT_REF_BOARD="commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08"
BOOTBRANCH_BOARD="commit:ece349ade2973e220f524ce59e59711cc919263f"

function post_family_config_branch_current__bananapir2_pin_sources() {
	declare -g KERNELSOURCE="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git"
	declare -g KERNELBRANCH="commit:dc6160265ffc795a1832bc1424f58291d152c7bb"
	declare -g KERNEL_MAJOR_MINOR="6.6"
	declare -g BOOTBRANCH="${BOOTBRANCH_BOARD}"
	declare -g ARMBIAN_FIRMWARE_GIT_REF="${ARMBIAN_FIRMWARE_GIT_REF_BOARD}"
}

function custom_kernel_config__bananapir2_io_and_otg() {
	opts_y+=(GPIO_CDEV HW_RANDOM_MTK RFKILL)
	opts_y+=(MEDIATEK_WATCHDOG NOP_USB_XCEIV USB_GADGET)
	opts_y+=(USB_MUSB_HDRC USB_MUSB_DUAL_ROLE USB_MUSB_MEDIATEK USB_ROLE_SWITCH)
}
