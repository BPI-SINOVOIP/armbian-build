#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0

function add_host_dependencies__bpi_m8_wireless() {
	declare -g EXTRA_BUILD_DEPS="${EXTRA_BUILD_DEPS} gcc-aarch64-linux-gnu dpkg-dev curl ca-certificates"
}

function post_family_config__bpi_m8_wireless_packages() {
	add_packages_to_image rfkill bluetooth bluez bluez-tools
}

function post_family_tweaks_bsp__bpi_m8_wireless_install() {
	local assets="${SRC}/packages/bsp/bpi-m8"
	install -d "${destination}/etc/default" "${destination}/etc/modprobe.d" \
		"${destination}/usr/lib/armbian" "${destination}/usr/bin" \
		"${destination}/lib/systemd/system" "${destination}/usr/share/armbian/firmware/aic8800d80"
	install -m 0644 "${assets}/bpi-m8-wireless.default" "${destination}/etc/default/bpi-m8-wireless"
	install -m 0644 "${assets}/bpi-m8-wireless-autoselect.conf" "${destination}/etc/modprobe.d/"
	install -m 0644 "${assets}/bpi-m8-aic8800.conf" "${destination}/etc/modprobe.d/"
	install -m 0755 "${assets}/bpi-m8-wireless-select" "${assets}/bpi-m8-bluetooth" \
		"${assets}/bpi-m8-wireless-install" "${destination}/usr/lib/armbian/"
	install -m 0644 "${assets}/bpi-m8-wireless.service" "${assets}/bpi-m8-bluetooth.service" "${destination}/lib/systemd/system/"
	install -d "${destination}/usr/share/doc/rtk_hciattach"
	install -m 0644 "${assets}/bluetooth/rtk-hciattach/copyright" \
		"${assets}/bluetooth/rtk-hciattach/COPYING" "${destination}/usr/share/doc/rtk_hciattach/"
	# Pin the userspace ABI independently of the build host. The helper also
	# records verified runtime dependencies for cached BSP artifact reversion.
	run_host_command_logged bash "${assets}/bluetooth/build-uart-helper.sh" \
		"${destination}" "${SRC}/cache/toolchain"
	# Keep package ownership separate from armbian-firmware. The same migration
	# runs on installed-board upgrades and after all firmware packages in images.
	cp -a "${SRC}/packages/bsp/aic8800/firmware/aic8800d80/." \
		"${destination}/usr/share/armbian/firmware/aic8800d80/"
	install -m 0644 "${SRC}/packages/bsp/aic8800/aichw.conf" \
		"${destination}/usr/share/armbian/firmware/aic8800d80/aichw.conf"
	# The framework already activates update-initramfs here; preserve it.
	cat "${assets}/bpi-m8-wireless.triggers" >> "${destination}/DEBIAN/triggers"
	printf '%s\n' /etc/default/bpi-m8-wireless >> "${destination}/DEBIAN/conffiles"
	postinst_functions+=(board_side_bpi_m8_wireless_postinst)
}

function post_family_tweaks__bpi_m8_wireless_enable() {
	chroot_sdcard /usr/lib/armbian/bpi-m8-wireless-install
}

function board_side_bpi_m8_wireless_postinst() {
	case "${1:-}" in
		configure | triggered)
			/usr/lib/armbian/bpi-m8-wireless-install "$1" || exit $?
			;;
	esac
}
