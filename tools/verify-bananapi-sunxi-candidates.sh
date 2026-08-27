#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-sunxi-a20-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunxi-a20-trixie-current-cli}"
boards_text="${BOARDS:-bananapi bananapipro}"
verify_archives="${VERIFY_ARCHIVES:-yes}"
candidate_family_name="${CANDIDATE_FAMILY_NAME:-Sunxi}"
verify_tmp_prefix="${VERIFY_TMP_PREFIX:-bananapi-verify}"
verification_evidence_level="${VERIFICATION_EVIDENCE_LEVEL:-L2}"
verification_extra_status_json="${VERIFICATION_EXTRA_STATUS_JSON:-}"
verification_pre_complete_hook="${VERIFICATION_PRE_COMPLETE_HOOK:-}"

read -r -a boards <<<"${boards_text}"

fail() {
	echo "驗證失敗：$*" >&2
	exit 1
}

write_verification_state() {
	local state=$1 detail=$2 temporary="${status_file}.partial"
	python3 - "${temporary}" "${state}" "${detail}" "${verification_evidence_level}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

path, state, detail, level = sys.argv[1:]
data = {
    "status": state,
    "detail": detail,
    "evidence_level": level,
    "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(path, path.removesuffix(".partial"))
PY
}

normalize_partition_table() {
	case "$1" in
		dos | msdos) printf 'msdos\n' ;;
		*) printf '%s\n' "$1" ;;
	esac
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

[[ -d "${output_dir}" ]] || fail "找不到候選輸出目錄：${output_dir}"
for command in mv python3; do
	command -v "${command}" >/dev/null || {
		echo "缺少建立失敗狀態所需命令：${command}" >&2
		exit 1
	}
done
status_file="${output_dir}/VERIFICATION_STATUS.json"
write_verification_state in_progress "驗證執行中"
for stale_evidence in VERIFICATION.tsv UBOOT_PAYLOAD_EVIDENCE.tsv FINAL_CONFIG_EVIDENCE.tsv; do
	[[ ! -e "${output_dir}/${stale_evidence}" ]] || unlink "${output_dir}/${stale_evidence}"
done
verification_state_active=yes
finish_verification_state() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 && "${verification_state_active}" == yes ]]; then
		for partial in VERIFICATION.tsv.partial UBOOT_PAYLOAD_EVIDENCE.tsv.partial \
			FINAL_CONFIG_EVIDENCE.tsv.partial; do
			[[ ! -e "${output_dir}/${partial}" ]] || unlink "${output_dir}/${partial}"
		done
		write_verification_state failed "驗證失敗，禁止沿用舊成功狀態"
	fi
	exit "${exit_status}"
}
trap finish_verification_state EXIT

for command in awk basename blkid cmp cut date fdtget find git grep lsblk losetup \
	md5sum mktemp mount mountpoint mv od python3 readlink sha256sum stat sudo udevadm unlink \
	sfdisk sgdisk umount xz; do
	command -v "${command}" >/dev/null || fail "缺少必要命令：${command}"
done

[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"
[[ -f "${output_dir}/CANDIDATES.tsv" ]] || fail "找不到 CANDIDATES.tsv"
[[ -f "${output_dir}/COMPLETION_STATUS.json" ]] || fail "找不到建置狀態"
python3 - "${output_dir}/COMPLETION_STATUS.json" <<'PY' || fail "建置狀態不是唯一 complete"
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
raise SystemExit(0 if status.get("status") == "complete" else 1)
PY
candidate_branch="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("candidate_branch", "current"))
PY
)"
case "${candidate_branch}" in
	current | edge | vendor | legacy) ;;
	*) fail "驗證設定的 candidate_branch 不受支援：${candidate_branch}" ;;
esac
case "${verify_archives}" in
	yes | no) ;;
	*) fail "VERIFY_ARCHIVES 只接受 yes 或 no" ;;
esac
case "${verification_evidence_level}" in
	L1 | L2) ;;
	*) fail "VERIFICATION_EVIDENCE_LEVEL 只接受 L1 或 L2" ;;
esac
if [[ "${verification_evidence_level}" == L2 && "${verify_archives}" != yes ]]; then
	fail "L2 驗證不得停用 XZ 串流同一性檢查"
fi
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
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

board_nested_field_optional() {
	python3 - "${validation_config}" "$1" "$2" "$3" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]].get(sys.argv[3], {}).get(sys.argv[4], "")
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

