#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# @description 以獨立檔名封裝 BSP sysctl 預設值，避免與發行版 systemd 套件衝突。

function post_family_tweaks_bsp__sysctl_coexist() {
	local sysctl_dir="${destination:?}/usr/lib/sysctl.d"
	if [[ ! -f "${sysctl_dir}/50-default.conf" || -L "${sysctl_dir}/50-default.conf" ||
		-e "${sysctl_dir}/60-armbian-defaults.conf" || -L "${sysctl_dir}/60-armbian-defaults.conf" ]]; then
		exit_with_error "BSP sysctl 來源或目的路徑不符，停止封裝"
		return 1
	fi
	# 保留原值；排序在發行版預設值之後、使用者 90-* 覆寫之前。
	run_host_command_logged mv -- "${sysctl_dir}/50-default.conf" "${sysctl_dir}/60-armbian-defaults.conf"
}
