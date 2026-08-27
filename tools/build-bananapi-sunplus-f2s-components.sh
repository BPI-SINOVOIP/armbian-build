#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2s-legacy.json"
work_root="${F2S_COMPONENT_WORK_ROOT:-${repo_dir}/.tmp/bananapi-sunplus-f2s-component}"
source_dir="${work_root}/source"
stage_dir="${work_root}/stage"
output_dir="${work_root}/output"
source_url="https://github.com/BPI-SINOVOIP/BPI-F2S-bsp.git"
source_revision="3eee97bd8fb7582c2d9942a533647c3d78222bb5"
source_date_epoch="1609074838"
reference_repo="${F2S_REFERENCE_REPO:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache/git-bare/kernel/.git}"
jobs="${F2S_BUILD_JOBS:-$(nproc)}"

for command in cut date fdtget git gzip install make nproc python3 sha256sum stat tar wc; do
	command -v "${command}" >/dev/null || {
		echo "F2S 元件建置缺少命令：${command}" >&2
		exit 1
	}
done
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || {
	echo "F2S_BUILD_JOBS 必須是正整數。" >&2
	exit 1
}

python3 "${repo_dir}/tools/check-bananapi-sunplus-f2s-source-policy.py" \
	"${validation_config}"

mkdir -p "${work_root}" "${stage_dir}" "${output_dir}"
if [[ ! -d "${source_dir}/.git" ]]; then
	[[ ! -e "${source_dir}" ]] || {
		echo "F2S 元件來源路徑存在但不是 Git 工作樹：${source_dir}" >&2
		exit 1
	}
	clone_args=(clone --no-checkout)
	if [[ -d "${reference_repo}" ]]; then
		clone_args+=(--reference-if-able "${reference_repo}")
	fi
	git "${clone_args[@]}" "${source_url}" "${source_dir}"
fi

actual_origin="$(git -C "${source_dir}" remote get-url origin)"
[[ "${actual_origin}" == "${source_url}" ]] || {
	echo "F2S 元件來源 origin 不符：${actual_origin}" >&2
	exit 1
}
git -C "${source_dir}" fetch --no-tags origin "${source_revision}"
git -C "${source_dir}" reset --hard "${source_revision}"
git -C "${source_dir}" clean -fdx

if [[ -n "$(find "${stage_dir}" -mindepth 1 -print -quit)" ]]; then
	find "${stage_dir}" -mindepth 1 -depth -delete
fi
if [[ -n "$(find "${output_dir}" -mindepth 1 -print -quit)" ]]; then
	find "${output_dir}" -mindepth 1 -depth -delete
fi

mapfile -t uboot_patches < <(find \
	"${repo_dir}/patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy" \
	-maxdepth 1 -type f -name '*.patch' -print | sort)
mapfile -t kernel_patches < <(find \
	"${repo_dir}/patch/kernel/archive/sunplus-sp7021-bpi-5.4" \
	-maxdepth 1 -type f -name '*.patch' -print | sort)
git -C "${source_dir}" apply --check "${uboot_patches[@]}" "${kernel_patches[@]}"
git -C "${source_dir}" apply "${uboot_patches[@]}" "${kernel_patches[@]}"

asset_manifest="${output_dir}/SOURCE_ASSETS.tsv"
printf '種類\t路徑\t大小\tSHA-256\t解壓大小\t解壓 SHA-256\t授權判定\n' \
	>"${asset_manifest}"
