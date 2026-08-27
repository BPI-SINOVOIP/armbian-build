#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
candidate_builder="${CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-meson-candidates.sh}"
cache_lower="${CACHE_LOWER:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache}"
cache_target="${CACHE_TARGET:-${repo_dir}/cache}"
overlay_root="${CACHE_OVERLAY_ROOT:-${repo_dir}/.tmp/bananapi-meson-cache-overlay}"
upper_dir="${overlay_root}/upper"
work_dir="${overlay_root}/work"

for command in findmnt flock mkdir mount mountpoint pgrep sudo umount; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

[[ -d "${cache_lower}" ]] || {
	echo "找不到唯讀快取下層：${cache_lower}" >&2
	exit 1
}
[[ -x "${candidate_builder}" ]] || {
	echo "候選建置器不存在或不可執行：${candidate_builder}" >&2
	exit 1
}
[[ "$(findmnt -no FSTYPE -T "${cache_lower}")" != overlay ]] || {
	echo "CACHE_LOWER 不得再指向 OverlayFS。" >&2
	exit 1
}
sudo -n true || {
	echo "OverlayFS 建置需要免互動 sudo。" >&2
	exit 1
}
if pgrep -af '[c]ompile.sh.*build' >/dev/null; then
	echo "偵測到其他 Armbian build，拒絕在快取下層可能變動時啟動。" >&2
	pgrep -af '[c]ompile.sh.*build' >&2 || true
	exit 1
fi

mkdir -p "${cache_target}" "${upper_dir}" "${work_dir}" "${repo_dir}/.tmp"
if mountpoint -q "${cache_target}"; then
	echo "快取目標已是掛載點：${cache_target}" >&2
	exit 1
fi
if [[ -n "$(find "${cache_target}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
	echo "快取目標不是空目錄，拒絕覆蓋：${cache_target}" >&2
	exit 1
fi

exec 8>"${overlay_root}/.mount.lock"
flock -n 8 || {
	echo "另一個隔離快取建置正在執行。" >&2
	exit 1
}

sudo mount -t overlay overlay \
	-o "lowerdir=${cache_lower},upperdir=${upper_dir},workdir=${work_dir}" \
	"${cache_target}"

cleanup_overlay() {
	local exit_status="${1}"
	trap - EXIT INT TERM
	if mountpoint -q "${cache_target}"; then
		if ! sudo umount "${cache_target}"; then
			echo "隔離快取卸載失敗：${cache_target}" >&2
			if ((exit_status == 0)); then
				exit_status=1
			fi
		fi
	fi
	exit "${exit_status}"
}
trap 'cleanup_overlay "$?"' EXIT
trap 'cleanup_overlay 130' INT
trap 'cleanup_overlay 143' TERM

"${candidate_builder}" "$@"
