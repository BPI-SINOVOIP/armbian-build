#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-vs680-m6-trixie-legacy-cli}"
verify_archive="${VERIFY_ARCHIVE:-yes}"

for command in awk blkid cmp cut date fdtget find git grep lsblk losetup \
	md5sum mktemp mount mountpoint od python3 sha256sum sfdisk stat sudo \
	udevadm umount xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "候選驗證失敗：$*" >&2
	exit 1
}

json_value() {
	python3 - "${validation_config}" "$@" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
for key in sys.argv[2:]:
    value = value[key]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

json_lines() {
	python3 - "${validation_config}" "$@" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
for key in sys.argv[2:]:
    value = value[key]
for item in value:
    print(item)
PY
}

read_metadata() {
	local file=$1 key=$2 matches=()
	mapfile -t matches < <(grep -E "^${key}=" "${file}")
	[[ ${#matches[@]} -eq 1 ]] || return 1
	printf '%s\n' "${matches[0]#*=}"
}

require_metadata() {
	local actual
	actual="$(read_metadata "$1" "$2")" ||
		fail "中繼資料缺少唯一欄位 $2：$1"
	[[ "${actual}" == "$3" ]] ||
		fail "中繼資料欄位 $2 不符：預期 $3，實際 ${actual}"
}

package_installed() {
	awk -v package="$2" '
		BEGIN { RS = ""; FS = "\n" }
		{
			name = ""; status = ""
			for (index = 1; index <= NF; index++) {
				if ($index ~ /^Package: /) name = substr($index, 10)
				if ($index ~ /^Status: /) status = substr($index, 9)
			}
			if (name == package && status == "install ok installed") found = 1
		}
		END { exit found ? 0 : 1 }
	' "$1/var/lib/dpkg/status"
}

[[ -f "${validation_config}" ]] || fail "找不到驗證契約"
[[ -f "${output_dir}/COMPLETION_STATUS.json" ]] || fail "找不到建置狀態"
grep -q '"status": "complete"' "${output_dir}/COMPLETION_STATUS.json" ||
	fail "完整候選建置尚未完成"
[[ -f "${output_dir}/CANDIDATES.tsv" ]] || fail "找不到 CANDIDATES.tsv"
[[ "$(awk 'NR > 1 { count++ } END { print count + 0 }' "${output_dir}/CANDIDATES.tsv")" == 1 ]] ||
	fail "候選矩陣必須恰有一筆 M6"
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹不是乾淨狀態"
case "${verify_archive}" in
	yes | no) ;;
	*) fail "VERIFY_ARCHIVE 只接受 yes 或 no" ;;
esac
sudo -n true || fail "唯讀 loop 驗證需要免互動 sudo"

"${repo_dir}/tools/verify-bananapi-vs680-m6-sources.sh"

read -r board release profile raw_size raw_sha256 xz_size xz_sha256 image_relative archive_relative source_commit uboot_tag \
	< <(awk -F '\t' 'NR == 2 { print $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11 }' "${output_dir}/CANDIDATES.tsv")
[[ "${board}" == bananapim6 && "${release}" == trixie && "${profile}" == cli ]] ||
	fail "候選矩陣身分不符"
[[ "${uboot_tag}" == "$(json_value boards bananapim6 uboot_tag)" ]] ||
	fail "候選矩陣 U-Boot 標籤不符"
image="${output_dir}/${image_relative}"
archive="${output_dir}/${archive_relative}"
metadata="${output_dir}/bananapim6/artifact.metadata.txt"
[[ -f "${image}" && -f "${archive}" && -f "${metadata}" ]] ||
	fail "IMG、XZ 或中繼資料不完整"
[[ "$(stat -c %s "${image}")" == "${raw_size}" ]] || fail "IMG 大小不符"
[[ "$(sha256sum "${image}" | cut -d' ' -f1)" == "${raw_sha256}" ]] || fail "IMG 雜湊不符"
[[ "$(stat -c %s "${archive}")" == "${xz_size}" ]] || fail "XZ 大小不符"
[[ "$(sha256sum "${archive}" | cut -d' ' -f1)" == "${xz_sha256}" ]] || fail "XZ 雜湊不符"
if [[ "${verify_archive}" == yes ]]; then
	xz -t "${archive}"
	[[ "$(xz -dc "${archive}" | sha256sum | cut -d' ' -f1)" == "${raw_sha256}" ]] ||
		fail "XZ 串流與 IMG 不一致"
fi

require_metadata "${metadata}" board bananapim6
require_metadata "${metadata}" release trixie
require_metadata "${metadata}" branch legacy
require_metadata "${metadata}" source_commit "${source_commit}"
require_metadata "${metadata}" uboot_git_ref "$(json_value boards bananapim6 uboot_git_ref)"
require_metadata "${metadata}" uboot_revision "$(json_value boards bananapim6 uboot_revision)"
require_metadata "${metadata}" linux_git_ref "$(json_value linux_ref)"
require_metadata "${metadata}" linux_revision "$(json_value linux_commit)"

table_json="$(sfdisk --json "${image}")" || fail "無法解析 MBR 分割表"
[[ "$(printf '%s' "${table_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["partitiontable"]["label"])')" == dos ]] ||
	fail "分割表不是 DOS/MBR"
expected_start="$(json_value boards bananapim6 partition_start_sector)"
actual_start="$(printf '%s' "${table_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["partitiontable"]["partitions"][0]["start"])')"
[[ "${actual_start}" == "${expected_start}" ]] || fail "boot 分割區起點不符"
[[ "$(printf '%s' "${table_json}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["partitiontable"]["partitions"]))')" == 2 ]] ||
	fail "M6 映像必須恰有 boot 與 root 兩個分割區"

loop_device=""
boot_mount="$(mktemp -d "${repo_dir}/.tmp/m6-boot-ro.XXXXXX")"
root_mount="$(mktemp -d "${repo_dir}/.tmp/m6-root-ro.XXXXXX")"
cleanup() {
	local exit_status=$?
	trap - EXIT INT TERM
	release_mounts
	exit "${exit_status}"
}
release_mounts() {
	if mountpoint -q "${root_mount}"; then sudo umount "${root_mount}"; fi
	if mountpoint -q "${boot_mount}"; then sudo umount "${boot_mount}"; fi
	if [[ -n "${loop_device}" ]]; then sudo losetup -d "${loop_device}"; fi
	rmdir "${root_mount}" "${boot_mount}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

loop_device="$(sudo losetup --find --show --read-only --partscan "${image}")"
udevadm settle
[[ "$(lsblk -dnro RO "${loop_device}")" == 1 ]] || fail "loop 裝置不是唯讀"
boot_partition="${loop_device}p1"
root_partition="${loop_device}p2"
[[ -b "${boot_partition}" && -b "${root_partition}" ]] || fail "找不到雙分割區 loop 裝置"
[[ "$(sudo blkid -s LABEL -o value "${boot_partition}")" == "$(json_value boards bananapim6 boot_partition_label)" ]] ||
	fail "boot 分割區標籤不符"
[[ "$(sudo blkid -s LABEL -o value "${root_partition}")" == "$(json_value boards bananapim6 root_partition_label)" ]] ||
	fail "root 分割區標籤不符"
sudo mount -o ro,nosuid,nodev,noexec "${boot_partition}" "${boot_mount}"
sudo mount -o ro,nosuid,nodev,noexec "${root_partition}" "${root_mount}"

for required in Image uInitrd boot.scr armbianEnv.txt dtb/synaptics/vs680-a0-bananapi-m6.dtb; do
	[[ -s "${boot_mount}/${required}" ]] || fail "boot 分割區缺少：${required}"
done
grep -Fqx 'fdtfile=synaptics/vs680-a0-bananapi-m6.dtb' "${boot_mount}/armbianEnv.txt" ||
	fail "armbianEnv.txt 的 fdtfile 不符"

dtb="${boot_mount}/dtb/synaptics/vs680-a0-bananapi-m6.dtb"
[[ "$(fdtget -t s "${dtb}" / model)" == "$(json_value boards bananapim6 model)" ]] ||
	fail "DTB model 不符"
actual_compatibles="$(fdtget -t s "${dtb}" / compatible)"
while IFS= read -r compatible; do
	[[ " ${actual_compatibles} " == *" ${compatible} "* ]] || fail "DTB 缺少 compatible：${compatible}"
done < <(json_lines boards bananapim6 compatible)
expected_dtb_sha256="$(json_value boards bananapim6 dtb_sha256)"
[[ -z "${expected_dtb_sha256}" || "$(sha256sum "${dtb}" | cut -d' ' -f1)" == "${expected_dtb_sha256}" ]] ||
	fail "DTB SHA-256 不符"

while IFS= read -r node; do
	fdtget -p "${dtb}" "${node}" >/dev/null || fail "DTB 缺少節點：${node}"
	status="$(fdtget -t s "${dtb}" "${node}" status 2>/dev/null || true)"
	[[ -z "${status}" || "${status}" == okay ]] || fail "DTB 節點未啟用：${node}=${status}"
done < <(json_lines boards bananapim6 required_status_nodes)
while IFS= read -r node; do
	fdtget -p "${dtb}" "${node}" >/dev/null || fail "DTB 缺少節點：${node}"
done < <(json_lines boards bananapim6 required_present_nodes)

sd_node="$(json_value boards bananapim6 sd_node)"
[[ "$(fdtget -t u "${dtb}" "${sd_node}" bus-width)" == "$(json_value boards bananapim6 sd_bus_width)" ]] ||
	fail "SD bus-width 不符"
while IFS= read -r specification; do
	node="${specification%=*}"
	expected="${specification##*=}"
	[[ "$(fdtget -t u "${dtb}" "${node}" bus-width)" == "${expected}" ]] ||
		fail "儲存 bus-width 不符：${node}"
done < <(json_lines boards bananapim6 additional_bus_widths)
while IFS= read -r specification; do
	node="${specification%:*}"
	property="${specification##*:}"
	fdtget "${dtb}" "${node}" "${property}" >/dev/null 2>&1 ||
		fail "DTB 缺少布林屬性：${node}:${property}"
done < <(json_lines boards bananapim6 required_boolean_properties)
while IFS= read -r specification; do
	node="${specification%%:*}"
	rest="${specification#*:}"
	property="${rest%%=*}"
	expected="${rest#*=}"
	[[ "$(fdtget -t s "${dtb}" "${node}" "${property}")" == "${expected}" ]] ||
		fail "DTB 字串屬性不符：${node}:${property}"
done < <(json_lines boards bananapim6 required_string_properties)

uboot_dir="${root_mount}/usr/lib/linux-u-boot-legacy-bananapim6"
uboot_metadata="${uboot_dir}/u-boot-metadata.sh"
uboot_config="${uboot_dir}/u-boot-config-target-1"
[[ -s "${uboot_metadata}" && -s "${uboot_config}" ]] || fail "rootfs 缺少 U-Boot 套件證據"
require_metadata "${uboot_metadata}" UBOOT_GIT_SOURCE "$(json_value boards bananapim6 uboot_git_source)"
require_metadata "${uboot_metadata}" UBOOT_GIT_BRANCH "$(json_value boards bananapim6 uboot_git_ref)"
require_metadata "${uboot_metadata}" UBOOT_GIT_REVISION "$(json_value boards bananapim6 uboot_revision)"
while IFS= read -r option; do
	grep -Fqx "${option}" "${uboot_config}" || fail "U-Boot 設定不符：${option}"
done < <(json_lines boards bananapim6 uboot_required_config_options)

tzk="${uboot_dir}/bpi-m6-tzk-4MB.bin"
uboot="${uboot_dir}/u-boot.bin"
[[ -s "${tzk}" && -s "${uboot}" ]] || fail "U-Boot 套件缺少 TZK 或 u-boot.bin"
expected_tzk_sha256="$(json_value opaque_boot_payloads packages/blobs/vs680/bpi-m6-tzk-4MB.bin sha256)"
[[ "$(sudo sha256sum "${tzk}" | cut -d' ' -f1)" == "${expected_tzk_sha256}" ]] || fail "套件 TZK 雜湊不符"
expected_uboot_sha256="$(json_lines boards bananapim6 uboot_payload_sha256 | \
	awk -F '=' '$1 == "u-boot.bin" { print $2 }')"
[[ "${expected_uboot_sha256}" =~ ^[0-9a-f]{64}$ ]] ||
	fail "契約缺少 U-Boot 成品雜湊"
[[ "$(sudo sha256sum "${uboot}" | cut -d' ' -f1)" == "${expected_uboot_sha256}" ]] ||
	fail "套件 U-Boot 雜湊不符"
sudo cmp "${tzk}" "${repo_dir}/packages/blobs/vs680/bpi-m6-tzk-4MB.bin" >/dev/null ||
	fail "套件 TZK 與受控來源不一致"

[[ "$(json_value boards bananapim6 payload_overlap_policy allowed)" == true ]] ||
	fail "契約未允許受控 payload 覆蓋"
tzk_offset="$(json_lines boards bananapim6 uboot_payloads | \
	awk -F '@' '$1 == "bpi-m6-tzk-4MB.bin" { print $2 }')"
uboot_offset="$(json_lines boards bananapim6 uboot_payloads | \
	awk -F '@' '$1 == "u-boot.bin" { print $2 }')"
[[ "${tzk_offset}" =~ ^[0-9]+$ && "${uboot_offset}" =~ ^[0-9]+$ ]] ||
	fail "契約缺少有效 payload 位移"
[[ "${uboot_offset}" == "$(json_value boards bananapim6 payload_overlap_policy overlap_starts_at_image_offset)" ]] ||
	fail "U-Boot 位移與覆蓋契約不一致"
tzk_size="$(sudo stat -c %s "${tzk}")"
uboot_size="$(sudo stat -c %s "${uboot}")"
expected_tzk_minimum="$(json_lines boards bananapim6 uboot_payload_minimum_sizes | \
	awk -F '=' '$1 == "bpi-m6-tzk-4MB.bin" { print $2 }')"
expected_uboot_minimum="$(json_lines boards bananapim6 uboot_payload_minimum_sizes | \
	awk -F '=' '$1 == "u-boot.bin" { print $2 }')"
[[ "${expected_tzk_minimum}" =~ ^[0-9]+$ && "${expected_uboot_minimum}" =~ ^[0-9]+$ ]] ||
	fail "契約缺少有效 payload 大小下限"
(( tzk_size >= expected_tzk_minimum )) || fail "TZK 大小低於契約下限"
(( uboot_size >= expected_uboot_minimum )) || fail "U-Boot 大小低於契約下限"
tzk_prefix_size=$((uboot_offset - tzk_offset))
(( uboot_size < tzk_size - tzk_prefix_size )) || fail "U-Boot 超出 TZK 受控覆蓋區"
sudo cmp -n "${tzk_prefix_size}" -i "0:${tzk_offset}" "${tzk}" "${image}" >/dev/null ||
	fail "映像 TZK 前段不符"
sudo cmp -n "${uboot_size}" -i "0:${uboot_offset}" "${uboot}" "${image}" >/dev/null ||
	fail "映像 U-Boot 區段不符"
tzk_tail_skip=$((tzk_prefix_size + uboot_size))
image_tail_skip=$((uboot_offset + uboot_size))
tzk_tail_size=$((tzk_size - tzk_tail_skip))
sudo cmp -n "${tzk_tail_size}" -i "${tzk_tail_skip}:${image_tail_skip}" "${tzk}" "${image}" >/dev/null ||
	fail "映像 TZK 尾段不符"

while IFS= read -r required_string; do
	grep -aFq "${required_string}" "${uboot}" || fail "U-Boot 缺少身分字串：${required_string}"
done < <(json_lines boards bananapim6 uboot_required_binary_strings)

mapfile -t kernel_metadata_files < <(find "${root_mount}/usr/lib" -path '*/linux-image-*/armbian-kernel-metadata.sh' -type f -print)
[[ ${#kernel_metadata_files[@]} -eq 1 ]] || fail "rootfs 缺少唯一 Linux 來源中繼資料"
grep -Fqx "declare KERNEL_GIT_SOURCE=\"$(json_value linux_source)\"" "${kernel_metadata_files[0]}" ||
	fail "Linux 來源中繼資料不符"
grep -Fqx "declare KERNEL_GIT_BRANCH=\"$(json_value linux_ref)\"" "${kernel_metadata_files[0]}" ||
	fail "Linux ref 中繼資料不符"
grep -Fqx "declare KERNEL_GIT_REVISION=\"$(json_value linux_commit)\"" "${kernel_metadata_files[0]}" ||
	fail "Linux revision 中繼資料不符"

source_note="${root_mount}/usr/share/doc/armbian-bsp-bananapim6/bpi-m6-tzk-4MB.bin.SOURCE.zh-TW.md"
[[ -s "${source_note}" ]] || fail "映像缺少 TZK 來源與授權紀錄"
[[ "$(sudo sha256sum "${source_note}" | cut -d' ' -f1)" == "$(json_value installed_firmware_blobs /usr/share/doc/armbian-bsp-bananapim6/bpi-m6-tzk-4MB.bin.SOURCE.zh-TW.md)" ]] ||
	fail "映像內 TZK 來源紀錄不符"
while IFS= read -r package; do
	package_installed "${root_mount}" "${package}" || fail "映像缺少診斷套件：${package}"
done < <(json_lines common_packages)

verifier_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
verification_config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
cat >"${output_dir}/VERIFICATION_STATUS.json" <<EOF
{
  "status": "complete",
  "evidence_level": "L2",
  "board": "bananapim6",
  "source_commit": "${source_commit}",
  "verifier_commit": "${verifier_commit}",
  "validation_config_sha256": "${verification_config_sha256}",
  "image_sha256": "${raw_sha256}",
  "verified_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hardware_claims_allowed": false,
  "public_release_allowed": false
}
EOF

trap - EXIT INT TERM
release_mounts
echo "BPI-M6 完整候選唯讀驗證通過：${image}"