board_values() {
	python3 - "${validation_config}" "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]].get(sys.argv[3], [])
if isinstance(value, list):
    for item in value:
        print(item)
elif value:
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
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

validate_firmware_source_log() {
	local log_file=$1
	grep -Fq "Fetching SHA1 of 'commit' '${firmware_revision}'" "${log_file}" ||
		fail "建置日誌未以精確提交解析 Armbian 韌體"
	grep -Fq "SHA1 of commit ${firmware_revision}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體完整提交解析結果"
	grep -Fq "${firmware_git_source}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體來源"
	grep -Fq "armbian-firmware-git ${firmware_revision}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體固定來源取用紀錄"
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
			name = ""; status = ""; provides = ""; reading_provides = 0
			for (field_index = 1; field_index <= NF; field_index++) {
				if ($field_index ~ /^Package: /) name = substr($field_index, 10)
				if ($field_index ~ /^Status: /) status = substr($field_index, 9)
				if ($field_index ~ /^Provides: /) {
					provides = substr($field_index, 11)
					reading_provides = 1
				} else if (reading_provides && $field_index ~ /^[[:space:]]/) {
					provides = provides " " $field_index
				} else {
					reading_provides = 0
				}
			}
			if (status != "install ok installed") next
			if (name == package) found = 1
			provided_count = split(provides, provided_packages, ",")
			for (provided_index = 1; provided_index <= provided_count; provided_index++) {
				provided = provided_packages[provided_index]
				sub(/[[:space:]]*\(.*/, "", provided)
				gsub(/^[[:space:]]+|[[:space:]]+$/, "", provided)
				if (provided == package) found = 1
			}
		}
		END { exit found ? 0 : 1 }
	' "$1/var/lib/dpkg/status"
}

validate_boot_area() {
	local image=$1 board=$2 signature partition_table partition_name partition_json actual_table actual_name required_partitions required_partition_types
	signature="$(od -An -tx1 -j510 -N2 "${image}" | awk '{ print $1 $2 }')"
	[[ "${signature}" == 55aa ]] || fail "${image} 缺少 DOS MBR 簽章"
	partition_table="$(board_field_optional "${board}" partition_table)"
	[[ -n "${partition_table}" ]] || return 0
	partition_json="$(sfdisk --json "${image}")" || fail "${board} 無法解析分割表"
	actual_table="$(printf '%s\n' "${partition_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["partitiontable"]["label"])')"
	actual_table="$(normalize_partition_table "${actual_table}")"
	partition_table="$(normalize_partition_table "${partition_table}")"
	[[ "${actual_table}" == "${partition_table}" ]] ||
		fail "${board} 的分割表不是 ${partition_table}"
	if [[ "${partition_table}" == gpt ]]; then
		sgdisk -v "${image}" >/dev/null || fail "${board} 的 GPT 結構或 CRC 不完整"
	fi
	required_partitions="$(board_field_optional "${board}" required_partitions)"
	if [[ -n "${required_partitions}" ]]; then
		python3 - "${required_partitions}" "${partition_json}" <<'PY'
import json
import sys

specifications = sys.argv[1].split()
table = json.loads(sys.argv[2])["partitiontable"]
partitions = table.get("partitions", [])
if len(partitions) != len(specifications):
    raise SystemExit(
        f"分割區數量不符：預期 {len(specifications)}，實際 {len(partitions)}"
    )
for index, specification in enumerate(specifications):
    number, name, start, size = specification.split(":", 3)
    if int(number) != index + 1:
        raise SystemExit(f"GPT 分割區編號不連續：{specification}")
    partition = partitions[index]
    actual = (
        partition.get("name", ""),
        str(partition.get("start", "")),
        str(partition.get("size", "")),
    )
    expected = (name, start, size)
    for field, actual_value, expected_value in zip(("名稱", "起點", "大小"), actual, expected):
        if expected_value != "*" and actual_value != expected_value:
            raise SystemExit(
                f"GPT 第 {number} 分割區{field}不符：預期 {expected_value}，實際 {actual_value}"
            )
PY
	fi
	required_partition_types="$(board_field_optional "${board}" required_partition_types)"
	if [[ -n "${required_partition_types}" ]]; then
		python3 - "${required_partition_types}" "${partition_json}" <<'PY'
import json
import sys

specifications = sys.argv[1].split()
partitions = json.loads(sys.argv[2])["partitiontable"].get("partitions", [])
if len(specifications) != len(partitions):
    raise SystemExit("分割區類型數量與實際分割區不符")
for index, specification in enumerate(specifications):
    number, expected_type = specification.split(":", 1)
    if int(number) != index + 1:
        raise SystemExit(f"分割區類型編號不連續：{specification}")
    actual_type = str(partitions[index].get("type", "")).lower().removeprefix("0x")
    if actual_type != expected_type.lower().removeprefix("0x"):
        raise SystemExit(
            f"第 {number} 分割區類型不符：預期 {expected_type}，實際 {actual_type}"
        )
PY
	fi
	partition_name="$(board_field_optional "${board}" partition_name)"
	if [[ -n "${partition_name}" ]]; then
		actual_name="$(printf '%s\n' "${partition_json}" | python3 -c 'import json,sys; p=json.load(sys.stdin)["partitiontable"]["partitions"]; print(p[0].get("name", "") if p else "")')"
		[[ "${actual_name}" == "${partition_name}" ]] ||
			fail "${board} 的第一分割區名稱不是 ${partition_name}"
	fi
}

validate_installed_kernel_source() {
	local mount_dir=$1 linux_source linux_revision linux_ref metadata_files=() metadata_file
	linux_source="$(top_field_optional linux_source)"
	linux_revision="$(top_field_optional linux_commit)"
	[[ -n "${linux_source}${linux_revision}" ]] || return 0
	[[ -n "${linux_source}" && "${linux_revision}" =~ ^[0-9a-f]{40}$ ]] ||
		fail "Linux 來源政策欄位不完整"
	linux_ref="$(top_field_optional linux_ref)"
	[[ -n "${linux_ref}" ]] || linux_ref="commit:${linux_revision}"
	mapfile -t metadata_files < <(find "${mount_dir}/usr/lib" -path \
		'*/linux-image-*/armbian-kernel-metadata.sh' -type f -print)
	[[ ${#metadata_files[@]} -eq 1 ]] || fail "映像缺少唯一 Linux 來源中繼資料"
	metadata_file="${metadata_files[0]}"
	grep -Fqx "declare KERNEL_GIT_SOURCE=\"${linux_source}\"" "${metadata_file}" ||
		fail "Linux Git 來源不符"
	grep -Fqx "declare KERNEL_GIT_BRANCH=\"${linux_ref}\"" "${metadata_file}" ||
		fail "Linux Git 分支不符"
	grep -Fqx "declare KERNEL_GIT_REVISION=\"${linux_revision}\"" "${metadata_file}" ||
		fail "Linux Git revision 不符"
}

validate_installed_uboot() {
	local image=$1 mount_dir=$2 board=$3
	local uboot_tag uboot_version uboot_git_source uboot_git_ref uboot_revision
	local component component_upper component_prefix component_source component_ref component_revision
	local payload_specs package_only_payloads payload_spec payload_name offset uboot_dir payload
	local metadata_file md5sums_file payload_size payload_sha256 minimum_spec
	local rkbin_source rkbin_ref rkbin_revision partition_table partition_start_sector sector_size payload_end first_partition_byte
	local uboot_target_index uboot_config_file uboot_target_metadata uboot_defconfig option_line target_fragment forbidden_fragment required_fragment
	local final_uboot_config_sha256 actual_uboot_config_sha256 exact_size_spec
	local overlap_allowed earlier_payload later_payload overlap_start write_order
	local earlier_offset later_offset earlier_size later_size earlier_end later_end prefix_size tail_skip tail_size
	local uboot_binary_name uboot_binary uboot_version_fallback=no
	local -a payload_names=()
	local -A payload_offsets=() payload_paths=() payload_sizes=() payload_hashes=()
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
	if grep -Fqx "declare UBOOT_VERSION=\"${uboot_version}\"" "${metadata_file}"; then
		:
	elif grep -Fqx 'declare UBOOT_VERSION="0"' "${metadata_file}" &&
		grep -Fq "declare UBOOT_ARTIFACT_VERSION=\"${uboot_version}-" "${metadata_file}"; then
		# 部分舊 vendor 樹跳過 Makefile 版本；稍後仍須核對二進位版本字串。
		uboot_version_fallback=yes
	else
		fail "${board} 的 U-Boot 版本不是 ${uboot_tag}"
	fi
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
	partition_table="$(board_field_optional "${board}" partition_table)"
	if [[ -n "${partition_table}" ]]; then
		grep -Fqx "declare UBOOT_PARTITION_TYPE=\"${partition_table}\"" "${metadata_file}" ||
			fail "${board} 的 U-Boot 分割表政策不符"
	fi
	for component in atf crust; do
		component_source="$(board_field_optional "${board}" "${component}_git_source")"
		component_ref="$(board_field_optional "${board}" "${component}_git_ref")"
		component_revision="$(board_field_optional "${board}" "${component}_revision")"
		[[ -n "${component_source}${component_ref}${component_revision}" ]] || continue
		[[ -n "${component_source}" && -n "${component_ref}" && -n "${component_revision}" ]] ||
			fail "${board} 的 ${component} 來源政策欄位不完整"
		[[ "${component_revision}" =~ ^[0-9a-f]{40}$ ]] ||
			fail "${board} 的 ${component} revision 格式不符"
		component_upper="${component^^}"
		component_prefix="UBOOT_${component_upper}_GIT"
		grep -Fqx "declare ${component_prefix}_SOURCE=\"${component_source}\"" "${metadata_file}" ||
			fail "${board} 的 ${component} Git 來源不符"
		grep -Fqx "declare ${component_prefix}_BRANCH=\"${component_ref}\"" "${metadata_file}" ||
			fail "${board} 的 ${component} Git 分支不符"
		grep -Fqx "declare ${component_prefix}_REVISION=\"${component_revision}\"" "${metadata_file}" ||
			fail "${board} 的 ${component} Git revision 不符"
	done
	rkbin_revision="$(top_field_optional rkbin_commit)"
	if [[ -n "${rkbin_revision}" ]]; then
		[[ "${rkbin_revision}" =~ ^[0-9a-f]{40}$ ]] || fail "${board} 的 rkbin revision 格式不符"
		rkbin_source="$(top_field_optional rkbin_source)"
		[[ -n "${rkbin_source}" ]] || rkbin_source="https://github.com/armbian/rkbin"
		rkbin_ref="$(top_field_optional rkbin_ref)"
		[[ -n "${rkbin_ref}" ]] || rkbin_ref="commit:${rkbin_revision}"
		grep -Fqx "declare UBOOT_RKBIN_GIT_SOURCE=\"${rkbin_source}\"" "${metadata_file}" ||
			fail "${board} 的 rkbin Git 來源不符"
		grep -Fqx "declare UBOOT_RKBIN_GIT_BRANCH=\"${rkbin_ref}\"" "${metadata_file}" ||
			fail "${board} 的 rkbin Git 分支不符"
		grep -Fqx "declare UBOOT_RKBIN_GIT_REVISION=\"${rkbin_revision}\"" "${metadata_file}" ||
			fail "${board} 的 rkbin Git revision 不符"
	fi
	uboot_target_index="$(board_field_optional "${board}" uboot_target_index)"
	if [[ -n "${uboot_target_index}" ]]; then
		[[ "${uboot_target_index}" =~ ^[1-9][0-9]*$ ]] || fail "${board} 的 U-Boot target 編號無效"
		uboot_config_file="${uboot_dir}/u-boot-config-target-${uboot_target_index}"
		uboot_target_metadata="${uboot_dir}/u-boot-metadata-target-${uboot_target_index}.sh"
		[[ -s "${uboot_config_file}" && -s "${uboot_target_metadata}" ]] ||
			fail "${board} 缺少 U-Boot target 設定證據"
		while IFS= read -r option_line; do
			[[ -n "${option_line}" ]] || continue
			grep -Fqx "${option_line}" "${uboot_config_file}" ||
				fail "${board} 的 U-Boot 設定不符：${option_line}"
		done < <(board_values "${board}" uboot_required_config_options)
		while IFS= read -r target_fragment; do
			[[ -n "${target_fragment}" ]] || continue
			grep -Fq -- "${target_fragment}" "${uboot_target_metadata}" ||
				fail "${board} 的 U-Boot target 缺少：${target_fragment}"
		done < <(board_values "${board}" uboot_target_make_contains)
		while IFS= read -r forbidden_fragment; do
			[[ -n "${forbidden_fragment}" ]] || continue
			if grep -Fq -- "${forbidden_fragment}" "${uboot_target_metadata}"; then
				fail "${board} 的 U-Boot target 含禁止片段：${forbidden_fragment}"
			fi
		done < <(board_values "${board}" uboot_target_make_forbidden)
		actual_uboot_config_sha256="$(sha256sum "${uboot_config_file}" | cut -d' ' -f1)"
		final_uboot_config_sha256="$(board_field_optional "${board}" final_uboot_config_sha256)"
		if [[ -n "${final_uboot_config_sha256}" ]]; then
			[[ "${final_uboot_config_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
				fail "${board} 的最終 U-Boot 設定雜湊格式不符"
			[[ "${actual_uboot_config_sha256}" == "${final_uboot_config_sha256}" ]] ||
				fail "${board} 的最終 U-Boot 設定雜湊不符"
		fi
		printf '%s\tuboot\t%s\t%s\n' "${board}" \
			"usr/lib/linux-u-boot-${candidate_branch}-${board}/$(basename "${uboot_config_file}")" \
			"${actual_uboot_config_sha256}" >>"${output_dir}/FINAL_CONFIG_EVIDENCE.tsv.partial"
	fi
	uboot_binary_name="$(board_field_optional "${board}" uboot_binary_for_string_checks)"
	[[ -n "${uboot_binary_name}" ]] || uboot_binary_name="u-boot.bin"
	[[ "${uboot_binary_name}" =~ ^[A-Za-z0-9._+-]+$ ]] ||
		fail "${board} 的 U-Boot 字串檢查載荷名稱無效"
	uboot_binary="${uboot_dir}/${uboot_binary_name}"
	if [[ "${uboot_version_fallback}" == yes ]]; then
		[[ -s "${uboot_binary}" ]] || fail "${board} 缺少可檢查的 U-Boot 載荷：${uboot_binary_name}"
		grep -aFq -- "U-Boot ${uboot_version}" "${uboot_binary}" ||
			fail "${board} 的 U-Boot 二進位版本字串不是 ${uboot_version}"
	fi
	while IFS= read -r forbidden_fragment; do
		[[ -n "${forbidden_fragment}" ]] || continue
		[[ -s "${uboot_binary}" ]] || fail "${board} 缺少可檢查的 U-Boot 載荷：${uboot_binary_name}"
		if grep -aFq -- "${forbidden_fragment}" "${uboot_binary}"; then
			fail "${board} 的 U-Boot 仍含禁止的供應商開機路徑：${forbidden_fragment}"
		fi
	done < <(board_values "${board}" uboot_forbidden_binary_strings)
	while IFS= read -r required_fragment; do
		[[ -n "${required_fragment}" ]] || continue
		[[ -s "${uboot_binary}" ]] || fail "${board} 缺少可檢查的 U-Boot 載荷：${uboot_binary_name}"
		grep -aFq -- "${required_fragment}" "${uboot_binary}" ||
			fail "${board} 的 U-Boot 缺少必要開機路徑：${required_fragment}"
	done < <(board_values "${board}" uboot_required_binary_strings)
	uboot_defconfig="$(board_field_optional "${board}" uboot_defconfig)"
	if [[ -n "${uboot_defconfig}" ]]; then
		[[ -s "${mount_dir}/usr/lib/u-boot/${uboot_defconfig}" ]] ||
			fail "${board} 缺少 U-Boot defconfig：${uboot_defconfig}"
	fi
	partition_start_sector="$(board_field_optional "${board}" partition_start_sector)"
	sector_size="$(board_field_optional "${board}" logical_sector_size)"
	[[ -n "${sector_size}" ]] || sector_size=512
	[[ "${sector_size}" =~ ^[1-9][0-9]*$ ]] || fail "${board} 的邏輯 sector 大小無效"
	validate_uboot_payload_file() {
		local checked_payload_name=$1 checked_payload checked_payload_path
		local checked_expected_md5 checked_actual_md5 checked_size checked_minimum=1 checked_exact_size=""
		local checked_sha256_spec checked_expected_sha256="" checked_actual_sha256
		checked_payload="${uboot_dir}/${checked_payload_name}"
		checked_payload_path="usr/lib/linux-u-boot-${candidate_branch}-${board}/${checked_payload_name}"
		[[ -s "${checked_payload}" ]] || fail "${board} 缺少 U-Boot payload：${checked_payload_name}"
		checked_expected_md5="$(awk -v path="${checked_payload_path}" '$2 == path { print $1 }' "${md5sums_file}")"
		[[ "${checked_expected_md5}" =~ ^[0-9a-f]{32}$ ]] ||
			fail "${board} 缺少唯一 payload MD5：${checked_payload_name}"
		checked_actual_md5="$(sudo md5sum "${checked_payload}" | cut -d' ' -f1)"
		[[ "${checked_actual_md5}" == "${checked_expected_md5}" ]] ||
			fail "${board} 的 U-Boot payload 已被修改：${checked_payload_name}"
		for checked_sha256_spec in $(board_field_optional "${board}" uboot_payload_sha256); do
			[[ "${checked_sha256_spec}" =~ ^[^=]+=[0-9a-f]{64}$ ]] ||
				fail "${board} 的 payload SHA-256 規格無效"
			[[ "${checked_sha256_spec%%=*}" == "${checked_payload_name}" ]] || continue
			[[ -z "${checked_expected_sha256}" ]] ||
				fail "${board} 的 payload SHA-256 規格重複：${checked_payload_name}"
			checked_expected_sha256="${checked_sha256_spec#*=}"
		done
		if [[ -n "${checked_expected_sha256}" ]]; then
			checked_actual_sha256="$(sudo sha256sum "${checked_payload}" | cut -d' ' -f1)"
			[[ "${checked_actual_sha256}" == "${checked_expected_sha256}" ]] ||
				fail "${board} 的 payload SHA-256 不符：${checked_payload_name}"
		fi
		for minimum_spec in $(board_field_optional "${board}" uboot_payload_minimum_sizes); do
			[[ "${minimum_spec%=*}" == "${checked_payload_name}" ]] || continue
			checked_minimum="${minimum_spec##*=}"
		done
		[[ "${checked_minimum}" =~ ^[1-9][0-9]*$ ]] ||
			fail "${board} 的 payload 最小大小無效：${checked_payload_name}"
		checked_size="$(stat -c %s "${checked_payload}")"
		(( checked_size >= checked_minimum )) ||
			fail "${board} 的 U-Boot payload 太小：${checked_payload_name}"
		for exact_size_spec in $(board_field_optional "${board}" uboot_payload_sizes); do
			[[ "${exact_size_spec}" =~ ^[^=]+=[1-9][0-9]*$ ]] ||
				fail "${board} 的 payload 精確大小規格無效"
			[[ "${exact_size_spec%%=*}" == "${checked_payload_name}" ]] || continue
			[[ -z "${checked_exact_size}" ]] ||
				fail "${board} 的 payload 精確大小規格重複：${checked_payload_name}"
			checked_exact_size="${exact_size_spec#*=}"
		done
		if [[ -n "${checked_exact_size}" && "${checked_size}" != "${checked_exact_size}" ]]; then
			fail "${board} 的 U-Boot payload 精確大小不符：${checked_payload_name}"
		fi
		printf '%s\t%s\n' "${checked_payload}" "${checked_size}"
	}
	for payload_spec in ${payload_specs}; do
		[[ "${payload_spec}" =~ ^[^@]+@[0-9]+$ ]] || fail "${board} 的 U-Boot payload 規格不符"
		payload_name="${payload_spec%@*}"
		offset="${payload_spec##*@}"
		[[ -z "${payload_offsets[${payload_name}]+存在}" ]] ||
			fail "${board} 的 U-Boot payload 名稱重複：${payload_name}"
		payload="${uboot_dir}/${payload_name}"
		validate_uboot_payload_file "${payload_name}" >/dev/null
		payload_size="$(stat -c %s "${payload}")"
		payload_sha256="$(sudo sha256sum "${payload}" | cut -d' ' -f1)"
		if [[ -n "${partition_start_sector}" ]]; then
			payload_end=$((offset + payload_size))
			first_partition_byte=$((partition_start_sector * sector_size))
			(( payload_end <= first_partition_byte )) ||
				fail "${board} 的 ${payload_name} 超出第一分割區前保留區"
		fi
		payload_names+=("${payload_name}")
		payload_offsets["${payload_name}"]="${offset}"
		payload_paths["${payload_name}"]="${payload}"
		payload_sizes["${payload_name}"]="${payload_size}"
		payload_hashes["${payload_name}"]="${payload_sha256}"
	done
	overlap_allowed="$(board_nested_field_optional "${board}" payload_overlap_policy allowed)"
	case "${overlap_allowed}" in
	"" | false)
		for payload_name in "${payload_names[@]}"; do
			offset="${payload_offsets[${payload_name}]}"
			payload="${payload_paths[${payload_name}]}"
			payload_size="${payload_sizes[${payload_name}]}"
			sudo cmp --silent --ignore-initial="0:${offset}" --bytes="${payload_size}" \
				"${payload}" "${image}" || fail "${board} 映像 ${offset} 偏移與 ${payload_name} 不同"
			printf '%s\t%s\timage\t%s\t%s\t%s\n' \
				"${board}" "${payload_name}" "${offset}" "${payload_size}" \
				"${payload_hashes[${payload_name}]}" \
				>>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
		done
		;;
	true)
		[[ ${#payload_names[@]} -eq 2 ]] || fail "${board} 的受控重疊只允許兩個 payload"
		earlier_payload="$(board_nested_field_optional "${board}" payload_overlap_policy earlier_payload)"
		later_payload="$(board_nested_field_optional "${board}" payload_overlap_policy later_payload)"
		overlap_start="$(board_nested_field_optional "${board}" payload_overlap_policy overlap_starts_at_image_offset)"
		write_order="$(board_field_optional "${board}" payload_write_order)"
		[[ -n "${earlier_payload}" && -n "${later_payload}" && "${earlier_payload}" != "${later_payload}" ]] ||
			fail "${board} 的受控重疊 payload 身分不完整"
		[[ "${write_order}" == "${earlier_payload} ${later_payload}" ]] ||
			fail "${board} 的 payload 寫入順序與重疊契約不一致"
		[[ -n "${payload_offsets[${earlier_payload}]+存在}" &&
			-n "${payload_offsets[${later_payload}]+存在}" ]] ||
			fail "${board} 的受控重疊 payload 未完整封裝"
		earlier_offset="${payload_offsets[${earlier_payload}]}"
		later_offset="${payload_offsets[${later_payload}]}"
		earlier_size="${payload_sizes[${earlier_payload}]}"
		later_size="${payload_sizes[${later_payload}]}"
		[[ "${overlap_start}" =~ ^[0-9]+$ && "${later_offset}" == "${overlap_start}" ]] ||
			fail "${board} 的受控重疊起點與後寫 payload 位移不一致"
		earlier_end=$((earlier_offset + earlier_size))
		later_end=$((later_offset + later_size))
		(( earlier_offset < later_offset && later_offset < earlier_end )) ||
			fail "${board} 的 payload 沒有形成契約指定的重疊"
		(( later_end < earlier_end )) ||
			fail "${board} 的後寫 payload 未保留可驗證的先寫 payload 尾段"
		prefix_size=$((later_offset - earlier_offset))
		sudo cmp --silent --ignore-initial="0:${earlier_offset}" --bytes="${prefix_size}" \
			"${payload_paths[${earlier_payload}]}" "${image}" ||
			fail "${board} 映像的先寫 payload 前段不符"
		sudo cmp --silent --ignore-initial="0:${later_offset}" --bytes="${later_size}" \
			"${payload_paths[${later_payload}]}" "${image}" ||
			fail "${board} 映像的後寫 payload 不符"
		tail_skip=$((later_end - earlier_offset))
		tail_size=$((earlier_end - later_end))
		sudo cmp --silent --ignore-initial="${tail_skip}:${later_end}" --bytes="${tail_size}" \
			"${payload_paths[${earlier_payload}]}" "${image}" ||
			fail "${board} 映像的先寫 payload 尾段不符"
		for payload_name in "${payload_names[@]}"; do
			printf '%s\t%s\timage-controlled-overlap\t%s\t%s\t%s\n' \
				"${board}" "${payload_name}" "${payload_offsets[${payload_name}]}" \
				"${payload_sizes[${payload_name}]}" "${payload_hashes[${payload_name}]}" \
				>>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
		done
		;;
	*) fail "${board} 的 payload 重疊允許值只接受 true 或 false" ;;
	esac
	package_only_payloads="$(board_field_optional "${board}" uboot_package_only_payloads)"
	for payload_name in ${package_only_payloads}; do
		validate_uboot_payload_file "${payload_name}" >/dev/null
		payload="${uboot_dir}/${payload_name}"
		payload_size="$(stat -c %s "${payload}")"
		payload_sha256="$(sudo sha256sum "${payload}" | cut -d' ' -f1)"
		printf '%s\t%s\tpackage-only\t-\t%s\t%s\n' \
			"${board}" "${payload_name}" "${payload_size}" "${payload_sha256}" \
			>>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
	done
}

validate_mounted_image() (
	local image=$1 board=$2
	local dtb_relative dtb_basename dtb_path fdt_override model compatible expected node node_status option_line option value package
	local loop_device partition boot_partition mount_dir config_file overlay_prefix overlay overlay_directory default_overlays required_overlays overlays_line sd_node sd_bus_width requirement required_node required_width kernel_family root_partition_number boot_partition_number
	local boot_configuration extlinux_fdt expected_start_sector actual_start_sector property_spec property_node property_name property_expected installed_manifest installed_spec installed_path installed_sha256
	local vendor_boot_directory root_uuid final_kernel_config_sha256 actual_kernel_config_sha256 forbidden_asset
	local boot_partition_label root_partition_label root_partition_filesystem_type boot_script_source boot_script_source_sha256 boot_script_source_path boot_script_payload
	local dtb_sha256 alias_spec alias_name alias_expected forbidden_fragment
	local required_module_path
	local -a module_matches=() config_files=() config_hashes=()
	dtb_relative="$(board_field "${board}" dtb)"
	dtb_basename="$(basename "${dtb_relative}")"
	mount_dir="$(mktemp -d "${repo_dir}/.tmp/${verify_tmp_prefix}.XXXXXX")"
	boot_script_payload=""
	loop_device="$(sudo losetup --find --show --partscan --read-only "${image}")"
	cleanup_image() {
		if mountpoint -q "${mount_dir}/boot"; then sudo umount "${mount_dir}/boot"; fi
		if mountpoint -q "${mount_dir}"; then sudo umount "${mount_dir}"; fi
		sudo losetup -d "${loop_device}" 2>/dev/null || true
		[[ -z "${boot_script_payload}" || ! -e "${boot_script_payload}" ]] || unlink "${boot_script_payload}"
		rmdir "${mount_dir}" 2>/dev/null || true
	}
	trap cleanup_image EXIT
	udevadm settle
	[[ "$(lsblk -dnro RO "${loop_device}")" == 1 ]] || fail "${board} 的 loop 裝置不是唯讀"
	root_partition_number="$(board_field_optional "${board}" root_partition_number)"
	[[ -n "${root_partition_number}" ]] || root_partition_number=1
	[[ "${root_partition_number}" =~ ^[1-9][0-9]*$ ]] || fail "${board} 的根分割區編號無效"
	partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" | awk -v wanted="${root_partition_number}" '$2 == "part" { count++ } count == wanted { print $1; exit }')"
	[[ -n "${partition}" ]] || fail "${board} 沒有可掛載分割區"
	[[ "$(lsblk -dnro RO "${partition}")" == 1 ]] || fail "${board} 的根分割區不是唯讀"
	root_partition_label="$(board_field_optional "${board}" root_partition_label)"
	if [[ -n "${root_partition_label}" ]]; then
		[[ "$(sudo blkid -s LABEL -o value "${partition}")" == "${root_partition_label}" ]] ||
			fail "${board} 的根分割區標籤不是 ${root_partition_label}"
	fi
	root_partition_filesystem_type="$(board_field_optional "${board}" root_partition_filesystem_type)"
	if [[ -n "${root_partition_filesystem_type}" ]]; then
		[[ "$(sudo blkid -s TYPE -o value "${partition}")" == "${root_partition_filesystem_type}" ]] ||
			fail "${board} 的根分割區檔案系統不是 ${root_partition_filesystem_type}"
	fi
	boot_partition_number="$(board_field_optional "${board}" boot_partition_number)"
	expected_start_sector="$(board_field_optional "${board}" root_partition_start_sector)"
	if [[ -z "${expected_start_sector}" && -z "${boot_partition_number}" ]]; then
		expected_start_sector="$(board_field_optional "${board}" partition_start_sector)"
	fi
	if [[ -n "${expected_start_sector}" ]]; then
		actual_start_sector="$(read_partition_start_sector "${partition}")"
		[[ "${actual_start_sector}" == "${expected_start_sector}" ]] ||
			fail "${board} 的根分割區起點不是 ${expected_start_sector} sector"
	fi
	sudo mount -o ro,noload,nosuid,nodev,noexec "${partition}" "${mount_dir}"
	if [[ -n "${boot_partition_number}" ]]; then
		[[ "${boot_partition_number}" =~ ^[1-9][0-9]*$ ]] || fail "${board} 的 boot 分割區編號無效"
		boot_partition="$(lsblk -nrpo NAME,TYPE "${loop_device}" | awk -v wanted="${boot_partition_number}" '$2 == "part" { count++ } count == wanted { print $1; exit }')"
		[[ -n "${boot_partition}" ]] || fail "${board} 缺少 boot 分割區"
		[[ "$(lsblk -dnro RO "${boot_partition}")" == 1 ]] || fail "${board} 的 boot 分割區不是唯讀"
		boot_partition_label="$(board_field_optional "${board}" boot_partition_label)"
		if [[ -n "${boot_partition_label}" ]]; then
			[[ "$(sudo blkid -s LABEL -o value "${boot_partition}")" == "${boot_partition_label}" ]] ||
				fail "${board} 的 boot 分割區標籤不是 ${boot_partition_label}"
		fi
		[[ -d "${mount_dir}/boot" ]] || fail "${board} 根檔案系統缺少 /boot 掛載點"
		sudo mount -o ro,nosuid,nodev,noexec "${boot_partition}" "${mount_dir}/boot"
	fi

	[[ -s "${mount_dir}/boot/zImage" || -s "${mount_dir}/boot/Image" || \
		-s "${mount_dir}/boot/uImage" ]] || fail "${board} 缺少核心映像"
	[[ -s "${mount_dir}/boot/uInitrd" ]] || fail "${board} 缺少 initrd"
	boot_configuration="$(board_field_optional "${board}" boot_configuration)"
	[[ -n "${boot_configuration}" ]] || boot_configuration="armbian_env"
	if [[ "${boot_configuration}" == separate_fat_armbian_env ]]; then
		overlay_prefix="$(board_field_optional "${board}" overlay_prefix)"
	else
		overlay_prefix="$(board_field "${board}" overlay_prefix)"
	fi
	overlay_directory="$(board_field_optional "${board}" overlay_directory)"
	[[ -n "${overlay_directory}" ]] || overlay_directory="overlay"
	case "${boot_configuration}" in
		armbian_env)
			[[ -n "${overlay_prefix}" ]] || fail "${board} 的 armbian_env 模式缺少 overlay_prefix"
			[[ -s "${mount_dir}/boot/armbianEnv.txt" ]] || fail "${board} 缺少 armbianEnv.txt"
			if grep -q '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt"; then
				fdt_override="$(grep '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt")"
				[[ "${fdt_override}" == "fdtfile=${dtb_relative}" || \
					"${fdt_override}" == "fdtfile=${dtb_basename}" ]] || fail "${board} 的 fdtfile 覆寫不符"
			fi
			grep -qx "overlay_prefix=${overlay_prefix}" "${mount_dir}/boot/armbianEnv.txt" ||
				fail "${board} 的 overlay_prefix 不符"
			;;
		separate_fat_armbian_env)
			command -v dumpimage >/dev/null || fail "${board} 驗證 boot.scr 需要 dumpimage"
			[[ -n "${boot_partition_number}" ]] || fail "${board} 的獨立 FAT boot 模式缺少 boot 分割區"
			[[ -s "${mount_dir}/boot/armbianEnv.txt" ]] || fail "${board} 缺少 armbianEnv.txt"
			[[ "$(grep -Fxc "fdtfile=${dtb_relative}" "${mount_dir}/boot/armbianEnv.txt")" == 1 ]] ||
				fail "${board} 的 armbianEnv.txt 必須有唯一且精確的 fdtfile"
			root_uuid="$(sudo blkid -s UUID -o value "${partition}")"
			[[ -n "${root_uuid}" ]] || fail "${board} 無法讀取根檔案系統 UUID"
			[[ "$(grep -Fxc "rootdev=UUID=${root_uuid}" "${mount_dir}/boot/armbianEnv.txt")" == 1 ]] ||
				fail "${board} 的 armbianEnv.txt 未唯一對應第二分割區 UUID"
			[[ "$(grep -c '^rootdev=' "${mount_dir}/boot/armbianEnv.txt")" == 1 ]] ||
				fail "${board} 的 armbianEnv.txt 含多重 rootdev"
			[[ -s "${mount_dir}/boot/boot.scr" ]] || fail "${board} 缺少 boot.scr"
			boot_script_source="$(board_field "${board}" boot_script_source)"
			boot_script_source_sha256="$(board_field "${board}" boot_script_source_sha256)"
			[[ "${boot_script_source}" =~ ^[A-Za-z0-9._/-]+$ && "${boot_script_source}" != *..* ]] ||
				fail "${board} 的 boot script 來源路徑不合法"
			boot_script_source_path="$(readlink -f "${repo_dir}/${boot_script_source}")"
			[[ "${boot_script_source_path}" == "${repo_dir}/"* && -f "${boot_script_source_path}" ]] ||
				fail "${board} 的 boot script 來源不在倉庫內"
			[[ "${boot_script_source_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
				fail "${board} 的 boot script 來源雜湊格式不符"
			[[ "$(sha256sum "${boot_script_source_path}" | cut -d' ' -f1)" == "${boot_script_source_sha256}" ]] ||
				fail "${board} 的 boot script 受控來源雜湊不符"
			boot_script_payload="$(mktemp "${repo_dir}/.tmp/${verify_tmp_prefix}.boot-script.XXXXXX")"
			dumpimage -T script -p 0 -o "${boot_script_payload}" "${mount_dir}/boot/boot.scr" >/dev/null ||
				fail "${board} 無法抽取 boot.scr 內容"
			if ! python3 - "${boot_script_source_path}" "${boot_script_payload}" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
extracted = Path(sys.argv[2]).read_bytes()
if extracted == source:
    raise SystemExit(0)
has_length_prefix = (
    len(extracted) == len(source) + 8
    and int.from_bytes(extracted[:4], "big") == len(source)
    and extracted[4:8] == b"\0" * 4
    and extracted[8:] == source
)
raise SystemExit(0 if has_length_prefix else 1)
PY
			then
				fail "${board} 的 boot.scr 與受控 boot 命令內容不同"
			fi
			;;
		extlinux)
			extlinux_fdt="$(board_field "${board}" extlinux_fdt)"
			[[ -s "${mount_dir}/boot/extlinux/extlinux.conf" ]] || fail "${board} 缺少 extlinux.conf"
			grep -Fqx "  fdt ${extlinux_fdt}" "${mount_dir}/boot/extlinux/extlinux.conf" ||
				fail "${board} 的 extlinux FDT 不符"
			;;
		sunplus_uenv)
			[[ -s "${mount_dir}/boot/uEnv.txt" ]] || fail "${board} 缺少 Sunplus uEnv.txt"
			vendor_boot_directory="$(board_field "${board}" vendor_boot_directory)"
			for expected in uImage uInitrd "${dtb_basename}"; do
				[[ -s "${mount_dir}/boot/${vendor_boot_directory}/${expected}" ]] ||
					fail "${board} 缺少 vendor boot 檔案：${vendor_boot_directory}/${expected}"
			done
			root_uuid="$(sudo blkid -s UUID -o value "${partition}")"
			[[ -n "${root_uuid}" ]] || fail "${board} 無法讀取根檔案系統 UUID"
			grep -Fqx "root=UUID=${root_uuid}" "${mount_dir}/boot/uEnv.txt" ||
				fail "${board} 的 uEnv.txt 未固定本映像根檔案系統 UUID"
			if grep -Eq '(^|[[:space:]])root=/dev/mmcblk' "${mount_dir}/boot/uEnv.txt"; then
				fail "${board} 的 uEnv.txt 仍使用不穩定的 mmcblk 根裝置"
			fi
			;;
		*) fail "${board} 的開機設定類型不支援：${boot_configuration}" ;;
	esac
	if [[ "${boot_configuration}" == separate_fat_armbian_env ]]; then
		default_overlays="$(board_field_optional "${board}" default_overlays)"
	else
		default_overlays="$(board_field "${board}" default_overlays)"
	fi
	if [[ -n "${default_overlays}" ]]; then
		overlays_line="$(grep '^overlays=' "${mount_dir}/boot/armbianEnv.txt")" || fail "${board} 缺少預設 overlays"
		for overlay in ${default_overlays}; do
			[[ " ${overlays_line#overlays=} " == *" ${overlay} "* ]] || fail "${board} 未預設啟用 overlay：${overlay}"
		done
	fi
	if [[ "${boot_configuration}" == separate_fat_armbian_env ]]; then
		required_overlays="$(board_field_optional "${board}" required_overlays)"
	else
		required_overlays="$(board_field "${board}" required_overlays)"
	fi
	for overlay in ${required_overlays}; do
		[[ -s "${mount_dir}/boot/dtb/${overlay_directory}/${overlay_prefix}-${overlay}.dtbo" ]] ||
			fail "${board} 缺少 overlay：${overlay_prefix}-${overlay}.dtbo"
	done
	dtb_path="$(board_field_optional "${board}" dtb_image_path)"
	if [[ -n "${dtb_path}" ]]; then
		dtb_path="${mount_dir}${dtb_path}"
	else
		dtb_path="${mount_dir}/boot/dtb/${dtb_relative}"
	fi
	if [[ ! -s "${dtb_path}" ]]; then
		dtb_path="${mount_dir}/boot/dtb/${dtb_basename}"
	fi
	[[ -s "${dtb_path}" ]] || fail "${board} 缺少 DTB：${dtb_relative}"
	dtb_sha256="$(board_field_optional "${board}" dtb_sha256)"
	if [[ -n "${dtb_sha256}" ]]; then
		[[ "$(sha256sum "${dtb_path}" | cut -d' ' -f1)" == "${dtb_sha256}" ]] ||
			fail "${board} 的 DTB 雜湊不符"
	fi
	while IFS= read -r forbidden_fragment; do
		[[ -n "${forbidden_fragment}" ]] || continue
		if grep -aFq -- "${forbidden_fragment}" "${dtb_path}"; then
			fail "${board} 的 DTB 仍含禁止的供應商開機路徑：${forbidden_fragment}"
		fi
	done < <(board_values "${board}" dtb_forbidden_binary_strings)
	model="$(fdtget -t s "${dtb_path}" / model)"
	[[ "${model}" == "$(board_field "${board}" model)" ]] || fail "${board} 的 DTB model 不符"
	compatible="$(fdtget -t s "${dtb_path}" / compatible)"
	for expected in $(board_field "${board}" compatible); do
		[[ " ${compatible} " == *" ${expected} "* ]] || fail "${board} 缺少相容字串 ${expected}"
	done
	for node in $(board_field "${board}" required_status_nodes); do
		[[ "$(fdtget -t s "${dtb_path}" "${node}" status)" == okay ]] || fail "${board} 節點未啟用：${node}"
	done
	for node in $(board_field_optional "${board}" required_present_nodes); do
		fdtget -l "${dtb_path}" "${node}" >/dev/null || fail "${board} 缺少 DT 節點：${node}"
		node_status="$(fdtget -t s "${dtb_path}" "${node}" status 2>/dev/null || true)"
		case "${node_status}" in
		"" | ok | okay) ;;
		*) fail "${board} DT 節點不可用：${node}，status=${node_status}" ;;
		esac
	done
	for node in $(board_field_optional "${board}" required_disabled_nodes); do
		[[ "$(fdtget -t s "${dtb_path}" "${node}" status)" == disabled ]] ||
			fail "${board} 節點未停用：${node}"
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
	for property_spec in $(board_field_optional "${board}" required_uint_properties); do
		property_node="${property_spec%%:*}"
		property_name="${property_spec#*:}"
		property_expected="${property_name#*=}"
		property_name="${property_name%%=*}"
		[[ "$(fdtget -t u "${dtb_path}" "${property_node}" "${property_name}")" == "${property_expected}" ]] ||
			fail "${board} 的 DT 數值屬性不符：${property_node}:${property_name}"
	done
	for alias_spec in $(board_field_optional "${board}" required_aliases); do
		alias_name="${alias_spec%%=*}"
		alias_expected="${alias_spec#*=}"
		[[ "$(fdtget -t s "${dtb_path}" /aliases "${alias_name}")" == "${alias_expected}" ]] ||
			fail "${board} 的 DT alias 不符：${alias_name}"
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

	mapfile -t config_files < <(find "${mount_dir}/boot" -maxdepth 1 -type f -name 'config-*' -print | sort)
	[[ ${#config_files[@]} -gt 0 ]] || fail "${board} 缺少核心設定檔"
	mapfile -t config_hashes < <(
		sha256sum "${config_files[@]}" | awk '{ print $1 }' | sort -u
	)
	[[ ${#config_hashes[@]} -eq 1 ]] ||
		fail "${board} 必須只有一份唯一核心設定內容，實際 ${#config_hashes[@]} 種"
	config_file="${config_files[0]}"
	while IFS= read -r option_line; do
		option="${option_line%%=*}"; value="${option_line#*=}"
		grep -qx "${option}=${value}" "${config_file}" || fail "${board} 核心設定不符：${option}=${value}"
	done < <(common_values common_kernel_options)
	actual_kernel_config_sha256="${config_hashes[0]}"
	final_kernel_config_sha256="$(board_field_optional "${board}" final_kernel_config_sha256)"
	if [[ -n "${final_kernel_config_sha256}" ]]; then
		[[ "${final_kernel_config_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
			fail "${board} 的最終核心設定雜湊格式不符"
		[[ "${actual_kernel_config_sha256}" == "${final_kernel_config_sha256}" ]] ||
			fail "${board} 的最終核心設定雜湊不符"
	fi
	printf '%s\tkernel\tboot/%s\t%s\n' "${board}" "$(basename "${config_file}")" \
		"${actual_kernel_config_sha256}" >>"${output_dir}/FINAL_CONFIG_EVIDENCE.tsv.partial"
	while IFS= read -r forbidden_asset; do
		[[ -n "${forbidden_asset}" ]] || continue
		[[ "${forbidden_asset}" =~ ^[A-Za-z0-9._+-]+$ ]] ||
			fail "${board} 的禁止封裝資產名稱不合法：${forbidden_asset}"
		if [[ -n "$(sudo find "${mount_dir}" -xdev -name "${forbidden_asset}" -print -quit)" ]] ||
			{ mountpoint -q "${mount_dir}/boot" &&
				[[ -n "$(sudo find "${mount_dir}/boot" -xdev -name "${forbidden_asset}" -print -quit)" ]]; }; then
			fail "${board} 映像含禁止封裝資產：${forbidden_asset}"
		fi
	done < <(board_values "${board}" forbidden_packaged_assets)
	while IFS= read -r required_module_path; do
		[[ -n "${required_module_path}" ]] || continue
		[[ "${required_module_path}" =~ ^kernel/[A-Za-z0-9_./+-]+\.ko([.](xz|zst))?$ &&
			"${required_module_path}" != *..* ]] ||
			fail "${board} 的必要核心模組路徑不合法：${required_module_path}"
		mapfile -t module_matches < <(find "${mount_dir}/lib/modules" -type f \
			-path "*/${required_module_path}" -print)
		[[ ${#module_matches[@]} -eq 1 ]] ||
			fail "${board} 找到 ${#module_matches[@]} 個必要核心模組：${required_module_path}"
	done < <(common_values required_kernel_module_paths 2>/dev/null || true)
	for package in $(common_values common_packages); do
		package_installed "${mount_dir}" "${package}" || fail "${board} 缺少套件 ${package}"
	done
	for installed_manifest in installed_firmware_blobs installed_file_sha256; do
		while IFS= read -r installed_spec; do
			[[ -n "${installed_spec}" ]] || continue
			installed_path="${installed_spec%%=*}"
			installed_sha256="${installed_spec#*=}"
			[[ -f "${mount_dir}${installed_path}" ]] || fail "${board} 缺少受控檔案 ${installed_path}"
			[[ "$(sudo sha256sum "${mount_dir}${installed_path}" | cut -d' ' -f1)" == "${installed_sha256}" ]] ||
				fail "${board} 的受控檔案雜湊不符：${installed_path}"
		done < <(common_values "${installed_manifest}" 2>/dev/null || true)
	done
	kernel_family="$(top_field_optional kernel_family)"
	[[ -n "${kernel_family}" ]] || kernel_family="sunxi"
	for package in "linux-image-${candidate_branch}-${kernel_family}" "linux-dtb-${candidate_branch}-${kernel_family}" \
		"linux-u-boot-${board}-${candidate_branch}" "armbian-bsp-cli-${board}-${candidate_branch}"; do
		package_installed "${mount_dir}" "${package}" || fail "${board} 缺少 Armbian 套件 ${package}"
	done
	validate_installed_kernel_source "${mount_dir}"
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
candidate_matrix_sha256="$(sha256sum "${output_dir}/CANDIDATES.tsv" | cut -d' ' -f1)"
if [[ "${verification_evidence_level}" == L2 ]]; then
	[[ "${candidate_source_commit}" == "${verifier_commit}" ]] ||
		fail "L2 候選來源提交與驗證器提交不一致"
	[[ "${build_validation_config_sha256}" == "${verification_config_sha256}" ]] ||
		fail "L2 建置與驗證使用的 validation 雜湊不一致"
fi
python3 - "${output_dir}/COMPLETION_STATUS.json" "${candidate_source_commit}" \
	"${candidate_source_tree}" "${build_validation_config_sha256}" \
	"${candidate_matrix_sha256}" <<'PY' || fail "建置完成狀態未綁定候選來源與矩陣"
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
expected = {
    "status": "complete",
    "source_commit": sys.argv[2],
    "source_tree": sys.argv[3],
    "validation_config_sha256": sys.argv[4],
    "candidates_sha256": sys.argv[5],
}
raise SystemExit(0 if all(status.get(key) == value for key, value in expected.items()) else 1)
PY
verify_firmware_source_resolution="$(top_field_optional verify_firmware_source_resolution)"
firmware_git_source=""
firmware_git_ref=""
firmware_revision=""
case "${verify_firmware_source_resolution}" in
	"" | false) verify_firmware_source_resolution="" ;;
	true)
		firmware_git_source="$(top_field_optional firmware_source)"
		firmware_git_ref="$(top_field_optional firmware_ref)"
		firmware_revision="$(top_field_optional firmware_commit)"
		[[ -n "${firmware_git_source}" &&
			"${firmware_git_ref}" == "commit:${firmware_revision}" &&
			"${firmware_revision}" =~ ^[0-9a-f]{40}$ ]] ||
			fail "Armbian 韌體固定來源政策欄位不完整"
		;;
	*) fail "verify_firmware_source_resolution 只接受 true 或 false" ;;
esac

verification_file="${output_dir}/VERIFICATION.tsv"
printf 'board\tidentity\tread_only_content\tevidence_level\n' >"${verification_file}.partial"
printf 'board\tpayload\tplacement\toffset\tsize\tsha256\n' \
	>"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial"
printf 'board\tcomponent\tpath\tsha256\n' \
	>"${output_dir}/FINAL_CONFIG_EVIDENCE.tsv.partial"
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
	for key in uboot_git_source uboot_git_ref uboot_revision uboot_version \
		atf_git_source atf_git_ref atf_revision \
		crust_git_source crust_git_ref crust_revision; do
		expected="$(board_field_optional "${board}" "${key}")"
		[[ -z "${expected}" ]] || require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	for key in linux_git_source linux_git_ref linux_revision rkbin_git_source rkbin_git_ref rkbin_revision; do
		case "${key}" in
			linux_git_source) expected="$(top_field_optional linux_source)" ;;
			linux_git_ref)
				expected="$(top_field_optional linux_ref)"
				[[ -n "${expected}" ]] || { value="$(top_field_optional linux_commit)"; [[ -z "${value}" ]] || expected="commit:${value}"; }
				;;
			linux_revision) expected="$(top_field_optional linux_commit)" ;;
			rkbin_git_source)
				expected="$(top_field_optional rkbin_source)"
				[[ -n "${expected}" ]] || { value="$(top_field_optional rkbin_commit)"; [[ -z "${value}" ]] || expected="https://github.com/armbian/rkbin"; }
				;;
			rkbin_git_ref)
				expected="$(top_field_optional rkbin_ref)"
				[[ -n "${expected}" ]] || { value="$(top_field_optional rkbin_commit)"; [[ -z "${value}" ]] || expected="commit:${value}"; }
				;;
			rkbin_revision) expected="$(top_field_optional rkbin_commit)" ;;
		esac
		[[ -z "${expected}" ]] || require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	if [[ "${verify_firmware_source_resolution}" == true ]]; then
		for item in \
			"verify_firmware_source_resolution true" \
			"firmware_git_source ${firmware_git_source}" \
			"firmware_git_ref ${firmware_git_ref}" \
			"firmware_revision ${firmware_revision}"; do
			read -r key expected <<<"${item}"
			require_metadata_value "${metadata}" "${key}" "${expected}"
		done
		build_log_relative="$(read_metadata_value "${metadata}" build_log)" ||
			fail "${board} 的中繼資料缺少建置日誌"
		[[ "${build_log_relative}" =~ ^logs/[A-Za-z0-9._+-]+\.log$ ]] ||
			fail "${board} 的建置日誌路徑不合法"
		[[ -f "${output_dir}/${build_log_relative}" ]] ||
			fail "${board} 缺少建置日誌"
		validate_firmware_source_log "${output_dir}/${build_log_relative}"
	fi
	validate_boot_area "${image}" "${board}"
	validate_mounted_image "${image}" "${board}"
	printf '%s\tpass\tpass\t%s\n' "${board}" "${verification_evidence_level}" >>"${verification_file}.partial"
done < <(tail -n +2 "${output_dir}/CANDIDATES.tsv")

mv "${verification_file}.partial" "${verification_file}"
mv "${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv.partial" \
	"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv"
mv "${output_dir}/FINAL_CONFIG_EVIDENCE.tsv.partial" \
	"${output_dir}/FINAL_CONFIG_EVIDENCE.tsv"
uboot_payload_manifest_sha256="$(sha256sum \
	"${output_dir}/UBOOT_PAYLOAD_EVIDENCE.tsv" | cut -d' ' -f1)"
final_config_manifest_sha256="$(sha256sum \
	"${output_dir}/FINAL_CONFIG_EVIDENCE.tsv" | cut -d' ' -f1)"
{
	printf '{\n  "status": "complete",\n  "evidence_level": "%s",\n' "${verification_evidence_level}"
	printf '  "source_commit": "%s",\n' "${candidate_source_commit}"
	printf '  "verifier_commit": "%s",\n' "${verifier_commit}"
	printf '  "build_validation_config_sha256": "%s",\n' "${build_validation_config_sha256}"
	printf '  "verification_config_sha256": "%s",\n' "${verification_config_sha256}"
	printf '  "candidate_matrix_sha256": "%s",\n' "${candidate_matrix_sha256}"
	printf '  "uboot_payload_manifest_sha256": "%s",\n' "${uboot_payload_manifest_sha256}"
	printf '  "final_config_manifest_sha256": "%s",\n' "${final_config_manifest_sha256}"
	printf '  "verified_utc": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"${status_file}.partial"

if [[ -n "${verification_pre_complete_hook}" ]]; then
	resolved_hook="$(readlink -f "${verification_pre_complete_hook}")"
	[[ "${resolved_hook}" == "${repo_dir}/tools/"* && -x "${resolved_hook}" ]] ||
		fail "驗證完成前 hook 必須是倉庫 tools 內的可執行檔"
	"${resolved_hook}" "${status_file}.partial"
fi
if [[ -n "${verification_extra_status_json}" ]]; then
	resolved_extra="$(readlink -f "${verification_extra_status_json}")"
	[[ "${resolved_extra}" == "${repo_dir}/.tmp/"* && -f "${resolved_extra}" ]] ||
		fail "驗證附加狀態必須是倉庫 .tmp 內的 JSON"
	python3 - "${status_file}.partial" "${resolved_extra}" <<'PY'
import json
import os
import sys

status_path, extra_path = sys.argv[1:]
with open(status_path, encoding="utf-8") as stream:
    status = json.load(stream)
with open(extra_path, encoding="utf-8") as stream:
    extra = json.load(stream)
protected = {
    "status", "evidence_level", "source_commit", "verifier_commit",
    "build_validation_config_sha256", "verification_config_sha256",
    "candidate_matrix_sha256", "uboot_payload_manifest_sha256",
    "final_config_manifest_sha256", "verified_utc",
}
if not isinstance(extra, dict) or protected.intersection(extra):
    raise SystemExit("附加驗證狀態格式不合法或覆寫受保護欄位")
status.update(extra)
temporary = status_path + ".merge"
with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
os.replace(temporary, status_path)
PY
fi
python3 - "${status_file}.partial" "${verification_evidence_level}" \
	"${candidate_source_commit}" "${verifier_commit}" \
	"${build_validation_config_sha256}" "${verification_config_sha256}" \
	"${candidate_matrix_sha256}" "${uboot_payload_manifest_sha256}" \
	"${final_config_manifest_sha256}" <<'PY' || fail "驗證完成狀態的受保護欄位遭到修改"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
expected = {
    "status": "complete",
    "evidence_level": sys.argv[2],
    "source_commit": sys.argv[3],
    "verifier_commit": sys.argv[4],
    "build_validation_config_sha256": sys.argv[5],
    "verification_config_sha256": sys.argv[6],
    "candidate_matrix_sha256": sys.argv[7],
    "uboot_payload_manifest_sha256": sys.argv[8],
    "final_config_manifest_sha256": sys.argv[9],
}
raise SystemExit(0 if all(status.get(key) == value for key, value in expected.items()) else 1)
PY
mv "${status_file}.partial" "${status_file}"
verification_state_active=no
trap - EXIT
echo "${candidate_family_name} 候選映像全部通過 ${verification_evidence_level} 唯讀守門。"
