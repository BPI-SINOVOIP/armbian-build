#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_target="${CACHE_TARGET:-${repo_dir}/cache}"
revision="8aec7f20bcf5555d7d219c2bad295b4a627b6521"
source_tree="${cache_target}/sources/u-boot-worktree/u-boot/rzv2n-v2021.10"

for command in find findmnt git mountpoint readlink sudo; do
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
source_real="$(readlink -f -- "${source_tree}")"
[[ "${source_real}" == "${cache_real}"/* ]] || fail "U-Boot 來源不在隔離 cache 內"
[[ "$(git -C "${source_tree}" rev-parse HEAD)" == "${revision}" ]] ||
	fail "U-Boot 不是固定提交"
git -C "${source_tree}" diff --cached --quiet || fail "U-Boot 索引含有未受控差異"
sudo -n true || fail "整理 OverlayFS upper 需要免互動 sudo"

# checkout-index 只回復已變動的追蹤檔，並寫入 OverlayFS upper。
while IFS= read -r -d '' relative; do
	sudo -n git -c "safe.directory=${source_tree}" -C "${source_tree}" \
		checkout-index --force -- "${relative}"
done < <(git -C "${source_tree}" diff --name-only -z)
while IFS= read -r -d '' relative; do
	case "${relative}" in
		/* | ../* | */../* | */..) fail "不安全的未追蹤路徑：${relative}" ;;
	esac
	target="${source_tree}/${relative}"
	[[ "${target}" == "${source_tree}"/* ]] || fail "未追蹤路徑逸出來源樹"
	sudo -n find "${target}" -xdev -depth -delete
done < <(git -C "${source_tree}" ls-files --others --exclude-standard -z)

[[ -z "$(git -C "${source_tree}" status --porcelain --untracked-files=all)" ]] ||
	fail "U-Boot 來源整理後仍不乾淨"
echo "AI2N 固定 U-Boot 來源已在專屬 OverlayFS 內恢復乾淨。"
