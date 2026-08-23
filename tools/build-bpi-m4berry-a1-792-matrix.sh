#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${SOURCE_DIR:-/media/pi/SMCI/armbian/bpi-v26.2.1/output/images/2026.05/bpi-m4b}"
output_dir="${OUTPUT_DIR:-$repo_dir/output/images/2026.08/bpi-m4berry-a1-port-792-matrix}"
work_dir="${WORK_DIR:-$repo_dir/.tmp/bpi-m4berry-a1-792-matrix-20260823}"
uboot_deb="${UBOOT_DEB:-$repo_dir/output/debs/linux-u-boot-bananapim4berry-current_26.05.0-trunk_arm64__2025.04-S3482-P25cb-Hc6a9-Vce89-Be6d8-R448a.deb}"
delivery_doc="${DELIVERY_DOC:-$repo_dir/docs/bananapi-m4berry-a1-792-image-matrix-delivery-20260823.md}"
test_record_template="${TEST_RECORD_TEMPLATE:-$repo_dir/docs/evidence/bananapi-m4berry-a1-ddr/M4B-A1-mass-validation-record-template.tsv}"

artifact_tag="m4zero-a1-port-candidate-792mhz"
expected_deb_sha256="f44be3274eefc3886d5c9f84bd15fc251082b17e9472bcaa5a4a43d3e7c13309"
expected_uboot_sha256="93c3dc0766a85974bf8675ac770bf1ebb15b9b0afdb7b1187fcb774ae9951005"
expected_build_id="P25cb"
expected_uboot_version="2025.04"
expected_uboot_size=866745
uboot_offset=8192
uboot_end=$((uboot_offset + expected_uboot_size))
qualification_status="m4b_a1_candidate_hardware_validation_pending"

source_entries=(
	"5b18de3289543fae0d40d4505d2f84dfd43794c814db287c7235e73612af8425 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.32.img.xz"
	"878df8b42b0fb62b8ecc78738ec76c4cf556423a274038d8ba8ca61a5de2db94 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.32_xfce_desktop.img.xz"
	"71e67af410c2e9277663a78832896b7165cc85c5db05d406a57b20a791b19ea3 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.32.img.xz"
	"c276fbe904762c5c02ee2cdba47b30363e927bcffb0af3a9c55969c24b5132a7 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.32_xfce_desktop.img.xz"
	"00684e495992772fc2aa47d35508720162ed18b1aabcfbb55a8a55c552f62608 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.32.img.xz"
	"c46d579bab666927399e53a27ddd7be89b715e8ef57eb46c6b1855c6e85c3ad0 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.32_xfce_desktop.img.xz"
	"8682e0bcdbe18f245e39fc33e0e9898d9685fa49ce7ab7db49f183d8735d0e4c Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.32.img.xz"
	"bfc45107d82872bb8a5972a516d94e6c3084b6276fe6d49f6d3076d971ed9b5a Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.32_xfce_desktop.img.xz"
	"42ad861ba51476ab3081e32b18f3d132113fef3306bce72fde908d14c4129f26 Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.32.img.xz"
	"ba5726f3732a693f2e6288862e51b54fe4b05d2318ca4a7d654d11f8e0fad5dd Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.32_xfce_desktop.img.xz"
)

for command in chmod cp cut dd dirname dpkg-deb find flock grep head jq mkdir mktemp mv rm sed sfdisk sha256sum stat tail wc xz; do
	command -v "$command" >/dev/null || {
		echo "缺少必要命令：$command" >&2
		exit 1
	}
done

[[ "${#source_entries[@]}" == 10 ]] || {
	echo "來源矩陣必須固定為十個映像" >&2
	exit 1
}
[[ -f "$uboot_deb" && -f "$delivery_doc" && -f "$test_record_template" ]] || {
	echo "找不到 U-Boot 套件、交付文件或測試模板" >&2
	exit 1
}

mkdir -p "$output_dir/bootloader" "$work_dir"
exec 9>"$output_dir/.build.lock"
flock -n 9 || {
	echo "另一個 M4 Berry 矩陣建置程序正在執行：$output_dir" >&2
	exit 1
}

completion_status="$output_dir/COMPLETION_STATUS.txt"
printf 'status=in_progress\n' >"$completion_status.partial"
mv "$completion_status.partial" "$completion_status"

