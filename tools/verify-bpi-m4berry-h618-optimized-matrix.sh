#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix}"
expected_releases=(bookworm trixie jammy noble resolute)
expected_profiles=(cli xfce)
expected_count=$(( ${#expected_releases[@]} * ${#expected_profiles[@]} ))
verify_archives="${VERIFY_ARCHIVES:-yes}"

for command in awk basename find grep lsblk losetup mktemp mount mountpoint \
	sha256sum sort sudo udevadm umount wc xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

[[ -d "${output_dir}" ]] || {
	echo "找不到映像目錄：${output_dir}" >&2
	exit 1
}
[[ -f "${output_dir}/MATRIX.tsv" ]] || {
	echo "找不到完成的矩陣清單：${output_dir}/MATRIX.tsv" >&2
	exit 1
}
grep -qx 'status=complete' "${output_dir}/COMPLETION_STATUS.txt" || {
	echo "矩陣狀態不是 complete。" >&2
	exit 1
}
sudo -n true || {
	echo "唯讀掛載驗證需要免互動 sudo。" >&2
	exit 1
}

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

case "${verify_archives}" in
	yes | no) ;;
	*) fail "VERIFY_ARCHIVES 只接受 yes 或 no" ;;
esac

package_installed() {
	local root_dir=$1
	local package=$2
	awk -v package="${package}" '
		BEGIN { RS = ""; FS = "\n" }
		{
			name = ""
			status = ""
			for (field_index = 1; field_index <= NF; field_index++) {
				if ($field_index ~ /^Package: /) {
					name = substr($field_index, 10)
				}
				if ($field_index ~ /^Status: /) {
					status = substr($field_index, 9)
				}
			}
			if (name == package && status == "install ok installed") {
				found = 1
			}
		}
		END { exit found ? 0 : 1 }
	' "${root_dir}/var/lib/dpkg/status"
}

validate_mounted_image() (
	local image=$1
	local release=$2
	local profile=$3
	local loop_device partition mount_dir config_file package

	mount_dir=$(mktemp -d "${repo_dir}/.tmp/m4berry-verify.XXXXXX")
	loop_device=$(sudo losetup --find --show --partscan --read-only "${image}")
	cleanup_image() {
		if mountpoint -q "${mount_dir}"; then
			sudo umount "${mount_dir}"
		fi
		sudo losetup -d "${loop_device}" 2>/dev/null || true
		rmdir "${mount_dir}" 2>/dev/null || true
	}
	trap cleanup_image EXIT

	udevadm settle
	partition=$(lsblk -nrpo NAME,TYPE "${loop_device}" |
		awk '$2 == "part" { print $1; exit }')
	[[ -n "${partition}" ]] || fail "${image} 沒有可掛載的分割區"
	sudo mount -o ro,noload "${partition}" "${mount_dir}"

	awk -F= '
		$1 == "extraargs" {
			value = substr($0, index($0, "=") + 1)
			count = split(value, arguments, /[[:space:]]+/)
			for (argument_index = 1; argument_index <= count; argument_index++) {
				if (arguments[argument_index] == "cma=256M") {
					found = 1
				}
			}
		}
		END { exit found ? 0 : 1 }
	' "${mount_dir}/boot/armbianEnv.txt" ||
		fail "${release} ${profile} 未設定 cma=256M"

	config_file=$(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print -quit)
	[[ -n "${config_file}" ]] || fail "${release} ${profile} 缺少核心設定檔"
	grep -qx 'CONFIG_VIDEO_SUNXI_CEDRUS=y' "${config_file}" ||
		fail "${release} ${profile} 未內建 Cedrus"
	grep -qx 'CONFIG_SUN50I_H6_PRCM_PPU=y' "${config_file}" ||
		fail "${release} ${profile} 未內建 PPU 電源域"
	grep -qx 'CONFIG_DRM_PANFROST=m' "${config_file}" ||
		fail "${release} ${profile} 未啟用 Panfrost"
	grep -qx 'CONFIG_CRYPTO_DEV_SUN8I_CE=m' "${config_file}" ||
		fail "${release} ${profile} 未啟用 Crypto Engine"

	[[ -s "${mount_dir}/boot/Image" ]] || fail "${release} ${profile} 缺少核心映像"
	[[ -s "${mount_dir}/boot/dtb/allwinner/sun50i-h618-bananapi-m4-berry.dtb" ]] ||
		fail "${release} ${profile} 缺少 M4 Berry DTB"
	[[ -s "${mount_dir}/boot/dtb/allwinner/overlay/sun50i-h616-pwm1-pg19.dtbo" ]] ||
		fail "${release} ${profile} 缺少 PG19 PWM overlay"
	[[ -s "${mount_dir}/boot/dtb/allwinner/overlay/README.sun50i-h616-overlays" ]] ||
		fail "${release} ${profile} 缺少 40-pin overlay 文件"
	[[ -x "${mount_dir}/usr/local/bin/bpi-m4berry-hw-info" ]] ||
		fail "${release} ${profile} 缺少硬體盤點工具"
	[[ -x "${mount_dir}/usr/local/sbin/bpi-m4berry-io-compat-install" ]] ||
		fail "${release} ${profile} 缺少相容層安裝工具"
	[[ -s "${mount_dir}/etc/udev/rules.d/99-bananapi-m4berry-io.rules" ]] ||
		fail "${release} ${profile} 缺少 I/O 權限規則"

	for package in gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils; do
		package_installed "${mount_dir}" "${package}" ||
			fail "${release} ${profile} 缺少套件 ${package}"
	done
	if [[ "${profile}" == xfce ]]; then
		for package in xfce4 gstreamer1.0-tools gstreamer1.0-plugins-bad libdrm-tests; do
			package_installed "${mount_dir}" "${package}" ||
				fail "${release} ${profile} 缺少套件 ${package}"
		done
	fi

	echo "映像內容通過：${release} ${profile}"
)

mapfile -t images < <(find "${output_dir}" -maxdepth 1 -type f -name '*.img' -print | sort)
mapfile -t archives < <(find "${output_dir}" -maxdepth 1 -type f -name '*.img.xz' -print | sort)
mapfile -t partials < <(find "${output_dir}" -maxdepth 1 -type f -name '*.partial' -print)
[[ ${#images[@]} -eq ${expected_count} ]] ||
	fail "原始映像預期 ${expected_count} 個，實際 ${#images[@]} 個"
[[ ${#archives[@]} -eq ${expected_count} ]] ||
	fail "壓縮映像預期 ${expected_count} 個，實際 ${#archives[@]} 個"
[[ ${#partials[@]} -eq 0 ]] || fail "仍有未完成的 .partial 檔案"

row_count=$(awk 'NR > 1 && NF == 8 { count++ } END { print count + 0 }' "${output_dir}/MATRIX.tsv")
[[ ${row_count} -eq ${expected_count} ]] ||
	fail "矩陣清單預期 ${expected_count} 筆，實際 ${row_count} 筆"

for release in "${expected_releases[@]}"; do
	for profile in "${expected_profiles[@]}"; do
		count=$(awk -F '\t' -v release="${release}" -v profile="${profile}" \
			'$1 == release && $2 == profile { count++ } END { print count + 0 }' \
			"${output_dir}/MATRIX.tsv")
		[[ ${count} -eq 1 ]] || fail "${release} ${profile} 的矩陣紀錄數不是 1"
	done
done

if [[ "${verify_archives}" == yes ]]; then
	echo "驗證所有 SHA-256。"
	(
		cd "${output_dir}"
		sha256sum -c -- ./*.sha256
	)

	echo "驗證所有 xz 串流。"
	xz -t -- "${archives[@]}"
else
	echo "依 VERIFY_ARCHIVES=no 略過 SHA-256 與 xz；只檢查映像內容。"
fi

while IFS=$'\t' read -r release profile _ _ _ _ img_filename _; do
	[[ "${release}" == release ]] && continue
	validate_mounted_image "${output_dir}/${img_filename}" "${release}" "${profile}"
done <"${output_dir}/MATRIX.tsv"

echo "M4 Berry H618 十映像矩陣全部通過唯讀驗證。"
