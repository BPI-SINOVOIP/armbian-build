#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-meson-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-meson-trixie-current-cli}"
boards_text="${BOARDS:-bananapim5 bananapim2pro bananapicm4io bananapim2s}"
verify_archives="${VERIFY_ARCHIVES:-yes}"

read -r -a boards <<<"${boards_text}"

for command in awk basename cmp cut date df fdtget find grep lsblk losetup md5sum mktemp \
	modinfo mount mountpoint od python3 sha256sum sort stat sudo udevadm \
	umount wc xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"
[[ -f "${output_dir}/CANDIDATES.tsv" ]] || fail "找不到 CANDIDATES.tsv"
[[ -f "${output_dir}/COMPLETION_STATUS.json" ]] || fail "找不到建置狀態"
grep -q '"status": "complete"' "${output_dir}/COMPLETION_STATUS.json" ||
	fail "建置狀態不是 complete"
case "${verify_archives}" in
	yes | no) ;;
	*) fail "VERIFY_ARCHIVES 只接受 yes 或 no" ;;
esac
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹有已追蹤或未追蹤變更"
if [[ -d "${repo_dir}/userpatches" ]] &&
	find "${repo_dir}/userpatches" -mindepth 1 \( -type f -o -type l \) -print -quit |
	grep -q .; then
	fail "userpatches 含有 Git 未追蹤的來源覆寫"
fi

source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
source_tree="$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')"
validation_config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
expected_fip_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["fip_commit"])
PY
)"