actual_deb_sha256="$(sha256sum "$uboot_deb" | cut -d' ' -f1)"
[[ "$actual_deb_sha256" == "$expected_deb_sha256" ]] || {
	echo "U-Boot 套件雜湊不符：$uboot_deb" >&2
	exit 1
}

deb_dir="$(mktemp -d "$work_dir/deb.XXXXXX")"
cleanup() {
	rm -rf -- "$deb_dir"
}
trap cleanup EXIT
dpkg-deb -x "$uboot_deb" "$deb_dir"

package_dir="$deb_dir/usr/lib/linux-u-boot-current-bananapim4berry"
matrix_uboot="$package_dir/u-boot-sunxi-with-spl.bin"
matrix_defconfig="$package_dir/u-boot-defconfig-target-1"
[[ -f "$matrix_uboot" && -f "$matrix_defconfig" ]] || {
	echo "U-Boot 套件內容不完整" >&2
	exit 1
}
[[ "$(stat -c %s "$matrix_uboot")" == "$expected_uboot_size" ]] || {
	echo "U-Boot 二進位大小不符" >&2
	exit 1
}
[[ "$(sha256sum "$matrix_uboot" | cut -d' ' -f1)" == "$expected_uboot_sha256" ]] || {
	echo "U-Boot 二進位雜湊不符" >&2
	exit 1
}
grep -aFq "U-Boot SPL ${expected_uboot_version}_armbian-${expected_uboot_version}-S3482-${expected_build_id}-" "$matrix_uboot" || {
	echo "U-Boot Build ID 不符" >&2
	exit 1
}

expected_defconfig_lines=(
	"CONFIG_DRAM_SUNXI_DX_ODT=0x07070707"
	"CONFIG_DRAM_SUNXI_DX_DRI=0x0e0e0e0e"
	"CONFIG_DRAM_SUNXI_CA_DRI=0x0d0d"
	"CONFIG_DRAM_SUNXI_ODT_EN=0xaaaaeeee"
	"CONFIG_DRAM_SUNXI_TPR6=0x3a808080"
	"CONFIG_DRAM_SUNXI_TPR10=0x402f6663"
	"CONFIG_DRAM_SUNXI_TPR11=0x25252523"
	"CONFIG_DRAM_SUNXI_TPR12=0x110f0f10"
	"CONFIG_DRAM_CLK=792"
)
for config_line in "${expected_defconfig_lines[@]}"; do
	grep -Fxq "$config_line" "$matrix_defconfig" || {
		echo "最終 defconfig 缺少：$config_line" >&2
		exit 1
	}
done

deb_output="$output_dir/bootloader/$(basename "$uboot_deb")"
uboot_output="$output_dir/bootloader/u-boot-sunxi-with-spl-${expected_build_id}.bin"
defconfig_output="$output_dir/bootloader/bananapi_m4_berry-${expected_build_id}.defconfig"
cp "$uboot_deb" "$deb_output.partial"
cp "$matrix_uboot" "$uboot_output.partial"
cp "$matrix_defconfig" "$defconfig_output.partial"
[[ "$(sha256sum "$deb_output.partial" | cut -d' ' -f1)" == "$expected_deb_sha256" ]]
[[ "$(sha256sum "$uboot_output.partial" | cut -d' ' -f1)" == "$expected_uboot_sha256" ]]
mv "$deb_output.partial" "$deb_output"
mv "$uboot_output.partial" "$uboot_output"
mv "$defconfig_output.partial" "$defconfig_output"

hash_xz_prefix() {
	local image="$1"
	local length="$2"
	set +o pipefail
	xz -dc "$image" | head -c "$length" | sha256sum | cut -d' ' -f1
	set -o pipefail
}

hash_xz_suffix() {
	local image="$1"
	local start_byte="$2"
	xz -dc "$image" | tail -c "+$start_byte" | sha256sum | cut -d' ' -f1
}

