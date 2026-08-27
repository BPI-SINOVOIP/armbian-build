#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-realtek-rtd1395-m4-legacy.json"
work_dir="${M4_COMPONENT_WORK_DIR:-${repo_dir}/.tmp/bananapi-realtek-m4-component}"
source_dir="${work_dir}/source"
stage_dir="${work_dir}/stage"
output_dir="${M4_COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy}"
source_repository="${M4_SOURCE_REPOSITORY:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache/sources/u-boot-worktree/u-boot-bananapim4/25f5b88ec4ba34029f964693dc34028b26e6c67c}"
source_revision="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["uboot_commit"])
PY
)"
source_date_epoch="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["source_date_epoch"])
PY
)"

for command in date git make patch python3 sha256sum stat strings tar; do
	command -v "${command}" >/dev/null || {
		echo "M4 元件建置缺少命令：${command}" >&2
		exit 1
	}
done

python3 "${repo_dir}/tools/check-bananapi-realtek-m4-source-policy.py" \
	"${validation_config}"

available_kib="$(df -Pk "${repo_dir}" | awk 'NR == 2 {print $4}')"
minimum_kib=$((50 * 1024 * 1024))
if ((available_kib < minimum_kib)); then
	echo "M4 元件建置拒絕：可用空間未達 50 GiB。" >&2
	exit 1
fi

[[ -d "${source_repository}" ]] || {
	echo "M4 固定來源物件庫不存在：${source_repository}" >&2
	exit 1
}
for path in "${work_dir}" "${output_dir}"; do
	[[ ! -e "${path}" ]] || {
		echo "M4 隔離路徑已存在；為避免覆寫證據，本次不重用：${path}" >&2
		exit 1
	}
done

mkdir -p "${work_dir}" "${stage_dir}/modules" "${output_dir}"
GIT_OPTIONAL_LOCKS=0 git clone --shared --no-checkout \
	"${source_repository}" "${source_dir}"
git -C "${source_dir}" checkout --detach "${source_revision}"
[[ "$(git -C "${source_dir}" rev-parse HEAD)" == "${source_revision}" ]] || {
	echo "M4 隔離來源提交不符。" >&2
	exit 1
}
M4_SOURCE_DIR="${source_dir}" python3 \
	"${repo_dir}/tools/check-bananapi-realtek-m4-source-policy.py" \
	"${validation_config}"

for patch_file in \
	"${repo_dir}/patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0001-host-tools-use-local-libfdt-headers.patch" \
	"${repo_dir}/patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0002-uenv-use-stable-root-label.patch" \
	"${repo_dir}/patch/u-boot/u-boot-realtek-rtd139x-bpi-legacy/0003-build-use-source-date-epoch.patch" \
	"${repo_dir}/patch/kernel/archive/realtek-rtd139x-bpi-4.9/0001-scripts-dtc-remove-duplicate-yylloc-definition.patch" \
	"${repo_dir}/patch/kernel/archive/realtek-rtd139x-bpi-4.9/0002-dts-identify-bananapi-m4.patch"; do
	patch --directory="${source_dir}" --strip=1 --fuzz=0 \
		--input="${patch_file}"
done

cross_compile="${source_dir}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_aarch64-linux-gnu/bin/aarch64-linux-gnu-"
read -r expected_toolchain_size expected_toolchain_sha256 < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    toolchain = json.load(stream)["build_toolchain"]
print(toolchain["size"], toolchain["sha256"])
PY
)
[[ "$(stat -c %s "${cross_compile}gcc")" == "${expected_toolchain_size}" ]] || {
	echo "M4 固定工具鏈 GCC 大小不符。" >&2
	exit 1
}
[[ "$(sha256sum "${cross_compile}gcc" | cut -d' ' -f1)" == \
	"${expected_toolchain_sha256}" ]] || {
	echo "M4 固定工具鏈 GCC 雜湊不符。" >&2
	exit 1
}

jobs="${M4_BUILD_JOBS:-$(nproc)}"
if ((jobs > 8)); then
	jobs=8
fi
export SOURCE_DATE_EPOCH="${source_date_epoch}"
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP="$(date -u -d "@${source_date_epoch}" '+%Y-%m-%d %H:%M:%S UTC')"
export KBUILD_BUILD_USER="armbian"
export KBUILD_BUILD_HOST="bananapi-m4"

(
	cd "${source_dir}"
	./configure BPI-M4-720P
	make -C u-boot-rtk rtd1395_bananapi_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C u-boot-rtk -j"${jobs}" all \
		"CROSS_COMPILE=${cross_compile}" BUILD_BOOTCODE_ONLY=true
) 2>&1 | tee "${output_dir}/u-boot-build-first.log"

uboot_first_sha256="$(sha256sum "${source_dir}/u-boot-rtk/u-boot.bin" | cut -d' ' -f1)"
make -C "${source_dir}/u-boot-rtk" distclean \
	"CROSS_COMPILE=${cross_compile}" >/dev/null
