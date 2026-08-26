#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-sunxi-a20-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunxi-a20-trixie-current-cli}"
boards_text="${BOARDS:-bananapi bananapipro}"
verify_archives="${VERIFY_ARCHIVES:-yes}"

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
	local uboot_tag uboot_version payload_name offset uboot_dir payload
	local metadata_file md5sums_file payload_path expected_md5 actual_md5 payload_size
	uboot_tag="$(board_field "${board}" uboot_tag)"
	uboot_version="${uboot_tag#v}"
	payload_name="$(board_field "${board}" uboot_payload)"
	offset="$(board_field "${board}" uboot_offset)"
	uboot_dir="${mount_dir}/usr/lib/linux-u-boot-current-${board}"
	payload="${uboot_dir}/${payload_name}"
	metadata_file="${uboot_dir}/u-boot-metadata.sh"
	md5sums_file="${mount_dir}/var/lib/dpkg/info/linux-u-boot-${board}-current.md5sums"
	payload_path="usr/lib/linux-u-boot-current-${board}/${payload_name}"

	[[ -s "${payload}" && -s "${metadata_file}" && -s "${md5sums_file}" ]] ||
		fail "${board} 缺少可驗證的 U-Boot 套件 payload"
	grep -qx "declare UBOOT_VERSION=\"${uboot_version}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot 版本不是 ${uboot_tag}"
	grep -qx "declare UBOOT_GIT_BRANCH=\"tag:${uboot_tag}\"" "${metadata_file}" ||
		fail "${board} 的 U-Boot Git 分支不符"
	expected_md5="$(awk -v path="${payload_path}" '$2 == path { print $1 }' "${md5sums_file}")"
	[[ "${expected_md5}" =~ ^[0-9a-f]{32}$ ]] || fail "${board} 缺少唯一 payload MD5"
	actual_md5="$(md5sum "${payload}" | cut -d' ' -f1)"
	[[ "${actual_md5}" == "${expected_md5}" ]] || fail "${board} 的 U-Boot payload 已被修改"
	payload_size="$(stat -c %s "${payload}")"
	(( payload_size > 32768 )) || fail "${board} 的 U-Boot payload 太小"
	cmp --silent --ignore-initial="0:${offset}" --bytes="${payload_size}" \
		"${payload}" "${image}" || fail "${board} 映像 ${offset} 偏移與 U-Boot payload 不同"
}

validate_mounted_image() (
	local image=$1 board=$2
	local dtb_relative dtb_basename dtb_path fdt_override model compatible expected node option_line option value package
	local loop_device partition mount_dir config_file overlay_prefix overlay default_overlays overlays_line sd_node sd_bus_width requirement required_node required_width
	dtb_relative="$(board_field "${board}" dtb)"
	dtb_basename="$(basename "${dtb_relative}")"
	mount_dir="$(mktemp -d "${repo_dir}/.tmp/sunxi-verify.XXXXXX")"
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
	sudo mount -o ro,noload "${partition}" "${mount_dir}"

	[[ -s "${mount_dir}/boot/zImage" || -s "${mount_dir}/boot/Image" ]] || fail "${board} 缺少核心映像"
	[[ -s "${mount_dir}/boot/uInitrd" ]] || fail "${board} 缺少 initrd"
	if grep -q '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt"; then
		fdt_override="$(grep '^fdtfile=' "${mount_dir}/boot/armbianEnv.txt")"
		[[ "${fdt_override}" == "fdtfile=${dtb_relative}" || \
			"${fdt_override}" == "fdtfile=${dtb_basename}" ]] || fail "${board} 的 fdtfile 覆寫不符"
	fi
	overlay_prefix="$(board_field "${board}" overlay_prefix)"
	grep -qx "overlay_prefix=${overlay_prefix}" "${mount_dir}/boot/armbianEnv.txt" || fail "${board} 的 overlay_prefix 不符"
	default_overlays="$(board_field "${board}" default_overlays)"
	if [[ -n "${default_overlays}" ]]; then
		overlays_line="$(grep '^overlays=' "${mount_dir}/boot/armbianEnv.txt")" || fail "${board} 缺少預設 overlays"
		for overlay in ${default_overlays}; do
			[[ " ${overlays_line#overlays=} " == *" ${overlay} "* ]] || fail "${board} 未預設啟用 overlay：${overlay}"
		done
	fi
	for overlay in $(board_field "${board}" required_overlays); do
		[[ -s "${mount_dir}/boot/dtb/overlay/${overlay_prefix}-${overlay}.dtbo" ]] ||
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
	for package in linux-image-current-sunxi linux-dtb-current-sunxi \
		"linux-u-boot-${board}-current" "armbian-bsp-cli-${board}-current"; do
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
		"xz_sha256 ${xz_sha256}"; do
		read -r key expected <<<"${item}"
		require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	validate_boot_area "${image}"
	validate_mounted_image "${image}" "${board}"
	printf '%s\tpass\tpass\tL2\n' "${board}" >>"${verification_file}.partial"
done < <(tail -n +2 "${output_dir}/CANDIDATES.tsv")

mv "${verification_file}.partial" "${verification_file}"
status_file="${output_dir}/VERIFICATION_STATUS.json"
{
	printf '{\n  "status": "complete",\n  "evidence_level": "L2",\n'
	printf '  "source_commit": "%s",\n' "${candidate_source_commit}"
	printf '  "verifier_commit": "%s",\n' "${verifier_commit}"
	printf '  "build_validation_config_sha256": "%s",\n' "${build_validation_config_sha256}"
	printf '  "verification_config_sha256": "%s",\n' "${verification_config_sha256}"
	printf '  "verified_utc": "%s"\n}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"${status_file}.partial"
mv "${status_file}.partial" "${status_file}"
echo "Sunxi 候選映像全部通過 L2 唯讀守門。"