validate_raw_image() {
	local image="$1"
	local partition_start embedded_sha256
	partition_start="$(sfdisk -J "$image" | jq -r '.partitiontable.partitions[0].start')"
	[[ "$partition_start" == 8192 ]] || {
		echo "第一個分割區起始 sector 不符：$image" >&2
		return 1
	}
	embedded_sha256="$(dd if="$image" bs=1M skip="$uboot_offset" count="$expected_uboot_size" iflag=skip_bytes,count_bytes status=none | sha256sum | cut -d' ' -f1)"
	[[ "$embedded_sha256" == "$expected_uboot_sha256" ]] || {
		echo "IMG 內嵌 bootloader 雜湊不符：$image" >&2
		return 1
	}
	grep -aFq "U-Boot SPL ${expected_uboot_version}_armbian-${expected_uboot_version}-S3482-${expected_build_id}-" "$image" || {
		echo "IMG 內嵌 Build ID 不符：$image" >&2
		return 1
	}
}

validate_outside_bootloader_region() {
	local source_image="$1"
	local output_image="$2"
	local source_prefix source_suffix output_prefix output_suffix
	source_prefix="$(hash_xz_prefix "$source_image" "$uboot_offset")"
	source_suffix="$(hash_xz_suffix "$source_image" "$((uboot_end + 1))")"
	output_prefix="$(head -c "$uboot_offset" "$output_image" | sha256sum | cut -d' ' -f1)"
	output_suffix="$(tail -c "+$((uboot_end + 1))" "$output_image" | sha256sum | cut -d' ' -f1)"
	[[ "$source_prefix" == "$output_prefix" && "$source_suffix" == "$output_suffix" ]] || {
		echo "bootloader 範圍外資料不一致：$output_image" >&2
		return 1
	}
}

validate_xz_image() {
	local image="$1"
	local expected_raw_sha256="$2"
	local decompressed_sha256
	xz -t "$image"
	decompressed_sha256="$(xz -dc "$image" | sha256sum | cut -d' ' -f1)"
	[[ "$decompressed_sha256" == "$expected_raw_sha256" ]] || {
		echo "XZ 解壓內容與 IMG 不一致：$image" >&2
		return 1
	}
}

read_metadata_value() {
	local key="$1"
	local metadata="$2"
	sed -n "s/^${key}=//p" "$metadata"
}

