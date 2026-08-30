#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json}"
sdk_root="${SDK_ROOT:-/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0}"
component_root="${COMPONENT_ROOT:-${repo_dir}/.tmp/bananapi-sm10-components}"
jobs="${JOBS:-$(nproc)}"
source_verifier="${repo_dir}/tools/verify-bananapi-spacemit-k3-sm10-sources.sh"
container_image="${COMPONENT_CONTAINER_IMAGE:-harbor.spacemit.com/bianbu/k3-bsp-builder:latest}"
containerized="${SM10_COMPONENT_CONTAINERIZED:-0}"
cross_compile="${CROSS_COMPILE:-${sdk_root}/output/k3/host/bin/riscv64-unknown-linux-gnu-}"

fail() {
	echo "SM10 元件建置失敗：$*" >&2
	exit 1
}

if [[ "${containerized}" != "1" ]]; then
	command -v docker >/dev/null || fail "缺少必要命令：docker"
	[[ -d "${sdk_root}" ]] || fail "找不到 SDK：${sdk_root}"
	[[ ! -e "${component_root}" ]] ||
		fail "輸出目錄已存在；請指定新的 COMPONENT_ROOT：${component_root}"
	source_evidence_root="${component_root}-source-evidence"
	[[ ! -e "${source_evidence_root}" ]] ||
		fail "來源證據目錄已存在：${source_evidence_root}"
	SOURCE_EVIDENCE_ROOT="${source_evidence_root}" \
		SDK_ROOT="${sdk_root}" VALIDATION_CONFIG="${config}" "${source_verifier}"
	container_image_id="$(docker image inspect --format '{{.Id}}' "${container_image}" 2>/dev/null)" ||
		fail "找不到官方 SDK 容器映像：${container_image}"
	docker run --rm --init --security-opt seccomp=unconfined \
		--user "$(id -u):$(id -g)" \
		-e HOME=/tmp \
		-e COMPONENT_CONTAINER_IMAGE="${container_image}" \
		-e COMPONENT_CONTAINER_IMAGE_ID="${container_image_id}" \
		-e COMPONENT_ROOT="${component_root}" \
		-e JOBS="${jobs}" \
		-e SDK_ROOT="${sdk_root}" \
		-e SM10_COMPONENT_CONTAINERIZED=1 \
		-e VALIDATION_CONFIG="${config}" \
		-e VERIFIED_SOURCE_EVIDENCE="${source_evidence_root}" \
		-v "${repo_dir}:${repo_dir}:rw" \
		-v "${sdk_root}:${sdk_root}:ro" \
		-w "${repo_dir}" \
		"${container_image}" \
		bash "${BASH_SOURCE[0]}"
	exit $?
fi

for command in fdtget git install make nproc python3 sha256sum stat; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

[[ -x "${source_verifier}" ]] || fail "找不到來源驗證器"
[[ -x "${cross_compile}gcc" ]] || fail "找不到固定 SDK 交叉編譯器：${cross_compile}gcc"
[[ ! -e "${component_root}" ]] || fail "輸出目錄已存在；請指定新的 COMPONENT_ROOT：${component_root}"

compiler_version="$("${cross_compile}gcc" --version | head -n 1)"
compiler_driver="$(readlink -f "${cross_compile}gcc")"
compiler_driver_sha256="$(sha256sum "${compiler_driver}" | cut -d' ' -f1)"
compiler_binary="${sdk_root}/output/k3/host/opt/ext-toolchain/bin/riscv64-unknown-linux-gnu-gcc"
[[ -x "${compiler_binary}" ]] || fail "找不到 SDK GCC 本體：${compiler_binary}"
compiler_binary_sha256="$(sha256sum "${compiler_binary}" | cut -d' ' -f1)"

mkdir -p "${component_root}/src" "${component_root}/build" "${component_root}/artifacts"
verified_source_evidence="${VERIFIED_SOURCE_EVIDENCE:-}"
[[ -d "${verified_source_evidence}" ]] || fail "找不到主機來源稽核證據"
cp -a "${verified_source_evidence}" "${component_root}/source-evidence"

