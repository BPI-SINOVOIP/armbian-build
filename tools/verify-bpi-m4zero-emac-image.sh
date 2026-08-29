#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1:-}"
uboot_deb="${2:-}"

usage() {
	echo "用法：$0 <映像.img> <U-Boot 套件.deb>" >&2
}

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

[[ -n "${image}" && -n "${uboot_deb}" ]] || {
	usage
	exit 2
}
[[ -f "${image}" ]] || fail "找不到映像：${image}"
[[ -f "${uboot_deb}" ]] || fail "找不到 U-Boot 套件：${uboot_deb}"

for command in awk cut dd dpkg-deb fdtget find grep lsblk losetup mktemp \
	modinfo mount mountpoint sha256sum stat sudo tar udevadm umount xz; do
	command -v "${command}" >/dev/null || fail "缺少必要命令：${command}"
done
sudo -n true || fail "唯讀掛載需要免互動 sudo"

mkdir -p "${repo_dir}/.tmp"
extract_dir="$(mktemp -d "${repo_dir}/.tmp/m4zero-emac-uboot.XXXXXX")"
mount_dir="$(mktemp -d "${repo_dir}/.tmp/m4zero-emac-image.XXXXXX")"
loop_device=""

cleanup() {
	if mountpoint -q "${mount_dir}"; then
		sudo -n umount "${mount_dir}" || true
	fi
	if [[ -n "${loop_device}" ]]; then
		sudo -n losetup -d "${loop_device}" 2>/dev/null || true
	fi
	rm -rf "${extract_dir}"
	rmdir "${mount_dir}" 2>/dev/null || true
}
trap cleanup EXIT

