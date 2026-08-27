#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-vs680-m6-legacy.json}"
cache_lower="${CACHE_LOWER:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache}"
verify_remote_refs="${VERIFY_REMOTE_REFS:-no}"

for command in cut find git grep python3 sha256sum stat wc; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "來源驗證失敗：$*" >&2
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
else:
    print(value)
PY
}

find_git_tree_with_commit() {
	local parent=$1 revision=$2 candidate
	[[ -d "${parent}" ]] || return 1
	while IFS= read -r -d '' candidate; do
		if git -C "${candidate}" cat-file -e "${revision}^{commit}" 2>/dev/null; then
			printf '%s\n' "${candidate}"
			return 0
		fi
	done < <(find "${parent}" -mindepth 1 -maxdepth 1 -type d -print0)
	return 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證契約：${validation_config}"
case "${verify_remote_refs}" in
	yes | no) ;;
	*) fail "VERIFY_REMOTE_REFS 只接受 yes 或 no" ;;
esac

linux_revision="$(json_value source_commits linux revision)"
uboot_revision="$(json_value source_commits uboot revision)"
linux_tree="${LINUX_SOURCE_TREE:-${cache_lower}/sources/linux-kernel-worktree/5.4__vs680__arm64}"
uboot_parent="${UBOOT_SOURCE_PARENT:-${cache_lower}/sources/u-boot-worktree/u-boot-vs680}"
uboot_tree="${UBOOT_SOURCE_TREE:-}"

[[ -d "${linux_tree}" ]] || fail "找不到唯讀 Linux 來源樹：${linux_tree}"
git -C "${linux_tree}" cat-file -e "${linux_revision}^{commit}" 2>/dev/null ||
	fail "Linux 來源樹缺少固定提交：${linux_revision}"
if [[ -z "${uboot_tree}" ]]; then
	uboot_tree="$(find_git_tree_with_commit "${uboot_parent}" "${uboot_revision}")" ||
		fail "U-Boot 快取中找不到固定提交：${uboot_revision}"
fi
git -C "${uboot_tree}" cat-file -e "${uboot_revision}^{commit}" 2>/dev/null ||
	fail "U-Boot 來源樹缺少固定提交：${uboot_revision}"

expected_linux_dts_blob="71adf7c51edfe04d624291351034cbf08e0aaf69"
actual_linux_dts_blob="$(git -C "${linux_tree}" rev-parse "${linux_revision}:arch/arm64/boot/dts/synaptics/vs680-a0-bananapi-m6.dts")"
[[ "${actual_linux_dts_blob}" == "${expected_linux_dts_blob}" ]] ||
	fail "Linux M6 DTS 基底 blob 不符"
expected_uboot_dts_blob="8afc2e1e73e2bb29fbdf0790a620500cfa21fec2"
actual_uboot_dts_blob="$(git -C "${uboot_tree}" rev-parse "${uboot_revision}:arch/arm/dts/vs680-bananapi-m6.dts")"
[[ "${actual_uboot_dts_blob}" == "${expected_uboot_dts_blob}" ]] ||
	fail "U-Boot M6 DTS 基底 blob 不符"

git -C "${linux_tree}" apply --check \
	"${repo_dir}/patch/kernel/archive/bananapim6-legacy/001-identify-bananapi-m6-and-retain-vs680-compatibility.patch" ||
	fail "Linux M6 身分修補無法套用固定提交"
git -C "${uboot_tree}" apply --check \
	"${repo_dir}/patch/u-boot/legacy/u-boot-vs680-bananapim6/001-identify-bananapi-m6.patch" ||
	fail "U-Boot M6 身分修補無法套用固定提交"

tzk_path="${repo_dir}/packages/blobs/vs680/bpi-m6-tzk-4MB.bin"
[[ -f "${tzk_path}" ]] || fail "找不到受控 TZK 載荷：${tzk_path}"
expected_tzk_size="$(json_value opaque_boot_payloads packages/blobs/vs680/bpi-m6-tzk-4MB.bin size)"
expected_tzk_sha256="$(json_value opaque_boot_payloads packages/blobs/vs680/bpi-m6-tzk-4MB.bin sha256)"
[[ "$(stat -c %s "${tzk_path}")" == "${expected_tzk_size}" ]] ||
	fail "TZK 大小不符"
[[ "$(sha256sum "${tzk_path}" | cut -d' ' -f1)" == "${expected_tzk_sha256}" ]] ||
	fail "TZK SHA-256 不符"

expected_sm_size="$(json_value opaque_boot_payloads uboot:arch/arm/mach-synaptics/sm.bin size)"
expected_sm_sha256="$(json_value opaque_boot_payloads uboot:arch/arm/mach-synaptics/sm.bin sha256)"
actual_sm_size="$(git -C "${uboot_tree}" show "${uboot_revision}:arch/arm/mach-synaptics/sm.bin" | wc -c)"
actual_sm_sha256="$(git -C "${uboot_tree}" show "${uboot_revision}:arch/arm/mach-synaptics/sm.bin" | sha256sum | cut -d' ' -f1)"
[[ "${actual_sm_size}" == "${expected_sm_size}" ]] || fail "U-Boot sm.bin 大小不符"
[[ "${actual_sm_sha256}" == "${expected_sm_sha256}" ]] || fail "U-Boot sm.bin SHA-256 不符"

while IFS=$'\t' read -r relative expected; do
	[[ "$(sha256sum "${repo_dir}/${relative}" | cut -d' ' -f1)" == "${expected}" ]] ||
		fail "受控來源檔案雜湊不符：${relative}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    values = json.load(stream)["source_file_sha256"]
for path in sorted(values):
    print(f"{path}\t{values[path]}")
PY
)

shopt -s nullglob
optional_archives=("${repo_dir}"/packages/bsp/vs680/*.tgz)
(( ${#optional_archives[@]} == 0 )) ||
	fail "packages/bsp/vs680 含未納入契約的可選封裝檔"

if [[ "${verify_remote_refs}" == yes ]]; then
	while IFS=$'\t' read -r component source original_ref revision; do
		actual="$(git ls-remote "${source}" "${original_ref}" | cut -f1)"
		[[ "${actual}" == "${revision}" ]] ||
			fail "遠端 ${component} 分支不再指向已記錄提交：${actual:-不存在}"
	done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    values = json.load(stream)["source_commits"]
for component, value in sorted(values.items()):
    if value.get("original_ref"):
        print(
            component,
            value["source"],
            value["original_ref"],
            value["revision"],
            sep="\t",
        )
PY
)
fi

echo "BPI-M6 固定來源、修補基底與不透明載荷驗證通過。"
