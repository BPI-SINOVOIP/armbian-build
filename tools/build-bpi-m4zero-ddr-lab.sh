#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git_sha="$(git -C "$repo_dir" rev-parse HEAD)"
git_short="$(git -C "$repo_dir" rev-parse --short=9 HEAD)"
build_stamp="${BUILD_STAMP:-$(date +%Y%m%d-%H%M%S)}"
source_date_epoch="${SOURCE_DATE_EPOCH:-1786579200}"
output_dir="${OUTPUT_DIR:-$repo_dir/output/evidence/bpi-m4zero-ddr-lab/build-${build_stamp}-${git_short}}"
source_dir="$repo_dir/cache/sources/u-boot-worktree/u-boot/v2026.01"
patch_path="$repo_dir/patch/u-boot/v2026.01/board_bananapim4zero/015-sunxi-h616-add-standalone-ddr-lab.patch"

required_commands=(
	aarch64-linux-gnu-nm
	aarch64-linux-gnu-objdump
	aarch64-linux-gnu-size
	cmp
	dd
	dpkg-deb
	find
	git
	install
	readelf
	rg
	sha256sum
	stat
	strings
	tee
)

for command_name in "${required_commands[@]}"; do
	command -v "$command_name" >/dev/null || {
		echo "缺少必要命令：$command_name" >&2
		exit 1
	}
done

[[ -f "$patch_path" ]] || {
	echo "找不到 DDR 實驗器補丁：$patch_path" >&2
	exit 1
}

mkdir -p "$output_dir" "$output_dir/extracted-deb"

build_command=(
	./compile.sh
	uboot
	BOARD=bananapim4zero
	BRANCH=current
	RELEASE=trixie
	ARTIFACT_IGNORE_CACHE=yes
	"SOURCE_DATE_EPOCH=$source_date_epoch"
)

printf '%q ' "${build_command[@]}" >"$output_dir/build-command.txt"
printf '\nSOURCE_DATE_EPOCH=%q\n' "$source_date_epoch" >>"$output_dir/build-command.txt"

{
	printf '開始時間：%s\n' "$(date --iso-8601=seconds)"
	printf 'Armbian 提交：%s\n' "$git_sha"
	printf 'SOURCE_DATE_EPOCH：%s\n' "$source_date_epoch"
	cd "$repo_dir"
	SOURCE_DATE_EPOCH="$source_date_epoch" "${build_command[@]}"
	printf '結束時間：%s\n' "$(date --iso-8601=seconds)"
} 2>&1 | tee "$output_dir/build.log"

artifacts=(
	"$source_dir/spl/u-boot-spl"
	"$source_dir/spl/u-boot-spl-nodtb.bin"
	"$source_dir/spl/sunxi-spl.bin"
	"$source_dir/u-boot-sunxi-with-spl.bin"
	"$source_dir/.config"
)
for artifact in "${artifacts[@]}"; do
	[[ -f "$artifact" ]] || {
		echo "建置缺少必要產物：$artifact" >&2
		exit 1
	}
done

config="$source_dir/.config"
grep -qx 'CONFIG_DRAM_SUNXI_H616_LAB=y' "$config"
grep -qx 'CONFIG_DRAM_CLK=480' "$config"
grep -qx '# CONFIG_DRAM_SUNXI_H616_DIAGNOSTICS is not set' "$config"
grep -qx '# CONFIG_SPL_MMC is not set' "$config"
grep -qx '# CONFIG_SPL_RAW_IMAGE_SUPPORT is not set' "$config"

spl_elf="$source_dir/spl/u-boot-spl"
spl_raw="$source_dir/spl/u-boot-spl-nodtb.bin"
spl_egon="$source_dir/spl/sunxi-spl.bin"
combined="$source_dir/u-boot-sunxi-with-spl.bin"
spl_raw_size="$(stat -c %s "$spl_raw")"
spl_egon_size="$(stat -c %s "$spl_egon")"
spl_limit_hex="$(sed -n 's/^CONFIG_SPL_MAX_SIZE=//p' "$config")"
spl_limit="$((spl_limit_hex))"

(( spl_raw_size < spl_limit )) || {
	echo "SPL 超過 linker 上限：$spl_raw_size >= $spl_limit" >&2
	exit 1
}
(( spl_egon_size <= spl_limit )) || {
	echo "eGON SPL 超過 linker 上限：$spl_egon_size > $spl_limit" >&2
	exit 1
}

egon_magic="$(dd if="$spl_egon" bs=1 skip=4 count=8 status=none)"
[[ "$egon_magic" == 'eGON.BT0' ]] || {
	echo "SPL 缺少 eGON.BT0 header" >&2
	exit 1
}

cmp -n "$spl_egon_size" "$spl_egon" "$combined"

strings "$spl_elf" | rg '^M4ZLAB2_' | sort -u >"$output_dir/protocol-markers.txt"
for marker in READY START TEST BENCH ERROR FINAL REJECT BOOT_ERROR; do
	rg -q "^M4ZLAB2_${marker}" "$output_dir/protocol-markers.txt" || {
		echo "SPL 缺少協定標記：M4ZLAB2_$marker" >&2
		exit 1
	}
done
if strings "$spl_elf" | rg -q '^M4ZDDR1_'; then
	echo "實驗器不得保留 O1 大量診斷標記" >&2
	exit 1
