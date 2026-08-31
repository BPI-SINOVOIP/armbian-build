#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
matrix_file="${MATRIX_FILE:-${repo_dir}/config/bananapi-latest-release-matrix.tsv}"
release_root="${RELEASE_ROOT:-/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08}"
source_commit="${SOURCE_COMMIT:-$(git -C "${repo_dir}" rev-parse HEAD)}"
source_short="${source_commit:0:12}"
state_root="${STATE_ROOT:-${repo_dir}/output/bananapi-latest-rebuild/${source_short}}"
archive_root="${ARCHIVE_ROOT:-$(dirname "$(dirname "${release_root}")")/archive/2026.08}"
xz_threads="${XZ_THREADS:-6}"
minimum_free_gib="${MINIMUM_FREE_GIB:-15}"
selected_board=""
dry_run=no

usage() {
	cat <<'EOF'
用法：tools/rebuild-bananapi-latest-release.sh [選項]

依受版本控制矩陣逐板重建最新映像。每板新矩陣完整通過後，才替換中央舊矩陣。

選項：
  --board ID     只處理指定板卡識別碼或交付目錄名稱
  --dry-run      只列出矩陣與建置命令，不修改檔案
  -h, --help     顯示本說明

可覆寫環境變數：
  MATRIX_FILE、RELEASE_ROOT、ARCHIVE_ROOT、SOURCE_COMMIT、STATE_ROOT、XZ_THREADS、MINIMUM_FREE_GIB
EOF
}

while (($#)); do
	case "$1" in
		--board)
			shift
			selected_board="${1:?--board 缺少值}"
			;;
		--dry-run)
			dry_run=yes
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf '未知選項：%s\n' "$1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

for command in awk basename cp df find flock git grep lsblk losetup mkdir mktemp mount \
	mountpoint mv readlink rmdir rm sed sha256sum sort stat sudo sync tee umount wc xz; do
	command -v "${command}" >/dev/null || {
		printf '缺少必要命令：%s\n' "${command}" >&2
		exit 1
	}
done

[[ "${xz_threads}" =~ ^[0-9]+$ ]] || {
	printf 'XZ_THREADS 必須是非負整數。\n' >&2
	exit 2
}
[[ "${minimum_free_gib}" =~ ^[0-9]+$ ]] || {
	printf 'MINIMUM_FREE_GIB 必須是非負整數。\n' >&2
	exit 2
}
[[ -f "${matrix_file}" ]] || {
	printf '找不到矩陣：%s\n' "${matrix_file}" >&2
	exit 1
}
git -C "${repo_dir}" cat-file -e "${source_commit}^{commit}" || {
	printf '來源提交不存在：%s\n' "${source_commit}" >&2
	exit 1
}

find_board_file() {
	local board="$1" suffix
	for suffix in conf csc wip eos; do
		if [[ -f "${repo_dir}/config/boards/${board}.${suffix}" ]]; then
			printf '%s\n' "${repo_dir}/config/boards/${board}.${suffix}"
			return 0
		fi
	done
	return 1
}

assert_clean_source() {
	local actual_commit
	actual_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
	[[ "${actual_commit}" == "${source_commit}" ]] || {
		printf '來源提交已改變：預期 %s，實際 %s。\n' "${source_commit}" "${actual_commit}" >&2
		return 1
	}
	git -C "${repo_dir}" diff --quiet -- || {
		printf '來源工作樹含未提交變更，拒絕建立不可重現映像。\n' >&2
		return 1
	}
	git -C "${repo_dir}" diff --cached --quiet -- || {
		printf '來源索引含未提交變更，拒絕建立不可重現映像。\n' >&2
		return 1
	}
}

check_free_space() {
	local available required
	available="$(df -PB1 "${release_root}" | awk 'NR == 2 { print $4 }')"
	required=$((minimum_free_gib * 1024 * 1024 * 1024))
	((available >= required)) || {
		printf '可用空間不足：目前 %s bytes，最低要求 %s GiB。\n' \
			"${available}" "${minimum_free_gib}" >&2
		return 1
	}
}

