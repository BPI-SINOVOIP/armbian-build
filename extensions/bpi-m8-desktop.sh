#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Stable display configuration and multimedia support for BPI-M8.

function post_family_config__bpi_m8_multimedia_packages() {
	add_packages_to_image alsa-ucm-conf gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-alsa gstreamer1.0-x
}

function post_family_tweaks_bsp__bpi_m8_display_assets() {
	local assets="${SRC}/packages/bsp/bpi-m8/desktop"
	run_host_command_logged install -Dm644 "${assets}/99-bpi-m8-multimedia.rules" \
		"${destination}/etc/udev/rules.d/99-bpi-m8-multimedia.rules"
	run_host_command_logged install -Dm644 "${assets}/99-bpi-m8-render-only.rules" \
		"${destination}/etc/udev/rules.d/99-bpi-m8-render-only.rules"
	run_host_command_logged install -Dm644 "${assets}/20-bpi-m8-modesetting.conf" \
		"${destination}/usr/share/armbian/bpi-m8/desktop/20-bpi-m8-modesetting.conf"
	# GLib's package trigger compiles this default after the greeter schema is
	# installed. Keep configng's theme configuration and display modes intact.
	run_host_command_logged install -Dm644 "${assets}/90-bpi-m8-slick-greeter.gschema.override" \
		"${destination}/usr/share/glib-2.0/schemas/90-bpi-m8-slick-greeter.gschema.override"
	run_host_command_logged install -Dm755 "${assets}/bpi-m8-desktop-install" \
		"${destination}/usr/lib/armbian/bpi-m8-desktop-install"
	cat "${assets}/bpi-m8-desktop.triggers" >> "${destination}/DEBIAN/triggers"
	postinst_functions+=(board_side_bpi_m8_desktop_postinst)
	local audio="${SRC}/packages/bsp/bpi-m8/sound"
	run_host_command_logged install -Dm644 "${audio}/BPI-M8.conf" \
		"${destination}/usr/share/alsa/ucm2/BPI-M8/BPI-M8.conf"
	run_host_command_logged install -Dm644 "${audio}/HiFi.conf" \
		"${destination}/usr/share/alsa/ucm2/BPI-M8/HiFi.conf"
	run_host_command_logged install -Dm644 "${audio}/sndi2s0.conf" \
		"${destination}/usr/share/alsa/ucm2/conf.d/sndi2s0/sndi2s0.conf"
	local firmware
	for firmware in rgx.fw.36.56.104.183 rgx.sh.36.56.104.183; do
		run_host_command_logged install -Dm644 "${SRC}/packages/blobs/pvr/img-bxm/firmware/${firmware}" \
			"${destination}/lib/firmware/${firmware}"
	done
}

function post_family_tweaks__bpi_m8_desktop() {
	chroot_sdcard /usr/lib/armbian/bpi-m8-desktop-install
}

function board_side_bpi_m8_desktop_postinst() {
	case "${1:-}" in
		configure)
			/usr/lib/armbian/bpi-m8-desktop-install || exit $?
			;;
		triggered)
			case " ${2:-} " in
				*'/udev/rules.d/71-seat.rules '* | *'/usr/lib/xorg/Xorg '* | *'/usr/bin/Xorg '*)
					/usr/lib/armbian/bpi-m8-desktop-install || exit $?
					;;
			esac
			;;
	esac
}

function post_family_tweaks__bpi_m8_cedarx() {
	local sources="${SRC}/packages/blobs/cedarx/debs"
	local package staging
	staging="${SDCARD}/tmp/bpi-m8-cedarx"
	local packages=(
		libcedarc-dev_2.0.0_arm64.deb
		gstreamer1.0-omx-allwinner_1.18.3-1.1_arm64.deb
		gstreamer1.0-omx-allwinner-config_1.18.3-1.1_arm64.deb
		gst-omx-listcomponents_1.18.3-1_arm64.deb
	)
	run_host_command_logged mkdir -p "${staging}"
	for package in "${packages[@]}"; do
		run_host_command_logged cp "${sources}/${package}" "${staging}/${package}"
	done
	chroot_sdcard "dpkg -i /tmp/bpi-m8-cedarx/*.deb"
	chroot_sdcard ldconfig
	run_host_command_logged rm -rf "${staging}"
}
