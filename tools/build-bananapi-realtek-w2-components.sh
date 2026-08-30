#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-realtek-rtd1296-w2-legacy.json"
work_dir="${W2_COMPONENT_WORK_DIR:-${repo_dir}/.tmp/bananapi-realtek-w2-component}"
source_dir="${work_dir}/source"
output_dir="${W2_COMPONENT_OUTPUT_DIR:-${repo_dir}/output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy}"
stage_dir="${work_dir}/stage"
source_repository="${W2_SOURCE_REPOSITORY:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache/sources/u-boot-worktree/u-boot-bananapiw2/6e6aefc35dc50b1b8231cdb03a995d088f29eb21}"
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

for command in date git make patch python3 sha256sum stat tar; do
	command -v "${command}" >/dev/null || {
		echo "W2 元件建置缺少命令：${command}" >&2
		exit 1
	}
done

python3 "${repo_dir}/tools/check-bananapi-realtek-w2-source-policy.py" \
	"${validation_config}"

available_kib="$(df -Pk "${repo_dir}" | awk 'NR == 2 {print $4}')"
minimum_kib=$((50 * 1024 * 1024))
if ((available_kib < minimum_kib)); then
	echo "W2 元件建置拒絕：可用空間未達 50 GiB。" >&2
	exit 1
fi

[[ -d "${source_repository}" ]] || {
	echo "W2 固定來源工作樹不存在：${source_repository}" >&2
	exit 1
}
[[ ! -e "${source_dir}" ]] || {
	echo "W2 隔離來源目錄已存在；為避免覆寫證據，本次不重用：${source_dir}" >&2
	exit 1
}
[[ ! -e "${output_dir}" ]] || {
	echo "W2 元件證據目錄已存在；為避免覆寫，本次拒絕建置：${output_dir}" >&2
	exit 1
}

mkdir -p "${work_dir}" "${output_dir}" "${stage_dir}/modules"
GIT_OPTIONAL_LOCKS=0 git clone --shared --no-checkout \
	"${source_repository}" "${source_dir}"
git -C "${source_dir}" checkout --detach "${source_revision}"
[[ "$(git -C "${source_dir}" rev-parse HEAD)" == "${source_revision}" ]] || {
	echo "W2 隔離來源提交不符。" >&2
	exit 1
}

for patch_file in \
	"${repo_dir}/patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy/0001-host-tools-use-local-libfdt-headers.patch" \
	"${repo_dir}/patch/u-boot/u-boot-realtek-rtd129x-bpi-legacy/0003-build-use-source-date-epoch.patch" \
	"${repo_dir}/patch/kernel/archive/realtek-rtd129x-bpi-4.9/0001-scripts-dtc-remove-duplicate-yylloc-definition.patch" \
	"${repo_dir}/patch/kernel/archive/realtek-rtd129x-bpi-4.9/0002-dts-identify-bananapi-w2.patch"; do
	patch --directory="${source_dir}" --strip=1 --fuzz=0 \
		--input="${patch_file}"
done

vendor_uenv="${source_dir}/rtk-pack/rtk/bpi-w2/configs/default/linux/uEnv.txt"
sed -i -E 's|^root=.*|root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait|' "${vendor_uenv}"
grep -Fqx 'root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait' "${vendor_uenv}" || {
	echo "W2 根標籤正規化失敗。" >&2
	exit 1
}

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
	echo "W2 固定工具鏈 GCC 大小不符。" >&2
	exit 1
}
[[ "$(sha256sum "${cross_compile}gcc" | cut -d' ' -f1)" == \
	"${expected_toolchain_sha256}" ]] || {
	echo "W2 固定工具鏈 GCC 雜湊不符。" >&2
	exit 1
}

jobs="${W2_BUILD_JOBS:-$(nproc)}"
if ((jobs > 8)); then
	jobs=8
fi
export SOURCE_DATE_EPOCH="${source_date_epoch}"
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP="$(date -u -d "@${source_date_epoch}" '+%Y-%m-%d %H:%M:%S UTC')"
export KBUILD_BUILD_USER="armbian"
export KBUILD_BUILD_HOST="bananapi-w2"

(
	cd "${source_dir}"
	./configure BPI-W2-720P
	make -C u-boot-rtk rtd1296_sd_bananapi_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C u-boot-rtk -j"${jobs}" all \
		"CROSS_COMPILE=${cross_compile}" BUILD_BOOTCODE_ONLY=true
) 2>&1 | tee "${output_dir}/u-boot-build-first.log"