safe_remove_work_dir() {
	local path="$1" parent="$2" resolved_parent resolved_path
	resolved_parent="$(readlink -f -- "${parent}")"
	resolved_path="$(readlink -m -- "${path}")"
	[[ "${resolved_path}" == "${resolved_parent}"/.staging-* || \
		"${resolved_path}" == "${resolved_parent}"/.previous-* ]] || {
		printf '拒絕移除不安全路徑：%s\n' "${path}" >&2
		return 1
	}
	rm -rf --one-file-system -- "${path}"
}

verify_raw_image() {
	local image="$1" board="$2" loop_device root_partition root_fstype mount_dir result=0
	loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")" || return 1
	mount_dir="$(mktemp -d "${state_root}/mount.XXXXXX")" || {
		sudo -n losetup -d "${loop_device}" || true
		return 1
	}
	root_partition="$(lsblk -lnpo NAME,FSTYPE "${loop_device}" | \
		awk '$2 == "ext4" || $2 == "btrfs" { print $1; exit }')"
	root_fstype="$(lsblk -lnpo NAME,FSTYPE "${loop_device}" | \
		awk '$2 == "ext4" || $2 == "btrfs" { print $2; exit }')"
	if [[ -z "${root_partition}" ]]; then
		printf '映像沒有可辨識的 ext4 或 btrfs 根分割區：%s\n' "${image}" >&2
		result=1
	elif [[ "${root_fstype}" == ext4 ]]; then
		sudo -n mount -o ro,noload,nosuid,nodev,noexec "${root_partition}" "${mount_dir}" || result=1
	else
		sudo -n mount -o ro,nosuid,nodev,noexec "${root_partition}" "${mount_dir}" || result=1
	fi
	if ((result == 0)); then
		[[ -f "${mount_dir}/etc/armbian-release" && -d "${mount_dir}/boot" ]] || result=1
		if ((result == 0)) && ! grep -Eq "^BOARD=['\"]?${board}(['\"])?$" \
			"${mount_dir}/etc/armbian-release"; then
			printf '映像內 BOARD 與目標不一致：%s\n' "${image}" >&2
			result=1
		fi
	fi
	if mountpoint -q "${mount_dir}"; then
		sudo -n umount "${mount_dir}" || result=1
	fi
	rmdir "${mount_dir}" 2>/dev/null || true
	sudo -n losetup -d "${loop_device}" || result=1
	return "${result}"
}

item_marker_path() {
	local folder="$1" release="$2" profile="$3"
	printf '%s/items/%s-%s-%s.complete\n' "${state_root}" "${folder}" "${release}" "${profile}"
}

item_is_complete() {
	local stage="$1" folder="$2" release="$3" profile="$4"
	local marker archive digest actual marker_source sha_file
	marker="$(item_marker_path "${folder}" "${release}" "${profile}")"
	[[ -f "${marker}" ]] || return 1
	marker_source="$(sed -n 's/^source_commit=//p' "${marker}")"
	archive="$(sed -n 's/^archive=//p' "${marker}")"
	digest="$(sed -n 's/^sha256=//p' "${marker}")"
	sha_file="${stage}/${archive}.sha"
	[[ "${marker_source}" == "${source_commit}" && -n "${archive}" && \
		"${digest}" =~ ^[0-9a-f]{64}$ && -f "${stage}/${archive}" && \
		-f "${sha_file}" ]] || return 1
	actual="$(sha256sum "${stage}/${archive}" | awk '{ print $1 }')"
	[[ "${actual}" == "${digest}" ]] || return 1
	(
		cd "${stage}"
		sha256sum -c "$(basename "${sha_file}")" >/dev/null
	) || return 1
	xz -t "${stage}/${archive}"
}