read_revision() {
	python3 - "${config}" "$1" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["source_commits"][sys.argv[2]])
PY
}

clone_fixed() {
	local source_path=$1 destination=$2 revision=$3
	git clone --shared --no-checkout "${sdk_root}/${source_path}" "${destination}"
	git -C "${destination}" checkout --detach "${revision}"
	[[ -z "$(git -C "${destination}" status --porcelain --untracked-files=all)" ]] ||
		fail "暫存來源工作樹不乾淨：${source_path}"
}

linux_revision="$(read_revision bsp-src/linux-6.18)"
uboot_revision="$(read_revision bsp-src/uboot-2022.10)"
opensbi_revision="$(read_revision bsp-src/opensbi)"

linux_src="${component_root}/src/linux"
uboot_src="${component_root}/src/uboot"
opensbi_src="${component_root}/src/opensbi"
clone_fixed bsp-src/linux-6.18 "${linux_src}" "${linux_revision}"
clone_fixed bsp-src/uboot-2022.10 "${uboot_src}" "${uboot_revision}"
clone_fixed bsp-src/opensbi "${opensbi_src}" "${opensbi_revision}"

install -m 0644 \
	"${repo_dir}/patch/kernel/archive/spacemit-k3-bpi-6.18/dt/Makefile" \
	"${linux_src}/arch/riscv/boot/dts/spacemit/Makefile"
install -m 0644 \
	"${repo_dir}/patch/kernel/archive/spacemit-k3-bpi-6.18/dt/k3-bananapi-sm10.dts" \
	"${linux_src}/arch/riscv/boot/dts/spacemit/k3-bananapi-sm10.dts"

linux_build="${component_root}/build/linux"
make -C "${linux_src}" O="${linux_build}" ARCH=riscv \
	CROSS_COMPILE="${cross_compile}" k3_bianbu_defconfig
make -C "${linux_src}" O="${linux_build}" ARCH=riscv \
	CROSS_COMPILE="${cross_compile}" -j"${jobs}" Image \
	spacemit/k3-bananapi-sm10.dtb

SOURCE_DATE_EPOCH="$(git -C "${uboot_src}" show -s --format=%ct "${uboot_revision}")"
export SOURCE_DATE_EPOCH
# SpacemiT 的板級 config.mk 僅在暫存來源樹內建置時會產生 FSBL 等檔案。
uboot_build="${uboot_src}"
make -C "${uboot_src}" ARCH=riscv CROSS_COMPILE="${cross_compile}" k3_defconfig
make -C "${uboot_src}" ARCH=riscv CROSS_COMPILE="${cross_compile}" -j"${jobs}"

opensbi_build="${component_root}/build/opensbi"
make -C "${opensbi_src}" O="${opensbi_build}" \
	CROSS_COMPILE="${cross_compile}" PLATFORM=generic PLATFORM_DEFCONFIG=k3_defconfig \
	-j"${jobs}"

artifact_dir="${component_root}/artifacts"
install -m 0644 "${linux_build}/arch/riscv/boot/Image" "${artifact_dir}/Image"
install -m 0644 \
	"${linux_build}/arch/riscv/boot/dts/spacemit/k3-bananapi-sm10.dtb" \
	"${artifact_dir}/k3-bananapi-sm10.dtb"
install -m 0644 "${linux_build}/.config" "${artifact_dir}/linux.config"

for file in FSBL.bin bootinfo_block.bin u-boot.itb u-boot-env-default.bin; do
	if [[ -f "${uboot_src}/${file}" ]]; then
		install -m 0644 "${uboot_src}/${file}" "${artifact_dir}/${file}"
	elif [[ -f "${uboot_build}/${file}" ]]; then
		install -m 0644 "${uboot_build}/${file}" "${artifact_dir}/${file}"
	else
		fail "U-Boot 未產生 ${file}"
	fi
