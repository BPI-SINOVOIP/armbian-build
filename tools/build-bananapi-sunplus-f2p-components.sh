#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
component_root="${F2P_COMPONENT_ROOT:-${repo_dir}/.tmp/bananapi-sunplus-f2p-component}"
source_dir="${component_root}/source"
manifest="${component_root}/COMPONENT_BUILD_MANIFEST.json"
build_log="${component_root}/component-build.log"
clone_source="${F2P_BSP_GIT_SOURCE:-https://github.com/BPI-SINOVOIP/BPI-F2S-bsp.git}"
jobs="${F2P_JOBS:-$(nproc)}"

for command in date fdtget git jq make nproc sha256sum stat tee; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "F2P 元件建置拒絕：$*" >&2
	exit 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證契約"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || fail "F2P_JOBS 必須是正整數"
[[ ! -e "${source_dir}" ]] || fail "目標來源目錄已存在；請指定新的 F2P_COMPONENT_ROOT"

bsp_commit="$(jq -r '.source_commits.bsp.revision' "${validation_config}")"
mkdir -p "${component_root}"
exec > >(tee "${build_log}") 2>&1

echo "建立 F2P 固定來源元件工作目錄：${component_root}"
git clone --no-checkout "${clone_source}" "${source_dir}"
git -C "${source_dir}" checkout --detach "${bsp_commit}"
actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
[[ "${actual_commit}" == "${bsp_commit}" ]] || fail "BSP 提交不符"
[[ -z "$(git -C "${source_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "初始來源目錄不乾淨"

git -C "${source_dir}" apply \
	"${repo_dir}/patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy/0001-scripts-dtc-remove-duplicate-yylloc-definition.patch"

commit_epoch="$(git -C "${source_dir}" show -s --format=%ct HEAD)"
export SOURCE_DATE_EPOCH="${commit_epoch}"
KBUILD_BUILD_TIMESTAMP="$(date -u -d "@${commit_epoch}" '+%Y-%m-%d %H:%M:%S UTC')"
export KBUILD_BUILD_TIMESTAMP
export KBUILD_BUILD_USER="bananapi"
export KBUILD_BUILD_HOST="armbian"

cross_compile="${source_dir}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf-"
[[ -x "${cross_compile}gcc" ]] || fail "找不到 BSP 隨附交叉編譯器"

(
	cd "${source_dir}"
	./configure bpi-f2p
	make -C u-boot-sp sp7021_bpi_f2p_defconfig "CROSS_COMPILE=${cross_compile}"
	make -C u-boot-sp -j"${jobs}" all "CROSS_COMPILE=${cross_compile}"
	make -C linux-sp ARCH=arm sp7021_chipC_bpi-f2p_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C linux-sp ARCH=arm -j"${jobs}" uImage dtbs modules \
		"CROSS_COMPILE=${cross_compile}"
)

declare -A artifacts=(
	[uboot_image]="u-boot-sp/u-boot.img"
	[uboot_dtb]="u-boot-sp/arch/arm/dts/sp7021-bpi-f2p.dtb"
	[linux_uimage]="linux-sp/arch/arm/boot/uImage"
	[linux_dtb]="linux-sp/arch/arm/boot/dts/sp7021-bpi-f2p.dtb"
	[ispboot]="sp-pack/sp7021/common/bin/ISPBOOOT.BIN"
)
for name in "${!artifacts[@]}"; do
	[[ -s "${source_dir}/${artifacts[${name}]}" ]] || fail "缺少元件產物：${name}"
done

model="$(fdtget -t s "${source_dir}/${artifacts[linux_dtb]}" / model)"
compatible="$(fdtget -t s "${source_dir}/${artifacts[linux_dtb]}" / compatible)"
[[ "${model}" == "SP7021/CA7/BPI-F2P" ]] || fail "Linux DTB 板級身分不符"
[[ "${compatible}" == "sunplus,sp7021-achip" ]] || fail "Linux DTB compatible 不符"

module_count="$(find "${source_dir}/linux-sp" -type f -name '*.ko' | wc -l)"
(( module_count > 0 )) || fail "沒有產生 Linux 模組"

artifact_json='{}'
for name in "${!artifacts[@]}"; do
	relative="${artifacts[${name}]}"
	path="${source_dir}/${relative}"
	artifact_json="$(jq \
		--arg name "${name}" --arg path "${relative}" \
		--arg sha256 "$(sha256sum "${path}" | cut -d' ' -f1)" \
		--argjson size "$(stat -c %s "${path}")" \
		'. + {($name): {path: $path, size: $size, sha256: $sha256}}' \
		<<<"${artifact_json}")"
done

jq -n \
	--arg source "https://github.com/BPI-SINOVOIP/BPI-F2S-bsp.git" \
	--arg revision "${actual_commit}" \
	--arg toolchain "$("${cross_compile}gcc" -dumpfullversion)" \
	--arg model "${model}" --arg compatible "${compatible}" \
	--argjson module_count "${module_count}" \
	--argjson artifacts "${artifact_json}" \
	'{
		schema_version: 1,
		status: "complete",
		scope: "component-only",
		source: $source,
		revision: $revision,
		toolchain_version: $toolchain,
		model: $model,
		compatible: $compatible,
		module_count: $module_count,
		artifacts: $artifacts,
		rootfs_image_built: false,
		hardware_tested: false
	}' >"${manifest}.partial"
mv "${manifest}.partial" "${manifest}"

echo "F2P 元件建置完成：${manifest}"
