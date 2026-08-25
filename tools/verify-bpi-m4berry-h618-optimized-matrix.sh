#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix}"
expected_releases=(bookworm trixie jammy noble resolute)
expected_profiles=(cli xfce)
expected_count=$(( ${#expected_releases[@]} * ${#expected_profiles[@]} ))
verify_archives="${VERIFY_ARCHIVES:-yes}"

for command in awk basename cut find grep lsblk losetup mktemp modinfo mount mountpoint \
	sha256sum sort stat sudo udevadm umount wc xz; do
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
fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

read_metadata_value() {
	local metadata_file=$1
	local key=$2
	local matches=()

	mapfile -t matches < <(grep -E "^${key}=" "${metadata_file}")
	[[ ${#matches[@]} -eq 1 ]] || return 1
	printf '%s\n' "${matches[0]#*=}"
}

require_metadata_value() {
	local metadata_file=$1
	local key=$2
	local expected=$3
	local actual

	actual="$(read_metadata_value "${metadata_file}" "${key}")" ||
		fail "中繼資料缺少唯一欄位 ${key}：${metadata_file}"
	[[ "${actual}" == "${expected}" ]] ||
		fail "中繼資料欄位 ${key} 不符：預期 ${expected}，實際 ${actual}"
}

validate_source_commit() {
	local commit=$1
	[[ "${commit}" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]
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

validate_artifact_identity() {
	local release=$1
	local profile=$2
	local expected_raw_size=$3
	local expected_raw_sha256=$4
	local expected_xz_size=$5
	local expected_xz_sha256=$6
	local img_filename=$7
	local xz_filename=$8
	local expected_source_commit=$9
	local image archive metadata actual_raw_size actual_raw_sha256
	local actual_xz_size actual_xz_sha256 decompressed_sha256 metadata_source_commit

	[[ "$(basename "${img_filename}")" == "${img_filename}" ]] ||
		fail "${release} ${profile} 的 IMG 檔名含有路徑"
	[[ "$(basename "${xz_filename}")" == "${xz_filename}" ]] ||
		fail "${release} ${profile} 的 XZ 檔名含有路徑"
	[[ "${xz_filename}" == "${img_filename}.xz" ]] ||
		fail "${release} ${profile} 的 IMG/XZ 並非同名產物"
	validate_source_commit "${expected_source_commit}" ||
		fail "${release} ${profile} 的矩陣來源不明"

	image="${output_dir}/${img_filename}"
	archive="${output_dir}/${xz_filename}"
	metadata="${image}.metadata.txt"
	[[ -f "${image}" ]] || fail "找不到原始映像：${image}"
	[[ -f "${archive}" ]] || fail "找不到壓縮映像：${archive}"
	[[ -f "${metadata}" ]] || fail "找不到可信中繼資料：${metadata}"

	actual_raw_size=$(stat -c %s "${image}")
	actual_raw_sha256=$(sha256sum "${image}" | cut -d' ' -f1)
	actual_xz_size=$(stat -c %s "${archive}")
	actual_xz_sha256=$(sha256sum "${archive}" | cut -d' ' -f1)
	[[ "${actual_raw_size}" == "${expected_raw_size}" ]] ||
		fail "${release} ${profile} 的 IMG 大小與矩陣不符"
	[[ "${actual_raw_sha256}" == "${expected_raw_sha256}" ]] ||
		fail "${release} ${profile} 的 IMG SHA-256 與矩陣不符"
	[[ "${actual_xz_size}" == "${expected_xz_size}" ]] ||
		fail "${release} ${profile} 的 XZ 大小與矩陣不符"
	[[ "${actual_xz_sha256}" == "${expected_xz_sha256}" ]] ||
		fail "${release} ${profile} 的 XZ SHA-256 與矩陣不符"

	decompressed_sha256=$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)
	[[ "${decompressed_sha256}" == "${actual_raw_sha256}" ]] ||
		fail "${release} ${profile} 的 XZ 解壓資料 SHA-256 與同名 IMG 不一致"

	metadata_source_commit="$(read_metadata_value "${metadata}" source_commit)" ||
		fail "${release} ${profile} 的中繼資料缺少唯一來源"
	validate_source_commit "${metadata_source_commit}" ||
		fail "${release} ${profile} 的中繼資料來源不明"
	require_metadata_value "${metadata}" board bananapim4berry
	require_metadata_value "${metadata}" release "${release}"
	require_metadata_value "${metadata}" profile "${profile}"
	require_metadata_value "${metadata}" build_method full_compile_sh_build
	require_metadata_value "${metadata}" source_commit "${expected_source_commit}"
	require_metadata_value "${metadata}" raw_size "${actual_raw_size}"
	require_metadata_value "${metadata}" raw_sha256 "${actual_raw_sha256}"
	require_metadata_value "${metadata}" xz_size "${actual_xz_size}"
	require_metadata_value "${metadata}" xz_sha256 "${actual_xz_sha256}"

	echo "產物來源與同一性通過：${release} ${profile}"
}

validate_mounted_image() (
	local image=$1
	local release=$2
	local profile=$3
	local loop_device partition mount_dir config_file package module_file modprobe_dir

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
	grep -qx 'CONFIG_RTW88_8821CU=m' "${config_file}" ||
		fail "${release} ${profile} 未啟用主線 RTL8821CU 驅動"

	module_file=$(find "${mount_dir}/lib/modules" -type f \
		-name 'rtw88_8821cu.ko*' -print -quit)
	[[ -n "${module_file}" ]] || fail "${release} ${profile} 缺少 rtw88_8821cu 模組"
	modinfo -F alias "${module_file}" | grep -Eqi 'usb:v0BDApC820' ||
		fail "${release} ${profile} 的 rtw88_8821cu 不支援 0bda:c820"
	for modprobe_dir in etc/modprobe.d usr/lib/modprobe.d lib/modprobe.d; do
		[[ -d "${mount_dir}/${modprobe_dir}" ]] || continue
		if grep -RhsEq \
			'^[[:space:]]*blacklist[[:space:]]+rtw88_8821cu([[:space:]]|$)' \
			"${mount_dir}/${modprobe_dir}"; then
			fail "${release} ${profile} 封鎖了 rtw88_8821cu"
		fi
	done

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
mapfile -t metadata_files < <(find "${output_dir}" -maxdepth 1 -type f -name '*.img.metadata.txt' -print | sort)
mapfile -t partials < <(find "${output_dir}" -maxdepth 1 -type f -name '*.partial' -print)
[[ ${#images[@]} -eq ${expected_count} ]] ||
	fail "原始映像預期 ${expected_count} 個，實際 ${#images[@]} 個"
[[ ${#archives[@]} -eq ${expected_count} ]] ||
	fail "壓縮映像預期 ${expected_count} 個，實際 ${#archives[@]} 個"
[[ ${#metadata_files[@]} -eq ${expected_count} ]] ||
	fail "中繼資料預期 ${expected_count} 個，實際 ${#metadata_files[@]} 個"
[[ ${#partials[@]} -eq 0 ]] || fail "仍有未完成的 .partial 檔案"

IFS= read -r matrix_header <"${output_dir}/MATRIX.tsv"
expected_header=$'release\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_filename\txz_filename\tsource_commit'
[[ "${matrix_header}" == "${expected_header}" ]] || fail "矩陣欄位格式不符"
row_count=$(awk 'NR > 1 && NF == 9 { count++ } END { print count + 0 }' "${output_dir}/MATRIX.tsv")
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
	echo "依 VERIFY_ARCHIVES=no 略過旁車 SHA-256 與批次 xz 測試；仍執行串流同一性驗證。"
fi

while IFS=$'\t' read -r release profile raw_size raw_sha256 xz_size xz_sha256 \
	img_filename xz_filename source_commit; do
	[[ "${release}" == release ]] && continue
	validate_artifact_identity "${release}" "${profile}" "${raw_size}" \
		"${raw_sha256}" "${xz_size}" "${xz_sha256}" "${img_filename}" \
		"${xz_filename}" "${source_commit}"
done <"${output_dir}/MATRIX.tsv"

sudo -n true || {
	echo "唯讀掛載驗證需要免互動 sudo。" >&2
	exit 1
}

while IFS=$'\t' read -r release profile _ _ _ _ img_filename _ _; do
	[[ "${release}" == release ]] && continue
	validate_mounted_image "${output_dir}/${img_filename}" "${release}" "${profile}"
done <"${output_dir}/MATRIX.tsv"

echo "M4 Berry H618 十映像矩陣全部通過唯讀驗證。"
