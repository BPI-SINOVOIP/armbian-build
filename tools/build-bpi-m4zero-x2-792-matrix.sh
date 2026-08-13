#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${SOURCE_DIR:-/media/pi/SMCI/armbian/bpi-v26.2.1/output/images/2026.07/bpi-m4z-u0-safe-480}"
output_dir="${OUTPUT_DIR:-$repo_dir/output/images/2026.08/bpi-m4zero-x2-cross-board-792-matrix}"
work_dir="${WORK_DIR:-$repo_dir/.tmp/bpi-m4zero-x2-792-matrix-20260813}"
x2_evidence_dir="${X2_EVIDENCE_DIR:-$repo_dir/output/evidence/bpi-m4zero-opi-ddr/X2-20260813-cross-board-pushed-v5-918c0e93a}"
x2_deb="${X2_DEB:-$x2_evidence_dir/linux-u-boot-bananapim4zero-current_26.05.0-trunk_arm64__2026.01-S127a-P1f88-Hc6a9-V3946-Be6d8-R448a.deb}"
delivery_doc="$repo_dir/docs/bananapi-m4zero-x2-792-image-matrix-delivery-20260814.md"
test_record_template="$repo_dir/docs/evidence/bananapi-m4zero-opi-ddr/X2-mass-validation-record-template.tsv"

expected_deb_sha256="5353c323bab7fd4ff034a3be682da027dbb97ff833929a34cf71c6475a05de28"
expected_uboot_sha256="a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad"
expected_build_id="P1f88"
uboot_offset=8192
uboot_size=873977
uboot_end=$((uboot_offset + uboot_size))

for command in basename chmod cp cut dd dirname dpkg-deb find flock grep head jq mkdir mktemp mv rm sed sfdisk sha256sum stat tail wc xz; do
	command -v "$command" >/dev/null || {
		echo "缺少必要命令：$command" >&2
		exit 1
	}
done

mkdir -p "$output_dir/bootloader" "$work_dir"
exec 9>"$output_dir/.build.lock"
flock -n 9 || {
	echo "另一個 X2 矩陣建置程序正在執行：$output_dir" >&2
	exit 1
}

completion_status="$output_dir/COMPLETION_STATUS.txt"
printf 'status=in_progress\n' >"$completion_status.partial"
mv "$completion_status.partial" "$completion_status"

[[ -f "$x2_deb" ]] || {
	echo "找不到鎖定的 X2 U-Boot 套件：$x2_deb" >&2
	exit 1
}
[[ -f "$delivery_doc" && -f "$test_record_template" ]] || {
	echo "找不到交付文件或測試紀錄模板" >&2
	exit 1
}

[[ "$(sha256sum "$x2_deb" | cut -d' ' -f1)" == "$expected_deb_sha256" ]] || {
	echo "X2 U-Boot 套件雜湊不符：$x2_deb" >&2
	exit 1
}

deb_dir="$(mktemp -d "$work_dir/deb.XXXXXX")"
cleanup() {
	rm -rf -- "$deb_dir"
}
trap cleanup EXIT

dpkg-deb -x "$x2_deb" "$deb_dir"
x2_uboot="$deb_dir/usr/lib/linux-u-boot-current-bananapim4zero/u-boot-sunxi-with-spl.bin"

[[ "$(stat -c %s "$x2_uboot")" == "$uboot_size" ]] || {
	echo "X2 bootloader 大小不符" >&2
	exit 1
}
[[ "$(sha256sum "$x2_uboot" | cut -d' ' -f1)" == "$expected_uboot_sha256" ]] || {
	echo "X2 bootloader 雜湊不符" >&2
	exit 1
}
grep -aFq "U-Boot SPL 2026.01_armbian-2026.01-S127a-${expected_build_id}-" "$x2_uboot" || {
	echo "X2 bootloader Build ID 不符" >&2
	exit 1
}

deb_output="$output_dir/bootloader/$(basename "$x2_deb")"
uboot_output="$output_dir/bootloader/u-boot-sunxi-with-spl-${expected_build_id}.bin"
cp "$x2_deb" "$deb_output.partial"
[[ "$(sha256sum "$deb_output.partial" | cut -d' ' -f1)" == "$expected_deb_sha256" ]]
mv "$deb_output.partial" "$deb_output"
cp "$x2_uboot" "$uboot_output.partial"
[[ "$(sha256sum "$uboot_output.partial" | cut -d' ' -f1)" == "$expected_uboot_sha256" ]]
mv "$uboot_output.partial" "$uboot_output"