fi
if strings "$spl_elf" | rg -q '^M4ZLAB2_.*%[^ ]*ll'; then
	echo "實驗器格式字串含 tiny-printf 不支援的 ll 修飾符" >&2
	exit 1
fi

aarch64-linux-gnu-nm -S "$spl_elf" >"$output_dir/spl-symbols.txt"
rg -q ' [Dd] sunxi_h616_dram_runtime_clk$' "$output_dir/spl-symbols.txt"
rg -q ' T sunxi_h616_dram_lab_bootstrap_begin$' "$output_dir/spl-symbols.txt"
rg -q ' T sunxi_h616_dram_lab_set_bootstrap$' "$output_dir/spl-symbols.txt"
rg -q ' T sunxi_h616_dram_lab_run$' "$output_dir/spl-symbols.txt"
if rg -q ' [Bb] (lab_|sunxi_h616_dram_runtime_clk)' "$output_dir/spl-symbols.txt"; then
	echo "實驗器狀態不得放在尚未清零的 BSS" >&2
	exit 1
fi
if rg -q ' (mmc_init|spl_mmc_load|load_simple_fit|spl_load_simple_fit)$' \
	"$output_dir/spl-symbols.txt"; then
	echo "SPL 仍含下一階段 MMC/FIT loader 符號" >&2
	exit 1
fi

aarch64-linux-gnu-objdump -d "$spl_elf" >"$output_dir/spl-disassembly.txt"
aarch64-linux-gnu-size "$spl_elf" >"$output_dir/spl-size.txt"
readelf -S "$spl_elf" >"$output_dir/spl-sections.txt"

package_build_id="$(
	strings "$spl_elf" \
		| sed -n 's/^U-Boot SPL [^_]*_armbian-\([^ ]*\) (.*/\1/p' \
		| head -n 1
)"
[[ -n "$package_build_id" ]] || {
	echo "無法從 SPL 取得 Armbian Build ID" >&2
	exit 1
}
mapfile -t deb_candidates < <(
	find "$repo_dir/output/packages-hashed/global" "$repo_dir/output/debs" \
		-maxdepth 1 -type f \
		-name "linux-u-boot-bananapim4zero-current_*${package_build_id}*.deb" \
		-printf '%T@\t%p\n' | sort -nr
)
(( ${#deb_candidates[@]} > 0 )) || {
	echo "找不到本輪 U-Boot 套件：$package_build_id" >&2
	exit 1
}
deb_path="${deb_candidates[0]#*$'\t'}"
dpkg-deb -x "$deb_path" "$output_dir/extracted-deb"
package_combined="$output_dir/extracted-deb/usr/lib/linux-u-boot-current-bananapim4zero/u-boot-sunxi-with-spl.bin"
[[ -f "$package_combined" ]] || {
	echo "U-Boot 套件缺少組合二進位" >&2
	exit 1
}
cmp "$combined" "$package_combined"

install -m 0644 "$spl_raw" "$output_dir/u-boot-spl-nodtb.bin"
install -m 0644 "$spl_egon" "$output_dir/sunxi-spl-ddr-lab.bin"
install -m 0644 "$spl_elf" "$output_dir/u-boot-spl.elf"
install -m 0644 "$combined" "$output_dir/u-boot-sunxi-with-spl.bin"
install -m 0644 "$config" "$output_dir/u-boot.config"
install -m 0644 "$deb_path" "$output_dir/"
install -m 0644 "$patch_path" "$output_dir/"

{
	printf 'Armbian 提交：%s\n' "$git_sha"
	printf 'Armbian 分支：%s\n' "$(git -C "$repo_dir" branch --show-current)"
	printf 'U-Boot upstream 提交：%s\n' "$(git -C "$source_dir" rev-parse HEAD)"
	printf '\nArmbian 工作樹：\n'
	git -C "$repo_dir" status --short --branch
	printf '\nU-Boot 工作樹：\n'
	git -C "$source_dir" status --short
} >"$output_dir/git-state.txt"

{
	printf '檢查項目\t結果\n'
	printf '單一執行期實驗器\t通過\n'
	printf '安全啟動時脈\t480 MHz\n'
	printf 'O1 大量診斷\t已停用\n'
	printf 'SPL MMC/raw loader\t已停用且符號不存在\n'
	printf 'eGON header\t通過（%s）\n' "$egon_magic"
	printf '未封裝 SPL\t%s bytes\n' "$spl_raw_size"
	printf 'eGON SPL\t%s bytes\n' "$spl_egon_size"
	printf 'SPL linker 上限\t%s bytes\n' "$spl_limit"
	printf 'SPL 剩餘空間\t%s bytes\n' "$((spl_limit - spl_raw_size))"
	printf '實驗器 BSS 狀態\t未發現\n'
	printf '套件與來源組合檔\t逐位元一致\n'
	printf '套件 Build ID\t%s\n' "$package_build_id"
	printf '實機驗證\t尚未執行\n'
} >"$output_dir/validation.tsv"

{
	printf '檔名\t大小\tSHA-256\n'
	while IFS= read -r file; do
		printf '%s\t%s\t%s\n' "${file#"$output_dir/"}" \
			"$(stat -c %s "$file")" "$(sha256sum "$file" | cut -d' ' -f1)"
	done < <(find "$output_dir" -maxdepth 1 -type f ! -name manifest.tsv | sort)
} >"$output_dir/manifest.tsv"

echo "DDR 實驗器建置與離線驗證完成：$output_dir"
echo "實機驗證尚未執行"