board_field() {
	local board=$1
	local field=$2
	python3 - "${validation_config}" "${board}" "${field}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
value = data["boards"][sys.argv[2]][sys.argv[3]]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

common_values() {
	local field=$1
	python3 - "${validation_config}" "${field}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
value = data[sys.argv[2]]
if isinstance(value, dict):
    for key in sorted(value):
        print(f"{key}={value[key]}")
else:
    for item in value:
        print(item)
PY
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

dt_has_property() {
	local dtb=$1
	local node=$2
	local property=$3
	fdtget -p "${dtb}" "${node}" | grep -qx "${property}"
}

validate_boot_area() {
	local image=$1
	local signature
	signature="$(od -An -tx1 -j510 -N2 "${image}" | awk '{ print $1 $2 }')"
	[[ "${signature}" == 55aa ]] || fail "${image} 缺少 DOS MBR 簽章"
	od -An -v -tu1 -j512 -N1048576 "${image}" |
		awk '
			{
				for (field_index = 1; field_index <= NF; field_index++) {
					if ($field_index != 0) found = 1
				}
			}
			END { exit found ? 0 : 1 }
		' || fail "${image} 的前 1 MiB 開機區全為零"
}

validate_installed_uboot() {
	local image=$1
	local mount_dir=$2
	local board=$3
	local dtb_relative=$4
	local uboot_tag uboot_version uboot_dir payload metadata_file md5sums_file
	local payload_path expected_md5 actual_md5 payload_size

	uboot_tag="$(board_field "${board}" uboot_tag)"
	uboot_version="${uboot_tag#v}"
	uboot_dir="${mount_dir}/usr/lib/linux-u-boot-current-${board}"
	payload="${uboot_dir}/u-boot.bin"
	metadata_file="${uboot_dir}/u-boot-metadata.sh"
	md5sums_file="${mount_dir}/var/lib/dpkg/info/linux-u-boot-${board}-current.md5sums"
	payload_path="usr/lib/linux-u-boot-current-${board}/u-boot.bin"

	[[ -s "${payload}" && -s "${metadata_file}" && -s "${md5sums_file}" ]] ||
		fail "${board} 缺少可驗證的 U-Boot 套件 payload"
	grep -qx "declare UBOOT_VERSION=\"${uboot_version}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot 版本不是 ${uboot_tag}"
	grep -qx "declare UBOOT_GIT_BRANCH=\"tag:${uboot_tag}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot Git 分支不符"
	grep -qx "declare UBOOT_KERNEL_DTB=\"${dtb_relative}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot 核心 DTB 不符"

	expected_md5="$(awk -v path="${payload_path}" '$2 == path { print $1 }' "${md5sums_file}")"
	[[ "${expected_md5}" =~ ^[0-9a-f]{32}$ ]] ||
		fail "${board} 的 U-Boot 套件缺少唯一 payload MD5"
	actual_md5="$(md5sum "${payload}" | cut -d' ' -f1)"
	[[ "${actual_md5}" == "${expected_md5}" ]] ||
		fail "${board} 的 U-Boot 套件 payload 已被修改"

	payload_size="$(stat -c %s "${payload}")"
	(( payload_size > 512 )) || fail "${board} 的 U-Boot payload 太小"
	cmp --silent --bytes=442 "${payload}" "${image}" ||
		fail "${board} 的映像前 442 bytes 與 U-Boot payload 不同"
	cmp --silent --ignore-initial=512:512 --bytes="$((payload_size - 512))" \
		"${payload}" "${image}" ||
		fail "${board} 的映像開機區與 U-Boot payload 不同"
}

validate_mounted_image() (
	local image=$1
	local board=$2
	local dtb_relative dtb_path overlay overlay_root config_file
	local loop_device partition mount_dir emmc_node emmc_frequency
	local expected_frequency expected_no_hs400 package option_line option value

	dtb_relative="$(board_field "${board}" dtb)"
	expected_frequency="$(board_field "${board}" emmc_max_frequency)"
	expected_no_hs400="$(board_field "${board}" emmc_no_hs400)"
	mount_dir="$(mktemp -d "${repo_dir}/.tmp/meson-verify.XXXXXX")"
	loop_device="$(sudo losetup --find --show --partscan --read-only "${image}")"
	cleanup_image() {
		if mountpoint -q "${mount_dir}"; then
			sudo umount "${mount_dir}"
		fi
		sudo losetup -d "${loop_device}" 2>/dev/null || true
		rmdir "${mount_dir}" 2>/dev/null || true
	}
	trap cleanup_image EXIT

	udevadm settle
	partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" |
		awk '$2 == "part" { print $1; exit }')"
	[[ -n "${partition}" ]] || fail "${board} 沒有可掛載分割區"
	sudo mount -o ro,noload "${partition}" "${mount_dir}"

	[[ -s "${mount_dir}/boot/Image" ]] || fail "${board} 缺少核心映像"
	[[ -s "${mount_dir}/boot/uInitrd" ]] || fail "${board} 缺少 initrd"
	[[ -f "${mount_dir}/boot/armbianEnv.txt" ]] || fail "${board} 缺少 armbianEnv.txt"
	grep -qx "fdtfile=${dtb_relative}" "${mount_dir}/boot/armbianEnv.txt" ||
		fail "${board} 的 fdtfile 不等於 ${dtb_relative}"

	dtb_path="${mount_dir}/boot/dtb/${dtb_relative}"
	[[ -s "${dtb_path}" ]] || fail "${board} 缺少 DTB：${dtb_relative}"
	overlay_root="${mount_dir}/boot/dtb/amlogic/overlay"
	[[ -s "${overlay_root}/README.meson-overlays" ]] ||
		fail "${board} 缺少 Meson overlay 文件"
	for overlay in $(board_field "${board}" required_overlays); do
		[[ -s "${overlay_root}/${overlay}" ]] ||
			fail "${board} 缺少 overlay：${overlay}"
	done

	config_file="$(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print -quit)"
	[[ -n "${config_file}" ]] || fail "${board} 缺少核心設定檔"
	while IFS= read -r option_line; do
		option="${option_line%%=*}"
		value="${option_line#*=}"
		grep -qx "${option}=${value}" "${config_file}" ||
			fail "${board} 核心設定不符：${option}=${value}"
	done < <(common_values common_kernel_options)

	for package in $(common_values common_packages); do
		package_installed "${mount_dir}" "${package}" ||
			fail "${board} 缺少套件 ${package}"
	done
	for package in \
		linux-image-current-meson64 \
		linux-dtb-current-meson64 \
		"linux-u-boot-${board}-current" \
		"armbian-bsp-cli-${board}-current"; do
		package_installed "${mount_dir}" "${package}" ||
			fail "${board} 缺少 Armbian 套件 ${package}"
	done
	validate_installed_uboot "${image}" "${mount_dir}" "${board}" "${dtb_relative}"

	emmc_node="$(fdtget -t s "${dtb_path}" /aliases mmc1)"
	[[ -n "${emmc_node}" ]] || fail "${board} 缺少 eMMC alias"
	[[ "$(fdtget -t u "${dtb_path}" "${emmc_node}" bus-width)" == 8 ]] ||
		fail "${board} 的 eMMC 不是 8-bit"
	dt_has_property "${dtb_path}" "${emmc_node}" mmc-hs200-1_8v ||
		fail "${board} 的 eMMC 未宣告 HS200"
	emmc_frequency="$(fdtget -t u "${dtb_path}" "${emmc_node}" max-frequency)"
	[[ "${emmc_frequency}" == "${expected_frequency}" ]] ||
		fail "${board} 的 eMMC 上限為 ${emmc_frequency}，預期 ${expected_frequency}"
	if [[ "${expected_no_hs400}" == true ]]; then
		dt_has_property "${dtb_path}" "${emmc_node}" no-mmc-hs400 ||
			fail "${board} 缺少 no-mmc-hs400"
	else
		if dt_has_property "${dtb_path}" "${emmc_node}" no-mmc-hs400; then
			fail "${board} 意外套用了 no-mmc-hs400"
		fi
	fi

	grep -Eq '^[[:space:]]*GOVERNOR="?ondemand"?[[:space:]]*$' \
		"${mount_dir}/etc/default/cpufrequtils" ||
		fail "${board} 未使用 ondemand 調速器"

	echo "映像唯讀內容通過：${board}"
)

expected_header=$'board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tfip_commit'
IFS= read -r actual_header <"${output_dir}/CANDIDATES.tsv"
[[ "${actual_header}" == "${expected_header}" ]] || fail "CANDIDATES.tsv 欄位不符"
row_count="$(awk 'NR > 1 && NF == 11 { count++ } END { print count + 0 }' "${output_dir}/CANDIDATES.tsv")"
[[ "${row_count}" -eq "${#boards[@]}" ]] ||
	fail "候選矩陣預期 ${#boards[@]} 筆，實際 ${row_count}"
for expected_board in "${boards[@]}"; do
	board_count="$(awk -F '\t' -v board="${expected_board}" \
		'NR > 1 && $1 == board { count++ } END { print count + 0 }' \
		"${output_dir}/CANDIDATES.tsv")"
	[[ "${board_count}" -eq 1 ]] ||
		fail "${expected_board} 的矩陣紀錄數不是 1"
done

sudo -n true || fail "唯讀掛載驗證需要免互動 sudo"
verification_file="${output_dir}/VERIFICATION.tsv"
printf 'board\tidentity\tread_only_content\tevidence_level\n' >"${verification_file}.partial"

while IFS=$'\t' read -r board release profile raw_size raw_sha256 xz_size \
	xz_sha256 img_path xz_path matrix_source_commit matrix_fip_commit; do
	[[ "${board}" == board ]] && continue
	[[ " ${boards[*]} " == *" ${board} "* ]] || fail "矩陣含未要求板卡：${board}"
	[[ "${release}" == trixie && "${profile}" == cli ]] ||
		fail "${board} 的 release/profile 不符"
	[[ "${img_path}" == "${board}/"* && "${img_path}" != *..* ]] ||
		fail "${board} 的 IMG 路徑不安全"
	[[ "${xz_path}" == "${img_path}.xz" ]] || fail "${board} 的 IMG/XZ 路徑不成對"
	[[ "${matrix_source_commit}" == "${source_commit}" ]] ||
		fail "${board} 的來源提交不是目前 HEAD"
	[[ "${matrix_fip_commit}" == "${expected_fip_commit}" ]] ||
		fail "${board} 的 FIP 提交不符"

	image="${output_dir}/${img_path}"
	archive="${output_dir}/${xz_path}"
	metadata="${output_dir}/${board}/artifact.metadata.txt"
	fip_manifest="${output_dir}/${board}/fip-blobs.sha256"
	[[ -f "${image}" && -f "${archive}" && -f "${metadata}" ]] ||
		fail "${board} 缺少 IMG、XZ 或中繼資料"
	[[ -s "${fip_manifest}" ]] || fail "${board} 缺少 FIP blob 清單"
	[[ "$(stat -c %s "${image}")" == "${raw_size}" ]] || fail "${board} IMG 大小不符"
	[[ "$(sha256sum "${image}" | cut -d' ' -f1)" == "${raw_sha256}" ]] || fail "${board} IMG SHA-256 不符"
	[[ "$(stat -c %s "${archive}")" == "${xz_size}" ]] || fail "${board} XZ 大小不符"
	[[ "$(sha256sum "${archive}" | cut -d' ' -f1)" == "${xz_sha256}" ]] || fail "${board} XZ SHA-256 不符"
	if [[ "${verify_archives}" == yes ]]; then
		xz -t "${archive}"
	fi
	[[ "$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)" == "${raw_sha256}" ]] ||
		fail "${board} 的 XZ 解壓資料與 IMG 不一致"

	require_metadata_value "${metadata}" schema_version 1
	require_metadata_value "${metadata}" board "${board}"
	require_metadata_value "${metadata}" release trixie
	require_metadata_value "${metadata}" branch current
	require_metadata_value "${metadata}" profile cli
	require_metadata_value "${metadata}" build_method full_compile_sh_build
	require_metadata_value "${metadata}" source_commit "${source_commit}"
	require_metadata_value "${metadata}" source_tree "${source_tree}"
	require_metadata_value "${metadata}" validation_config_sha256 "${validation_config_sha256}"
	require_metadata_value "${metadata}" fip_commit "${expected_fip_commit}"
	require_metadata_value "${metadata}" fip_directory "$(board_field "${board}" fip_directory)"
	require_metadata_value "${metadata}" family "$(board_field "${board}" family)"
	require_metadata_value "${metadata}" uboot_tag "$(board_field "${board}" uboot_tag)"
	require_metadata_value "${metadata}" fip_manifest_sha256 \
		"$(board_field "${board}" fip_manifest_sha256)"
	[[ "$(sha256sum "${fip_manifest}" | cut -d' ' -f1)" == \
		"$(board_field "${board}" fip_manifest_sha256)" ]] ||
		fail "${board} 的 FIP blob 清單與受控設定不符"
	require_metadata_value "${metadata}" raw_size "${raw_size}"
	require_metadata_value "${metadata}" raw_sha256 "${raw_sha256}"
	require_metadata_value "${metadata}" xz_size "${xz_size}"
	require_metadata_value "${metadata}" xz_sha256 "${xz_sha256}"

	validate_boot_area "${image}"
	validate_mounted_image "${image}" "${board}"
	printf '%s\tpass\tpass\tL2\n' "${board}" >>"${verification_file}.partial"
done <"${output_dir}/CANDIDATES.tsv"

mv "${verification_file}.partial" "${verification_file}"
{
	printf '{\n'
	printf '  "status": "complete",\n'
	printf '  "evidence_level": "L2",\n'
	printf '  "source_commit": "%s",\n' "${source_commit}"
	printf '  "verified_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf '}\n'
} >"${output_dir}/VERIFICATION_STATUS.json.partial"
mv "${output_dir}/VERIFICATION_STATUS.json.partial" "${output_dir}/VERIFICATION_STATUS.json"
echo "Meson 候選映像全部通過 L2 唯讀守門。"
