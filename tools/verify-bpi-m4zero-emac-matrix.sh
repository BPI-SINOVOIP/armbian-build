#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
work_dir="${WORK_DIR:-${repo_dir}/.tmp/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
system_tmp_dir="${SYSTEM_TMP_DIR:-/tmp}"
expected_releases=(bookworm trixie jammy noble resolute)
expected_profiles=(cli xfce)
expected_count=$(( ${#expected_releases[@]} * ${#expected_profiles[@]} ))

for command in awk basename cmp df dpkg-deb find flock git grep lsblk losetup mktemp mount \
	mountpoint rm sha256sum sort stat sudo udevadm umount xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

[[ -d "${output_dir}" ]] || fail "找不到交付目錄：${output_dir}"
[[ -d "${work_dir}" ]] || fail "找不到矩陣工作目錄：${work_dir}"
[[ -d "${system_tmp_dir}" && -w "${system_tmp_dir}" ]] ||
	fail "系統暫存目錄不存在或不可寫入：${system_tmp_dir}"

# 與建置器共用同一把排他鎖，避免讀到仍在更新的清單或壓縮映像。
exec 9>"${work_dir}/.build.lock"
flock 9

matrix_file="${work_dir}/MATRIX.tsv"
[[ -f "${matrix_file}" ]] || fail "找不到矩陣清單"
image_manifest="${output_dir}/IMAGE_MANIFEST.tsv"
provenance_file="${output_dir}/BUILD_PROVENANCE.tsv"
image_sums="${output_dir}/SHA256SUMS"
delivery_sums="${output_dir}/DELIVERY_METADATA_SHA256SUMS"
for delivery_file in "${image_manifest}" "${provenance_file}" "${image_sums}" "${delivery_sums}"; do
	[[ -f "${delivery_file}" ]] || fail "交付目錄缺少清單：$(basename "${delivery_file}")"
done
expected_header=$'release\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_filename\txz_filename'
IFS= read -r actual_header <"${matrix_file}"
[[ "${actual_header}" == "${expected_header}" ]] || fail "矩陣清單欄位標頭不符"
cmp -s "${matrix_file}" "${image_manifest}" || fail "公開映像清單與建置矩陣不一致"
grep -qx 'status=complete' "${work_dir}/COMPLETION_STATUS.txt" ||
	fail "矩陣建置狀態不是 complete"
sudo -n true || fail "唯讀掛載需要免互動 sudo"

audit_dir="$(mktemp -d "${system_tmp_dir}/m4zero-emac-audit.XXXXXX")"
cleanup_audit() {
	rm -rf -- "${audit_dir}"
}
trap cleanup_audit EXIT
awk -F '\t' 'NR > 1 { print $6 "  " $8 }' "${matrix_file}" | sort >"${audit_dir}/expected-image-sums"
sort "${image_sums}" >"${audit_dir}/actual-image-sums"
cmp -s "${audit_dir}/expected-image-sums" "${audit_dir}/actual-image-sums" ||
	fail "SHA256SUMS 與建置矩陣不一致"
awk '$2 != "VALIDATION_REPORT.txt" { print }' "${delivery_sums}" >"${audit_dir}/metadata-sums"
grep -Eq '^[0-9a-f]{64}[[:space:]]+VALIDATION_REPORT\.txt$' "${delivery_sums}" ||
	fail "交付中繼資料雜湊缺少驗證報告"
(
	cd "${output_dir}"
	sha256sum -c "${audit_dir}/metadata-sums" >/dev/null
) || fail "交付中繼資料雜湊不符"

mapfile -t archives < <(find "${output_dir}" -maxdepth 1 -type f -name '*.img.xz' -print | sort)
mapfile -t partials < <(find "${output_dir}" "${work_dir}" -maxdepth 1 -type f -name '*.partial' -print)
[[ ${#archives[@]} -eq ${expected_count} ]] ||
	fail "壓縮映像預期 ${expected_count} 個，實際 ${#archives[@]} 個"
[[ ${#partials[@]} -eq 0 ]] || fail "仍有未完成的 .partial 檔案"

row_count="$(awk 'NR > 1 && NF == 8 { count++ } END { print count + 0 }' "${matrix_file}")"
[[ ${row_count} -eq ${expected_count} ]] ||
	fail "矩陣清單預期 ${expected_count} 筆，實際 ${row_count} 筆"

for release in "${expected_releases[@]}"; do
	for profile in "${expected_profiles[@]}"; do
		count="$(awk -F '\t' -v release="${release}" -v profile="${profile}" \
			'$1 == release && $2 == profile { count++ } END { print count + 0 }' \
			"${matrix_file}")"
		[[ ${count} -eq 1 ]] || fail "${release} ${profile} 的矩陣紀錄數不是 1"
	done
done

mapfile -t uboot_packages < <(
	find "${repo_dir}/output/debs" -type f \
		-name 'linux-u-boot-bananapim4zeroemac-current_*arm64*.deb' -print
)
[[ ${#uboot_packages[@]} -eq 1 ]] ||
	fail "預期只有一個 M4 Zero EMAC current U-Boot 套件，實際 ${#uboot_packages[@]} 個"
uboot_deb="${uboot_packages[0]}"
uboot_package_sha256="$(sha256sum "${uboot_deb}" | awk '{ print $1 }')"
uboot_extract_dir="${audit_dir}/uboot"
mkdir -p "${uboot_extract_dir}"
dpkg-deb -x "${uboot_deb}" "${uboot_extract_dir}"
mapfile -t audit_uboot_binaries < <(
	find "${uboot_extract_dir}/usr/lib" -type f -name 'u-boot-sunxi-with-spl.bin' -print
)
[[ ${#audit_uboot_binaries[@]} -eq 1 ]] || fail "U-Boot 套件的目標二進位數量不是 1"
uboot_binary_sha256="$(sha256sum "${audit_uboot_binaries[0]}" | awk '{ print $1 }')"

expected_provenance_header=$'release\tprofile\tartifact_source_commit\tkernel_version\tuboot_version\tuboot_package_sha256\tuboot_binary_sha256\tdram_clock_mhz\tcma_mib\txz_filename\tuserpatches_sha256'
IFS= read -r actual_provenance_header <"${provenance_file}"
[[ "${actual_provenance_header}" == "${expected_provenance_header}" ]] ||
	fail "建置來源清單欄位標頭不符"
provenance_count="$(awk -F '\t' 'NR > 1 && NF == 11 { count++ } END { print count + 0 }' "${provenance_file}")"
[[ ${provenance_count} -eq ${expected_count} ]] ||
	fail "建置來源清單預期 ${expected_count} 筆，實際 ${provenance_count} 筆"
for expected_release in "${expected_releases[@]}"; do
	for expected_profile in "${expected_profiles[@]}"; do
		count="$(awk -F '\t' -v release="${expected_release}" -v profile="${expected_profile}" \
			'$1 == release && $2 == profile { count++ } END { print count + 0 }' \
			"${provenance_file}")"
		[[ ${count} -eq 1 ]] ||
			fail "${expected_release} ${expected_profile} 的建置來源紀錄數不是 1"
	done
done
while IFS=$'\t' read -r release profile artifact_source_commit kernel_version uboot_version \
	recorded_uboot_package_sha256 recorded_uboot_binary_sha256 dram_clock_mhz cma_mib xz_filename \
	recorded_userpatches_sha256; do
	[[ "${release}" == release ]] && continue
	[[ "${artifact_source_commit}" =~ ^[0-9a-f]{40,64}$ ]] ||
		fail "${release} ${profile} 的來源提交格式不符"
	git -C "${repo_dir}" cat-file -e "${artifact_source_commit}^{commit}" ||
		fail "${release} ${profile} 的來源提交不存在於倉庫"
	[[ "${kernel_version}" == 6.18.48-current-sunxi64 ]] ||
		fail "${release} ${profile} 的核心版本來源紀錄不符"
	[[ "${uboot_version}" == v2026.01 ]] || fail "${release} ${profile} 的 U-Boot 版本來源紀錄不符"
	[[ "${recorded_uboot_package_sha256}" == "${uboot_package_sha256}" ]] ||
		fail "${release} ${profile} 的 U-Boot 套件雜湊來源紀錄不符"
	[[ "${recorded_uboot_binary_sha256}" == "${uboot_binary_sha256}" ]] ||
		fail "${release} ${profile} 的 U-Boot 二進位雜湊來源紀錄不符"
	[[ "${dram_clock_mhz}" == 792 && "${cma_mib}" == 256 ]] ||
		fail "${release} ${profile} 的 DDR／CMA 來源紀錄不符"
	[[ "${recorded_userpatches_sha256}" == unrecorded ||
		"${recorded_userpatches_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
		fail "${release} ${profile} 的 userpatches 來源紀錄格式不符"
	awk -F '\t' -v release="${release}" -v profile="${profile}" -v xz="${xz_filename}" '
		$1 == release && $2 == profile && $8 == xz { found++ }
		END { exit found == 1 ? 0 : 1 }
	' "${matrix_file}" || fail "${release} ${profile} 的來源紀錄與映像清單不一致"
done <"${provenance_file}"

package_installed() {
	local root_dir=$1
	local package=$2
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
	' "${root_dir}/var/lib/dpkg/status"
}

validate_release_profile() (
	local image=$1
	local release=$2
	local profile=$3
	local mount_dir loop_device partition codename
	local partitions=()

	mount_dir="$(mktemp -d "${system_tmp_dir}/m4zero-emac-profile.XXXXXX")"
	loop_device=""
	cleanup_mount() {
		if mountpoint -q "${mount_dir}"; then
			sudo -n umount "${mount_dir}" || true
		fi
		if [[ -n "${loop_device}" ]]; then
			sudo -n losetup -d "${loop_device}" 2>/dev/null || true
		fi
		rm -rf -- "${mount_dir}"
	}
	trap cleanup_mount EXIT

	loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")"
	udevadm settle
	mapfile -t partitions < <(lsblk -nrpo NAME,TYPE "${loop_device}" | awk '$2 == "part" { print $1 }')
	[[ ${#partitions[@]} -eq 1 ]] ||
		fail "${release} ${profile} 的可掛載分割區數量不是 1"
	partition="${partitions[0]}"
	sudo -n mount -o ro,noload "${partition}" "${mount_dir}"

	codename="$(awk -F= '$1 == "VERSION_CODENAME" { gsub(/"/, "", $2); print $2 }' "${mount_dir}/etc/os-release")"
	[[ "${codename}" == "${release}" ]] ||
		fail "${release} ${profile} 的根檔案系統代號為 ${codename}"
	grep -qx 'BOARD=bananapim4zeroemac' "${mount_dir}/etc/armbian-release" ||
		fail "${release} ${profile} 的板型識別不符"
	grep -qx 'KERNEL_TARGET=current' "${mount_dir}/etc/armbian-release" ||
		fail "${release} ${profile} 不是 current 分支"

	if [[ "${profile}" == xfce ]]; then
		package_installed "${mount_dir}" xfce4 || fail "${release} xfce 缺少 xfce4"
		for package in gstreamer1.0-tools gstreamer1.0-plugins-bad libdrm-tests; do
			package_installed "${mount_dir}" "${package}" ||
				fail "${release} xfce 缺少 ${package}"
		done
	else
		if package_installed "${mount_dir}" xfce4; then
			fail "${release} cli 不應安裝 xfce4"
		fi
	fi
	echo "發行版與映像角色通過：${release} ${profile}"
)

verify_matrix_entry() (
	local release=$1
	local profile=$2
	local raw_size=$3
	local raw_sha256=$4
	local xz_size=$5
	local xz_sha256=$6
	local img_filename=$7
	local xz_filename=$8
	local archive archive_sum recorded_archive_sha256 entry_dir image
	local available_bytes required_bytes

	[[ "$(basename -- "${img_filename}")" == "${img_filename}" ]] ||
		fail "${release} ${profile} 的原始映像檔名含有路徑"
	[[ "$(basename -- "${xz_filename}")" == "${xz_filename}" ]] ||
		fail "${release} ${profile} 的壓縮映像檔名含有路徑"
	[[ "${img_filename}" == *.img ]] || fail "${release} ${profile} 的原始映像副檔名不符"
	[[ "${xz_filename}" == "${img_filename}.xz" ]] ||
		fail "${release} ${profile} 的壓縮映像檔名與原始映像不一致"
	[[ "${raw_size}" =~ ^[0-9]+$ && "${xz_size}" =~ ^[0-9]+$ ]] ||
		fail "${release} ${profile} 的映像大小不是整數"
	[[ "${raw_sha256}" =~ ^[0-9a-f]{64}$ && "${xz_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
		fail "${release} ${profile} 的 SHA-256 格式不符"

	archive="${output_dir}/${xz_filename}"
	archive_sum="${archive}.sha"
	[[ -f "${archive}" ]] || fail "${release} ${profile} 缺少壓縮映像"
	[[ -f "${archive_sum}" ]] || fail "${release} ${profile} 缺少壓縮映像雜湊檔"
	[[ "$(stat -c %s "${archive}")" == "${xz_size}" ]] ||
		fail "${release} ${profile} 壓縮大小不符"
	[[ "$(sha256sum "${archive}" | awk '{print $1}')" == "${xz_sha256}" ]] ||
		fail "${release} ${profile} 壓縮雜湊不符"
	recorded_archive_sha256="$(awk 'NF { print $1; exit }' "${archive_sum}")"
	[[ "${recorded_archive_sha256}" == "${xz_sha256}" ]] ||
		fail "${release} ${profile} 壓縮雜湊檔內容不符"
	xz -t "${archive}"
	available_bytes="$(df -B1 --output=avail "${system_tmp_dir}" | awk 'NR == 2 { print $1 }')"
	required_bytes=$(( raw_size + 1073741824 ))
	[[ "${available_bytes}" =~ ^[0-9]+$ && ${available_bytes} -ge ${required_bytes} ]] ||
		fail "${release} ${profile} 的系統暫存空間不足，至少需要 ${required_bytes} 位元組"

	entry_dir="$(mktemp -d "${system_tmp_dir}/m4zero-emac-matrix.XXXXXX")"
	cleanup_entry() {
		rm -rf -- "${entry_dir}"
	}
	trap cleanup_entry EXIT
	image="${entry_dir}/${img_filename}"
	xz -dc -- "${archive}" >"${image}"
	[[ "$(stat -c %s "${image}")" == "${raw_size}" ]] ||
		fail "${release} ${profile} 解壓後原始大小不符"
	[[ "$(sha256sum "${image}" | awk '{print $1}')" == "${raw_sha256}" ]] ||
		fail "${release} ${profile} 解壓後原始雜湊不符"

	BPI_M4ZERO_EMAC_LOCK_FD=9 \
	SYSTEM_TMP_DIR="${system_tmp_dir}" \
		"${repo_dir}/tools/verify-bpi-m4zero-emac-image.sh" \
		"${image}" "${uboot_deb}" "${archive}" "${raw_sha256}" "${raw_size}"
	validate_release_profile "${image}" "${release}" "${profile}"
)

while IFS=$'\t' read -r release profile raw_size raw_sha256 xz_size xz_sha256 img_filename xz_filename; do
	[[ "${release}" == release ]] && continue
	verify_matrix_entry "${release}" "${profile}" "${raw_size}" "${raw_sha256}" \
		"${xz_size}" "${xz_sha256}" "${img_filename}" "${xz_filename}"
done <"${matrix_file}"

echo "M4 Zero EMAC 十映像矩陣全部通過唯讀驗證。"