source_entries=(
	"8d60f3b4d4115074694e6798f6e37b825f3b97644e758ba005694023e38b8182 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_bookworm_current_6.18.32_u0-safe-480mhz.img.xz"
	"de80417dc0d122b9e9b55d2854226810f948c294fc1eea834265632790b50319 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_bookworm_current_6.18.32_u0-safe-480mhz_xfce_desktop.img.xz"
	"80f9b188d6315b9a7d189a3e08b3b174ffbeb6b6173c74c98007a4ff1dbb6348 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_u0-safe-480mhz.img.xz"
	"ab24c888e5875503621a685accb05327019ad144971755a173f1e1118184fb5d Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_u0-safe-480mhz_xfce_desktop.img.xz"
	"d5742b22ea26200722d5fbfac8fd0867499e8a5db8e5c63b1c02775e4b2333da Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_u0-safe-480mhz.img.xz"
	"b60b3e874540a58e7a46b220a2821f02f5b5fa9ae369e9b42cd122a86a2052ce Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_u0-safe-480mhz_xfce_desktop.img.xz"
	"b4d4f472d045f9b9735276b1ec1696aae2641409009eb825d11908a1de63b033 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_resolute_current_6.18.32_u0-safe-480mhz.img.xz"
	"3559a948e704a9ac9e3c28abb0c87c89b969c7f946c4a25cdf3cfb226a3fc35e Armbian-unofficial_26.05.0-trunk_Bananapim4zero_resolute_current_6.18.32_u0-safe-480mhz_xfce_desktop.img.xz"
	"95de63e4684f4067870a73db9804b602394fd9708e32ee49bad0e6ba5665b2d5 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_trixie_current_6.18.32_u0-safe-480mhz.img.xz"
	"bce73d04a1e78ee64df43ff1b026201b73ef9b61d60edc41a5b259a4a5dab0f8 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_trixie_current_6.18.32_u0-safe-480mhz_xfce_desktop.img.xz"
)

extract_xz_prefix() {
	local image="$1"
	local prefix="$2"
	set +o pipefail
	xz -dc "$image" | head -c "$uboot_end" >"$prefix"
	set -o pipefail
	[[ "$(stat -c %s "$prefix")" == "$uboot_end" ]]
}

hash_xz_prefix() {
	local image="$1"
	local length="$2"
	set +o pipefail
	xz -dc "$image" | head -c "$length" | sha256sum | cut -d' ' -f1
	set -o pipefail
}

validate_raw_image() {
	local image="$1"
	local partition_start embedded_sha256

	partition_start="$(sfdisk -J "$image" | jq -r '.partitiontable.partitions[0].start')"
	[[ "$partition_start" == "8192" ]] || {
		echo "第一個分割區起始位置不符：$image" >&2
		return 1
	}

	embedded_sha256="$(dd if="$image" bs=1M skip="$uboot_offset" count="$uboot_size" iflag=skip_bytes,count_bytes status=none | sha256sum | cut -d' ' -f1)"
	[[ "$embedded_sha256" == "$expected_uboot_sha256" ]] || {
		echo "IMG 內嵌 bootloader 雜湊不符：$image" >&2
		return 1
	}
	grep -aFq "U-Boot SPL 2026.01_armbian-2026.01-S127a-${expected_build_id}-" "$image" || {
		echo "IMG 內嵌 Build ID 不符：$image" >&2
		return 1
	}
}

validate_outside_bootloader_region() {
	local source_image="$1"
	local output_image="$2"
	local source_prefix source_suffix output_prefix output_suffix

	source_prefix="$(hash_xz_prefix "$source_image" "$uboot_offset")"
	source_suffix="$(xz -dc "$source_image" | tail -c "+$((uboot_end + 1))" | sha256sum | cut -d' ' -f1)"
	output_prefix="$(head -c "$uboot_offset" "$output_image" | sha256sum | cut -d' ' -f1)"
	output_suffix="$(tail -c "+$((uboot_end + 1))" "$output_image" | sha256sum | cut -d' ' -f1)"

	[[ "$source_prefix" == "$output_prefix" && "$source_suffix" == "$output_suffix" ]] || {
		echo "bootloader 範圍外資料與鎖定來源不一致：$output_image" >&2
		return 1
	}
}

