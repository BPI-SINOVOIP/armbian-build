# Allwinner H618；沿用官方 M4 Zero，預設啟用 FPC24 乙太網路。
# shellcheck source=config/boards/bananapim4zero.conf
source "${SRC}/config/boards/bananapim4zero.conf"
enable_extension "bpi-m4zeroemac-mesa"

BOARD_NAME="BananaPi BPI-M4-Zero EMAC"
BOARD_MAINTAINER=""
KERNEL_TARGET="current"
DEFAULT_OVERLAYS="bananapi-m4-zero-fpc24-eth gpu bananapi-m4-zero-thermal bananapi-m4-zero-hdmi-playback"
PACKAGE_LIST_BOARD+=" ethtool gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"
PACKAGE_LIST_BOARD+=" libegl1 libgles2 libgl1-mesa-dri mesa-utils"
PACKAGE_LIST_BOARD+=" gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-gl"

function image_specific_armbian_env_ready__bananapi_m4zeroemac_cma() {
	local env_file="${SDCARD}/boot/armbianEnv.txt"
	# 為影音緩衝區保留連續記憶體，不更動共用核心設定。
	if grep -Eq '^extraargs=.*cma=' "${env_file}"; then
		sed -E -i '/^extraargs=/ s/cma=[^[:space:]]+/cma=256M/' "${env_file}"
	elif grep -q '^extraargs=' "${env_file}"; then
		sed -i '/^extraargs=/ s/$/ cma=256M/' "${env_file}"
	else
		printf 'extraargs=cma=256M\n' >> "${env_file}"
	fi
}

function post_family_tweaks__bananapi_m4zeroemac_mesa() {
	[[ "${RELEASE}" == "trixie" ]] || return 0
	# Trixie 原版缺少 G31 修正；只選官方 backports 的 Mesa，不做全系統升級。
	install -D -m 0644 "${SRC}/packages/bsp/bananapim4zeroemac/mesa-trixie.pref" \
		"${SDCARD}/etc/apt/preferences.d/bananapi-h618-mesa"
	do_with_retries 3 chroot_sdcard_apt_get_update
	do_with_retries 3 chroot_sdcard_apt_get --no-remove --no-install-recommends -t trixie-backports \
		install libegl-mesa0 libgbm1 libgl1-mesa-dri libglx-mesa0 mesa-libgallium
	local mesa_version
	mesa_version=$(dpkg-query --admindir="${SDCARD}/var/lib/dpkg" -W -f='${Version}' libgl1-mesa-dri)
	dpkg --compare-versions "${mesa_version}" ge 25.2.0 || exit_with_error "Mesa 缺少必要的 G31 修正" "${mesa_version}"
}

function post_family_tweaks_bsp__bananapi_m4zeroemac_io() {
	install -D -m 0755 "${SRC}/packages/bsp/bananapim4zeroemac/bpi-h618-hw-info" \
		"${destination}/usr/bin/bpi-h618-hw-info"
	install -D -m 0644 "${SRC}/packages/bsp/bananapim4zeroemac/99-bananapi-h618-io.rules" \
		"${destination}/etc/udev/rules.d/99-bananapi-h618-io.rules"
	postinst_functions+=(board_side_bananapi_m4zeroemac_gpio_group)
	# 框架將此函式內容展開至板端 postinst。
	# shellcheck disable=SC2317
	function board_side_bananapi_m4zeroemac_gpio_group() {
		if [[ "${1:-}" == "configure" ]]; then
			getent group gpio > /dev/null || groupadd --system gpio || exit 1
		fi
	}
}