done
install -m 0644 "${uboot_build}/.config" "${artifact_dir}/uboot.config"
install -m 0644 \
	"${opensbi_build}/platform/generic/firmware/fw_dynamic.itb" \
	"${artifact_dir}/fw_dynamic.itb"
install -m 0644 \
	"${opensbi_build}/platform/generic/firmware/fw_dynamic.elf" \
	"${artifact_dir}/fw_dynamic.elf"

model="$(fdtget "${artifact_dir}/k3-bananapi-sm10.dtb" / model)"
compatible="$(fdtget "${artifact_dir}/k3-bananapi-sm10.dtb" / compatible)"
[[ "${model}" == "BananaPi BPI-SM10" ]] || fail "專屬 DTB model 不符：${model}"
[[ "${compatible}" == "bananapi,bpi-sm10 spacemit,k3-com260" ]] ||
	fail "專屬 DTB compatible 不符：${compatible}"

manifest="${component_root}/COMPONENTS.tsv"
printf 'component\tartifact\tsource_revision\tsize\tsha256\n' >"${manifest}.partial"
record_artifact() {
	local component=$1 artifact=$2 revision=$3 path="${artifact_dir}/$2"
	printf '%s\t%s\t%s\t%s\t%s\n' "${component}" "${artifact}" "${revision}" \
		"$(stat -c %s "${path}")" "$(sha256sum "${path}" | cut -d' ' -f1)" \
		>>"${manifest}.partial"
}
record_artifact linux Image "${linux_revision}"
record_artifact linux k3-bananapi-sm10.dtb "${linux_revision}"
record_artifact linux linux.config "${linux_revision}"
record_artifact uboot FSBL.bin "${uboot_revision}"
record_artifact uboot bootinfo_block.bin "${uboot_revision}"
record_artifact uboot u-boot.itb "${uboot_revision}"
record_artifact uboot u-boot-env-default.bin "${uboot_revision}"
record_artifact uboot uboot.config "${uboot_revision}"
record_artifact opensbi fw_dynamic.itb "${opensbi_revision}"
record_artifact opensbi fw_dynamic.elf "${opensbi_revision}"
mv "${manifest}.partial" "${manifest}"

python3 - "${component_root}/COMPONENT_STATUS.json.partial" \
	"${model}" "${compatible}" "${jobs}" "${container_image}" \
	"${COMPONENT_CONTAINER_IMAGE_ID:-未記錄}" "${cross_compile}" \
	"${compiler_version}" "${compiler_driver}" "${compiler_driver_sha256}" \
	"${compiler_binary}" "${compiler_binary_sha256}" <<'PY'
import json, sys
(
    path,
    model,
    compatible,
    jobs,
    container_image,
    container_image_id,
    cross_compile,
    compiler_version,
    compiler_driver,
    compiler_driver_sha256,
    compiler_binary,
    compiler_binary_sha256,
) = sys.argv[1:]
data = {
    "status": "complete",
    "built_components": ["linux", "uboot", "opensbi"],
    "not_rebuilt": ["esos", "powervr", "vpu-firmware"],
    "dtb_model": model,
    "dtb_compatible": compatible.split(),
    "jobs": int(jobs),
    "build_environment": {
        "type": "官方 SDK 容器",
        "container_image": container_image,
        "container_image_id": container_image_id,
        "cross_compile": cross_compile,
        "compiler_version": compiler_version,
        "compiler_driver": compiler_driver,
        "compiler_driver_sha256": compiler_driver_sha256,
        "compiler_binary": compiler_binary,
        "compiler_binary_sha256": compiler_binary_sha256,
    },
    "hardware_validation": False,
    "public_distribution_approved": False,
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY
mv "${component_root}/COMPONENT_STATUS.json.partial" \
	"${component_root}/COMPONENT_STATUS.json"

echo "SM10 元件建置完成：${component_root}"