validate_xz_image() {
	local image="$1"
	local expected_raw_sha256="$2"
	local prefix="$deb_dir/verify-prefix.bin"
	local embedded_sha256 decompressed_sha256

	xz -t "$image"
	decompressed_sha256="$(xz -dc "$image" | sha256sum | cut -d' ' -f1)"
	[[ "$decompressed_sha256" == "$expected_raw_sha256" ]] || {
		echo "XZ 解壓內容與同名 IMG 不一致：$image" >&2
		return 1
	}

	extract_xz_prefix "$image" "$prefix"
	embedded_sha256="$(dd if="$prefix" bs=1M skip="$uboot_offset" count="$uboot_size" iflag=skip_bytes,count_bytes status=none | sha256sum | cut -d' ' -f1)"
	[[ "$embedded_sha256" == "$expected_uboot_sha256" ]] || {
		echo "XZ 內嵌 bootloader 雜湊不符：$image" >&2
		return 1
	}
	grep -aFq "U-Boot SPL 2026.01_armbian-2026.01-S127a-${expected_build_id}-" "$prefix" || {
		echo "XZ 內嵌 Build ID 不符：$image" >&2
		return 1
	}
}

read_metadata_value() {
	local key="$1"
	local metadata="$2"
	sed -n "s/^${key}=//p" "$metadata"
}