require_metadata_value() {
	local key="$1"
	local expected="$2"
	local metadata="$3"
	local actual
	actual="$(read_metadata_value "$key" "$metadata")"
	[[ "$actual" == "$expected" ]] || {
		echo "中繼資料不符：$metadata，$key，預期 $expected，實際 $actual" >&2
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

	release="$(sed -n 's/.*Bananapim4berry_\([^_]*\)_current.*/\1/p' <<<"$source_name")"
	profile="cli"
	if [[ "$source_name" == *_xfce_desktop.img.xz ]]; then
		profile="xfce"
		output_xz_name="${source_name/_xfce_desktop.img.xz/_${artifact_tag}_xfce_desktop.img.xz}"
	else
		output_xz_name="${source_name/.img.xz/_${artifact_tag}.img.xz}"
	fi
	[[ -n "$release" ]] || {
		echo "無法解析發行版：$source_name" >&2
		exit 1
	}

	output_img_name="${output_xz_name%.xz}"
	output_xz="$output_dir/$output_xz_name"
	output_img="$output_dir/$output_img_name"
	metadata="$output_img.metadata.txt"

	if [[ ! -f "$output_img" ]]; then
		echo "建立 $release $profile IMG：$output_img_name"
		xz -dc "$source_image" >"$output_img.partial"
		dd if="$matrix_uboot" of="$output_img.partial" bs=1M seek="$uboot_offset" oflag=seek_bytes conv=notrunc status=none
		validate_raw_image "$output_img.partial"
		validate_outside_bootloader_region "$source_image" "$output_img.partial"
		raw_size="$(stat -c %s "$output_img.partial")"
		raw_sha256="$(sha256sum "$output_img.partial" | cut -d' ' -f1)"
		{
			printf 'qualification_status=%s\n' "$qualification_status"
			printf 'board=bananapim4berry\n'
			printf 'release=%s\n' "$release"
			printf 'profile=%s\n' "$profile"
			printf 'kernel=6.18.32-current-sunxi64\n'
			printf 'uboot_version=%s\n' "$expected_uboot_version"
			printf 'uboot_build_id=%s\n' "$expected_build_id"
			printf 'dram_clock_mhz=792\n'
			printf 'capacity_detection=automatic_2g_4g_candidate\n'
			printf 'uboot_offset=%s\n' "$uboot_offset"
			printf 'uboot_size=%s\n' "$expected_uboot_size"
			printf 'uboot_sha256=%s\n' "$expected_uboot_sha256"
			printf 'uboot_deb_sha256=%s\n' "$expected_deb_sha256"
			printf 'source=%s\n' "$source_image"
			printf 'source_xz_sha256=%s\n' "$source_xz_sha256"
			printf 'raw_size=%s\n' "$raw_size"
			printf 'raw_sha256=%s\n' "$raw_sha256"
			printf 'outside_bootloader_region_unchanged=yes\n'
		} >"$metadata.partial"
		mv "$metadata.partial" "$metadata"
		mv "$output_img.partial" "$output_img"
	else
		echo "驗證既有 IMG：$output_img_name"
		[[ -f "$metadata" ]] || {
			echo "既有 IMG 缺少中繼資料：$metadata" >&2
			exit 1
		}
		validate_raw_image "$output_img"
		validate_outside_bootloader_region "$source_image" "$output_img"
		raw_size="$(stat -c %s "$output_img")"
		raw_sha256="$(sha256sum "$output_img" | cut -d' ' -f1)"
		require_metadata_value qualification_status "$qualification_status" "$metadata"
		require_metadata_value board bananapim4berry "$metadata"
		require_metadata_value release "$release" "$metadata"
		require_metadata_value profile "$profile" "$metadata"
		require_metadata_value uboot_build_id "$expected_build_id" "$metadata"
		require_metadata_value uboot_sha256 "$expected_uboot_sha256" "$metadata"
		require_metadata_value source_xz_sha256 "$source_xz_sha256" "$metadata"
		require_metadata_value raw_sha256 "$raw_sha256" "$metadata"
	fi

	if [[ ! -f "$output_xz" ]]; then
		echo "壓縮 $release $profile XZ：$output_xz_name"
		xz -T0 -6 -c "$output_img" >"$output_xz.partial"
		validate_xz_image "$output_xz.partial" "$raw_sha256"
		mv "$output_xz.partial" "$output_xz"
	else
		echo "驗證既有 XZ：$output_xz_name"
		validate_xz_image "$output_xz" "$raw_sha256"
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
	echo "產物數量不符：預期各 $expected_count，IMG=$raw_count，XZ=$xz_count，中繼資料=$metadata_count" >&2
	exit 1
}
[[ "$(wc -l <"$matrix.partial")" == "$((expected_count + 1))" ]] || {
	echo "矩陣清單筆數不符" >&2
	exit 1
}
mv "$matrix.partial" "$matrix"

{
	printf 'qualification_status=M4B_A1_CANDIDATE_HARDWARE_VALIDATION_PENDING\n'
	printf 'distribution_scope=BOOKWORM_JAMMY_NOBLE_RESOLUTE_TRIXIE_CLI_XFCE\n'
	printf 'build_id=%s\n' "$expected_build_id"
	printf 'dram_clock_mhz=792\n'
	printf 'capacity_scope=2GiB_AND_4GiB_CANDIDATE\n'
	printf 'm4berry_hardware_pass=none\n'
	printf 'required_next_step=controlled_cold_boot_and_full_memory_stress\n'
} >"$output_dir/QUALIFICATION_STATUS.txt.partial"
mv "$output_dir/QUALIFICATION_STATUS.txt.partial" "$output_dir/QUALIFICATION_STATUS.txt"

cp "$delivery_doc" "$output_dir/README.md.partial"
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
		./SHA256SUMS-XZ ./bootloader/*.deb ./bootloader/*.bin \
		./bootloader/*.defconfig >SHA256SUMS.partial
	[[ "$(wc -l <SHA256SUMS.partial)" == "$((expected_count * 3 + 8))" ]]
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

echo "BPI-M4 Berry A1 792 MHz 候選完整矩陣已完成：$output_dir"
echo "IMG 數量：$raw_count"
echo "XZ 數量：$xz_count"
echo "Bootloader SHA-256：$expected_uboot_sha256"
