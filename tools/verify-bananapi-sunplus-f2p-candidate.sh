#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${repo_dir}/config/validation/bananapi-sunplus-sp7021-f2p-legacy.json"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunplus-sp7021-f2p-trixie-legacy-cli}"
board_dir="${output_dir}/bananapif2p"
metadata="${board_dir}/artifact.metadata.txt"
temporary_root="${repo_dir}/.tmp/verify-bananapi-sunplus-f2p"
boot_mount="${temporary_root}/boot"
root_mount="${temporary_root}/root"
uboot_extract="${temporary_root}/u-boot-offset.bin"

for command in dd dpkg-query fdtget find grep jq losetup mount mountpoint sha256sum sudo umount udevadm; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "F2P 候選驗證失敗：$*" >&2
	exit 1
}

metadata_value() {
	local key=$1
	grep -E "^${key}=" "${metadata}" | cut -d= -f2-
}

[[ -f "${validation_config}" && -f "${metadata}" ]] || fail "找不到驗證契約或映像中繼資料"
[[ "$(jq -r '.public_release_allowed' "${validation_config}")" == false ]] || fail "發布政策不得開放"
[[ "$(jq -r '.hardware_claims_allowed' "${validation_config}")" == false ]] || fail "硬體宣稱政策不得開放"
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] || fail "來源工作樹不乾淨"

image="${board_dir}/$(metadata_value image_filename)"
[[ -f "${image}" ]] || fail "找不到候選 IMG"
actual_image_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
[[ "${actual_image_sha256}" == "$(metadata_value raw_sha256)" ]] || fail "IMG 雜湊不符"

sudo -n true || fail "唯讀掛載需要免互動 sudo"
mkdir -p "${boot_mount}" "${root_mount}"
loop_device="$(sudo losetup --find --show --partscan --read-only "${image}")"

cleanup() {
	set +e
	mountpoint -q "${root_mount}" && sudo umount "${root_mount}"
	mountpoint -q "${boot_mount}" && sudo umount "${boot_mount}"
	[[ -z "${loop_device:-}" ]] || sudo losetup -d "${loop_device}"
}
trap cleanup EXIT INT TERM

udevadm settle
boot_partition="${loop_device}p1"
root_partition="${loop_device}p2"
[[ -b "${boot_partition}" && -b "${root_partition}" ]] || fail "映像不是預期的雙分割區配置"
sudo mount -o ro "${boot_partition}" "${boot_mount}"
sudo mount -o ro,noload "${root_partition}" "${root_mount}"

boot_linux_dir="${boot_mount}/bananapi/bpi-f2p/linux"
for relative in uImage uInitrd sp7021-bpi-f2p.dtb; do
	[[ -s "${boot_linux_dir}/${relative}" ]] || fail "FAT 開機區缺少 ${relative}"
done
[[ -s "${boot_mount}/uEnv.txt" && -s "${boot_mount}/ISPBOOOT.BIN" ]] || fail "FAT 開機區缺少 uEnv 或 ISPBOOOT.BIN"
grep -Fqx 'board=bpi-f2p' "${boot_mount}/uEnv.txt" || fail "uEnv 板級身分不符"
grep -Fqx 'dtb=sp7021-bpi-f2p.dtb' "${boot_mount}/uEnv.txt" || fail "uEnv DTB 不符"

expected_ispboot="$(jq -r '.firmware_blobs["sp-pack/sp7021/common/bin/ISPBOOOT.BIN"]' "${validation_config}")"
[[ "$(sudo sha256sum "${boot_mount}/ISPBOOOT.BIN" | cut -d' ' -f1)" == "${expected_ispboot}" ]] ||
	fail "ISPBOOOT.BIN 雜湊不符"
[[ -z "$(sudo find "${boot_mount}" "${root_mount}" -name 'BPI-F2S-xboot-emmc-boot0-0k.img.gz' -print -quit)" ]] ||
	fail "映像誤含 F2S eMMC xboot"

dtb="${boot_linux_dir}/sp7021-bpi-f2p.dtb"
expected_dtb="$(jq -r '.boards.bananapif2p.dtb_sha256' "${validation_config}")"
[[ "$(sudo sha256sum "${dtb}" | cut -d' ' -f1)" == "${expected_dtb}" ]] || fail "Linux DTB 雜湊不符"
[[ "$(sudo fdtget -t s "${dtb}" / model)" == "SP7021/CA7/BPI-F2P" ]] || fail "Linux DTB model 不符"
[[ "$(sudo fdtget -t s "${dtb}" / compatible)" == "sunplus,sp7021-achip" ]] || fail "Linux DTB compatible 不符"

while IFS= read -r node; do
	sudo fdtget -l "${dtb}" "${node}" >/dev/null 2>&1 || fail "DTB 缺少節點：${node}"
done < <(jq -r '.boards.bananapif2p.required_present_nodes[]' "${validation_config}")
while IFS= read -r node; do
	[[ "$(sudo fdtget -t s "${dtb}" "${node}" status)" == okay ]] || fail "DTB 節點未啟用：${node}"
done < <(jq -r '.boards.bananapif2p.required_status_nodes[]' "${validation_config}")
while IFS= read -r node; do
	[[ "$(sudo fdtget -t s "${dtb}" "${node}" status)" == disabled ]] || fail "DTB 節點未保持停用：${node}"
done < <(jq -r '.boards.bananapif2p.required_disabled_nodes[]' "${validation_config}")

dd if="${image}" of="${uboot_extract}" bs=1 skip=17408 count=1048576 status=none
while IFS= read -r required; do
	grep -aFq "${required}" "${uboot_extract}" || fail "映像內 U-Boot 缺少字串：${required}"
done < <(jq -r '.boards.bananapif2p.uboot_required_binary_strings[]' "${validation_config}")

while IFS= read -r package; do
	status="$(sudo dpkg-query --admindir="${root_mount}/var/lib/dpkg" -W -f='${Status}' "${package}" 2>/dev/null || true)"
	[[ "${status}" == "install ok installed" ]] || fail "rootfs 缺少診斷套件：${package}"
done < <(jq -r '.common_packages[]' "${validation_config}")

cleanup
trap - EXIT INT TERM
jq -n \
	--arg status complete \
	--arg evidence_level L2 \
	--arg source_commit "$(git -C "${repo_dir}" rev-parse HEAD)" \
	--arg image_sha256 "${actual_image_sha256}" \
	'{status: $status, evidence_level: $evidence_level, source_commit: $source_commit, image_sha256: $image_sha256, hardware_tested: false}' \
	>"${output_dir}/VERIFICATION_STATUS.json.partial"
mv "${output_dir}/VERIFICATION_STATUS.json.partial" "${output_dir}/VERIFICATION_STATUS.json"

echo "F2P 候選映像唯讀驗證通過：${image}"
