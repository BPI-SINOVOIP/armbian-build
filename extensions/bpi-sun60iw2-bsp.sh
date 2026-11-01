#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Board runtime integration. Build hooks live with the sun60iw2 family;
# device drivers and their build fixes live in their respective repositories.

enable_extension "bpi-m8-desktop"

function post_repo_customize_image__sun60iw2_armbian_config() {
	[[ "${LINUXFAMILY:-}" == sun60iw2 ]] || return 0
	m8_install_armbian_config
}

function post_family_tweaks__bpi_m8_boot_logo() {
	local logo="${SRC}/packages/blobs/splash/armbian-u-boot-24.bmp"
	run_host_command_logged install -m 644 "${logo}" "${SDCARD}/boot/boot.bmp"
	run_host_command_logged install -m 644 "${logo}" "${SDCARD}/boot/bootlogo.bmp"
}
