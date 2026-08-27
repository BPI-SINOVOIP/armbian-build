#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_target="${CACHE_TARGET:-${repo_dir}/cache}"
declare -A source_roots=(
	[atf]="${cache_target}/sources/arm-trusted-firmware"
	[linux]="${cache_target}/sources/linux-kernel-worktree"
	[uboot]="${cache_target}/sources/u-boot-worktree/u-boot"
)
declare -A source_revisions=(
	[atf]="a011da37865c7649db48efc29b18b36cf87e4bb3"
	[linux]="48c742429129c095045823c204209bb2a92fb5b4"
	[uboot]="8aec7f20bcf5555d7d219c2bad295b4a627b6521"
)

for command in find findmnt git mountpoint perl readlink sudo; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "AI2N 隔離來源準備失敗：$*" >&2
	exit 1
}

mountpoint -q "${cache_target}" || fail "cache 不是掛載點"
[[ "$(findmnt -no FSTYPE -T "${cache_target}")" == overlay ]] ||
	fail "只允許在專屬 OverlayFS 內整理來源"
cache_real="$(readlink -f -- "${cache_target}")"
sudo -n true || fail "整理 OverlayFS upper 需要免互動 sudo"

normalize_source_tree() {
	local component=$1 source_tree=$2 source_real relative
	source_real="$(readlink -f -- "${source_tree}")"
	[[ "${source_real}" == "${cache_real}"/* ]] ||
		fail "${component} 來源不在隔離 cache 內"
	git -C "${source_tree}" diff --cached --quiet ||
		fail "${component} 索引含有未受控差異"

	# checkout-index 只回復已變動的追蹤檔，並寫入 OverlayFS upper。
	while IFS= read -r -d '' relative; do
		sudo -n git -c "safe.directory=${source_tree}" -C "${source_tree}" \
			checkout-index --force -- "${relative}"
	done < <(git -C "${source_tree}" diff --name-only -z)
	(
		cd "${source_tree}"
		{
			git ls-files --others --exclude-standard -z
			git ls-files --others --ignored --exclude-standard -z
		} | sudo -n perl -0ne '
			chomp;
			unlink($_) or die "無法移除隔離來源殘留 $_: $!\n";
		'
	)

	[[ -z "$(git -C "${source_tree}" status --porcelain --untracked-files=all)" ]] ||
		fail "${component} 來源整理後仍不乾淨"
	[[ -z "$(git -C "${source_tree}" ls-files --others --ignored --exclude-standard)" ]] ||
		fail "${component} 來源仍有忽略的建置殘留"
}

for component in atf linux uboot; do
	matches=0
	while IFS= read -r source_tree; do
		actual_revision="$(git -C "${source_tree}" rev-parse HEAD 2>/dev/null || true)"
		[[ "${actual_revision}" == "${source_revisions[${component}]}" ]] || continue
		normalize_source_tree "${component}" "${source_tree}"
		((matches += 1))
	done < <(find "${source_roots[${component}]}" -mindepth 1 -maxdepth 1 \
		-type d -print 2>/dev/null | sort)
	((matches > 0)) || fail "找不到 ${component} 固定來源樹"
done
echo "AI2N 三個固定來源已在專屬 OverlayFS 內恢復乾淨。"
