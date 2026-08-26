#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-sunxi-a20-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunxi-a20-trixie-current-cli}"
boards_text="${BOARDS:-bananapi bananapipro}"
verify_archives="${VERIFY_ARCHIVES:-yes}"
candidate_family_name="${CANDIDATE_FAMILY_NAME:-Sunxi}"
verify_tmp_prefix="${VERIFY_TMP_PREFIX:-bananapi-verify}"

read -r -a boards <<<"${boards_text}"

for command in awk basename cmp cut date fdtget find git grep lsblk losetup \
	md5sum mktemp mount mountpoint od python3 sha256sum stat sudo udevadm \
	umount xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

read_partition_start_sector() {
	local partition=$1 block_name start_path
	block_name="$(basename -- "${partition}")"
	start_path="/sys/class/block/${block_name}/start"
	[[ -r "${start_path}" ]] || fail "無法讀取 ${partition} 的分割區起點"
	tr -d '[:space:]' <"${start_path}"
}

validate_default_userpatches() {
	local path relative template
	[[ -d "${repo_dir}/userpatches" ]] || return 0
	while IFS= read -r -d '' path; do
		relative="${path#"${repo_dir}"/userpatches/}"
		case "${relative}" in
			config-example.conf)
				template="${repo_dir}/config/templates/config-example.conf.template"
				;;
			customize-image.sh)
				template="${repo_dir}/config/templates/customize-image.sh.template"
				;;
			*) fail "userpatches 含有來源覆寫：${relative}" ;;
		esac
		cmp --silent "${path}" "${template}" ||
			fail "userpatches 預設檔已被修改：${relative}"
	done < <(find "${repo_dir}/userpatches" -mindepth 1 \
		\( -type f -o -type l \) -print0)
}

[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"
[[ -f "${output_dir}/CANDIDATES.tsv" ]] || fail "找不到 CANDIDATES.tsv"
[[ -f "${output_dir}/COMPLETION_STATUS.json" ]] || fail "找不到建置狀態"
grep -q '"status": "complete"' "${output_dir}/COMPLETION_STATUS.json" ||
	fail "建置狀態不是 complete"
candidate_branch="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("candidate_branch", "current"))
PY
)"
case "${candidate_branch}" in
	current | edge | vendor) ;;
	*) fail "驗證設定的 candidate_branch 不受支援：${candidate_branch}" ;;
esac
case "${verify_archives}" in
	yes | no) ;;
	*) fail "VERIFY_ARCHIVES 只接受 yes 或 no" ;;
esac
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹有已追蹤或未追蹤變更"
validate_default_userpatches
sudo -n true || fail "唯讀掛載驗證需要免互動 sudo"

verifier_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
verification_config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"

board_field() {
	python3 - "${validation_config}" "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]][sys.argv[3]]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

board_field_optional() {
	python3 - "${validation_config}" "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]].get(sys.argv[3], "")
if isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

common_values() {
	python3 - "${validation_config}" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)[sys.argv[2]]
if isinstance(value, dict):
    for key in sorted(value):
        print(f"{key}={value[key]}")
else:
    for item in value:
        print(item)
PY
}

top_field_optional() {
	python3 - "${validation_config}" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream).get(sys.argv[2], "")
print(value)
PY
}

