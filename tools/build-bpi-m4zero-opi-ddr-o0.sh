#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git_sha="$(git -C "$repo_dir" rev-parse HEAD)"
git_short="$(git -C "$repo_dir" rev-parse --short=9 HEAD)"
build_stamp="${BUILD_STAMP:-$(date +%Y%m%d-%H%M%S)}"
experiment="${EXPERIMENT:-O0}"
expect_diagnostics="${EXPECT_DIAGNOSTICS:-no}"
output_dir="${OUTPUT_DIR:-$repo_dir/output/evidence/bpi-m4zero-opi-ddr/${experiment}-${build_stamp}-${git_short}}"
extract_dir="$output_dir/extracted-deb"
build_log="$output_dir/build.log"

required_commands=(
	basename
	cmp
	cut
	date
	dpkg-deb
	find
	git
	grep
	head
	install
	mkdir
	rg
	sha256sum
	sort
	stat
	tee
	strings
	xargs
)

for command_name in "${required_commands[@]}"; do
	command -v "$command_name" >/dev/null || {
		echo "缺少必要命令：$command_name" >&2
		exit 1
	}
done

if [[ "$expect_diagnostics" == no && \
	-f "$repo_dir/patch/u-boot/v2026.01/board_bananapim4zero/014-sunxi-h616-add-structured-dram-diagnostics.patch" ]]; then
	echo "目前補丁堆疊已進入 O1；重建 O0 請切換至證據提交 a9bef393b" >&2
	exit 1
fi

mkdir -p "$output_dir" "$extract_dir"

build_command=(
	./compile.sh
	uboot
	BOARD=bananapim4zero
	BRANCH=current
	RELEASE=trixie
	ARTIFACT_IGNORE_CACHE=yes
)

printf '%q ' "${build_command[@]}" >"$output_dir/build-command.txt"
printf '\n' >>"$output_dir/build-command.txt"

{
	printf '實驗代號：%s\n' "$experiment"
	if [[ "$expect_diagnostics" == yes ]]; then
		printf '用途：Orange Pi Zero 3 DDR profile 加結構化唯讀診斷\n'
	else
		printf '用途：Orange Pi Zero 3 DDR profile 的 BPI-M4 Zero 乾淨基線\n'
	fi
	printf '開始時間：%s\n' "$(date --iso-8601=seconds)"
	printf 'Armbian 提交：%s\n' "$git_sha"
	printf '建置命令：'
	printf '%q ' "${build_command[@]}"
	printf '\n'
	cd "$repo_dir"
	"${build_command[@]}"
	printf '結束時間：%s\n' "$(date --iso-8601=seconds)"
} 2>&1 | tee "$build_log"

mapfile -t deb_candidates < <(
	find "$repo_dir/output/debs" -maxdepth 1 -type f \
		-name 'linux-u-boot-bananapim4zero-current_*.deb' \
		-printf '%T@\t%p\n' \
		| sort -nr
)
(( ${#deb_candidates[@]} > 0 )) || {
	echo "找不到本次 BPI-M4 Zero U-Boot 套件" >&2
	exit 1
}
deb_path="${deb_candidates[0]#*$'\t'}"

[[ -n "$deb_path" && -f "$deb_path" ]] || {
	echo "找不到本次 BPI-M4 Zero U-Boot 套件" >&2
	exit 1
}

dpkg-deb -x "$deb_path" "$extract_dir"

package_root="$extract_dir/usr/lib/linux-u-boot-current-bananapim4zero"
uboot_bin="$package_root/u-boot-sunxi-with-spl.bin"
uboot_config="$package_root/u-boot-config-target-1"
uboot_defconfig="$package_root/u-boot-defconfig-target-1"

for artifact_path in "$uboot_bin" "$uboot_config" "$uboot_defconfig"; do
	[[ -f "$artifact_path" ]] || {
		echo "套件缺少必要產物：$artifact_path" >&2
		exit 1
	}
done

grep -qx 'CONFIG_DRAM_CLK=792' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_DX_ODT=0x07070707' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_DX_DRI=0x0e0e0e0e' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_CA_DRI=0x0e0e' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_ODT_EN=0xaaaaeeee' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_TPR6=0x44000000' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_TPR10=0x402f6663' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_TPR11=0x24242624' "$uboot_config"
grep -qx 'CONFIG_DRAM_SUNXI_TPR12=0x0f0f100f' "$uboot_config"

case "$expect_diagnostics" in
	yes)
		grep -qx 'CONFIG_DRAM_SUNXI_H616_DIAGNOSTICS=y' "$uboot_config"
		;;
	no)
		if grep -qx 'CONFIG_DRAM_SUNXI_H616_DIAGNOSTICS=y' "$uboot_config"; then
			echo "O0 不得啟用結構化診斷；請切換至 O0 證據提交 a9bef393b" >&2
			exit 1
		fi
		;;
	*)
		echo "EXPECT_DIAGNOSTICS 只接受 yes 或 no" >&2
		exit 1
		;;
esac