build_item() {
	local stage="$1" folder="$2" board="$3" branch="$4" release="$5" profile="$6"
	local marker log_file board_token suffix status image archive digest forced=no
	local -a args candidates

	if item_is_complete "${stage}" "${folder}" "${release}" "${profile}"; then
		printf '沿用本次來源提交已完成項目：%s %s %s\n' "${board}" "${release}" "${profile}"
		return 0
	fi

	check_free_space || return 1
	assert_clean_source || return 1
	marker="${state_root}/markers/${folder}-${release}-${profile}-$RANDOM.marker"
	: > "${marker}"
	log_file="${state_root}/logs/${folder}-${release}-${profile}.log"
	args=(
		build "BOARD=${board}" "BRANCH=${branch}" "RELEASE=${release}"
		KERNEL_CONFIGURE=no EXPERT=yes SHARE_LOG=no DOWNLOAD_MIRROR=bfsu
		"COMPRESS_OUTPUTIMAGE=sha,img" ALLOW_HEADLESS_DESKTOP=yes
	)
	case "${profile}" in
		minimal)
			args+=(BUILD_DESKTOP=no BUILD_MINIMAL=yes)
			suffix=minimal
			;;
		xfce)
			args+=(BUILD_DESKTOP=yes BUILD_MINIMAL=no DESKTOP_ENVIRONMENT=xfce DESKTOP_TIER=mid)
			suffix=xfce_desktop
			;;
		*)
			printf '未知映像類型：%s\n' "${profile}" >&2
			return 2
			;;
	esac
	if [[ "${release}" == trixie && "${profile}" == minimal ]]; then
		args+=(ARTIFACT_IGNORE_CACHE=yes)
		forced=yes
	fi

	printf '完整建置：%s %s %s，強制重建元件=%s。\n' \
		"${board}" "${release}" "${profile}" "${forced}"
	set +e
	(
		cd "${repo_dir}"
		./compile.sh "${args[@]}"
	) 2>&1 | tee "${log_file}"
	status=${PIPESTATUS[0]}
	set -e
	if ((status != 0)); then
		printf '建置失敗：%s %s %s，狀態 %s。\n' \
			"${board}" "${release}" "${profile}" "${status}" >&2
		return "${status}"
	fi
	assert_clean_source || return 1

	board_token="${board^}"
	mapfile -t candidates < <(
		find "${repo_dir}/output/images" -maxdepth 1 -type f \
			\( -name "Armbian-*_${board_token}_${release}_${branch}_*_${suffix}.img" -o \
			-name "Bananapi-Armbian_*_${board_token}_${release}_${branch}_*_${suffix}.img" \) \
			-newer "${marker}" -print
	)
	rm -f -- "${marker}"
	if [[ ${#candidates[@]} -ne 1 ]]; then
		printf '新映像數量錯誤：%s %s %s，找到 %s 個。\n' \
			"${board}" "${release}" "${profile}" "${#candidates[@]}" >&2
		return 1
	fi
	image="${candidates[0]}"
	verify_raw_image "${image}" "${board}" || {
		printf '原始映像唯讀內容檢查失敗：%s\n' "${image}" >&2
		return 1
	}
	find "${stage}" -maxdepth 1 -type f \
		-name "*_${release}_${branch}_*_${suffix}.img.xz*" -delete
	archive="${stage}/$(basename "${image}").xz"
	rm -f -- "${archive}.partial"
	xz -T"${xz_threads}" -6 -c "${image}" > "${archive}.partial"
	xz -t "${archive}.partial"
	mv "${archive}.partial" "${archive}"
	digest="$(sha256sum "${archive}" | awk '{ print $1 }')"
	printf '%s  %s\n' "${digest}" "$(basename "${archive}")" > "${archive}.sha"
	sync -f "${archive}"
	rm -f -- "${image}" "${image}.sha" "${image}.txt"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'archive=%s\n' "$(basename "${archive}")"
		printf 'sha256=%s\n' "${digest}"
		printf 'fresh_artifacts=%s\n' "${forced}"
		printf 'log=%s\n' "${log_file}"
	} > "$(item_marker_path "${folder}" "${release}" "${profile}").partial"
	mv "$(item_marker_path "${folder}" "${release}" "${profile}").partial" \
		"$(item_marker_path "${folder}" "${release}" "${profile}")"
}

verify_board_dir() {
	local directory="$1" branch="$2" releases_csv="$3"
	local release expected archive sha_file count
	local -a releases
	IFS=, read -r -a releases <<< "${releases_csv}"
	expected=$(( ${#releases[@]} * 2 ))
	count="$(find "${directory}" -maxdepth 1 -type f -name '*.img.xz' | wc -l)"
	[[ "${count}" -eq "${expected}" ]] || {
		printf '矩陣數量錯誤：%s，實際 %s，預期 %s。\n' \
			"${directory}" "${count}" "${expected}" >&2
		return 1
	}
	[[ "$(find "${directory}" -maxdepth 1 -type f -name '*.img.xz.sha' | wc -l)" -eq "${expected}" ]] || return 1
	[[ -z "$(find "${directory}" -maxdepth 1 -type f -name '*.img' -print -quit)" ]] || return 1
	for release in "${releases[@]}"; do
		[[ "$(find "${directory}" -maxdepth 1 -type f \
			-name "*_${release}_${branch}_*_minimal.img.xz" | wc -l)" -eq 1 ]] || return 1
		[[ "$(find "${directory}" -maxdepth 1 -type f \
			-name "*_${release}_${branch}_*_xfce_desktop.img.xz" | wc -l)" -eq 1 ]] || return 1
	done
	while IFS= read -r -d '' archive; do
		sha_file="${archive}.sha"
		[[ -f "${sha_file}" ]] || return 1
		(
			cd "${directory}"
			sha256sum -c "$(basename "${sha_file}")" >/dev/null
		) || return 1
		xz -t "${archive}" || return 1
	done < <(find "${directory}" -maxdepth 1 -type f -name '*.img.xz' -print0 | sort -z)
}

write_release_note() {
	local path="$1" board="$2" branch="$3" releases_csv="$4" image_count="$5"
	cat > "${path}" <<EOF
# ${board} 最新內部候選映像

建置來源提交：\`${source_commit}\`

核心分支：\`${branch}\`

發行版：\`${releases_csv}\`

本目錄包含 ${image_count} 個映像，分別為精簡命令列版與 XFCE 桌面版。所有映像均由上述來源提交完整執行 \`compile.sh build\`，並通過原始映像唯讀內容檢查、SHA-256 與 XZ 串流完整性檢查。

本結果是軟體候選，不代表未執行的實機、全介面、長時間壓力、量產或再散布門檻已通過。燒錄前請再次核對同名 \`.img.xz.sha\`。
EOF
}

cleanup_local_board_outputs() {
	local board="$1" token
	token="${board^}"
	find "${repo_dir}/output/images" -maxdepth 1 -type f \
		\( -name "Armbian-*_${token}_*" -o -name "Bananapi-Armbian_*_${token}_*" \) \
		-delete
}

archive_r1_eos() {
	local source="${release_root}/bpi-r1" destination="${archive_root}/bpi-r1-eos"
	local archive sha_file
	[[ -d "${source}" ]] || return 0
	[[ ! -e "${destination}" ]] || {
		printf 'R1 歷史封存目的地已存在，拒絕猜測：%s\n' "${destination}" >&2
		return 1
	}
	[[ "$(find "${source}" -maxdepth 1 -type f -name '*.img.xz' | wc -l)" -eq 10 ]] || {
		printf 'R1 歷史映像數量不是 10，拒絕移動。\n' >&2
		return 1
	}
	while IFS= read -r -d '' archive; do
		sha_file="${archive}.sha"
		[[ -f "${sha_file}" ]] || return 1
		(
			cd "${source}"
			sha256sum -c "$(basename "${sha_file}")" >/dev/null
		) || return 1
		xz -t "${archive}" || return 1
	done < <(find "${source}" -maxdepth 1 -type f -name '*.img.xz' -print0 | sort -z)
	mkdir -p "${archive_root}"
	mv "${source}" "${destination}"
	printf 'source=%s\ndestination=%s\nstatus=archived\n' \
		"${source}" "${destination}" > "${state_root}/boards/bpi-r1-eos.complete"
	printf 'R1 已驗證並移至歷史封存區：%s\n' "${destination}"
}

rebuild_board() {
	local folder="$1" board="$2" branch="$3" releases_csv="$4"
	local stage destination previous release profile expected old_english
	local -a releases

	find_board_file "${board}" >/dev/null || {
		printf '找不到板卡設定：%s\n' "${board}" >&2
		return 1
	}
	IFS=, read -r -a releases <<< "${releases_csv}"
	expected=$(( ${#releases[@]} * 2 ))
	stage="${release_root}/.staging-${folder}-${source_short}"
	destination="${release_root}/${folder}"
	previous="${release_root}/.previous-${folder}-${source_short}"
	mkdir -p "${stage}"

	for release in "${releases[@]}"; do
		for profile in minimal xfce; do
			build_item "${stage}" "${folder}" "${board}" "${branch}" "${release}" "${profile}" || return 1
		done
	done

	write_release_note "${stage}/Release-Notes-zh-TW.md" \
		"${board}" "${branch}" "${releases_csv}" "${expected}"
	old_english="${destination}/Release-Notes-English.md"
	if [[ -f "${old_english}" ]]; then
		cp -- "${old_english}" "${stage}/Release-Notes-English.md"
	fi
	verify_board_dir "${stage}" "${branch}" "${releases_csv}" || return 1

	[[ ! -e "${previous}" ]] || safe_remove_work_dir "${previous}" "${release_root}"
	if [[ -d "${destination}" ]]; then
		mv "${destination}" "${previous}"
	fi
	if ! mv "${stage}" "${destination}"; then
		[[ ! -d "${previous}" ]] || mv "${previous}" "${destination}"
		return 1
	fi
	if ! verify_board_dir "${destination}" "${branch}" "${releases_csv}"; then
		safe_remove_work_dir "${release_root}/.staging-failed-${folder}-${source_short}" "${release_root}" 2>/dev/null || true
		mv "${destination}" "${release_root}/.staging-failed-${folder}-${source_short}"
		[[ ! -d "${previous}" ]] || mv "${previous}" "${destination}"
		return 1
	fi
	[[ ! -d "${previous}" ]] || safe_remove_work_dir "${previous}" "${release_root}"
	cleanup_local_board_outputs "${board}"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'board=%s\nfolder=%s\n' "${board}" "${folder}"
		printf 'images=%s\nstatus=complete\n' "${expected}"
	} > "${state_root}/boards/${folder}.complete.partial"
	mv "${state_root}/boards/${folder}.complete.partial" "${state_root}/boards/${folder}.complete"
	printf '完成並替換：%s，共 %s 個最新映像。\n' "${board}" "${expected}"
}

if [[ "${dry_run}" == yes ]]; then
	total=0
	while IFS=$'\t' read -r folder board branch releases_csv; do
		[[ "${folder}" == folder ]] && continue
		[[ -z "${selected_board}" || "${selected_board}" == "${board}" || "${selected_board}" == "${folder}" ]] || continue
		IFS=, read -r -a releases <<< "${releases_csv}"
		printf '%s\t%s\t%s\t%s\t%s\n' "${folder}" "${board}" "${branch}" "${releases_csv}" "$(( ${#releases[@]} * 2 ))"
		total=$((total + ${#releases[@]} * 2))
	done < "${matrix_file}"
	printf '預定建置映像總數：%s\n' "${total}"
	exit 0
fi

assert_clean_source
sudo -n true
mkdir -p "${release_root}" "${state_root}/boards" "${state_root}/items" \
	"${state_root}/logs" "${state_root}/markers"
exec 9> "${release_root}/.latest-rebuild.lock"
flock -n 9 || {
	printf '另一個最新矩陣重建或替換程序正在執行。\n' >&2
	exit 1
}

if [[ -z "${selected_board}" ]]; then
	archive_r1_eos
fi

summary="${state_root}/summary.tsv"
[[ -f "${summary}" ]] || printf 'folder\tboard\tbranch\tstatus\n' > "${summary}"
selected=0
failed=0
while IFS=$'\t' read -r folder board branch releases_csv; do
	[[ "${folder}" == folder ]] && continue
	[[ -z "${selected_board}" || "${selected_board}" == "${board}" || "${selected_board}" == "${folder}" ]] || continue
	selected=$((selected + 1))
	if [[ -f "${state_root}/boards/${folder}.complete" ]] && \
		verify_board_dir "${release_root}/${folder}" "${branch}" "${releases_csv}"; then
		printf '已完成並重驗：%s。\n' "${board}"
		printf '%s\t%s\t%s\tverified-existing\n' "${folder}" "${board}" "${branch}" >> "${summary}"
		continue
	fi
	if rebuild_board "${folder}" "${board}" "${branch}" "${releases_csv}"; then
		printf '%s\t%s\t%s\tcomplete\n' "${folder}" "${board}" "${branch}" >> "${summary}"
	else
		failed=$((failed + 1))
		printf '%s\t%s\t%s\tfailed\n' "${folder}" "${board}" "${branch}" >> "${summary}"
	fi
done < "${matrix_file}"

((selected > 0)) || {
	printf '沒有符合條件的板卡。\n' >&2
	exit 2
}
printf '本輪板卡：%s，失敗：%s，摘要：%s\n' "${selected}" "${failed}" "${summary}"
((failed == 0))