read_metadata_value() {
	local metadata_file=$1 key=$2 matches=()
	mapfile -t matches < <(grep -E "^${key}=" "${metadata_file}")
	[[ ${#matches[@]} -eq 1 ]] || return 1
	printf '%s\n' "${matches[0]#*=}"
}

require_metadata_value() {
	local actual
	actual="$(read_metadata_value "$1" "$2")" ||
		fail "中繼資料缺少唯一欄位 $2：$1"
	[[ "${actual}" == "$3" ]] ||
		fail "中繼資料欄位 $2 不符：預期 $3，實際 ${actual}"
}

package_installed() {
	awk -v package="$2" '
		BEGIN { RS = ""; FS = "\n" }
		{
			name = ""; status = ""
			for (field_index = 1; field_index <= NF; field_index++) {
				if ($field_index ~ /^Package: /) name = substr($field_index, 10)
				if ($field_index ~ /^Status: /) status = substr($field_index, 9)
			}
			if (name == package && status == "install ok installed") found = 1
		}
		END { exit found ? 0 : 1 }
	' "$1/var/lib/dpkg/status"
}

validate_boot_area() {
	local signature
	signature="$(od -An -tx1 -j510 -N2 "$1" | awk '{ print $1 $2 }')"
	[[ "${signature}" == 55aa ]] || fail "$1 缺少 DOS MBR 簽章"
}

validate_installed_uboot() {
	local image=$1 mount_dir=$2 board=$3
	local uboot_tag uboot_version uboot_git_source uboot_git_ref uboot_revision
	local payload_specs package_only_payloads payload_spec payload_name offset uboot_dir payload
	local metadata_file md5sums_file payload_size payload_sha256 minimum_spec
	uboot_tag="$(board_field "${board}" uboot_tag)"
	uboot_version="$(board_field_optional "${board}" uboot_version)"
	[[ -n "${uboot_version}" ]] || uboot_version="${uboot_tag#v}"
	uboot_git_source="$(board_field_optional "${board}" uboot_git_source)"
	uboot_git_ref="$(board_field_optional "${board}" uboot_git_ref)"
	[[ -n "${uboot_git_ref}" ]] || uboot_git_ref="tag:${uboot_tag}"
	uboot_revision="$(board_field_optional "${board}" uboot_revision)"
	if [[ -z "${uboot_revision}" && "${uboot_git_ref}" == commit:* ]]; then
		uboot_revision="${uboot_git_ref#commit:}"
	fi
	payload_specs="$(board_field_optional "${board}" uboot_payloads)"
	if [[ -z "${payload_specs}" ]]; then
		payload_specs="$(board_field "${board}" uboot_payload)@$(board_field "${board}" uboot_offset)"
	fi
	uboot_dir="${mount_dir}/usr/lib/linux-u-boot-${candidate_branch}-${board}"
	metadata_file="${uboot_dir}/u-boot-metadata.sh"
	md5sums_file="${mount_dir}/var/lib/dpkg/info/linux-u-boot-${board}-${candidate_branch}.md5sums"

	[[ -s "${metadata_file}" && -s "${md5sums_file}" ]] ||
		fail "${board} 缺少可驗證的 U-Boot 套件 payload"
	grep -Fqx "declare UBOOT_VERSION=\"${uboot_version}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot 版本不是 ${uboot_tag}"
	grep -Fqx "declare UBOOT_GIT_BRANCH=\"${uboot_git_ref}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot Git 分支不符"
	if [[ -n "${uboot_git_source}" ]]; then
		grep -Fqx "declare UBOOT_GIT_SOURCE=\"${uboot_git_source}\"" "${metadata_file}" ||
			fail "${board} 的 U-Boot Git 來源不符"
	fi
	if [[ -n "${uboot_revision}" ]]; then
		[[ "${uboot_revision}" =~ ^[0-9a-f]{40}$ ]] || fail "${board} 的 U-Boot revision 格式不符"
		grep -Fqx "declare UBOOT_GIT_REVISION=\"${uboot_revision}\"" "${metadata_file}" ||
			fail "${board} 的 U-Boot Git revision 不符"
	fi
	validate_uboot_payload_file() {
		local checked_payload_name=$1 checked_payload checked_payload_path
		local checked_expected_md5 checked_actual_md5 checked_size checked_minimum=1
		checked_payload="${uboot_dir}/${checked_payload_name}"
		checked_payload_path="usr/lib/linux-u-boot-${candidate_branch}-${board}/${checked_payload_name}"
		[[ -s "${checked_payload}" ]] || fail "${board} 缺少 U-Boot payload：${checked_payload_name}"
		checked_expected_md5="$(awk -v path="${checked_payload_path}" '$2 == path { print $1 }' "${md5sums_file}")"
		[[ "${checked_expected_md5}" =~ ^[0-9a-f]{32}$ ]] ||
			fail "${board} 缺少唯一 payload MD5：${checked_payload_name}"
		checked_actual_md5="$(md5sum "${checked_payload}" | cut -d' ' -f1)"
		[[ "${checked_actual_md5}" == "${checked_expected_md5}" ]] ||
			fail "${board} 的 U-Boot payload 已被修改：${checked_payload_name}"
		for minimum_spec in $(board_field_optional "${board}" uboot_payload_minimum_sizes); do
			[[ "${minimum_spec%=*}" == "${checked_payload_name}" ]] || continue
			checked_minimum="${minimum_spec##*=}"
		done
		[[ "${checked_minimum}" =~ ^[1-9][0-9]*$ ]] ||
			fail "${board} 的 payload 最小大小無效：${checked_payload_name}"
		checked_size="$(stat -c %s "${checked_payload}")"
		(( checked_size >= checked_minimum )) ||
			fail "${board} 的 U-Boot payload 太小：${checked_payload_name}"
		printf '%s\t%s\n' "${checked_payload}" "${checked_size}"
	}
	for payload_spec in ${payload_specs}; do
		[[ "${payload_spec}" =~ ^[^@]+@[0-9]+$ ]] || fail "${board} 的 U-Boot payload 規格不符"
		payload_name="${payload_spec%@*}"
		offset="${payload_spec##*@}"
		payload="${uboot_dir}/${payload_name}"
		validate_uboot_payload_file "${payload_name}" >/dev/null
		payload_size="$(stat -c %s "${payload}")"
		payload_sha256="$(sha256sum "${payload}" | cut -d' ' -f1)"
		cmp --silent --ignore-initial="0:${offset}" --bytes="${payload_size}" \
			"${payload}" "${image}" || fail "${board} 映像 ${offset} 偏移與 ${payload_name} 不同"
		printf '%s\t%s\timage\t%s\t%s\t%s\n' \
			"${board}" "${payload_name}" "${offset}" "${payload_size}" "${payload_sha256}" \
			>>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
	done
	package_only_payloads="$(board_field_optional "${board}" uboot_package_only_payloads)"
	for payload_name in ${package_only_payloads}; do
		validate_uboot_payload_file "${payload_name}" >/dev/null
		payload="${uboot_dir}/${payload_name}"
		payload_size="$(stat -c %s "${payload}")"
		payload_sha256="$(sha256sum "${payload}" | cut -d' ' -f1)"
		printf '%s\t%s\tpackage-only\t-\t%s\t%s\n' \
			"${board}" "${payload_name}" "${payload_size}" "${payload_sha256}" \
			>>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
	done
}

validate_mounted_image() (
	local image=$1 board=$2
	local dtb_relative dtb_basename dtb_path fdt_override model compatible expected node option_line option value package
	local loop_device partition mount_dir config_file overlay_prefix overlay overlay_directory default_overlays overlays_line sd_node sd_bus_width requirement required_node required_width kernel_family
	local boot_configuration extlinux_fdt expected_start_sector actual_start_sector property_spec property_node property_name property_expected installed_spec installed_path installed_sha256
	dtb_relative="$(board_field "${board}" dtb)"
	dtb_basename="$(basename "${dtb_relative}")"
	mount_dir="$(mktemp -d "${repo_dir}/.tmp/${verify_tmp_prefix}.XXXXXX")"
	loop_device="$(sudo losetup --find --show --partscan --read-only "${image}")"
	cleanup_image() {
		if mountpoint -q "${mount_dir}"; then sudo umount "${mount_dir}"; fi
		sudo losetup -d "${loop_device}" 2>/dev/null || true
		rmdir "${mount_dir}" 2>/dev/null || true
	}
	trap cleanup_image EXIT
	udevadm settle
	partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" | awk '$2 == "part" { print $1; exit }')"
	[[ -n "${partition}" ]] || fail "${board} 沒有可掛載分割區"
	expected_start_sector="$(board_field_optional "${board}" partition_start_sector)"
	if [[ -n "${expected_start_sector}" ]]; then
		actual_start_sector="$(read_partition_start_sector "${partition}")"
		[[ "${actual_start_sector}" == "${expected_start_sector}" ]] ||
			fail "${board} 的第一分割區起點不是 ${expected_start_sector} sector"
	fi
	sudo mount -o ro,noload "${partition}" "${mount_dir}"

	[[ -s "${mount_dir}/boot/zImage" || -s "${mount_dir}/boot/Image" ]] || fail "${board} 缺少核心映像"
	[[ -s "${mount_dir}/boot/uInitrd" ]] || fail "${board} 缺少 initrd"
	boot_configuration="$(board_field_optional "${board}" boot_configuration)"
	[[ -n "${boot_configuration}" ]] || boot_configuration="armbian_env"
	overlay_prefix="$(board_field "${board}" overlay_prefix)"
	overlay_directory="$(board_field_optional "${board}" overlay_directory)"
	[[ -n "${overlay_directory}" ]] || overlay_directory="overlay"
	case "${boot_configuration}" in
		armbian_env)
			[[ -s "${mount_dir}/boot/armbianEnv.txt" ]] || fail "${board} 缺少 armbianEnv.txt"
			if grep -q '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt"; then
				fdt_override="$(grep '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt")"
				[[ "${fdt_override}" == "fdtfile=${dtb_relative}" || \
					"${fdt_override}" == "fdtfile=${dtb_basename}" ]] || fail "${board} 的 fdtfile 覆寫不符"
			fi
			grep -qx "overlay_prefix=${overlay_prefix}" "${mount_dir}/boot/armbianEnv.txt" ||
				fail "${board} 的 overlay_prefix 不符"
			;;
		extlinux)
			extlinux_fdt="$(board_field "${board}" extlinux_fdt)"
			[[ -s "${mount_dir}/boot/extlinux/extlinux.conf" ]] || fail "${board} 缺少 extlinux.conf"
			grep -Fqx "  fdt ${extlinux_fdt}" "${mount_dir}/boot/extlinux/extlinux.conf" ||
				fail "${board} 的 extlinux FDT 不符"
			;;
		*) fail "${board} 的開機設定類型不支援：${boot_configuration}" ;;
	esac
	default_overlays="$(board_field "${board}" default_overlays)"
	if [[ -n "${default_overlays}" ]]; then
		overlays_line="$(grep '^overlays=' "${mount_dir}/boot/armbianEnv.txt")" || fail "${board} 缺少預設 overlays"
		for overlay in ${default_overlays}; do
			[[ " ${overlays_line#overlays=} " == *" ${overlay} "* ]] || fail "${board} 未預設啟用 overlay：${overlay}"
		done
	fi
	for overlay in $(board_field "${board}" required_overlays); do
		[[ -s "${mount_dir}/boot/dtb/${overlay_directory}/${overlay_prefix}-${overlay}.dtbo" ]] ||
			fail "${board} 缺少 overlay：${overlay_prefix}-${overlay}.dtbo"
	done
	dtb_path="${mount_dir}/boot/dtb/${dtb_relative}"
	if [[ ! -s "${dtb_path}" ]]; then
		dtb_path="${mount_dir}/boot/dtb/${dtb_basename}"
	fi
	[[ -s "${dtb_path}" ]] || fail "${board} 缺少 DTB：${dtb_relative}"
	model="$(fdtget -t s "${dtb_path}" / model)"
	[[ "${model}" == "$(board_field "${board}" model)" ]] || fail "${board} 的 DTB model 不符"
	compatible="$(fdtget -t s "${dtb_path}" / compatible)"
	for expected in $(board_field "${board}" compatible); do
		[[ " ${compatible} " == *" ${expected} "* ]] || fail "${board} 缺少相容字串 ${expected}"
	done
	for node in $(board_field "${board}" required_status_nodes); do
		[[ "$(fdtget -t s "${dtb_path}" "${node}" status)" == okay ]] || fail "${board} 節點未啟用：${node}"
	done
	for property_spec in $(board_field_optional "${board}" required_boolean_properties); do
		property_node="${property_spec%%:*}"
		property_name="${property_spec#*:}"
		fdtget "${dtb_path}" "${property_node}" "${property_name}" >/dev/null ||
			fail "${board} 缺少 DT 布林屬性：${property_node}:${property_name}"
	done
	for property_spec in $(board_field_optional "${board}" required_string_properties); do
		property_node="${property_spec%%:*}"
		property_name="${property_spec#*:}"
		property_expected="${property_name#*=}"
		property_name="${property_name%%=*}"
		[[ "$(fdtget -t s "${dtb_path}" "${property_node}" "${property_name}")" == "${property_expected}" ]] ||
			fail "${board} 的 DT 字串屬性不符：${property_node}:${property_name}"
	done
	sd_node="$(board_field "${board}" sd_node)"
	sd_bus_width="$(board_field "${board}" sd_bus_width)"
	[[ "$(fdtget -t u "${dtb_path}" "${sd_node}" bus-width)" == "${sd_bus_width}" ]] ||
		fail "${board} 的 SD 匯流排寬度不是 ${sd_bus_width}-bit"
	for requirement in $(board_field_optional "${board}" additional_bus_widths); do
		required_node="${requirement%=*}"
		required_width="${requirement##*=}"
		[[ "$(fdtget -t u "${dtb_path}" "${required_node}" bus-width)" == "${required_width}" ]] ||
			fail "${board} 的 ${required_node} 匯流排寬度不是 ${required_width}-bit"
	done

	config_file="$(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print -quit)"
	[[ -n "${config_file}" ]] || fail "${board} 缺少核心設定檔"
	while IFS= read -r option_line; do
		option="${option_line%%=*}"; value="${option_line#*=}"
		grep -qx "${option}=${value}" "${config_file}" || fail "${board} 核心設定不符：${option}=${value}"
	done < <(common_values common_kernel_options)
	for package in $(common_values common_packages); do
		package_installed "${mount_dir}" "${package}" || fail "${board} 缺少套件 ${package}"
	done
	while IFS= read -r installed_spec; do
		[[ -n "${installed_spec}" ]] || continue
		installed_path="${installed_spec%%=*}"
		installed_sha256="${installed_spec#*=}"
		[[ -f "${mount_dir}${installed_path}" ]] || fail "${board} 缺少韌體 ${installed_path}"
		[[ "$(sha256sum "${mount_dir}${installed_path}" | cut -d' ' -f1)" == "${installed_sha256}" ]] ||
			fail "${board} 的韌體雜湊不符：${installed_path}"
	done < <(common_values installed_firmware_blobs 2>/dev/null || true)
	kernel_family="$(top_field_optional kernel_family)"
	[[ -n "${kernel_family}" ]] || kernel_family="sunxi"
	for package in "linux-image-${candidate_branch}-${kernel_family}" "linux-dtb-${candidate_branch}-${kernel_family}" \
		"linux-u-boot-${board}-${candidate_branch}" "armbian-bsp-cli-${board}-${candidate_branch}"; do
		package_installed "${mount_dir}" "${package}" || fail "${board} 缺少 Armbian 套件 ${package}"
	done
	validate_installed_uboot "${image}" "${mount_dir}" "${board}"
	echo "映像唯讀內容通過：${board}"
)