while IFS=$'\t' read -r path expected_size expected_sha256 \
	expected_uncompressed_size expected_uncompressed_sha256; do
	asset="${source_dir}/${path}"
	[[ -f "${asset}" ]] || {
		echo "缺少 F2S 預建啟動資產：${path}" >&2
		exit 1
	}
	[[ "$(stat -c %s "${asset}")" == "${expected_size}" ]] || {
		echo "F2S 預建啟動資產大小不符：${path}" >&2
		exit 1
	}
	[[ "$(sha256sum "${asset}" | cut -d' ' -f1)" == "${expected_sha256}" ]] || {
		echo "F2S 預建啟動資產雜湊不符：${path}" >&2
		exit 1
	}
	if [[ "${expected_uncompressed_size}" != "-" ]]; then
		[[ "${path}" == *.gz ]] || {
			echo "F2S 解壓雜湊只允許用於 gzip 資產：${path}" >&2
			exit 1
		}
		[[ "$(gzip -cd -- "${asset}" | wc -c)" == \
			"${expected_uncompressed_size}" ]] || {
			echo "F2S 預建啟動資產解壓大小不符：${path}" >&2
			exit 1
		}
		[[ "$(gzip -cd -- "${asset}" | sha256sum | cut -d' ' -f1)" == \
			"${expected_uncompressed_sha256}" ]] || {
			echo "F2S 預建啟動資產解壓雜湊不符：${path}" >&2
			exit 1
		}
	fi
	printf '預建啟動資產\t%s\t%s\t%s\t%s\t%s\t未確認再散布授權\n' \
		"${path}" "${expected_size}" "${expected_sha256}" \
		"${expected_uncompressed_size}" "${expected_uncompressed_sha256}" \
		>>"${asset_manifest}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    assets = json.load(stream)["source_assets"]
for path in sorted(assets):
    asset = assets[path]
    print(
        f"{path}\t{asset['size']}\t{asset['sha256']}\t"
        f"{asset.get('uncompressed_size', '-')}\t"
        f"{asset.get('uncompressed_sha256', '-')}"
    )
PY
)

while IFS=$'\t' read -r kind path expected_sha256; do
	license_file="${source_dir}/${path}"
	[[ -f "${license_file}" ]] || {
		echo "F2S 缺少授權證據：${path}" >&2
		exit 1
	}
	[[ "$(sha256sum "${license_file}" | cut -d' ' -f1)" == \
		"${expected_sha256}" ]] || {
		echo "F2S 授權證據雜湊不符：${path}" >&2
		exit 1
	}
	printf '%s\t%s\t%s\t%s\t-\t-\t只適用對應原始碼子樹\n' \
		"${kind}" "${path}" "$(stat -c %s "${license_file}")" \
		"${expected_sha256}" >>"${asset_manifest}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
print(
    "Linux 授權檔\t"
    f"{config['linux_license_path']}\t{config['linux_license_sha256']}"
)
print(
    "U-Boot 授權檔\t"
    f"{config['uboot_license_path']}\t{config['uboot_license_sha256']}"
)
PY
)

export SOURCE_DATE_EPOCH="${source_date_epoch}"
export KBUILD_BUILD_TIMESTAMP="@${source_date_epoch}"
export KBUILD_BUILD_USER="bananapi"
export KBUILD_BUILD_HOST="f2s-candidate"
cross_compile="${source_dir}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf-"
[[ -x "${cross_compile}gcc" ]] || {
	echo "找不到 F2S BSP 固定工具鏈：${cross_compile}gcc" >&2
	exit 1
}
read -r expected_toolchain_size expected_toolchain_sha256 < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    toolchain = json.load(stream)["component_build_evidence"]["toolchain"]
print(toolchain["gcc_size"], toolchain["gcc_sha256"])
PY
)
[[ "$(stat -c %s "${cross_compile}gcc")" == "${expected_toolchain_size}" ]] || {
	echo "F2S 固定工具鏈 GCC 大小不符。" >&2
	exit 1
}
[[ "$(sha256sum "${cross_compile}gcc" | cut -d' ' -f1)" == \
	"${expected_toolchain_sha256}" ]] || {
	echo "F2S 固定工具鏈 GCC 雜湊不符。" >&2
	exit 1
}

(
	cd "${source_dir}"
	./configure bpi-f2s
	make -C u-boot-sp sp7021_bpi_f2s_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C u-boot-sp -j"${jobs}" all \
		"CROSS_COMPILE=${cross_compile}"
) 2>&1 | tee "${output_dir}/u-boot-build.log"