group_digits() {
	local value="$1"
	local grouped=""
	while ((${#value} > 3)); do
		grouped=",${value: -3}${grouped}"
		value="${value:0:${#value}-3}"
	done
	printf '%s%s' "$value" "$grouped"
}

require_metadata_value() {
	local key="$1"
	local expected="$2"
	local metadata="$3"
	local actual
	actual="$(read_metadata_value "$key" "$metadata")"
	[[ "$actual" == "$expected" ]] || {
		echo "中繼資料欄位不符：$metadata，$key，預期 $expected，實際 $actual" >&2
		return 1
	}
}

matrix="$output_dir/MATRIX.tsv"
printf 'release\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_filename\txz_filename\n' >"$matrix.partial"

for source_entry in "${source_entries[@]}"; do
	read -r expected_source_sha256 source_name <<<"$source_entry"
	source_image="$source_dir/$source_name"
	[[ -f "$source_image" ]] || {
		echo "找不到來源映像：$source_image" >&2
		exit 1
	}

	source_xz_sha256="$(sha256sum "$source_image" | cut -d' ' -f1)"
	[[ "$source_xz_sha256" == "$expected_source_sha256" ]] || {
		echo "來源映像雜湊不符：$source_image" >&2
		exit 1
	}
	xz -t "$source_image"

	output_xz_name="${source_name/_u0-safe-480mhz/_x2-cross-board-792mhz}"
	output_img_name="${output_xz_name%.xz}"
	output_xz="$output_dir/$output_xz_name"
	output_img="$output_dir/$output_img_name"
	metadata="$output_img.metadata.txt"
	release="$(sed -n 's/.*Bananapim4zero_\([^_]*\)_current.*/\1/p' <<<"$output_img_name")"
	profile="cli"
	[[ "$output_img_name" == *_xfce_desktop.img ]] && profile="xfce"

	if [[ -f "$output_img" ]]; then
		echo "驗證既有 IMG：$output_img_name"
		[[ -f "$metadata" ]] || {
			echo "既有 IMG 缺少中繼資料：$metadata" >&2
			exit 1
		}
		validate_raw_image "$output_img"
		validate_outside_bootloader_region "$source_image" "$output_img"
		raw_size="$(stat -c %s "$output_img")"
		raw_sha256="$(sha256sum "$output_img" | cut -d' ' -f1)"
		require_metadata_value qualification_status x2_four_board_g1_pass_mass_validation_pending "$metadata"
		require_metadata_value release "$release" "$metadata"
		require_metadata_value profile "$profile" "$metadata"
		require_metadata_value kernel 6.18.32-current-sunxi64 "$metadata"
		require_metadata_value uboot_build_id "$expected_build_id" "$metadata"
		require_metadata_value dram_clock_mhz 792 "$metadata"
		require_metadata_value uboot_sha256 "$expected_uboot_sha256" "$metadata"
		require_metadata_value source_xz_sha256 "$source_xz_sha256" "$metadata"
		require_metadata_value raw_size "$raw_size" "$metadata"
		require_metadata_value raw_sha256 "$raw_sha256" "$metadata"
		require_metadata_value outside_bootloader_region_unchanged yes "$metadata"
	else
		echo "建立 $release $profile IMG：$output_img_name"
		xz -dc "$source_image" >"$output_img.partial"

		partition_start="$(sfdisk -J "$output_img.partial" | jq -r '.partitiontable.partitions[0].start')"
		[[ "$partition_start" == "8192" ]] || {
			echo "來源映像第一個分割區不是 sector 8192：$source_name" >&2
			exit 1
		}

		dd if="$x2_uboot" of="$output_img.partial" bs=1M seek="$uboot_offset" oflag=seek_bytes conv=notrunc status=none
		validate_raw_image "$output_img.partial"
		validate_outside_bootloader_region "$source_image" "$output_img.partial"
		raw_size="$(stat -c %s "$output_img.partial")"
		raw_sha256="$(sha256sum "$output_img.partial" | cut -d' ' -f1)"

		{
			printf 'qualification_status=x2_four_board_g1_pass_mass_validation_pending\n'
			printf 'release=%s\n' "$release"
			printf 'profile=%s\n' "$profile"
			printf 'kernel=6.18.32-current-sunxi64\n'
			printf 'uboot_build_id=%s\n' "$expected_build_id"
			printf 'dram_clock_mhz=792\n'
			printf 'uboot_offset=%s\n' "$uboot_offset"
			printf 'uboot_size=%s\n' "$uboot_size"
			printf 'uboot_sha256=%s\n' "$expected_uboot_sha256"
			printf 'source=%s\n' "$source_image"
			printf 'source_xz_sha256=%s\n' "$source_xz_sha256"
			printf 'raw_size=%s\n' "$raw_size"
			printf 'raw_sha256=%s\n' "$raw_sha256"
			printf 'outside_bootloader_region_unchanged=yes\n'
		} >"$metadata.partial"
		mv "$metadata.partial" "$metadata"
		mv "$output_img.partial" "$output_img"
	fi

	if [[ -f "$output_xz" ]]; then
		echo "驗證既有 XZ 與 IMG：$output_xz_name"
		validate_xz_image "$output_xz" "$raw_sha256"
	else
		echo "壓縮 $release $profile XZ：$output_xz_name"
		xz -T0 -6 -c "$output_img" >"$output_xz.partial"
		validate_xz_image "$output_xz.partial" "$raw_sha256"
		mv "$output_xz.partial" "$output_xz"
	fi

	xz_size="$(stat -c %s "$output_xz")"
	xz_sha256="$(sha256sum "$output_xz" | cut -d' ' -f1)"
	{
		grep -v '^xz_\(size\|sha256\)=' "$metadata"
		printf 'xz_size=%s\n' "$xz_size"
		printf 'xz_sha256=%s\n' "$xz_sha256"
	} >"$metadata.partial"
	mv "$metadata.partial" "$metadata"

	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$release" "$profile" "$raw_size" "$raw_sha256" "$xz_size" "$xz_sha256" \
		"$output_img_name" "$output_xz_name" >>"$matrix.partial"
done

expected_count="${#source_entries[@]}"
raw_count="$(find "$output_dir" -maxdepth 1 -type f -name '*.img' | wc -l)"
xz_count="$(find "$output_dir" -maxdepth 1 -type f -name '*.img.xz' | wc -l)"
metadata_count="$(find "$output_dir" -maxdepth 1 -type f -name '*.metadata.txt' | wc -l)"
[[ "$raw_count" == "$expected_count" && "$xz_count" == "$expected_count" && "$metadata_count" == "$expected_count" ]] || {
	echo "產物數量不符：預期各 $expected_count，IMG=$raw_count，XZ=$xz_count，metadata=$metadata_count" >&2
	exit 1
}
[[ "$(wc -l <"$matrix.partial")" == "$((expected_count + 1))" ]] || {
	echo "矩陣清單筆數不符" >&2
	exit 1
}
mv "$matrix.partial" "$matrix"

{
	printf 'qualification_status=X2_FOUR_BOARD_G1_PASS_MASS_VALIDATION_PENDING\n'
	printf 'distribution_scope=BOOKWORM_JAMMY_NOBLE_RESOLUTE_TRIXIE_CLI_XFCE\n'
	printf 'build_id=%s\n' "$expected_build_id"
	printf 'dram_clock_mhz=792\n'
	printf 'hardware_g1_pass=0438,1116,S337,S322\n'
	printf 'required_next_step=repeated_cold_boot_and_long_memory_stress\n'
} >"$output_dir/QUALIFICATION_STATUS.txt.partial"
mv "$output_dir/QUALIFICATION_STATUS.txt.partial" "$output_dir/QUALIFICATION_STATUS.txt"

cp "$delivery_doc" "$output_dir/README.md.partial"
while IFS=$'\t' read -r release profile raw_size raw_sha256 xz_size xz_sha256 _img_filename xz_filename; do
	[[ "$release" == "release" ]] && continue
	expected_readme_row="| ${release^} | ${profile^^} | $(group_digits "$xz_size") | \`$xz_sha256\` |"
	grep -Fxq "$expected_readme_row" "$output_dir/README.md.partial" || {
		echo "交付文件缺少或錯配 $xz_filename 的完整表格列" >&2
		exit 1
	}
done <"$matrix"
mv "$output_dir/README.md.partial" "$output_dir/README.md"
cp "$test_record_template" "$output_dir/TEST_RECORD_TEMPLATE.tsv.partial"
mv "$output_dir/TEST_RECORD_TEMPLATE.tsv.partial" "$output_dir/TEST_RECORD_TEMPLATE.tsv"

(
	cd "$output_dir"
	sha256sum ./*.img.xz >SHA256SUMS-XZ.partial
	[[ "$(wc -l <SHA256SUMS-XZ.partial)" == "$expected_count" ]]
	sha256sum -c SHA256SUMS-XZ.partial
	mv SHA256SUMS-XZ.partial SHA256SUMS-XZ

	sha256sum ./*.img ./*.img.xz ./*.metadata.txt ./MATRIX.tsv \
		./QUALIFICATION_STATUS.txt ./README.md ./TEST_RECORD_TEMPLATE.tsv \
		./SHA256SUMS-XZ ./bootloader/*.deb ./bootloader/*.bin >SHA256SUMS.partial
	[[ "$(wc -l <SHA256SUMS.partial)" == "$((expected_count * 3 + 7))" ]]
	sha256sum -c SHA256SUMS.partial
	mv SHA256SUMS.partial SHA256SUMS
)

partial_file="$(find "$output_dir" -type f -name '*.partial' -print -quit)"
[[ -z "$partial_file" ]] || {
	echo "仍有未完成產物：$partial_file" >&2
	exit 1
}

{
	printf 'status=complete\n'
	printf 'img_count=%s\n' "$raw_count"
	printf 'xz_count=%s\n' "$xz_count"
	printf 'metadata_count=%s\n' "$metadata_count"
	printf 'matrix_sha256=%s\n' "$(sha256sum "$matrix" | cut -d' ' -f1)"
	printf 'sha256sums_sha256=%s\n' "$(sha256sum "$output_dir/SHA256SUMS" | cut -d' ' -f1)"
} >"$completion_status.partial"
mv "$completion_status.partial" "$completion_status"

chmod 0644 "$output_dir"/*.img "$output_dir"/*.img.xz \
	"$output_dir"/*.metadata.txt "$output_dir/MATRIX.tsv" \
	"$output_dir/SHA256SUMS" "$output_dir/SHA256SUMS-XZ" \
	"$output_dir/QUALIFICATION_STATUS.txt" "$output_dir/COMPLETION_STATUS.txt" \
	"$output_dir/README.md" "$output_dir/TEST_RECORD_TEMPLATE.tsv" \
	"$output_dir"/bootloader/*

echo "X2 792 MHz 完整矩陣已完成：$output_dir"
echo "IMG 數量：$raw_count"
echo "XZ 數量：$xz_count"
echo "Bootloader SHA-256：$expected_uboot_sha256"
