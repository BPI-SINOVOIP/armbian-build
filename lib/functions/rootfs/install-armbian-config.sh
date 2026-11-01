#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0

function m8_install_armbian_config() {
	m8_build_armbian_config_deb
	install_deb_chroot "${M8_CONFIGNG_DEB}" "" "" \
		"--allow-downgrades --allow-change-held-packages -o Dpkg::Options::=--force-confold"
	# Protect the board-specific installer from an unrelated APT upgrade.
	# New image builds still fetch upstream main and replace this local package.
	chroot_sdcard apt-mark hold armbian-config
}