uboot_first_sha256="$(sha256sum "${source_dir}/u-boot-sp/u-boot.img" | cut -d' ' -f1)"
touch "${source_dir}/u-boot-sp/u-boot.bin"
make -C "${source_dir}/u-boot-sp" -j"${jobs}" all \
	"CROSS_COMPILE=${cross_compile}" 2>&1 | tee -a "${output_dir}/u-boot-build.log"
uboot_second_sha256="$(sha256sum "${source_dir}/u-boot-sp/u-boot.img" | cut -d' ' -f1)"
[[ "${uboot_first_sha256}" == "${uboot_second_sha256}" ]] || {
	echo "F2S U-Boot 映像在改變輸入檔時間後無法重現。" >&2
	exit 1
}

install -m 0644 "${repo_dir}/config/kernel/linux-sunplus-sp7021-bpi-legacy.config" \
	"${source_dir}/linux-sp/.config"
for option in THERMAL THERMAL_OF SUNPLUS_SP7021_THERMAL WATCHDOG \
	WATCHDOG_CORE SUNPLUS_WATCHDOG USB_CONFIGFS USB_CONFIGFS_MASS_STORAGE; do
	"${source_dir}/linux-sp/scripts/config" --file \
		"${source_dir}/linux-sp/.config" --enable "${option}"
done
(
	cd "${source_dir}"
	make -C linux-sp ARCH=arm "CROSS_COMPILE=${cross_compile}" olddefconfig
	make -C linux-sp -j"${jobs}" ARCH=arm "CROSS_COMPILE=${cross_compile}" \
		uImage dtbs modules
	make -C linux-sp -j"${jobs}" ARCH=arm "CROSS_COMPILE=${cross_compile}" \
		"INSTALL_MOD_PATH=${stage_dir}/modules" modules_install
) 2>&1 | tee "${output_dir}/linux-build.log"

install -m 0644 "${source_dir}/u-boot-sp/u-boot.img" "${output_dir}/u-boot.img"
install -m 0644 "${source_dir}/u-boot-sp/u-boot.bin" "${output_dir}/u-boot.bin"
install -m 0644 "${source_dir}/u-boot-sp/u-boot.dtb" "${output_dir}/u-boot.dtb"
install -m 0644 "${source_dir}/linux-sp/arch/arm/boot/uImage" "${output_dir}/uImage"
install -m 0644 "${source_dir}/linux-sp/arch/arm/boot/zImage" "${output_dir}/zImage"
install -m 0644 \
	"${source_dir}/linux-sp/arch/arm/boot/dts/sp7021-bpi-f2s.dtb" \
	"${output_dir}/sp7021-bpi-f2s.dtb"
install -m 0644 "${source_dir}/linux-sp/.config" "${output_dir}/linux.config"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 \
	--numeric-owner -cJf "${output_dir}/linux-modules.tar.xz" \
	-C "${stage_dir}/modules" lib/modules

component_manifest="${output_dir}/COMPONENTS.tsv"
printf '產物\t大小\tSHA-256\n' >"${component_manifest}"
for name in u-boot.img u-boot.bin u-boot.dtb uImage zImage \
	sp7021-bpi-f2s.dtb linux.config linux-modules.tar.xz; do
	path="${output_dir}/${name}"
	printf '%s\t%s\t%s\n' "${name}" "$(stat -c %s "${path}")" \
		"$(sha256sum "${path}" | cut -d' ' -f1)" >>"${component_manifest}"
done

status_file="${output_dir}/COMPONENT_BUILD_STATUS.json"
cat >"${status_file}" <<EOF
{
  "status": "complete",
  "source_revision": "${source_revision}",
  "source_date_epoch": ${source_date_epoch},
  "full_rootfs_image_built": false,
  "uboot_rebuild_hash_match": true,
  "toolchain_gcc_sha256": "${expected_toolchain_sha256}",
  "output_directory": "${output_dir}",
  "completed_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "F2S U-Boot、Linux、DTB 與 modules 元件建置完成：${output_dir}"