dpkg-deb -x "${uboot_deb}" "${extract_dir}"
mapfile -t uboot_candidates < <(
	find "${extract_dir}/usr/lib" -type f -name 'u-boot-sunxi-with-spl.bin' -print
)
[[ ${#uboot_candidates[@]} -eq 1 ]] ||
	fail "U-Boot 套件內預期只有一個 u-boot-sunxi-with-spl.bin"

uboot_binary="${uboot_candidates[0]}"
uboot_size="$(stat -c %s "${uboot_binary}")"
uboot_sha256="$(sha256sum "${uboot_binary}" | cut -d' ' -f1)"
written_sha256="$(
	dd if="${image}" bs=1 skip=8192 count="${uboot_size}" status=none |
		sha256sum | cut -d' ' -f1
)"
[[ "${written_sha256}" == "${uboot_sha256}" ]] ||
	fail "映像內 U-Boot 與套件內容不一致"
echo "U-Boot 位元同一性通過：${uboot_sha256}"

image_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
archive="${image}.xz"
if [[ -f "${archive}" ]]; then
	xz -t "${archive}"
	decompressed_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
	[[ "${decompressed_sha256}" == "${image_sha256}" ]] ||
		fail "XZ 解壓資料與原始映像不一致"
	echo "XZ 串流同一性通過：${image_sha256}"
fi

loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")"
udevadm settle
partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" |
	awk '$2 == "part" { print $1; exit }')"
[[ -n "${partition}" ]] || fail "映像沒有可掛載的分割區"
sudo -n mount -o ro,noload "${partition}" "${mount_dir}"

read_env_value() {
	local key=$1
	local env_file="${mount_dir}/boot/armbianEnv.txt"
	local values=()
	mapfile -t values < <(awk -F= -v key="${key}" '$1 == key { print substr($0, index($0, "=") + 1) }' "${env_file}")
	[[ ${#values[@]} -eq 1 ]] || return 1
	printf '%s\n' "${values[0]}"
}

require_env_value() {
	local key=$1
	local expected=$2
	local actual
	actual="$(read_env_value "${key}")" ||
		fail "armbianEnv.txt 缺少唯一欄位 ${key}"
	[[ "${actual}" == "${expected}" ]] ||
		fail "armbianEnv.txt 的 ${key} 不符：${actual}"
}

require_env_token() {
	local key=$1
	local expected=$2
	local actual
	actual="$(read_env_value "${key}")" ||
		fail "armbianEnv.txt 缺少唯一欄位 ${key}"
	grep -Eq "(^|[[:space:]])${expected}([[:space:]]|$)" <<<"${actual}" ||
		fail "armbianEnv.txt 的 ${key} 缺少 ${expected}"
}

require_env_value fdtfile sun50i-h618-bananapi-m4-zero-emac.dtb
require_env_value overlays bananapi-m4-zero-emac-sdio-wifi-bt
require_env_token extraargs cma=256M

dtb="${mount_dir}/boot/dtb/allwinner/sun50i-h618-bananapi-m4-zero-emac.dtb"
overlay="${mount_dir}/boot/dtb/allwinner/overlay/sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtbo"
[[ -s "${dtb}" ]] || fail "缺少 M4 Zero EMAC DTB"
[[ -s "${overlay}" ]] || fail "缺少 M4 Zero EMAC Wi-Fi／Bluetooth overlay"
[[ "$(fdtget "${dtb}" / model)" == "BananaPi BPI-M4-Zero EMAC" ]] ||
	fail "DTB 板型名稱不符"
for node in /soc/gpu@1800000 /soc/pwm@300a000 /soc/ethernet@5030000; do
	[[ "$(fdtget "${dtb}" "${node}" status)" == okay ]] ||
		fail "DTB 節點未啟用：${node}"
done
phy_node=/soc/ethernet@5030000/mdio-mux/mdio@1/ethernet-phy@0
phy_compatible="$(fdtget "${dtb}" "${phy_node}" compatible)"
grep -Fq 'allwinner,sun50i-h618-ac300-ephy' <<<"${phy_compatible}" ||
	fail "DTB 缺少 AC300 EPHY"
[[ "$(fdtget "${dtb}" "${phy_node}" clock-names)" == "ephy pwm" ]] ||
	fail "AC300 EPHY 時鐘來源不符"

config_file="$(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print -quit)"
[[ -n "${config_file}" ]] || fail "缺少核心設定檔"
for setting in \
	CONFIG_DWMAC_SUN8I=m \
	CONFIG_AC300_PHY=y \
	CONFIG_RTW88_8821CU=m \
	CONFIG_DRM_PANFROST=m \
	CONFIG_VIDEO_SUNXI_CEDRUS=y \
	CONFIG_COMMON_CLK_PWM=y \
	CONFIG_SUN50I_H6_PRCM_PPU=y \
	CONFIG_PWM_SUNXI_ENHANCE=y \
	CONFIG_CRYPTO_DEV_SUN8I_CE=m; do
	grep -qx "${setting}" "${config_file}" || fail "核心設定缺少 ${setting}"
done

require_module() {
	local pattern=$1
	local module
	module="$(find "${mount_dir}/lib/modules" -type f -name "${pattern}" -print -quit)"
	[[ -n "${module}" ]] || fail "缺少核心模組：${pattern}"
	printf '%s\n' "${module}"
}

require_module 'dwmac-sun8i.ko*' >/dev/null
require_module 'panfrost.ko*' >/dev/null
require_module 'sun8i-ce.ko*' >/dev/null
rtw_module="$(require_module 'rtw88_8821cu.ko*')"
modinfo -F alias "${rtw_module}" | grep -Eqi 'usb:v0BDAp(C820|C811)' ||
	fail "RTL8821CU 模組缺少目標 USB 別名"

modprobe_dirs=()
for directory in etc/modprobe.d usr/lib/modprobe.d lib/modprobe.d; do
	[[ -d "${mount_dir}/${directory}" ]] && modprobe_dirs+=("${mount_dir}/${directory}")
done
if [[ ${#modprobe_dirs[@]} -gt 0 ]] &&
	grep -RhsEq '^[[:space:]]*blacklist[[:space:]]+rtw88_8821cu([[:space:]]|$)' \
		"${modprobe_dirs[@]}"; then
	fail "映像將 rtw88_8821cu 加入黑名單"
fi

package_installed() {
	local package=$1
	awk -v package="${package}" '
		BEGIN { RS = ""; FS = "\n" }
		{
			name = ""
			status = ""
			for (field_index = 1; field_index <= NF; field_index++) {
				if ($field_index ~ /^Package: /) name = substr($field_index, 10)
				if ($field_index ~ /^Status: /) status = substr($field_index, 9)
			}
			if (name == package && status == "install ok installed") found = 1
		}
		END { exit found ? 0 : 1 }
	' "${mount_dir}/var/lib/dpkg/status"
}

for package in bluez bluez-tools ethtool gpiod i2c-tools python3-libgpiod \
	python3-spidev rfkill v4l-utils; do
	package_installed "${package}" || fail "缺少套件：${package}"
done
for tool in usr/local/bin/bpi-h618-hw-info \
	usr/local/sbin/bpi-h618-io-compat-install; do
	[[ -x "${mount_dir}/${tool}" ]] || fail "缺少板型工具：${tool}"
done

echo "映像唯讀內容驗證通過：${image_sha256}"
