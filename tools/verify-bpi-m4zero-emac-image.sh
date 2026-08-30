#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="${WORK_DIR:-${repo_dir}/.tmp/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
system_tmp_dir="${SYSTEM_TMP_DIR:-/tmp}"
image="${1:-}"
uboot_deb="${2:-}"
archive_argument="${3:-}"
expected_image_sha256="${4:-}"
expected_image_size="${5:-}"

usage() {
	echo "用法：$0 <映像.img> <U-Boot 套件.deb> [來源映像.img.xz] [預期原始 SHA-256] [預期原始大小]" >&2
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

for command in awk basename cmp cut dd dpkg-deb dtc dumpimage e2fsck fdtget file find flock \
	grep lsblk lsinitramfs losetup mkdir mktemp modinfo mount mountpoint od readlink rm \
	sfdisk sha256sum sort stat sudo tr udevadm umount xz; do
	command -v "${command}" >/dev/null || fail "缺少必要命令：${command}"
done
sudo -n true || fail "唯讀掛載需要免互動 sudo"
[[ -d "${system_tmp_dir}" && -w "${system_tmp_dir}" ]] ||
	fail "系統暫存目錄不存在或不可寫入：${system_tmp_dir}"

if [[ -n "${BPI_M4ZERO_EMAC_LOCK_FD:-}" ]]; then
	[[ "${BPI_M4ZERO_EMAC_LOCK_FD}" =~ ^[0-9]+$ ]] || fail "繼承的建置鎖檔案描述符格式不符"
	lock_fd_path="/proc/$$/fd/${BPI_M4ZERO_EMAC_LOCK_FD}"
	[[ -e "${lock_fd_path}" ]] || fail "繼承的建置鎖檔案描述符不存在"
	[[ "$(readlink -f "${lock_fd_path}")" == "$(readlink -f "${work_dir}/.build.lock")" ]] ||
		fail "繼承的建置鎖未指向矩陣工作目錄"
	flock -n "${BPI_M4ZERO_EMAC_LOCK_FD}" || fail "繼承的建置鎖無效"
else
	[[ -d "${work_dir}" ]] || fail "找不到矩陣工作目錄，無法取得建置鎖：${work_dir}"
	exec 8>"${work_dir}/.build.lock"
	flock 8
fi

verification_dir="$(mktemp -d "${system_tmp_dir}/m4zero-emac-image.XXXXXX")"
extract_dir="${verification_dir}/uboot"
mount_dir="${verification_dir}/root"
mkdir -p "${extract_dir}" "${mount_dir}"
loop_device=""

cleanup() {
	if mountpoint -q "${mount_dir}"; then
		sudo -n umount "${mount_dir}" || true
	fi
	if [[ -n "${loop_device}" ]]; then
		sudo -n losetup -d "${loop_device}" 2>/dev/null || true
	fi
	rm -rf -- "${verification_dir}"
}
trap cleanup EXIT

archive="${archive_argument:-${image}.xz}"
archive_sum="${archive}.sha"
image_sum="${image}.sha"
[[ -f "${archive}" ]] || fail "缺少 XZ 壓縮映像：${archive}"
[[ -f "${archive_sum}" ]] || fail "缺少 XZ SHA-256 檔：${archive_sum}"
[[ "$(basename -- "${archive}")" == "$(basename -- "${image}").xz" ]] ||
	fail "原始映像與 XZ 壓縮映像檔名不一致"

image_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
image_size="$(stat -c %s "${image}")"
archive_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
recorded_archive_sha256="$(awk 'NF { print $1; exit }' "${archive_sum}")"
recorded_archive_filename="$(awk 'NF { print $2; exit }' "${archive_sum}")"
[[ "${recorded_archive_sha256}" == "${archive_sha256}" ]] ||
	fail "XZ SHA-256 記錄不符"
[[ "${recorded_archive_filename}" == "$(basename -- "${archive}")" ]] ||
	fail "XZ SHA-256 記錄的檔名不符"
xz -t "${archive}"
decompressed_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
[[ "${decompressed_sha256}" == "${image_sha256}" ]] ||
	fail "XZ 解壓資料與原始映像不一致"

if [[ -n "${expected_image_sha256}" ]]; then
	[[ "${expected_image_sha256}" =~ ^[0-9a-f]{64}$ ]] || fail "預期原始 SHA-256 格式不符"
	[[ "${image_sha256}" == "${expected_image_sha256}" ]] || fail "原始映像 SHA-256 與矩陣清單不符"
fi
if [[ -n "${expected_image_size}" ]]; then
	[[ "${expected_image_size}" =~ ^[0-9]+$ ]] || fail "預期原始大小格式不符"
	[[ "${image_size}" == "${expected_image_size}" ]] || fail "原始映像大小與矩陣清單不符"
fi

if [[ -z "${archive_argument}" ]]; then
	[[ -f "${image_sum}" ]] || fail "缺少原始映像 SHA-256 檔：${image_sum}"
	recorded_image_sha256="$(awk 'NF { print $1; exit }' "${image_sum}")"
	recorded_image_filename="$(awk 'NF { print $2; exit }' "${image_sum}")"
	[[ "${recorded_image_sha256}" == "${image_sha256}" ]] ||
		fail "原始映像 SHA-256 記錄不符"
	[[ "${recorded_image_filename}" == "$(basename -- "${image}")" ]] ||
		fail "原始映像 SHA-256 記錄的檔名不符"
fi
echo "XZ 與 SHA-256 同一性通過：${image_sha256}"

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

sfdisk --verify "${image}" >/dev/null || fail "映像分割表驗證失敗"
loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")"
udevadm settle
mapfile -t partitions < <(lsblk -nrpo NAME,TYPE "${loop_device}" | awk '$2 == "part" { print $1 }')
[[ ${#partitions[@]} -eq 1 ]] || fail "映像分割區數量不是 1"
partition="${partitions[0]}"
filesystem_type="$(lsblk -nrpo FSTYPE "${partition}" | awk 'NF { print; exit }')"
[[ "${filesystem_type}" == ext4 ]] || fail "根檔案系統不是 ext4：${filesystem_type}"
sudo -n e2fsck -fn "${partition}" || fail "根檔案系統 e2fsck 唯讀檢查失敗"
sudo -n mount -o ro,noload "${partition}" "${mount_dir}"

read_env_value() {
	local key=$1
	local env_file="${mount_dir}/boot/armbianEnv.txt"
	local values=()
	[[ -f "${env_file}" ]] || return 1
	mapfile -t values < <(awk -F= -v key="${key}" '$1 == key { print substr($0, index($0, "=") + 1) }' "${env_file}")
	[[ ${#values[@]} -eq 1 ]] || return 1
	printf '%s\n' "${values[0]}"
}

require_env_value() {
	local key=$1
	local expected=$2
	local actual
	actual="$(read_env_value "${key}")" || fail "armbianEnv.txt 缺少唯一欄位 ${key}"
	[[ "${actual}" == "${expected}" ]] || fail "armbianEnv.txt 的 ${key} 不符：${actual}"
}

require_env_token() {
	local key=$1
	local expected=$2
	local actual
	actual="$(read_env_value "${key}")" || fail "armbianEnv.txt 缺少唯一欄位 ${key}"
	grep -Eq "(^|[[:space:]])${expected}([[:space:]]|$)" <<<"${actual}" ||
		fail "armbianEnv.txt 的 ${key} 缺少 ${expected}"
}

require_boot_link() {
	local link_name=$1
	local target_name=$2
	local link_path="${mount_dir}/boot/${link_name}"
	[[ -L "${link_path}" ]] || fail "核心啟動連結不存在：/boot/${link_name}"
	[[ "$(readlink "${link_path}")" == "${target_name}" ]] ||
		fail "核心啟動連結目標不符：/boot/${link_name}"
	[[ -s "${mount_dir}/boot/${target_name}" ]] ||
		fail "核心啟動連結目標不存在：/boot/${target_name}"
}

require_boot_directory_link() {
	local link_name=$1
	local target_name=$2
	local link_path="${mount_dir}/boot/${link_name}"
	[[ -L "${link_path}" ]] || fail "核心啟動目錄連結不存在：/boot/${link_name}"
	[[ "$(readlink "${link_path}")" == "${target_name}" ]] ||
		fail "核心啟動目錄連結目標不符：/boot/${link_name}"
	[[ -d "${mount_dir}/boot/${target_name}" ]] ||
		fail "核心啟動目錄連結目標不存在：/boot/${target_name}"
}

require_env_value fdtfile sun50i-h618-bananapi-m4-zero-emac.dtb
require_env_value overlays bananapi-m4-zero-emac-sdio-wifi-bt
require_env_token extraargs cma=256M

mapfile -t config_files < <(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print | sort)
[[ ${#config_files[@]} -eq 1 ]] || fail "核心設定檔數量不是 1"
config_file="${config_files[0]}"
kernel_version="${config_file##*/config-}"
[[ "${kernel_version}" =~ ^[A-Za-z0-9._+~-]+$ ]] || fail "核心版本格式不符：${kernel_version}"
for boot_file in \
	"config-${kernel_version}" \
	"System.map-${kernel_version}" \
	"vmlinuz-${kernel_version}" \
	"initrd.img-${kernel_version}" \
	"uInitrd-${kernel_version}" \
	boot.cmd \
	boot.scr; do
	[[ -s "${mount_dir}/boot/${boot_file}" ]] || fail "缺少核心啟動檔：/boot/${boot_file}"
done
require_boot_link Image "vmlinuz-${kernel_version}"
require_boot_link uInitrd "uInitrd-${kernel_version}"
require_boot_directory_link dtb "dtb-${kernel_version}"
initrd_path="${mount_dir}/boot/initrd.img-${kernel_version}"
uinitrd_path="${mount_dir}/boot/uInitrd-${kernel_version}"
kernel_path="${mount_dir}/boot/vmlinuz-${kernel_version}"
boot_script_path="${mount_dir}/boot/boot.scr"
dumpimage -l "${boot_script_path}" >/dev/null || fail "boot.scr 不是有效的 U-Boot script 映像"
dumpimage -l "${uinitrd_path}" >/dev/null || fail "uInitrd 不是有效的 U-Boot ramdisk 映像"
boot_script_payload="${verification_dir}/boot.scr.payload"
dumpimage -T script -p 0 -o "${boot_script_payload}" "${boot_script_path}" >/dev/null ||
	fail "無法擷取 boot.scr payload"
boot_cmd_size="$(stat -c %s "${mount_dir}/boot/boot.cmd")"
boot_payload_size="$(stat -c %s "${boot_script_payload}")"
boot_payload_recorded_size="$(od -An -N4 -tu4 --endian=big "${boot_script_payload}" | tr -d '[:space:]')"
boot_payload_terminator="$(od -An -j4 -N4 -tx1 "${boot_script_payload}" | tr -d '[:space:]')"
[[ "${boot_payload_size}" -eq $(( boot_cmd_size + 8 )) &&
	"${boot_payload_recorded_size}" == "${boot_cmd_size}" && "${boot_payload_terminator}" == 00000000 ]] ||
	fail "boot.scr script 資料表與 boot.cmd 大小不一致"
boot_script_content="${verification_dir}/boot.scr.content"
dd if="${boot_script_payload}" of="${boot_script_content}" bs=1 skip=8 status=none
cmp -s "${boot_script_content}" "${mount_dir}/boot/boot.cmd" ||
	fail "boot.scr 內容與 boot.cmd 不一致"
kernel_file_type="$(file -b "${kernel_path}")"
grep -Fq 'Linux kernel ARM64 boot executable Image' <<<"${kernel_file_type}" ||
	fail "核心映像格式不是 ARM64 Linux Image：${kernel_file_type}"
uinitrd_payload="${verification_dir}/uInitrd.payload"
dumpimage -T ramdisk -p 0 -o "${uinitrd_payload}" "${uinitrd_path}" >/dev/null ||
	fail "無法擷取 uInitrd payload"
cmp -s "${uinitrd_payload}" "${initrd_path}" || fail "uInitrd 內容與同版 initrd.img 不一致"
initramfs_listing="${verification_dir}/initramfs.list"
lsinitramfs "${initrd_path}" >"${initramfs_listing}" || fail "無法列出 initrd 內容"
mapfile -t initrd_module_versions < <(
	awk -F/ '
			$1 == "usr" && $2 == "lib" && $3 == "modules" && NF >= 4 { print $4 }
			$1 == "lib" && $2 == "modules" && NF >= 3 { print $3 }
		' "${initramfs_listing}" | sort -u
)
[[ ${#initrd_module_versions[@]} -eq 1 && "${initrd_module_versions[0]}" == "${kernel_version}" ]] ||
	fail "initrd 內核心模組版本與核心啟動檔不一致"

mapfile -t module_directories < <(find "${mount_dir}/lib/modules" -mindepth 1 -maxdepth 1 -type d -print | sort)
[[ ${#module_directories[@]} -eq 1 ]] || fail "核心模組目錄數量不是 1"
module_dir="${module_directories[0]}"
[[ "${module_dir##*/}" == "${kernel_version}" ]] || fail "核心模組版本與核心啟動檔不一致"
[[ -s "${module_dir}/modules.dep" ]] || fail "核心模組缺少 modules.dep"
echo "核心啟動檔與模組版本一致：${kernel_version}"

dtb="${mount_dir}/boot/dtb/allwinner/sun50i-h618-bananapi-m4-zero-emac.dtb"
overlay="${mount_dir}/boot/dtb/allwinner/overlay/sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtbo"
[[ -s "${dtb}" ]] || fail "缺少 M4 Zero EMAC DTB"
[[ -s "${overlay}" ]] || fail "缺少 M4 Zero EMAC Wi-Fi／Bluetooth overlay"
overlay_dts="${verification_dir}/m4zero-emac-overlay.dts"
dtc -I dtb -O dts -q -o "${overlay_dts}" "${overlay}" || fail "Wi-Fi／Bluetooth overlay 無法解析"
overlay_compatible="$(fdtget "${overlay}" / compatible)" || fail "overlay 缺少 compatible"
for compatible in sinovoip,bpi-m4-zero-emac sinovoip,bpi-m4-zero \
	allwinner,sun50i-h616 allwinner,sun50i-h618; do
	grep -Eq "(^|[[:space:]])${compatible}([[:space:]]|$)" <<<"${overlay_compatible}" ||
		fail "overlay compatible 缺少 ${compatible}"
done
[[ "$(fdtget "${overlay}" /fragment@0/__overlay__ status)" == okay ]] ||
	fail "overlay 未啟用 mmc1"
[[ "$(fdtget "${overlay}" /fragment@1/__overlay__ status)" == okay ]] ||
	fail "overlay 未啟用 uart1"
[[ "$(fdtget -tx "${overlay}" /fragment@1/__overlay__ pinctrl-0)" == "ffffffff ffffffff" ]] ||
	fail "overlay 的 UART1 pinctrl 規格不符"
[[ "$(fdtget "${overlay}" /fragment@1/__overlay__ pinctrl-names)" == default ]] ||
	fail "overlay 的 UART1 pinctrl-names 不符"
fdtget "${overlay}" /fragment@1/__overlay__ uart-has-rtscts >/dev/null ||
	fail "overlay 缺少 UART1 RTS／CTS 設定"
overlay_bluetooth_node=/fragment@1/__overlay__/bluetooth
[[ "$(fdtget "${overlay}" "${overlay_bluetooth_node}" compatible)" == brcm,bcm43540-bt ]] ||
	fail "overlay 的 Bluetooth compatible 不符"
[[ "$(fdtget -tx "${overlay}" "${overlay_bluetooth_node}" host-wakeup-gpios)" == "ffffffff 6 10 0" ]] ||
	fail "overlay 的 Bluetooth host-wakeup GPIO 不符"
[[ "$(fdtget -tx "${overlay}" "${overlay_bluetooth_node}" device-wakeup-gpios)" == "ffffffff 6 11 0" ]] ||
	fail "overlay 的 Bluetooth device-wakeup GPIO 不符"
[[ "$(fdtget -tx "${overlay}" "${overlay_bluetooth_node}" shutdown-gpios)" == "ffffffff 6 13 0" ]] ||
	fail "overlay 的 Bluetooth shutdown GPIO 不符"
[[ "$(fdtget "${overlay}" "${overlay_bluetooth_node}" max-speed)" == 1500000 ]] ||
	fail "overlay 的 Bluetooth UART 速率不符"
[[ "$(fdtget -tx "${overlay}" "${overlay_bluetooth_node}" vbat-supply)" == ffffffff ]] ||
	fail "overlay 的 Bluetooth vbat-supply 不符"
[[ "$(fdtget -tx "${overlay}" "${overlay_bluetooth_node}" vddio-supply)" == ffffffff ]] ||
	fail "overlay 的 Bluetooth vddio-supply 不符"
[[ "$(fdtget "${overlay}" /__fixups__ mmc1)" == /fragment@0:target:0 ]] ||
	fail "overlay 的 mmc1 fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ uart1)" == /fragment@1:target:0 ]] ||
	fail "overlay 的 uart1 fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ uart1_pins)" == /fragment@1/__overlay__:pinctrl-0:0 ]] ||
	fail "overlay 的 uart1_pins fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ uart1_rts_cts_pins)" == /fragment@1/__overlay__:pinctrl-0:4 ]] ||
	fail "overlay 的 uart1_rts_cts_pins fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ pio)" == "/fragment@1/__overlay__/bluetooth:host-wakeup-gpios:0 /fragment@1/__overlay__/bluetooth:device-wakeup-gpios:0 /fragment@1/__overlay__/bluetooth:shutdown-gpios:0" ]] ||
	fail "overlay 的 Bluetooth GPIO fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ reg_vcc3v3)" == /fragment@1/__overlay__/bluetooth:vbat-supply:0 ]] ||
	fail "overlay 的 Bluetooth vbat-supply fixup 不符"
[[ "$(fdtget "${overlay}" /__fixups__ reg_vcc1v8)" == /fragment@1/__overlay__/bluetooth:vddio-supply:0 ]] ||
	fail "overlay 的 Bluetooth vddio-supply fixup 不符"

fdt_string() {
	fdtget "${dtb}" "$1" "$2" 2>/dev/null
}

fdt_hex() {
	fdtget -tx "${dtb}" "$1" "$2" 2>/dev/null
}

require_fdt_string() {
	local node=$1
	local property=$2
	local expected=$3
	local actual
	actual="$(fdt_string "${node}" "${property}")" || fail "DTB 缺少 ${node}/${property}"
	[[ "${actual}" == "${expected}" ]] || fail "DTB 的 ${node}/${property} 不符：${actual}"
}

require_fdt_phandle() {
	local source_node=$1
	local property=$2
	local target_node=$3
	local source_value target_value
	source_value="$(fdt_hex "${source_node}" "${property}")" ||
		fail "DTB 缺少 ${source_node}/${property}"
	target_value="$(fdt_hex "${target_node}" phandle)" ||
		fail "DTB 目標節點缺少 phandle：${target_node}"
	[[ "${source_value}" == "${target_value}" ]] ||
		fail "DTB 的 ${source_node}/${property} 未指向 ${target_node}"
}

emac_node=/soc/ethernet@5030000
mdio_node=${emac_node}/mdio
mdio_mux_node=${emac_node}/mdio-mux
internal_mdio_node=${mdio_mux_node}/mdio@1
phy_node=${internal_mdio_node}/ethernet-phy@0
rmii_pins_node=/soc/pinctrl@300b000/rmii-pins
calibration_node=/soc/efuse@3006000/ephy-calibration@2c
ac300_clock_node=/ac300-clk
pwm_controller_node=/soc/pwm@300a000
pwm5_node=/soc/pwm5@0300a000
pwm5_pin_node=/soc/pinctrl@300b000/pwm5-pin
ccu_node=/soc/clock@3001000
pio_node=/soc/pinctrl@300b000
mmc1_node=/soc/mmc@4021000
mmc1_pins_node=${pio_node}/mmc1-pins
wifi_node=${mmc1_node}/wifi@1
wifi_pwrseq_node=/wifi-pwrseq
reg_vcc3v3_node=/regulator-vcc3v3

require_fdt_string / model "BananaPi BPI-M4-Zero EMAC"
require_fdt_string /aliases ethernet0 "${emac_node}"
require_fdt_string /soc/gpu@1800000 status okay
require_fdt_string "${mmc1_node}" status disabled
[[ "$(fdt_string "${mmc1_node}" bus-width)" == 4 ]] || fail "DTB 的 SDIO 匯流排寬度不符"
fdtget "${dtb}" "${mmc1_node}" non-removable >/dev/null || fail "DTB 的 SDIO 缺少 non-removable"
fdtget "${dtb}" "${mmc1_node}" keep-power-in-suspend >/dev/null ||
	fail "DTB 的 SDIO 缺少 keep-power-in-suspend"
require_fdt_phandle "${mmc1_node}" pinctrl-0 "${mmc1_pins_node}"
require_fdt_phandle "${mmc1_node}" mmc-pwrseq "${wifi_pwrseq_node}"
require_fdt_phandle "${mmc1_node}" vmmc-supply "${reg_vcc3v3_node}"
require_fdt_string "${mmc1_pins_node}" function mmc1
require_fdt_string "${wifi_node}" compatible brcm,bcm4329-fmac
[[ "$(fdt_hex "${wifi_node}" reg)" == 1 ]] || fail "DTB 的板載 SDIO Wi-Fi 位址不符"
require_fdt_string "${wifi_pwrseq_node}" compatible mmc-pwrseq-simple
[[ "$(fdt_string "${wifi_pwrseq_node}" post-power-on-delay-ms)" == 200 ]] ||
	fail "DTB 的 Wi-Fi 上電延遲不符"
pio_phandle="$(fdt_hex "${pio_node}" phandle)"
[[ "$(fdt_hex "${wifi_pwrseq_node}" reset-gpios)" == "${pio_phandle} 6 12 1" ]] ||
	fail "DTB 的 Wi-Fi reset GPIO 不符"
require_fdt_string "${emac_node}" status okay
emac_compatible="$(fdt_string "${emac_node}" compatible)" || fail "DTB 缺少 EMAC compatible"
grep -Fq 'allwinner,sun50i-h616-internal-emac' <<<"${emac_compatible}" ||
	fail "DTB 的 EMAC compatible 不符"
require_fdt_string "${emac_node}" pinctrl-names default
require_fdt_phandle "${emac_node}" pinctrl-0 "${rmii_pins_node}"
require_fdt_string "${rmii_pins_node}" function emac1
require_fdt_string "${emac_node}" phy-mode rmii
require_fdt_phandle "${emac_node}" phy-handle "${phy_node}"
require_fdt_string "${emac_node}" reset-names stmmaceth
read -r -a emac_resets <<<"$(fdt_hex "${emac_node}" resets)"
ccu_phandle="$(fdt_hex "${ccu_node}" phandle)"
[[ "${emac_resets[*]}" == "${ccu_phandle} 1f" ]] || fail "DTB 的 EMAC reset 規格不符"
[[ "$(fdt_string "${rmii_pins_node}" pins)" == "PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PA8 PA9" ]] ||
	fail "DTB 的 RMII pin 列表不符"
[[ "$(fdt_string "${rmii_pins_node}" drive-strength)" == 40 ]] ||
	fail "DTB 的 RMII 驅動強度不符"
[[ "$(fdt_string "${emac_node}" allwinner,rx-delay-ps)" == 3100 ]] || fail "EMAC RX 延遲不符"
[[ "$(fdt_string "${emac_node}" allwinner,tx-delay-ps)" == 700 ]] || fail "EMAC TX 延遲不符"

require_fdt_string "${mdio_node}" compatible snps,dwmac-mdio
require_fdt_string "${mdio_mux_node}" compatible allwinner,sun8i-h3-mdio-mux
require_fdt_phandle "${mdio_mux_node}" mdio-parent-bus "${mdio_node}"
require_fdt_string "${internal_mdio_node}" compatible allwinner,sun8i-h3-mdio-internal
[[ "$(fdt_hex "${internal_mdio_node}" reg)" == 1 ]] || fail "DTB 的 internal MDIO 編號不符"
require_fdt_string "${phy_node}" status okay
[[ "$(fdt_hex "${phy_node}" reg)" == 0 ]] || fail "DTB 的 AC300 EPHY 位址不符"
phy_compatible="$(fdt_string "${phy_node}" compatible)" || fail "DTB 缺少 AC300 EPHY compatible"
grep -Fq 'allwinner,sun50i-h618-ac300-ephy' <<<"${phy_compatible}" || fail "DTB 缺少 AC300 EPHY"
require_fdt_string "${phy_node}" clock-names "ephy pwm"
require_fdt_string "${phy_node}" nvmem-cell-names calibration
require_fdt_phandle "${phy_node}" nvmem-cells "${calibration_node}"
[[ "$(fdt_hex "${calibration_node}" reg)" == "2c 2" ]] || fail "AC300 EPHY 校準 NVMEM 範圍不符"

require_fdt_string "${ac300_clock_node}" compatible pwm-clock
require_fdt_string "${ac300_clock_node}" status okay
[[ "$(fdt_string "${ac300_clock_node}" clock-frequency)" == 2000000 ]] ||
	fail "AC300 PWM 時鐘頻率不符"
read -r -a pwm_specifier <<<"$(fdt_hex "${ac300_clock_node}" pwms)"
pwm_phandle="$(fdt_hex "${pwm_controller_node}" phandle)"
[[ ${#pwm_specifier[@]} -eq 4 && "${pwm_specifier[0]}" == "${pwm_phandle}" &&
	"${pwm_specifier[1]}" == 5 && "${pwm_specifier[2]}" == 1f4 && "${pwm_specifier[3]}" == 0 ]] ||
	fail "AC300 PWM 規格不是第 5 通道、500 ns、一般極性"
require_fdt_string "${pwm_controller_node}" status okay
require_fdt_string "${pwm5_node}" status okay
require_fdt_string "${pwm5_node}" pinctrl-names default
require_fdt_phandle "${pwm5_node}" pinctrl-0 "${pwm5_pin_node}"
require_fdt_string "${pwm5_pin_node}" function pwm5
[[ "$(fdt_string "${pwm5_node}" clk_bypass_output)" == 1 ]] || fail "PWM5 時鐘旁路設定不符"
read -r -a phy_clocks <<<"$(fdt_hex "${phy_node}" clocks)"
ac300_clock_phandle="$(fdt_hex "${ac300_clock_node}" phandle)"
[[ "${phy_clocks[*]}" == "${ccu_phandle} 51 ${ac300_clock_phandle}" ]] ||
	fail "AC300 EPHY 的時鐘引用不符"

cpu_phandle="$(fdt_hex /cpus/cpu@0 phandle)"
thermal_base=/thermal-zones/cpu-thermal
trip0="${thermal_base}/trips/cpu-trip-0"
trip1="${thermal_base}/trips/cpu-trip-1"
map0="${thermal_base}/cooling-maps/map0"
map1="${thermal_base}/cooling-maps/map1"
[[ "$(fdt_string "${trip0}" temperature)" == 60000 ]] || fail "CPU 第一級被動節流溫度不符"
[[ "$(fdt_string "${trip1}" temperature)" == 70000 ]] || fail "CPU 第二級被動節流溫度不符"
require_fdt_string "${trip0}" type passive
require_fdt_string "${trip1}" type passive
[[ "$(fdt_hex "${map0}" trip)" == "$(fdt_hex "${trip0}" phandle)" ]] ||
	fail "CPU 第一級節流未綁定正確 trip"
[[ "$(fdt_hex "${map1}" trip)" == "$(fdt_hex "${trip1}" phandle)" ]] ||
	fail "CPU 第二級節流未綁定正確 trip"
[[ "$(fdt_hex "${map0}" cooling-device)" == "${cpu_phandle} 1 3" ]] ||
	fail "CPU 第一級 cooling-device 範圍不符"
[[ "$(fdt_hex "${map1}" cooling-device)" == "${cpu_phandle} 4 ffffffff" ]] ||
	fail "CPU 第二級 cooling-device 範圍不符"

for setting in \
	CONFIG_DWMAC_SUN8I=m \
	CONFIG_AC300_PHY=y \
	CONFIG_BRCMFMAC=m \
	CONFIG_BT_HCIUART=m \
	CONFIG_BT_HCIUART_BCM=y \
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
	local candidates=()
	local module vermagic
	mapfile -t candidates < <(find "${module_dir}" -type f -name "${pattern}" -print)
	[[ ${#candidates[@]} -eq 1 ]] || fail "核心模組 ${pattern} 的數量不是 1"
	module="${candidates[0]}"
	vermagic="$(modinfo -F vermagic "${module}")" || fail "無法讀取核心模組版本：${pattern}"
	[[ "${vermagic%% *}" == "${kernel_version}" ]] || fail "核心模組版本不符：${pattern}"
	printf '%s\n' "${module}"
}

require_module 'dwmac-sun8i.ko*' >/dev/null
require_module 'brcmfmac.ko*' >/dev/null
require_module 'hci_uart.ko*' >/dev/null
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

require_firmware_alias() {
	local alias_name=$1
	local target_name=$2
	local firmware_dir="${mount_dir}/lib/firmware/updates/brcm"
	local alias_path="${firmware_dir}/${alias_name}"
	local image_target="${firmware_dir}/${target_name}"
	local source_target="${repo_dir}/packages/bsp/bananapi/brcm/${target_name}"
	local image_firmware_sha256 source_firmware_sha256

	[[ -L "${alias_path}" ]] || fail "Broadcom 韌體別名不是符號連結：${alias_name}"
	[[ "$(readlink "${alias_path}")" == "${target_name}" ]] ||
		fail "Broadcom 韌體別名目標不符：${alias_name}"
	[[ -s "${image_target}" ]] || fail "映像缺少 Broadcom 韌體目標：${target_name}"
	[[ -s "${source_target}" ]] || fail "倉庫缺少 Broadcom 韌體來源：${target_name}"
	image_firmware_sha256="$(sha256sum "${image_target}" | cut -d' ' -f1)"
	source_firmware_sha256="$(sha256sum "${source_target}" | cut -d' ' -f1)"
	[[ "${image_firmware_sha256}" == "${source_firmware_sha256}" ]] ||
		fail "Broadcom 韌體與倉庫來源不一致：${target_name}"
}

require_firmware_alias \
	'brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.bin' \
	'cyfmac43455-sdio.bin'
require_firmware_alias \
	'brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.txt' \
	'cyfmac43455-sdio.1LC.txt'
require_firmware_alias \
	'brcmfmac43455-sdio.sinovoip,bpi-m4-zero-emac.clm_blob' \
	'cyfmac43455-sdio.1LC.clm_blob'
require_firmware_alias \
	'BCM4345C0.sinovoip,bpi-m4-zero-emac.hcd' \
	'BCM4345C0_003.001.025.0187.0366.1MW.hcd'

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
[[ -x "${mount_dir}/usr/sbin/ethtool" ]] || fail "缺少 ethtool 執行檔"

controlled_sysctl="${mount_dir}/usr/lib/sysctl.d/60-armbian-defaults.conf"
source_sysctl="${repo_dir}/packages/bsp/common/usr/lib/sysctl.d/60-armbian-defaults.conf"
[[ -s "${controlled_sysctl}" ]] || fail "缺少新版 Armbian sysctl 設定檔"
[[ -s "${source_sysctl}" ]] || fail "倉庫缺少受控 Armbian sysctl 來源"
cmp -s "${controlled_sysctl}" "${source_sysctl}" || fail "映像內 Armbian sysctl 與受控來源不一致"
mapfile -t bsp_package_lists < <(
	find "${mount_dir}/var/lib/dpkg/info" -maxdepth 1 -type f \
		-name 'armbian-bsp-cli-bananapim4zeroemac-current.list' -print
)
[[ ${#bsp_package_lists[@]} -eq 1 ]] || fail "BSP 套件檔案清單數量不是 1"
grep -Fxq '/usr/lib/sysctl.d/60-armbian-defaults.conf' "${bsp_package_lists[0]}" ||
	fail "BSP 套件未擁有受控 Armbian sysctl 設定檔"
if grep -Fxq '/usr/lib/sysctl.d/50-default.conf' "${bsp_package_lists[0]}"; then
	fail "BSP 套件仍包含會與舊版 systemd 衝突的 50-default.conf"
fi

for tool in usr/local/bin/bpi-h618-hw-info \
	usr/local/sbin/bpi-h618-io-compat-install; do
	[[ -x "${mount_dir}/${tool}" ]] || fail "缺少板型工具：${tool}"
done

echo "映像唯讀內容驗證通過：${image_sha256}"