(
	cd "${source_dir}"
	make -C u-boot-rtk rtd1395_bananapi_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C u-boot-rtk -j"${jobs}" all \
		"CROSS_COMPILE=${cross_compile}" BUILD_BOOTCODE_ONLY=true
) 2>&1 | tee "${output_dir}/u-boot-build-second.log"
uboot_second_sha256="$(sha256sum "${source_dir}/u-boot-rtk/u-boot.bin" | cut -d' ' -f1)"
[[ "${uboot_first_sha256}" == "${uboot_second_sha256}" ]] || {
	echo "M4 U-Boot 在固定時間與主機資訊下無法重現：${uboot_first_sha256} != ${uboot_second_sha256}" >&2
	exit 1
}

install -m 0644 "${repo_dir}/config/kernel/linux-realtek-rtd139x-bpi-legacy.config" \
	"${source_dir}/linux-rtk/.config"
(
	cd "${source_dir}"
	make -C linux-rtk ARCH=arm64 "CROSS_COMPILE=${cross_compile}" olddefconfig
	make -C linux-rtk -j"${jobs}" ARCH=arm64 \
		"CROSS_COMPILE=${cross_compile}" UIMAGE_LOADADDR=0x40008000 \
		Image dtbs modules
	make -C linux-rtk -j"${jobs}" ARCH=arm64 \
		"CROSS_COMPILE=${cross_compile}" \
		"INSTALL_MOD_PATH=${stage_dir}/modules" modules_install
) 2>&1 | tee "${output_dir}/linux-build.log"

install -m 0644 "${source_dir}/u-boot-rtk/u-boot.bin" \
	"${output_dir}/u-boot.bin"
install -m 0644 "${source_dir}/u-boot-rtk/.u-boot.cmd" \
	"${output_dir}/u-boot-link-command.txt"
install -m 0644 \
	"${source_dir}/rtk-pack/rtk/bpi-m4/configs/default/linux/uEnv.txt" \
	"${output_dir}/uEnv.txt"
install -m 0644 \
	"${source_dir}/rtk-pack/rtk/bpi-m4/configs/default/linux/bluecore.audio" \
	"${output_dir}/bluecore.audio"
install -m 0644 "${source_dir}/linux-rtk/arch/arm64/boot/Image" \
	"${output_dir}/Image"
for dtb in rtd-1395-bananapi-m4-1GB.dtb rtd-1395-bananapi-m4-2GB.dtb; do
	install -m 0644 \
		"${source_dir}/linux-rtk/arch/arm64/boot/dts/realtek/rtd139x/${dtb}" \
		"${output_dir}/${dtb}"
done
install -m 0644 "${source_dir}/linux-rtk/.config" \
	"${output_dir}/linux.config"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 \
	--numeric-owner -cJf "${output_dir}/linux-modules.tar.xz" \
	-C "${stage_dir}/modules" lib/modules

source_assets_manifest="${output_dir}/SOURCE_ASSETS.tsv"
printf '路徑\t大小\tSHA-256\n' >"${source_assets_manifest}"
python3 - "${validation_config}" "${source_dir}" >>"${source_assets_manifest}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
source = Path(sys.argv[2])
paths = [config["linux_license_path"], config["uboot_license_path"]]
paths.extend(config["conditional_unlinked_prebuilt_assets"])
paths.extend(config["linked_unrebuilt_source_assets"])
paths.extend(config["runtime_prebuilt_assets"])
paths.extend(config["excluded_source_assets"])
paths.extend([config["build_toolchain"]["path"], config["build_toolchain"]["manifest_path"]])
for relative in paths:
    path = source / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{relative}\t{path.stat().st_size}\t{digest}")
PY

prebuilt_manifest="${output_dir}/UBOOT_PREBUILT_INPUT_EVIDENCE.tsv"
printf '路徑\t分類\tMakefile命中\t內容嵌入吻合\t實際連結命令命中\t連結映射命中\t本次重建\t進入候選\n' \
	>"${prebuilt_manifest}"
while IFS= read -r asset; do
	asset_name="$(basename "${asset}")"
	asset_stem="${asset_name%%.a*}"
	grep -Fq "${asset_stem}" "${source_dir}/u-boot-rtk/Makefile" || {
		echo "M4 U-Boot Makefile 缺少條件式預建資產：${asset}" >&2
		exit 1
	}
	if grep -Fq "${asset_name}" "${source_dir}/u-boot-rtk/.u-boot.cmd" || \
		grep -Fq "${asset_name}" "${source_dir}/u-boot-rtk/u-boot.map"; then
		echo "M4 U-Boot 未連結資產意外進入實際連結證據：${asset}" >&2
		exit 1
	fi
	printf '%s\t條件式未連結預建資產\ttrue\t不適用\tfalse\tfalse\tfalse\tfalse\n' "${asset}"
done < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    for path in json.load(stream)["conditional_unlinked_prebuilt_assets"]:
        print(path)
PY
) >>"${prebuilt_manifest}"

grep -Fq 'image/rtd1395/src/app/libbootload.o' \
	"${source_dir}/u-boot-rtk/.u-boot.cmd" || {
	echo "M4 U-Boot 實際連結命令缺少 libbootload.o。" >&2
	exit 1
}
while IFS=$'\t' read -r asset section source_file; do
	[[ -f "${source_dir}/${source_file}" ]] || {
		echo "M4 U-Boot 啟動段缺少可追溯來源：${source_file}" >&2
		exit 1
	}
	grep -Fq "$(basename "${source_file}" .S)" \
		"${source_dir}/u-boot-rtk/image/rtd1395/src/Makefile" || {
		echo "M4 U-Boot 啟動段建置規則缺少來源：${source_file}" >&2
		exit 1
	}
	extracted="${stage_dir}/$(basename "${asset}").extracted"
	"${cross_compile}objcopy" --dump-section \
		"${section}=${extracted}" \
		"${source_dir}/u-boot-rtk/image/rtd1395/src/app/libbootload.o"
	[[ "$(sha256sum "${source_dir}/${asset}" | cut -d' ' -f1)" == \
		"$(sha256sum "${extracted}" | cut -d' ' -f1)" ]] || {
		echo "M4 U-Boot 啟動段與 libbootload.o 內容不符：${asset}" >&2
		exit 1
	}
	grep -Fq "${section}" "${source_dir}/u-boot-rtk/u-boot.map" || {
		echo "M4 U-Boot 連結映射缺少啟動段：${section}" >&2
		exit 1
	}
	printf '%s\t已嵌入但未重建來源資產\ttrue\ttrue\ttrue\ttrue\tfalse\ttrue\n' "${asset}"
done >>"${prebuilt_manifest}" <<'EOF'
u-boot-rtk/image/rtd1395/a_entry.img	.a_entry	u-boot-rtk/image/rtd1395/src/a_entry.S
u-boot-rtk/image/rtd1395/exc_dispatch.img	.exc_dispatch	u-boot-rtk/image/rtd1395/src/exc_dispatch.S
u-boot-rtk/image/rtd1395/exc_redirect.img	.exc_redirect	u-boot-rtk/image/rtd1395/src/exc_redirect.S
u-boot-rtk/image/rtd1395/isr_video.img	.isrvideoimg	u-boot-rtk/image/rtd1395/src/isr_video.S
u-boot-rtk/image/rtd1395/ros_bootvector.img	.rosbootvectorimg	u-boot-rtk/image/rtd1395/src/ros_bootvector.S
u-boot-rtk/image/rtd1395/v_entry.img	.v_entry	u-boot-rtk/image/rtd1395/src/v_entry.S
EOF

component_manifest="${output_dir}/COMPONENTS.tsv"
printf '產物\t大小\tSHA-256\n' >"${component_manifest}"
for name in u-boot.bin uEnv.txt bluecore.audio Image \
	rtd-1395-bananapi-m4-1GB.dtb rtd-1395-bananapi-m4-2GB.dtb \
	linux.config linux-modules.tar.xz u-boot-link-command.txt; do
	path="${output_dir}/${name}"
	printf '%s\t%s\t%s\n' "${name}" "$(stat -c %s "${path}")" \
		"$(sha256sum "${path}" | cut -d' ' -f1)" >>"${component_manifest}"
done

work_size_kib="$(du -sk "${work_dir}" | awk '{print $1}')"
maximum_kib=$((10 * 1024 * 1024))
if ((work_size_kib > maximum_kib)); then
	echo "M4 元件工作目錄超過 10 GiB 上限。" >&2
	exit 1
fi
uboot_warning_count="$(awk '/warning:|Warning \(/ { count++ } END { print count + 0 }' "${output_dir}"/u-boot-build-*.log)"
linux_warning_count="$(awk '/warning:|Warning \(/ { count++ } END { print count + 0 }' "${output_dir}/linux-build.log")"

cat >"${output_dir}/COMPONENT_BUILD_STATUS.json" <<EOF
{
  "status": "complete",
  "source_revision": "${source_revision}",
  "source_date_epoch": ${source_date_epoch},
  "full_rootfs_image_built": false,
  "uboot_rebuild_hash_match": true,
  "uboot_rebuild_sha256": "${uboot_second_sha256}",
  "toolchain_gcc_sha256": "${expected_toolchain_sha256}",
  "work_size_kib": ${work_size_kib},
  "uboot_warning_count": ${uboot_warning_count},
  "linux_warning_count": ${linux_warning_count},
  "linked_unrebuilt_source_asset_count": 6,
  "conditional_unlinked_prebuilt_asset_count": 4,
  "output_directory": "${output_dir}",
  "completed_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "M4 U-Boot、Linux、兩個 DTB 與 modules 元件建置完成：${output_dir}"