uboot_first_sha256="$(sha256sum "${source_dir}/u-boot-rtk/u-boot.bin" | cut -d' ' -f1)"
make -C "${source_dir}/u-boot-rtk" distclean \
	"CROSS_COMPILE=${cross_compile}" >/dev/null
(
	cd "${source_dir}"
	make -C u-boot-rtk rtd1296_sd_bananapi_defconfig \
		"CROSS_COMPILE=${cross_compile}"
	make -C u-boot-rtk -j"${jobs}" all \
		"CROSS_COMPILE=${cross_compile}" BUILD_BOOTCODE_ONLY=true
) 2>&1 | tee "${output_dir}/u-boot-build-second.log"
uboot_second_sha256="$(sha256sum "${source_dir}/u-boot-rtk/u-boot.bin" | cut -d' ' -f1)"
[[ "${uboot_first_sha256}" == "${uboot_second_sha256}" ]] || {
	echo "W2 U-Boot 在固定時間與主機資訊下無法重現：${uboot_first_sha256} != ${uboot_second_sha256}" >&2
	exit 1
}

install -m 0644 "${repo_dir}/config/kernel/linux-realtek-rtd129x-bpi-legacy.config" \
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
install -m 0644 \
	"${source_dir}/rtk-pack/rtk/bpi-w2/configs/default/linux/uEnv.txt" \
	"${output_dir}/uEnv.txt"
install -m 0644 \
	"${source_dir}/rtk-pack/rtk/bpi-w2/configs/default/linux/bluecore.audio" \
	"${output_dir}/bluecore.audio"
install -m 0644 "${source_dir}/linux-rtk/arch/arm64/boot/Image" \
	"${output_dir}/Image"
install -m 0644 \
	"${source_dir}/linux-rtk/arch/arm64/boot/dts/realtek/rtd129x/rtd-1296-bananapi-w2-2GB.dtb" \
	"${output_dir}/rtd-1296-bananapi-w2-2GB.dtb"
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
paths.extend(config["linked_prebuilt_assets"])
paths.extend(config["runtime_prebuilt_assets"])
paths.extend(config["excluded_source_assets"])
paths.extend(
    [
        config["build_toolchain"]["path"],
        config["build_toolchain"]["manifest_path"],
    ]
)
for relative in paths:
    path = source / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{relative}\t{path.stat().st_size}\t{digest}")
PY

linked_manifest="${output_dir}/UBOOT_LINKED_PREBUILT_ASSETS.tsv"
printf '路徑\t連結映射命中\n' >"${linked_manifest}"
while IFS= read -r asset; do
	map_asset="${asset#u-boot-rtk/}"
	grep -Fq "LOAD ${map_asset}" "${source_dir}/u-boot-rtk/u-boot.map" || {
		echo "W2 U-Boot 連結映射缺少預建資產：${asset}" >&2
		exit 1
	}
	printf '%s\ttrue\n' "${asset}"
done < <(
	python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    for path in json.load(stream)["linked_prebuilt_assets"]:
        print(path)
PY
) >>"${linked_manifest}"

component_manifest="${output_dir}/COMPONENTS.tsv"
printf '產物\t大小\tSHA-256\n' >"${component_manifest}"
for name in u-boot.bin uEnv.txt bluecore.audio Image \
	rtd-1296-bananapi-w2-2GB.dtb linux.config linux-modules.tar.xz; do
	path="${output_dir}/${name}"
	printf '%s\t%s\t%s\n' "${name}" "$(stat -c %s "${path}")" \
		"$(sha256sum "${path}" | cut -d' ' -f1)" >>"${component_manifest}"
done

work_size_kib="$(du -sk "${work_dir}" | awk '{print $1}')"
maximum_kib=$((10 * 1024 * 1024))
if ((work_size_kib > maximum_kib)); then
	echo "W2 元件工作目錄超過 10 GiB 上限。" >&2
	exit 1
fi

cat >"${output_dir}/COMPONENT_BUILD_STATUS.json" <<EOF
{
  "status": "complete",
  "source_revision": "${source_revision}",
  "source_date_epoch": ${source_date_epoch},
  "full_rootfs_image_built": false,
  "uboot_rebuild_hash_match": true,
  "toolchain_gcc_sha256": "${expected_toolchain_sha256}",
  "work_size_kib": ${work_size_kib},
  "output_directory": "${output_dir}",
  "completed_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "W2 U-Boot、Linux、DTB 與 modules 元件建置完成：${output_dir}"