if grep -q 'DRAM_SUNXI_KNOWN_GEOMETRY_AUTO_RANKS=y' "$uboot_config"; then
	echo "O0 不得帶入自製 Rank fallback" >&2
	exit 1
fi

source_dir="$repo_dir/cache/sources/u-boot-worktree/u-boot/v2026.01"
[[ -d "$source_dir/.git" || -f "$source_dir/.git" ]] || {
	echo "找不到本次 U-Boot 原始碼工作樹：$source_dir" >&2
	exit 1
}

if rg -q 'udelay\(150\)' "$source_dir/arch/arm/mach-sunxi/dram_helpers.c"; then
	echo "O0 不得帶入額外的 150 us 容量探測延遲" >&2
	exit 1
fi

source_combined="$source_dir/u-boot-sunxi-with-spl.bin"
[[ -f "$source_combined" ]] || {
	echo "U-Boot 原始碼工作樹缺少組合產物" >&2
	exit 1
}
cmp "$uboot_bin" "$source_combined"

if [[ "$expect_diagnostics" == yes ]]; then
	strings "$source_dir/spl/u-boot-spl" \
		| rg '^M4ZDDR1_' \
		| sort -u >"$output_dir/diagnostic-markers.txt"
	[[ -s "$output_dir/diagnostic-markers.txt" ]] || {
		echo "O1 SPL 缺少 M4ZDDR1 診斷標記" >&2
		exit 1
	}
else
	if strings "$source_dir/spl/u-boot-spl" | rg -q '^M4ZDDR1_'; then
		echo "O0 SPL 不得包含 M4ZDDR1 診斷標記" >&2
		exit 1
	fi
fi

install -m 0644 "$deb_path" "$output_dir/"
install -m 0644 "$uboot_bin" "$output_dir/u-boot-sunxi-with-spl.bin"
install -m 0644 "$uboot_config" "$output_dir/u-boot.config"
install -m 0644 "$uboot_defconfig" "$output_dir/u-boot.defconfig"

for source_artifact in \
	"$source_dir/spl/sunxi-spl.bin" \
	"$source_dir/u-boot.bin"; do
	if [[ -f "$source_artifact" ]]; then
		install -m 0644 "$source_artifact" "$output_dir/$(basename "$source_artifact")"
	fi
done

atf_path="$repo_dir/cache/sources/arm-trusted-firmware/lts-v2.12.9/build/sun50i_h616/debug/bl31.bin"
if [[ -f "$atf_path" ]]; then
	install -m 0644 "$atf_path" "$output_dir/bl31.bin"
fi

{
	printf 'Armbian 提交：%s\n' "$git_sha"
	printf 'Armbian 分支：%s\n' "$(git -C "$repo_dir" branch --show-current)"
	printf 'U-Boot upstream 提交：%s\n' "$(git -C "$source_dir" rev-parse HEAD)"
	printf '\nArmbian 工作樹：\n'
	git -C "$repo_dir" status --short --branch
	printf '\nU-Boot 工作樹：\n'
	git -C "$source_dir" status --short
} >"$output_dir/git-state.txt"

find "$repo_dir/patch/u-boot/v2026.01/board_bananapim4zero" \
	-maxdepth 1 -type f -name '*.patch' -print0 \
	| sort -z \
	| xargs -0 sha256sum >"$output_dir/patches.sha256"

{
	printf '檢查項目\t結果\n'
	printf 'U-Boot 套件建置\t通過\n'
	printf 'Orange Pi DDR profile\t通過\n'
	printf '792 MHz 設定\t通過\n'
	if [[ "$expect_diagnostics" == yes ]]; then
		printf 'M4ZDDR1 結構化診斷\t已啟用並找到標記\n'
	else
		printf 'M4ZDDR1 結構化診斷\t未啟用\n'
	fi
	printf '無自製 Rank fallback\t通過\n'
	printf '無 150 us 額外延遲\t通過\n'
	printf '套件與原始碼組合產物一致\t通過\n'
	printf '實機驗證\t尚未執行\n'
} >"$output_dir/validation.tsv"

{
	printf '檔名\t大小\tSHA-256\n'
	while IFS= read -r artifact_path; do
		artifact_name="${artifact_path#"$output_dir/"}"
		artifact_size="$(stat -c %s "$artifact_path")"
		artifact_sha="$(sha256sum "$artifact_path" | cut -d' ' -f1)"
		printf '%s\t%s\t%s\n' "$artifact_name" "$artifact_size" "$artifact_sha"
	done < <(
		find "$output_dir" -maxdepth 1 -type f \
			! -name 'manifest.tsv' \
			-printf '%p\n' \
			| sort
	)
} >"$output_dir/manifest.tsv"

(
	cd "$output_dir"
	sha256sum ./*.bin ./*.config ./*.defconfig ./*.deb 2>/dev/null \
		| sort >sha256sums.txt
)

echo "$experiment U-Boot 建置與離線驗證完成：$output_dir"
echo "注意：尚未執行實機驗證"