expected_header=$'board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tuboot_tag'
IFS= read -r actual_header <"${output_dir}/CANDIDATES.tsv"
[[ "${actual_header}" == "${expected_header}" ]] || fail "CANDIDATES.tsv 欄位不符"
row_count="$(awk 'NR > 1 && NF == 11 { count++ } END { print count + 0 }' "${output_dir}/CANDIDATES.tsv")"
[[ "${row_count}" -eq "${#boards[@]}" ]] || fail "候選矩陣筆數不符"
candidate_source_commit="$(awk -F '\t' 'NR == 2 { print $10 }' "${output_dir}/CANDIDATES.tsv")"
[[ "${candidate_source_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "候選來源提交格式不符"
[[ "$(awk -F '\t' -v commit="${candidate_source_commit}" 'NR > 1 && $10 != commit { count++ } END { print count + 0 }' "${output_dir}/CANDIDATES.tsv")" -eq 0 ]] ||
	fail "候選矩陣混用不同來源提交"
git -C "${repo_dir}" cat-file -e "${candidate_source_commit}^{commit}" || fail "本地找不到候選來源提交"
candidate_source_tree="$(git -C "${repo_dir}" rev-parse "${candidate_source_commit}^{tree}")"
validation_config_relative="${validation_config#"${repo_dir}"/}"
[[ "${validation_config_relative}" != "${validation_config}" ]] || fail "驗證設定必須位於來源倉庫內"
git -C "${repo_dir}" cat-file -e "${candidate_source_commit}:${validation_config_relative}" ||
	fail "候選來源提交缺少建置時驗證設定"
build_validation_config_sha256="$(git -C "${repo_dir}" show \
	"${candidate_source_commit}:${validation_config_relative}" | sha256sum | cut -d' ' -f1)"

verification_file="${output_dir}/VERIFICATION.tsv"
printf 'board\tidentity\tread_only_content\tevidence_level\n' >"${verification_file}.partial"
printf 'board\tpayload\tplacement\toffset\tsize\tsha256\n' \
	>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
while IFS=$'\t' read -r board release profile raw_size raw_sha256 xz_size \
	xz_sha256 img_path xz_path candidate_commit uboot_tag; do
	[[ "${release}" == trixie && "${profile}" == cli ]] || fail "${board} 發行版或設定不符"
	[[ "${candidate_commit}" == "${candidate_source_commit}" ]] || fail "${board} 來源提交不符"
	[[ "${uboot_tag}" == "$(board_field "${board}" uboot_tag)" ]] || fail "${board} U-Boot 標籤不符"
	image="${output_dir}/${img_path}"; archive="${output_dir}/${xz_path}"
	metadata="${output_dir}/${board}/artifact.metadata.txt"
	[[ -f "${image}" && -f "${archive}" && -f "${metadata}" ]] || fail "${board} 缺少產物"
	[[ "$(stat -c %s "${image}")" == "${raw_size}" ]] || fail "${board} IMG 大小不符"
	[[ "$(sha256sum "${image}" | cut -d' ' -f1)" == "${raw_sha256}" ]] || fail "${board} IMG 雜湊不符"
	[[ "$(stat -c %s "${archive}")" == "${xz_size}" ]] || fail "${board} XZ 大小不符"
	[[ "$(sha256sum "${archive}" | cut -d' ' -f1)" == "${xz_sha256}" ]] || fail "${board} XZ 雜湊不符"
	if [[ "${verify_archives}" == yes ]]; then
		xz -t "${archive}"
		[[ "$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)" == "${raw_sha256}" ]] || fail "${board} XZ 解壓不同"
	fi
	for item in "source_commit ${candidate_source_commit}" "source_tree ${candidate_source_tree}" \
		"validation_config_sha256 ${build_validation_config_sha256}" "raw_sha256 ${raw_sha256}" \
		"xz_sha256 ${xz_sha256}" "branch ${candidate_branch}"; do
		read -r key expected <<<"${item}"
		require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	for key in uboot_git_source uboot_git_ref uboot_revision uboot_version; do
		expected="$(board_field_optional "${board}" "${key}")"
		[[ -z "${expected}" ]] || require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	validate_boot_area "${image}"
	validate_mounted_image "${image}" "${board}"
	printf '%s\tpass\tpass\tL2\n' "${board}" >>"${verification_file}.partial"
done < <(tail -n +2 "${output_dir}/CANDIDATES.tsv")

mv "${verification_file}.partial" "${verification_file}"
mv "${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial" \
	"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv"
uboot_payload_manifest_sha256="$(sha256sum \
	"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv" | cut -d' ' -f1)"
status_file="${output_dir}/VERIFICATION_STATUS.json"
{
	printf '{\n  "status": "complete",\n  "evidence_level": "L2",\n'
	printf '  "source_commit": "%s",\n' "${candidate_source_commit}"
	printf '  "verifier_commit": "%s",\n' "${verifier_commit}"
	printf '  "build_validation_config_sha256": "%s",\n' "${build_validation_config_sha256}"
	printf '  "verification_config_sha256": "%s",\n' "${verification_config_sha256}"
	printf '  "uboot_payload_manifest_sha256": "%s",\n' "${uboot_payload_manifest_sha256}"
	printf '  "verified_utc": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"${status_file}.partial"
mv "${status_file}.partial" "${status_file}"
echo "${candidate_family_name} 候選映像全部通過 L2 唯讀守門。"
