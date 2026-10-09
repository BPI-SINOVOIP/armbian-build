# Allwinner H618；沿用官方 M4 Zero，預設啟用 FPC24 乙太網路。
# shellcheck source=config/boards/bananapim4zero.conf
source "${SRC}/config/boards/bananapim4zero.conf"

BOARD_NAME="BananaPi BPI-M4-Zero EMAC"
BOARD_MAINTAINER=""
KERNEL_TARGET="current"
DEFAULT_OVERLAYS="bananapi-m4-zero-fpc24-eth"
