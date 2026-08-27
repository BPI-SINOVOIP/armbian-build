#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli}"
board="bananapism10"
policy_checker="${repo_dir}/tools/check-bananapi-spacemit-k3-sm10-policy.py"

for command in awk cmp cut fdtget find git grep lsblk losetup mount mountpoint \
	mv od python3 sfdisk sgdisk sha256sum stat sudo udevadm umount xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "SM10 映像驗證失敗：$*" >&2
	exit 1
}

field() {
	python3 - "${config}" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))["boards"]["bananapism10"][sys.argv[2]]
if isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

read_metadata() {
	local key=$1 values=()
	mapfile -t values < <(grep -E "^${key}=" "${metadata}")
	[[ ${#values[@]} -eq 1 ]] || fail "中繼資料缺少唯一欄位：${key}"
	printf '%s\n' "${values[0]#*=}"
}

[[ -x "${policy_checker}" ]] || fail "找不到政策檢查器"
"${policy_checker}" "${config}"
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹不乾淨"
[[ -f "${output_dir}/COMPLETION_STATUS.json" ]] || fail "缺少建置狀態"
grep -Fq '"status": "complete"' "${output_dir}/COMPLETION_STATUS.json" ||
	fail "建置狀態不是 complete"

board_dir="${output_dir}/${board}"
metadata="${board_dir}/artifact.metadata.txt"
[[ -f "${metadata}" ]] || fail "缺少候選中繼資料"
[[ "$(read_metadata board)" == "${board}" ]] || fail "板卡欄位不符"
[[ "$(read_metadata release)" == trixie ]] || fail "發行版不是 trixie"
[[ "$(read_metadata branch)" == current ]] || fail "核心分支不是 current"
[[ "$(read_metadata profile)" == cli ]] || fail "候選不是 CLI"
[[ "$(read_metadata source_commit)" == "$(git -C "${repo_dir}" rev-parse HEAD)" ]] ||
	fail "候選來源提交不符"
[[ "$(read_metadata validation_config_sha256)" == \
	"$(sha256sum "${config}" | cut -d' ' -f1)" ]] || fail "驗證契約雜湊不符"
[[ "$(read_metadata uboot_revision)" == "$(field uboot_revision)" ]] ||
	fail "U-Boot revision 不符"
[[ "$(read_metadata linux_revision)" == \
	"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["linux_commit"])' "${config}")" ]] ||
	fail "Linux revision 不符"

image="${board_dir}/$(read_metadata image_filename)"
archive="${board_dir}/$(read_metadata archive_filename)"
[[ -f "${image}" && -f "${archive}" ]] || fail "IMG 與 XZ 不完整"
[[ "$(stat -c %s "${image}")" == "$(read_metadata raw_size)" ]] || fail "IMG 大小不符"
[[ "$(sha256sum "${image}" | cut -d' ' -f1)" == "$(read_metadata raw_sha256)" ]] ||
	fail "IMG SHA-256 不符"
[[ "$(stat -c %s "${archive}")" == "$(read_metadata xz_size)" ]] || fail "XZ 大小不符"
[[ "$(sha256sum "${archive}" | cut -d' ' -f1)" == "$(read_metadata xz_sha256)" ]] ||
	fail "XZ SHA-256 不符"
xz -t "${archive}"
[[ "$(xz -dc "${archive}" | sha256sum | cut -d' ' -f1)" == "$(read_metadata raw_sha256)" ]] ||
	fail "XZ 解壓資料與 IMG 不一致"

signature="$(od -An -tx1 -j510 -N2 "${image}" | awk '{print $1 $2}')"
[[ "${signature}" == 55aa ]] || fail "映像缺少 MBR 簽章"
sgdisk -v "${image}" >/dev/null || fail "GPT 結構或 CRC 不完整"
partition_json="$(sfdisk --json "${image}")"
python3 - "${partition_json}" "$(field boot_partition_start_sector)" \
	"$(field partition_start_sector)" <<'PY'
import json, sys
table = json.loads(sys.argv[1])["partitiontable"]
if table.get("label") != "gpt":
    raise SystemExit("分割表不是 GPT")
partitions = table.get("partitions", [])
if len(partitions) < 2:
    raise SystemExit("分割區數量不足")
expected = [int(sys.argv[2]), int(sys.argv[3])]
actual = [int(partitions[0]["start"]), int(partitions[1]["start"])]
if actual != expected:
    raise SystemExit(f"分割區起點不符：{actual}")
PY

sudo -n true || fail "唯讀掛載驗證需要免互動 sudo"
mkdir -p "${repo_dir}/.tmp"
mount_root="$(mktemp -d "${repo_dir}/.tmp/sm10-verify-root.XXXXXX")"
mount_boot="$(mktemp -d "${repo_dir}/.tmp/sm10-verify-boot.XXXXXX")"
loop_device="$(sudo losetup --find --show --partscan --read-only "${image}")"
cleanup() {
	if mountpoint -q "${mount_root}"; then sudo umount "${mount_root}"; fi
	if mountpoint -q "${mount_boot}"; then sudo umount "${mount_boot}"; fi
	sudo losetup -d "${loop_device}" 2>/dev/null || true
	rmdir "${mount_root}" "${mount_boot}" 2>/dev/null || true
}
trap cleanup EXIT
udevadm settle
mapfile -t partitions < <(lsblk -nrpo NAME,TYPE "${loop_device}" | awk '$2 == "part" {print $1}')
[[ ${#partitions[@]} -ge 2 ]] || fail "找不到兩個候選分割區"
sudo mount -o ro "${partitions[0]}" "${mount_boot}"
sudo mount -o ro,noload "${partitions[1]}" "${mount_root}"

for path in Image initramfs-generic.img env_k3.txt dtb/spacemit/k3-bananapi-sm10.dtb; do
	[[ -s "${mount_boot}/${path}" ]] || fail "開機分割區缺少 ${path}"
done
while IFS=$'\t' read -r key value; do
	grep -Fqx "${key}=${value}" "${mount_boot}/env_k3.txt" ||
		fail "env_k3 欄位不符：${key}"
done < <(python3 - "${config}" <<'PY'
import json, sys
env = json.load(open(sys.argv[1], encoding="utf-8"))["boards"]["bananapism10"]["boot_environment"]
for key, value in sorted(env.items()):
    print(f"{key}\t{value}")
PY
)

dtb="${mount_boot}/dtb/spacemit/k3-bananapi-sm10.dtb"
[[ "$(fdtget "${dtb}" / model)" == "$(field model)" ]] || fail "DTB model 不符"
mapfile -t compatible < <(fdtget "${dtb}" / compatible)
[[ "${compatible[*]}" == "bananapi,bpi-sm10 spacemit,k3-com260" ]] ||
	fail "DTB compatible 不符"

mapfile -t kernel_metadata < <(sudo find "${mount_root}/usr/lib" -path \
	'*/linux-image-*/armbian-kernel-metadata.sh' -type f -print)
[[ ${#kernel_metadata[@]} -eq 1 ]] || fail "缺少唯一核心來源中繼資料"
grep -Fqx "declare KERNEL_GIT_REVISION=\"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["linux_commit"])' "${config}")\"" \
	"${kernel_metadata[0]}" || fail "安裝核心 revision 不符"

uboot_dir="${mount_root}/usr/lib/linux-u-boot-current-${board}"
[[ -d "${uboot_dir}" ]] || fail "缺少安裝後 U-Boot 套件"
grep -Fqx "declare UBOOT_GIT_REVISION=\"$(field uboot_revision)\"" \
	"${uboot_dir}/u-boot-metadata.sh" || fail "安裝後 U-Boot revision 不符"

while IFS=$'\t' read -r name expected offset; do
	payload="${uboot_dir}/${name}"
	[[ -s "${payload}" ]] || fail "U-Boot 套件缺少 ${name}"
	[[ "$(sudo sha256sum "${payload}" | cut -d' ' -f1)" == "${expected}" ]] ||
		fail "U-Boot 套件載荷雜湊不符：${name}"
	size="$(sudo stat -c %s "${payload}")"
	sudo dd if="${image}" bs=1 skip="${offset}" count="${size}" status=none | \
		sudo cmp --silent - "${payload}" || fail "映像原始 offset 載荷不符：${name}"
done < <(python3 - "${config}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
hashes = data["bootloader_blobs"]
for specification in data["boards"]["bananapism10"]["uboot_payloads"]:
    name, offset = specification.split("@", 1)
    relative = f"packages/blobs/riscv64/spacemit-k3/bpi-sm10/{name}"
    print(f"{name}\t{hashes[relative]}\t{offset}")
PY
)

for name in u-boot-env-default.bin README.txt; do
	[[ -s "${uboot_dir}/${name}" ]] || fail "U-Boot 套件缺少 ${name}"
done
for key_name in kernel_key_prv.key root_key_prv.key spl_key_prv.key uboot_key_prv.key; do
	if sudo find "${mount_root}" "${mount_boot}" -name "${key_name}" -print -quit | grep -q .; then
		fail "候選映像不得包含 SDK 私鑰：${key_name}"
	fi
done

status_file="${output_dir}/VERIFICATION_STATUS.json"
python3 - "${status_file}.partial" "$(git -C "${repo_dir}" rev-parse HEAD)" \
	"$(read_metadata raw_sha256)" "$(sha256sum "${config}" | cut -d' ' -f1)" <<'PY'
import json, sys
path, commit, image_sha256, config_sha256 = sys.argv[1:]
data = {
    "status": "complete",
    "candidate_level": "L2 軟體候選",
    "verifier_commit": commit,
    "image_sha256": image_sha256,
    "validation_config_sha256": config_sha256,
    "read_only_mount": True,
    "hardware_validation": False,
    "public_distribution_approved": False,
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY
mv "${status_file}.partial" "${status_file}"

trap - EXIT
cleanup
echo "SM10 候選映像唯讀驗證完成：${output_dir}"
