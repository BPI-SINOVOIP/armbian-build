#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
evidence_dir="${1:-}"
source_xz="${2:-/media/pi/SMCI/armbian/bpi-v26.2.1/output/images/2026.07/bpi-m4z-u0-safe-480/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_u0-safe-480mhz.img.xz}"
output_dir="${OUTPUT_DIR:-$repo_dir/output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag}"
output_image="${OUTPUT_IMAGE:-$output_dir/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img}"
boot_offset=8192

if [[ -z "$evidence_dir" ]]; then
	echo "用法：$0 O1證據目錄 [U0來源映像.xz]" >&2
	exit 2
fi

required_commands=(
	cmp
	cut
	dd
	head
	mkdir
	mv
	rm
	sfdisk
	sha256sum
	stat
	tail
	xz
)

for command_name in "${required_commands[@]}"; do
	command -v "$command_name" >/dev/null || {
		echo "缺少必要命令：$command_name" >&2
		exit 1
	}
done

bootloader="$evidence_dir/u-boot-sunxi-with-spl.bin"
validation="$evidence_dir/validation.tsv"
[[ -f "$bootloader" && -f "$validation" ]] || {
	echo "O1 證據目錄缺少 bootloader 或驗證表" >&2
	exit 1
}
grep -Fqx $'M4ZDDR1 結構化診斷\t已啟用並找到標記' "$validation"
grep -Fqx $'實機驗證\t尚未執行' "$validation"
[[ -f "$source_xz" ]] || {
	echo "找不到來源映像：$source_xz" >&2
	exit 1
}
[[ ! -e "$output_image" && ! -e "$output_image.xz" ]] || {
	echo "輸出映像已存在，拒絕覆寫：$output_image" >&2
	exit 1
}

mkdir -p "$output_dir"
partial_image="$output_image.partial"
trap 'rm -f "$partial_image"' EXIT

xz -t "$source_xz"
source_xz_sha="$(sha256sum "$source_xz" | cut -d' ' -f1)"
xz -dc "$source_xz" >"$partial_image"

image_size_before="$(stat -c %s "$partial_image")"
bootloader_size="$(stat -c %s "$bootloader")"
boot_end="$((boot_offset + bootloader_size))"
(( boot_end < image_size_before )) || {
	echo "bootloader 超出來源映像範圍" >&2
	exit 1
}

prefix_before="$(head -c "$boot_offset" "$partial_image" | sha256sum | cut -d' ' -f1)"
suffix_before="$(tail -c "+$((boot_end + 1))" "$partial_image" | sha256sum | cut -d' ' -f1)"

dd if="$bootloader" of="$partial_image" bs=1024 seek=8 conv=notrunc status=progress
cmp -n "$bootloader_size" -i "$boot_offset":0 "$partial_image" "$bootloader"

image_size_after="$(stat -c %s "$partial_image")"
prefix_after="$(head -c "$boot_offset" "$partial_image" | sha256sum | cut -d' ' -f1)"
suffix_after="$(tail -c "+$((boot_end + 1))" "$partial_image" | sha256sum | cut -d' ' -f1)"

[[ "$image_size_before" == "$image_size_after" ]]
[[ "$prefix_before" == "$prefix_after" ]]
[[ "$suffix_before" == "$suffix_after" ]]

mv "$partial_image" "$output_image"
trap - EXIT

sfdisk -J "$output_image" >"$output_image.sfdisk.json"
sha256sum "$output_image" >"$output_image.sha256"
xz -T0 -6 -k "$output_image"
xz -t "$output_image.xz"
sha256sum "$output_image.xz" >"$output_image.xz.sha256"

bootloader_sha="$(sha256sum "$bootloader" | cut -d' ' -f1)"
image_sha="$(cut -d' ' -f1 "$output_image.sha256")"
image_xz_sha="$(cut -d' ' -f1 "$output_image.xz.sha256")"

{
	printf '項目\t值\n'
	printf '來源映像\t%s\n' "$source_xz"
	printf '來源映像 SHA-256\t%s\n' "$source_xz_sha"
	printf 'O1 證據目錄\t%s\n' "$evidence_dir"
	printf 'bootloader offset\t%s\n' "$boot_offset"
	printf 'bootloader size\t%s\n' "$bootloader_size"
	printf 'bootloader SHA-256\t%s\n' "$bootloader_sha"
	printf '映像大小\t%s\n' "$image_size_after"
	printf '映像 SHA-256\t%s\n' "$image_sha"
	printf '壓縮映像 SHA-256\t%s\n' "$image_xz_sha"
	printf '前綴不變\t通過\n'
	printf 'bootloader 回讀\t通過\n'
	printf '後綴不變\t通過\n'
	printf '檔案大小不變\t通過\n'
	printf 'xz 完整性\t通過\n'
	printf '實機驗證\t尚未執行\n'
} >"$output_image.manifest.tsv"

echo "O1 測試映像封裝完成：$output_image"
echo "注意：尚未執行實機驗證"
