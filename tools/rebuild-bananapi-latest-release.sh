#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
repo_dir="$(readlink -f -- "${repo_dir}")"
matrix_file="${MATRIX_FILE:-${repo_dir}/config/bananapi-latest-release-matrix.tsv}"
release_root="${RELEASE_ROOT:-/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08}"
source_commit="${SOURCE_COMMIT:-$(git -C "${repo_dir}" rev-parse HEAD)}"
bsp_base_commit="${BSP_BASE_COMMIT:-8893355b34efc97a1e7677c6541beb177ec014e1}"
source_short="${source_commit:0:12}"
state_root="${STATE_ROOT:-${repo_dir}/output/bananapi-latest-rebuild/${source_short}}"
archive_root="${ARCHIVE_ROOT:-$(dirname "$(dirname "${release_root}")")/archive/2026.08}"
xz_threads="${XZ_THREADS:-6}"
minimum_free_gib="${MINIMUM_FREE_GIB:-15}"
container_image="${ARMBIAN_CONTAINER_IMAGE:-ghcr.io/armbian/docker-armbian-build:armbian-debian-trixie-latest}"
source_remote_ref="${SOURCE_REMOTE_REF:-}"
selected_board=""
dry_run=no
allow_full_rebuild=no
matrix_sha256=""
userpatches_sha256=""
container_image_id=""
container_image_locked=""
build_context_sha256=""
expected_build_context_sha256="${EXPECTED_BUILD_CONTEXT_SHA256:-}"

usage() {
	cat <<'EOF'
用法：tools/rebuild-bananapi-latest-release.sh [選項]

依受版本控制矩陣逐板重建最新映像。每板新矩陣完整通過後，才以可回復交易替換中央舊矩陣。

選項：
  --board ID     只處理指定板卡識別碼或交付目錄名稱
  --dry-run      只列出矩陣與建置命令，不修改檔案
  --allow-full-rebuild
                 明確允許重建整個矩陣；一般補缺流程不得使用
  -h, --help     顯示本說明

可覆寫環境變數：
  REPO_DIR、MATRIX_FILE、RELEASE_ROOT、ARCHIVE_ROOT、SOURCE_COMMIT、BSP_BASE_COMMIT、STATE_ROOT、EXPECTED_BUILD_CONTEXT_SHA256、XZ_THREADS、MINIMUM_FREE_GIB
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
		--allow-full-rebuild)
			allow_full_rebuild=yes
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

required_commands=(
	awk basename cp date df docker find flock git grep head lsblk losetup mkdir
	mktemp mount mountpoint mv partprobe readlink rmdir rm sed sha256sum sleep
	sort stat sudo sync tee udevadm umount uname uuidgen wc xz
)
for command in "${required_commands[@]}"; do
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
	printf '建置工具提交不存在：%s\n' "${source_commit}" >&2
	exit 1
}
git -C "${repo_dir}" cat-file -e "${bsp_base_commit}^{commit}" || {
	printf 'BSP 整合基準提交不存在：%s\n' "${bsp_base_commit}" >&2
	exit 1
}
git -C "${repo_dir}" merge-base --is-ancestor "${bsp_base_commit}" "${source_commit}" || {
	printf '建置工具提交不包含 BSP 整合基準：%s\n' "${bsp_base_commit}" >&2
	exit 1
}

validate_matrix() {
	awk -F '\t' '
		NR == 1 {
			if ($0 != "folder\tboard\tbranch\treleases") exit 10
			next
		}
		NF != 4 { exit 11 }
		$1 !~ /^[a-z0-9-]+$/ || $2 !~ /^[a-z0-9-]+$/ ||
		$3 !~ /^(current|edge|legacy|vendor)$/ || $4 !~ /^[a-z0-9]+(,[a-z0-9]+)*$/ { exit 12 }
		seen_folder[$1]++ || seen_board[$2]++ { exit 13 }
		END { if (NR < 2) exit 14 }
	' "${matrix_file}" || {
		printf '建置矩陣欄位、字元、分支或唯一性檢查失敗：%s\n' "${matrix_file}" >&2
		return 1
	}
}

calculate_userpatches_hash() {
	local directory="${repo_dir}/userpatches" entry mode
	if [[ ! -d "${directory}" ]]; then
		printf 'none\n'
		return 0
	fi
	(
		cd "${directory}"
		while IFS= read -r -d '' entry; do
			mode="$(stat -c '%a' -- "${entry}")"
			if [[ -L "${entry}" ]]; then
				printf 'link\\0%s\\0%s\\0%s\\0' "${mode}" "${entry}" "$(readlink -- "${entry}")"
			elif [[ -f "${entry}" ]]; then
				printf 'file\\0%s\\0%s\\0%s\\0' "${mode}" "${entry}" \
					"$(sha256sum "${entry}" | awk '{ print $1 }')"
			elif [[ -d "${entry}" ]]; then
				printf 'directory\\0%s\\0%s\\0' "${mode}" "${entry}"
			else
			printf '不支援的 userpatches 項目：%s\n' "${entry}" >&2
				exit 1
			fi
		done < <(find . -mindepth 1 -print0 | LC_ALL=C sort -z)
	) | sha256sum | awk '{ print $1 }'
}

assert_pushed_source() {
	local branch
	if [[ -z "${source_remote_ref}" ]]; then
		branch="$(git -C "${repo_dir}" branch --show-current)" || return 1
		[[ -n "${branch}" ]] || {
			printf '來源目前不是具名分支，請設定 SOURCE_REMOTE_REF。\n' >&2
			return 1
		}
		source_remote_ref="origin/${branch}"
	fi
	git -C "${repo_dir}" show-ref --verify --quiet "refs/remotes/${source_remote_ref}" || {
		printf '找不到遠端追蹤分支：%s\n' "${source_remote_ref}" >&2
		return 1
	}
	git -C "${repo_dir}" merge-base --is-ancestor "${source_commit}" "${source_remote_ref}" || {
		printf '實際建置提交尚未推送到 %s：%s\n' "${source_remote_ref}" "${source_commit}" >&2
		return 1
	}
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
	local actual_commit actual_userpatches status
	actual_commit="$(git -C "${repo_dir}" rev-parse HEAD)" || return 1
	[[ "${actual_commit}" == "${source_commit}" ]] || {
		printf '建置工具提交已改變：預期 %s，實際 %s。\n' "${source_commit}" "${actual_commit}" >&2
		return 1
	}
	status="$(git -C "${repo_dir}" status --porcelain=v1 --untracked-files=normal)" || return 1
	[[ -z "${status}" ]] || {
		printf '來源工作樹或索引含未提交內容，拒絕建立映像。\n%s\n' "${status}" >&2
		return 1
	}
	actual_userpatches="$(calculate_userpatches_hash)" || return 1
	[[ "${actual_userpatches}" == "${userpatches_sha256}" ]] || {
		printf '未追蹤 userpatches 在建置期間改變，拒絕繼續。\n' >&2
		return 1
	}
}

check_free_space() {
	local available required
	available="$(df -PB1 "${release_root}" | awk 'NR == 2 { print $4 }')" || return 1
	required=$((minimum_free_gib * 1024 * 1024 * 1024))
	((available >= required)) || {
		printf '可用空間不足：目前 %s bytes，最低要求 %s GiB。\n' \
			"${available}" "${minimum_free_gib}" >&2
		return 1
	}
}

safe_remove_work_dir() {
	local path="$1" parent="$2" resolved_parent resolved_path
	resolved_parent="$(readlink -f -- "${parent}")" || return 1
	resolved_path="$(readlink -m -- "${path}")" || return 1
	[[ "${resolved_path}" == "${resolved_parent}"/.staging-* ||
		"${resolved_path}" == "${resolved_parent}"/.previous-* ||
		"${resolved_path}" == "${resolved_parent}"/.failed-* ]] || {
		printf '拒絕移除不安全路徑：%s\n' "${path}" >&2
		return 1
	}
	rm -rf --one-file-system -- "${path}"
}

verify_raw_image() (
	set -Eeuo pipefail
	local image="$1" board="$2" branch="$3" release="$4" profile="$5"
	local loop_device="" root_partition="" root_fstype="" boot_partition="" mount_dir=""
	local image_board="" image_release="" provenance=""

	cleanup() {
		local status=$?
		trap - EXIT INT TERM HUP
		set +e
		if [[ -n "${mount_dir}" ]] && mountpoint -q "${mount_dir}/boot"; then
			for _ in {1..20}; do
				sudo -n umount "${mount_dir}/boot" && break
				sleep 0.25
			done
			mountpoint -q "${mount_dir}/boot" && status=1
		fi
		if [[ -n "${mount_dir}" ]] && mountpoint -q "${mount_dir}"; then
			for _ in {1..20}; do
				sudo -n umount "${mount_dir}" && break
				sleep 0.25
			done
			mountpoint -q "${mount_dir}" && status=1
		fi
		if [[ -n "${loop_device}" ]] && ! mountpoint -q "${mount_dir}" &&
			! mountpoint -q "${mount_dir}/boot" &&
			sudo -n losetup "${loop_device}" >/dev/null 2>&1; then
			sudo -n losetup -d "${loop_device}" || status=1
		fi
		[[ -z "${mount_dir}" ]] || rmdir "${mount_dir}" 2>/dev/null || true
		exit "${status}"
	}
	trap cleanup EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM
	trap 'exit 129' HUP

	loop_device="$(sudo -n losetup --find --show --partscan --read-only "${image}")"
	mount_dir="$(mktemp -d "${state_root}/mount.XXXXXX")"
	sudo -n partprobe "${loop_device}" || true
	sudo -n udevadm settle
	for _ in {1..20}; do
		root_partition="$(lsblk -lnpo NAME,FSTYPE "${loop_device}" |
			awk '$2 == "ext4" || $2 == "btrfs" { print $1; exit }')"
		[[ -n "${root_partition}" ]] && break
		sleep 0.2
	done
	root_fstype="$(lsblk -lnpo NAME,FSTYPE "${loop_device}" |
		awk '$2 == "ext4" || $2 == "btrfs" { print $2; exit }')"
	[[ -n "${root_partition}" ]] || {
		printf '映像沒有可辨識的 ext4 或 btrfs 根分割區：%s\n' "${image}" >&2
		exit 1
	}
	if [[ "${root_fstype}" == ext4 ]]; then
		sudo -n mount -o ro,noload,nosuid,nodev,noexec "${root_partition}" "${mount_dir}"
	else
		sudo -n mount -o ro,nosuid,nodev,noexec "${root_partition}" "${mount_dir}"
	fi
	[[ -f "${mount_dir}/etc/armbian-release" && -d "${mount_dir}/boot" ]] || exit 1
	if [[ -z "$(find "${mount_dir}/boot" -mindepth 1 -maxdepth 2 -print -quit)" ]]; then
		boot_partition="$(lsblk -lnpo NAME,FSTYPE "${loop_device}" |
			awk '$2 == "vfat" { print $1; exit }')"
		[[ -n "${boot_partition}" ]] || {
			printf '映像的根檔案系統 /boot 為空，且沒有可辨識的 FAT 開機分割區：%s\n' \
				"${image}" >&2
			exit 1
		}
		sudo -n mount -o ro,nosuid,nodev,noexec "${boot_partition}" "${mount_dir}/boot"
	fi
	[[ -n "$(find "${mount_dir}/boot" -mindepth 1 -maxdepth 2 -print -quit)" ]] || {
		printf '映像的開機分割區沒有內容：%s\n' "${image}" >&2
		exit 1
	}
	image_board="$(sed -n -E "s/^BOARD=['\"]?([^'\"]+)['\"]?$/\1/p" \
		"${mount_dir}/etc/armbian-release" | head -n 1)"
	[[ "${image_board}" == "${board}" ]] || {
		printf '映像內 BOARD 與目標不一致：預期 %s，實際 %s。\n' "${board}" "${image_board}" >&2
		exit 1
	}
	image_release="$(sed -n -E 's/^VERSION_CODENAME="?([^"]+)"?$/\1/p' \
		"${mount_dir}/etc/os-release" | head -n 1)"
	[[ "${image_release}" == "${release}" ]] || {
		printf '映像內發行版與目標不一致：預期 %s，實際 %s。\n' "${release}" "${image_release}" >&2
		exit 1
	}
	provenance="${mount_dir}/etc/bananapi-build-provenance"
	[[ -f "${provenance}" ]] || {
		printf '映像缺少 Banana Pi 建置來源證據：%s\n' "${image}" >&2
		exit 1
	}
	for expected in \
		"bsp_base_commit=${bsp_base_commit}" \
		"source_commit=${source_commit}" \
		"matrix_sha256=${matrix_sha256}" \
		"userpatches_sha256=${userpatches_sha256}" \
		"build_context_sha256=${build_context_sha256}" \
		"board=${board}" "branch=${branch}" "release=${release}" "profile=${profile}"; do
		grep -Fqx -- "${expected}" "${provenance}" || {
			printf '映像建置來源證據不符：%s 缺少 %s。\n' "${image}" "${expected}" >&2
			exit 1
		}
	done
	awk -v prefix="linux-image-${branch}-" '
		$1 == "Package:" { matched = index($2, prefix) == 1 }
		matched && $0 == "Status: install ok installed" { found = 1 }
		END { exit(found ? 0 : 1) }
	' "${mount_dir}/var/lib/dpkg/status" || {
		printf '映像缺少目標核心分支套件：%s。\n' "${branch}" >&2
		exit 1
	}
	if [[ "${profile}" == xfce ]]; then
		[[ -f "${mount_dir}/usr/share/xsessions/xfce.desktop" ]] || {
			printf 'XFCE 映像缺少 xfce.desktop。\n' >&2
			exit 1
		}
		awk '
			$1 == "Package:" { matched = $2 == "xfce4-session" }
			matched && $0 == "Status: install ok installed" { found = 1 }
			END { exit(found ? 0 : 1) }
		' "${mount_dir}/var/lib/dpkg/status" || {
			printf 'XFCE 映像缺少 xfce4-session。\n' >&2
			exit 1
		}
	else
		[[ ! -e "${mount_dir}/usr/share/xsessions/xfce.desktop" ]] || {
			printf '精簡映像意外包含 XFCE 工作階段。\n' >&2
			exit 1
		}
	fi
)

item_marker_path() {
	local folder="$1" release="$2" profile="$3"
	printf '%s/items/%s-%s-%s.complete\n' "${state_root}" "${folder}" "${release}" "${profile}"
}

read_marker_value() {
	local marker="$1" key="$2"
	sed -n "s/^${key}=//p" "${marker}" | head -n 1
}

verify_sha_sidecar() {
	local archive="$1" sha_file="$2" expected_digest="$3"
	local sidecar_digest sidecar_name extra actual
	[[ -f "${archive}" && -f "${sha_file}" ]] || return 1
	[[ "$(wc -l < "${sha_file}")" -eq 1 ]] || return 1
	read -r sidecar_digest sidecar_name extra < "${sha_file}" || return 1
	[[ -z "${extra:-}" && "${sidecar_digest}" == "${expected_digest}" &&
		"${sidecar_name}" == "$(basename "${archive}")" ]] || return 1
	actual="$(sha256sum "${archive}" | awk '{ print $1 }')" || return 1
	[[ "${actual}" == "${expected_digest}" ]]
}

item_is_complete() {
	local stage="$1" folder="$2" board="$3" branch="$4" release="$5" profile="$6"
	local marker archive digest sha_file log_file log_digest resolved_log
	local framework_log framework_log_digest resolved_framework_log
	marker="$(item_marker_path "${folder}" "${release}" "${profile}")"
	[[ -f "${marker}" ]] || return 1
	[[ "$(read_marker_value "${marker}" source_commit)" == "${source_commit}" ]] || return 1
	[[ "$(read_marker_value "${marker}" bsp_base_commit)" == "${bsp_base_commit}" ]] || return 1
	[[ "$(read_marker_value "${marker}" matrix_sha256)" == "${matrix_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" userpatches_sha256)" == "${userpatches_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" build_context_sha256)" == "${build_context_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" folder)" == "${folder}" ]] || return 1
	[[ "$(read_marker_value "${marker}" board)" == "${board}" ]] || return 1
	[[ "$(read_marker_value "${marker}" branch)" == "${branch}" ]] || return 1
	[[ "$(read_marker_value "${marker}" release)" == "${release}" ]] || return 1
	[[ "$(read_marker_value "${marker}" profile)" == "${profile}" ]] || return 1
	archive="$(read_marker_value "${marker}" archive)"
	digest="$(read_marker_value "${marker}" sha256)"
	sha_file="${stage}/${archive}.sha"
	[[ -n "${archive}" && "${archive}" == "$(basename "${archive}")" &&
		"${digest}" =~ ^[0-9a-f]{64}$ && -f "${stage}/${archive}" && -f "${sha_file}" ]] || return 1
	verify_sha_sidecar "${stage}/${archive}" "${sha_file}" "${digest}" || return 1
	xz -t "${stage}/${archive}" || return 1
	log_file="$(read_marker_value "${marker}" log)"
	log_digest="$(read_marker_value "${marker}" log_sha256)"
	resolved_log="$(readlink -m -- "${log_file}")" || return 1
	[[ "${resolved_log}" == "$(readlink -f -- "${state_root}/logs")"/* &&
		"${log_digest}" =~ ^[0-9a-f]{64}$ && -s "${log_file}" ]] || return 1
	[[ "$(sha256sum "${log_file}" | awk '{ print $1 }')" == "${log_digest}" ]] || return 1
	framework_log="$(read_marker_value "${marker}" framework_log)"
	framework_log_digest="$(read_marker_value "${marker}" framework_log_sha256)"
	if [[ -n "${framework_log_digest}" ]]; then
		resolved_framework_log="$(readlink -m -- "${framework_log}")" || return 1
		[[ "${resolved_framework_log}" == "$(readlink -f -- "${state_root}/framework-logs")"/* &&
			"${framework_log_digest}" =~ ^[0-9a-f]{64}$ && -s "${framework_log}" ]] || return 1
		[[ "$(sha256sum "${framework_log}" | awk '{ print $1 }')" == "${framework_log_digest}" ]] || return 1
	fi
	return 0
}

prune_stage_item() {
	local stage="$1" folder="$2" release="$3" profile="$4"
	local marker archive suffix candidate
	marker="$(item_marker_path "${folder}" "${release}" "${profile}")"
	archive="$(read_marker_value "${marker}" archive)"
	if [[ "${profile}" == minimal ]]; then suffix=minimal; else suffix=xfce_desktop; fi
	while IFS= read -r -d '' candidate; do
		[[ "$(basename "${candidate}")" == "${archive}" ||
			"$(basename "${candidate}")" == "${archive}.sha" ]] && continue
		rm -f -- "${candidate}" || return 1
	done < <(find "${stage}" -maxdepth 1 -type f \
		-name "*_${release}_*_${suffix}.img.xz*" -print0)
}

build_item() {
	local stage="$1" folder="$2" board="$3" branch="$4" release="$5" profile="$6"
	local marker log_file board_token suffix status tee_status image archive digest forced=no
	local item_marker partial_archive build_uuid framework_log expected_name log_digest
	local preserved_framework_log framework_log_digest
	local -a args expected_names pipeline_status

	if item_is_complete "${stage}" "${folder}" "${board}" "${branch}" "${release}" "${profile}"; then
		prune_stage_item "${stage}" "${folder}" "${release}" "${profile}" || return 1
		printf '沿用本次來源與輸入雜湊已完成項目：%s %s %s\n' "${board}" "${release}" "${profile}"
		return 0
	fi

	check_free_space || return 1
	assert_clean_source || return 1
	marker="${state_root}/markers/${folder}-${release}-${profile}-$RANDOM.marker"
	: > "${marker}" || return 1
	build_uuid="$(uuidgen)"
	log_file="${state_root}/logs/${folder}-${release}-${profile}-${build_uuid}.log"
	framework_log="${repo_dir}/output/logs/log-build-${build_uuid}.log"
	preserved_framework_log="${state_root}/framework-logs/log-build-${build_uuid}.log"
	args=(
		build "BOARD=${board}" "BRANCH=${branch}" "RELEASE=${release}"
		KERNEL_CONFIGURE=no EXPERT=yes SHARE_LOG=no DOWNLOAD_MIRROR=bfsu
		"COMPRESS_OUTPUTIMAGE=sha,img" ALLOW_HEADLESS_DESKTOP=yes
		"ARMBIAN_BUILD_UUID=${build_uuid}"
		ENABLE_EXTENSIONS=bananapi-build-provenance
		BPI_RELEASE_PROVENANCE_REQUIRED=yes
		"BPI_RELEASE_BSP_BASE_COMMIT=${bsp_base_commit}"
		"BPI_RELEASE_SOURCE_COMMIT=${source_commit}"
		"BPI_RELEASE_MATRIX_SHA256=${matrix_sha256}"
		"BPI_RELEASE_USERPATCHES_SHA256=${userpatches_sha256}"
		"BPI_RELEASE_BUILD_CONTEXT_SHA256=${build_context_sha256}"
		"BPI_RELEASE_PROFILE=${profile}"
		"DOCKER_ARMBIAN_BASE_IMAGE_LOCKED=${container_image_locked}"
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
			rm -f -- "${marker}"
			return 2
			;;
	esac
	if [[ "${release}" == trixie && "${profile}" == minimal ]]; then
		args+=(ARTIFACT_IGNORE_CACHE=yes "CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust")
		forced=yes
	fi

	printf '完整建置：%s %s %s，強制清理與重建元件=%s。\n' \
		"${board}" "${release}" "${profile}" "${forced}"
	(
		cd "${repo_dir}" || exit 1
		./compile.sh "${args[@]}"
	) 2>&1 | tee "${log_file}"
	pipeline_status=("${PIPESTATUS[@]}")
	status=${pipeline_status[0]}
	tee_status=${pipeline_status[1]}
	if ((status != 0 || tee_status != 0)) || [[ ! -s "${log_file}" ]]; then
		rm -f -- "${marker}"
		printf '建置或日誌寫入失敗：%s %s %s，建置狀態 %s，日誌狀態 %s。\n' \
			"${board}" "${release}" "${profile}" "${status}" "${tee_status}" >&2
		((status != 0)) && return "${status}"
		return 74
	fi
	assert_clean_source || {
		rm -f -- "${marker}"
		return 1
	}

	[[ -s "${framework_log}" ]] || {
		printf '找不到本次 UUID 的 Armbian 內部建置日誌：%s\n' "${framework_log}" >&2
		rm -f -- "${marker}"
		return 1
	}
	cp -- "${framework_log}" "${preserved_framework_log}.partial" || return 1
	mv -T "${preserved_framework_log}.partial" "${preserved_framework_log}" || return 1
	framework_log_digest="$(sha256sum "${preserved_framework_log}" | awk '{ print $1 }')" || return 1
	mapfile -t expected_names < <(
		sed -n -E "s#.*image-${build_uuid}/([^[:space:]']+\\.img).*#\\1#p" "${framework_log}" | sort -u
	)
	rm -f -- "${marker}"
	if [[ ${#expected_names[@]} -ne 1 ]]; then
		printf '本次 UUID 的精確映像路徑數量錯誤：%s，找到 %s 個。\n' \
			"${build_uuid}" "${#expected_names[@]}" >&2
		return 1
	fi
	expected_name="${expected_names[0]}"
	board_token="${board^}"
	[[ "${expected_name}" == Armbian-*_${board_token}_${release}_${branch}_*_${suffix}.img ||
		"${expected_name}" == Bananapi-Armbian_*_${board_token}_${release}_${branch}_*_${suffix}.img ]] || {
		printf '本次 UUID 的映像檔名不符合矩陣：%s\n' "${expected_name}" >&2
		return 1
	}
	image="${repo_dir}/output/images/${expected_name}"
	[[ -f "${image}" ]] || {
		printf '本次 UUID 的映像不存在：%s\n' "${image}" >&2
		return 1
	}
	verify_raw_image "${image}" "${board}" "${branch}" "${release}" "${profile}" || {
		printf '原始映像唯讀內容檢查失敗：%s\n' "${image}" >&2
		return 1
	}
	archive="${stage}/$(basename "${image}").xz"
	partial_archive="${archive}.partial-${build_uuid}"
	rm -f -- "${partial_archive}" || return 1
	if ! xz -T"${xz_threads}" -6 -c "${image}" > "${partial_archive}"; then
		rm -f -- "${partial_archive}"
		return 1
	fi
	xz -t "${partial_archive}" || {
		rm -f -- "${partial_archive}"
		return 1
	}
	mv -T "${partial_archive}" "${archive}" || return 1
	digest="$(sha256sum "${archive}" | awk '{ print $1 }')" || return 1
	printf '%s  %s\n' "${digest}" "$(basename "${archive}")" > "${archive}.sha" || return 1
	verify_sha_sidecar "${archive}" "${archive}.sha" "${digest}" || return 1
	sync -f "${archive}" || return 1
	rm -f -- "${image}" "${image}.sha" "${image}.txt" || return 1
	log_digest="$(sha256sum "${log_file}" | awk '{ print $1 }')" || return 1
	item_marker="$(item_marker_path "${folder}" "${release}" "${profile}")"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'bsp_base_commit=%s\n' "${bsp_base_commit}"
		printf 'matrix_sha256=%s\n' "${matrix_sha256}"
		printf 'userpatches_sha256=%s\n' "${userpatches_sha256}"
		printf 'build_context_sha256=%s\n' "${build_context_sha256}"
		printf 'folder=%s\nboard=%s\nbranch=%s\n' "${folder}" "${board}" "${branch}"
		printf 'release=%s\nprofile=%s\n' "${release}" "${profile}"
		printf 'archive=%s\n' "$(basename "${archive}")"
		printf 'sha256=%s\n' "${digest}"
		printf 'fresh_artifacts=%s\n' "${forced}"
		printf 'build_uuid=%s\n' "${build_uuid}"
		printf 'log=%s\n' "${log_file}"
		printf 'log_sha256=%s\n' "${log_digest}"
		printf 'framework_log=%s\n' "${preserved_framework_log}"
		printf 'framework_log_sha256=%s\n' "${framework_log_digest}"
	} > "${item_marker}.partial" || return 1
	mv -T "${item_marker}.partial" "${item_marker}" || return 1
	sync -f "${item_marker}" || return 1
	prune_stage_item "${stage}" "${folder}" "${release}" "${profile}"
}

verify_board_dir() {
	local directory="$1" folder="$2" board="$3" branch="$4" releases_csv="$5"
	local release profile suffix expected archive marker board_token count marker_digest
	local -a releases
	[[ -d "${directory}" && ! -L "${directory}" ]] || return 1
	IFS=, read -r -a releases <<< "${releases_csv}"
	expected=$(( ${#releases[@]} * 2 ))
	count="$(find "${directory}" -maxdepth 1 -type f -name '*.img.xz' | wc -l)" || return 1
	[[ "${count}" -eq "${expected}" ]] || {
		printf '矩陣數量錯誤：%s，實際 %s，預期 %s。\n' \
			"${directory}" "${count}" "${expected}" >&2
		return 1
	}
	[[ "$(find "${directory}" -maxdepth 1 -type f -name '*.img.xz.sha' | wc -l)" -eq "${expected}" ]] || return 1
	[[ -z "$(find "${directory}" -maxdepth 1 -type f -name '*.img' -print -quit)" ]] || return 1
	board_token="${board^}"
	for release in "${releases[@]}"; do
		for profile in minimal xfce; do
			if [[ "${profile}" == minimal ]]; then suffix=minimal; else suffix=xfce_desktop; fi
			marker="$(item_marker_path "${folder}" "${release}" "${profile}")"
			item_is_complete "${directory}" "${folder}" "${board}" "${branch}" "${release}" "${profile}" || return 1
			archive="$(read_marker_value "${marker}" archive)"
			[[ "${archive}" == Armbian-*_${board_token}_${release}_${branch}_*_${suffix}.img.xz ||
				"${archive}" == Bananapi-Armbian_*_${board_token}_${release}_${branch}_*_${suffix}.img.xz ]] || return 1
			marker_digest="$(read_marker_value "${marker}" sha256)"
			[[ "$(sha256sum "${directory}/${archive}" | awk '{ print $1 }')" == "${marker_digest}" ]] || return 1
		done
	done
}

write_release_note() {
	local path="$1" board="$2" branch="$3" releases_csv="$4" image_count="$5"
	cat > "${path}" <<EOF
# ${board} 最新內部候選映像

BSP 整合基準提交：\`${bsp_base_commit}\`

建置工具與最終來源提交：\`${source_commit}\`

建置矩陣 SHA-256：\`${matrix_sha256}\`

核心分支：\`${branch}\`

發行版：\`${releases_csv}\`

本目錄包含 ${image_count} 個映像，分別為精簡命令列版與 XFCE 桌面版。所有映像均由上述最終來源提交執行 \`compile.sh build\`；第一個 Trixie 精簡映像另強制清理並重建 U-Boot、Kernel、ATF 與 Crust 等實際適用元件。同板後續映像只可沿用本輪已驗證的元件快取。

每個映像均通過原始映像唯讀內容與板型檢查、SHA-256 及 XZ 串流完整性檢查。這是軟體候選結果，不代表未執行的實機、全介面、長時間壓力、量產或再散布門檻已通過。燒錄前請再次核對同名 \`.img.xz.sha\`。
EOF
}

transaction_path() {
	local folder="$1"
	printf '%s/transactions/%s.state\n' "${state_root}" "${folder}"
}

write_transaction() {
	local folder="$1" phase="$2" had_previous="$3" path
	path="$(transaction_path "${folder}")"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'bsp_base_commit=%s\n' "${bsp_base_commit}"
		printf 'matrix_sha256=%s\n' "${matrix_sha256}"
		printf 'userpatches_sha256=%s\n' "${userpatches_sha256}"
		printf 'build_context_sha256=%s\n' "${build_context_sha256}"
		printf 'folder=%s\nphase=%s\nhad_previous=%s\n' "${folder}" "${phase}" "${had_previous}"
	} > "${path}.partial" || return 1
	mv -T "${path}.partial" "${path}" || return 1
	sync -f "${path}" || return 1
}

transaction_is_valid() {
	local folder="$1" path phase had_previous
	path="$(transaction_path "${folder}")"
	[[ -f "${path}" && ! -L "${path}" ]] || return 1
	[[ "$(read_marker_value "${path}" source_commit)" == "${source_commit}" ]] || return 1
	[[ "$(read_marker_value "${path}" bsp_base_commit)" == "${bsp_base_commit}" ]] || return 1
	[[ "$(read_marker_value "${path}" matrix_sha256)" == "${matrix_sha256}" ]] || return 1
	[[ "$(read_marker_value "${path}" userpatches_sha256)" == "${userpatches_sha256}" ]] || return 1
	[[ "$(read_marker_value "${path}" build_context_sha256)" == "${build_context_sha256}" ]] || return 1
	[[ "$(read_marker_value "${path}" folder)" == "${folder}" ]] || return 1
	phase="$(read_marker_value "${path}" phase)"
	had_previous="$(read_marker_value "${path}" had_previous)"
	[[ "${phase}" =~ ^(prepared|old-moved|new-installed|verified|committed)$ ]] || return 1
	[[ "${had_previous}" == yes || "${had_previous}" == no ]]
}

write_board_complete() {
	local folder="$1" board="$2" branch="$3" expected="$4" path
	path="${state_root}/boards/${folder}.complete"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'bsp_base_commit=%s\n' "${bsp_base_commit}"
		printf 'matrix_sha256=%s\n' "${matrix_sha256}"
		printf 'userpatches_sha256=%s\n' "${userpatches_sha256}"
		printf 'build_context_sha256=%s\n' "${build_context_sha256}"
		printf 'board=%s\nfolder=%s\nbranch=%s\n' "${board}" "${folder}" "${branch}"
		printf 'images=%s\nstatus=complete\n' "${expected}"
	} > "${path}.partial" || return 1
	mv -T "${path}.partial" "${path}" || return 1
	sync -f "${path}" || return 1
}

board_is_complete() {
	local folder="$1" board="$2" branch="$3" releases_csv="$4" expected="$5" marker
	marker="${state_root}/boards/${folder}.complete"
	[[ ! -e "${release_root}/.previous-${folder}-${source_short}" ]] || return 1
	[[ ! -f "$(transaction_path "${folder}")" ]] || return 1
	[[ -f "${marker}" ]] || return 1
	[[ "$(read_marker_value "${marker}" source_commit)" == "${source_commit}" ]] || return 1
	[[ "$(read_marker_value "${marker}" bsp_base_commit)" == "${bsp_base_commit}" ]] || return 1
	[[ "$(read_marker_value "${marker}" matrix_sha256)" == "${matrix_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" userpatches_sha256)" == "${userpatches_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" build_context_sha256)" == "${build_context_sha256}" ]] || return 1
	[[ "$(read_marker_value "${marker}" folder)" == "${folder}" ]] || return 1
	[[ "$(read_marker_value "${marker}" board)" == "${board}" ]] || return 1
	[[ "$(read_marker_value "${marker}" branch)" == "${branch}" ]] || return 1
	[[ "$(read_marker_value "${marker}" images)" == "${expected}" ]] || return 1
	[[ "$(read_marker_value "${marker}" status)" == complete ]] || return 1
	verify_board_dir "${release_root}/${folder}" "${folder}" "${board}" "${branch}" "${releases_csv}"
}

recover_board_transaction() {
	local folder="$1" board="$2" branch="$3" releases_csv="$4" expected="$5"
	local destination previous failed transaction stage phase had_previous
	destination="${release_root}/${folder}"
	previous="${release_root}/.previous-${folder}-${source_short}"
	failed="${release_root}/.failed-${folder}-${source_short}"
	stage="${release_root}/.staging-${folder}-${source_short}"
	transaction="$(transaction_path "${folder}")"

	if [[ -d "${previous}" ]]; then
		[[ ! -L "${previous}" ]] || {
			printf '回復目錄不可為符號連結：%s\n' "${previous}" >&2
			return 1
		}
		transaction_is_valid "${folder}" || {
			printf '回復目錄存在，但交易狀態缺少或無效；保留現場並停止：%s\n' "${folder}" >&2
			return 1
		}
		had_previous="$(read_marker_value "${transaction}" had_previous)"
		[[ "${had_previous}" == yes ]] || {
			printf '回復目錄與交易狀態矛盾；保留現場並停止：%s\n' "${folder}" >&2
			return 1
		}
		if [[ -d "${destination}" ]] && verify_board_dir "${destination}" "${folder}" "${board}" "${branch}" "${releases_csv}"; then
			write_board_complete "${folder}" "${board}" "${branch}" "${expected}" || return 1
			safe_remove_work_dir "${previous}" "${release_root}" || return 1
			rm -f -- "${transaction}" || return 1
			printf '完成上次中斷後的新矩陣提交：%s。\n' "${board}"
			return 0
		fi
		if [[ -e "${destination}" ]]; then
			[[ ! -e "${failed}" ]] || safe_remove_work_dir "${failed}" "${release_root}" || return 1
			mv -T "${destination}" "${failed}" || return 1
		fi
		mv -T "${previous}" "${destination}" || {
			printf '無法從回復目錄恢復正式矩陣：%s\n' "${folder}" >&2
			return 1
		}
		[[ ! -e "${failed}" ]] || safe_remove_work_dir "${failed}" "${release_root}" || return 1
		rm -f -- "${transaction}" || return 1
		printf '已從中斷交易恢復舊矩陣：%s。\n' "${board}"
		return 0
	fi

	if [[ -f "${transaction}" ]]; then
		transaction_is_valid "${folder}" || {
			printf '交易狀態無效；保留現場並停止：%s\n' "${folder}" >&2
			return 1
		}
		phase="$(read_marker_value "${transaction}" phase)"
		had_previous="$(read_marker_value "${transaction}" had_previous)"
		[[ -e "${destination}" ]] || {
			printf '交易狀態存在，但正式與回復目錄都不存在；拒絕自動猜測：%s\n' "${folder}" >&2
			return 1
		}
		if [[ "${had_previous}" == yes ]]; then
			[[ "${phase}" == prepared ]] || {
				printf '交易宣告有舊矩陣，但回復目錄遺失；保留現場並停止：%s\n' "${folder}" >&2
				return 1
			}
			rm -f -- "${transaction}" || return 1
			return 0
		fi
		if verify_board_dir "${destination}" "${folder}" "${board}" "${branch}" "${releases_csv}"; then
			write_board_complete "${folder}" "${board}" "${branch}" "${expected}" || return 1
			rm -f -- "${transaction}" || return 1
			return 0
		fi
		if [[ "${phase}" =~ ^(new-installed|verified|committed)$ && ! -e "${stage}" ]]; then
			mv -T "${destination}" "${stage}" || return 1
		fi
		rm -f -- "${transaction}" || return 1
	fi
}

rollback_install() {
	local folder="$1" stage="$2" destination="$3" previous="$4" had_previous="$5"
	local failed="${release_root}/.failed-${folder}-${source_short}"
	if [[ -e "${destination}" ]]; then
		[[ ! -e "${failed}" ]] || safe_remove_work_dir "${failed}" "${release_root}" || return 1
		mv -T "${destination}" "${failed}" || return 1
	fi
	if [[ "${had_previous}" == yes ]]; then
		mv -T "${previous}" "${destination}" || return 1
	fi
	if [[ -e "${failed}" ]]; then
		if [[ ! -e "${stage}" ]]; then
			mv -T "${failed}" "${stage}" || return 1
		else
			safe_remove_work_dir "${failed}" "${release_root}" || return 1
		fi
	fi
	rm -f -- "$(transaction_path "${folder}")" || return 1
}

install_board_transaction() {
	local folder="$1" board="$2" branch="$3" releases_csv="$4" expected="$5"
	local stage="$6" destination previous had_previous=no
	destination="${release_root}/${folder}"
	previous="${release_root}/.previous-${folder}-${source_short}"
	[[ ! -e "${previous}" ]] || {
		printf '替換前仍存在未處理回復目錄：%s\n' "${previous}" >&2
		return 1
	}
	write_transaction "${folder}" prepared no || return 1
	if [[ -d "${destination}" ]]; then
		had_previous=yes
		write_transaction "${folder}" prepared yes || return 1
		mv -T "${destination}" "${previous}" || return 1
		write_transaction "${folder}" old-moved yes || {
			mv -T "${previous}" "${destination}" || true
			return 1
		}
	fi
	if ! mv -T "${stage}" "${destination}"; then
		if [[ "${had_previous}" == yes ]]; then
			mv -T "${previous}" "${destination}" || true
		fi
		return 1
	fi
	write_transaction "${folder}" new-installed "${had_previous}" || {
		rollback_install "${folder}" "${stage}" "${destination}" "${previous}" "${had_previous}" || true
		return 1
	}
	if ! verify_board_dir "${destination}" "${folder}" "${board}" "${branch}" "${releases_csv}"; then
		rollback_install "${folder}" "${stage}" "${destination}" "${previous}" "${had_previous}" || true
		return 1
	fi
	write_transaction "${folder}" verified "${had_previous}" || {
		rollback_install "${folder}" "${stage}" "${destination}" "${previous}" "${had_previous}" || true
		return 1
	}
	write_board_complete "${folder}" "${board}" "${branch}" "${expected}" || {
		rollback_install "${folder}" "${stage}" "${destination}" "${previous}" "${had_previous}" || true
		return 1
	}
	write_transaction "${folder}" committed "${had_previous}" || return 1
	sync -f "${destination}" || return 1
	if [[ "${had_previous}" == yes ]]; then
		safe_remove_work_dir "${previous}" "${release_root}" || return 1
	fi
	rm -f -- "$(transaction_path "${folder}")" || return 1
}

archive_r1_eos() {
	local source="${release_root}/bpi-r1" destination="${archive_root}/bpi-r1-eos"
	local archive sha_file digest
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
		digest="$(sha256sum "${archive}" | awk '{ print $1 }')" || return 1
		verify_sha_sidecar "${archive}" "${sha_file}" "${digest}" || return 1
		xz -t "${archive}" || return 1
	done < <(find "${source}" -maxdepth 1 -type f -name '*.img.xz' -print0 | sort -z)
	mkdir -p "${archive_root}" || return 1
	[[ "$(stat -c '%d' "${source}")" == "$(stat -c '%d' "${archive_root}")" ]] || {
		printf 'R1 封存來源與目的地不在同一檔案系統，拒絕非交易式搬移。\n' >&2
		return 1
	}
	mv -T "${source}" "${destination}" || return 1
	printf 'source=%s\ndestination=%s\nstatus=archived\n' \
		"${source}" "${destination}" > "${state_root}/boards/bpi-r1-eos.complete" || return 1
	printf 'R1 已驗證並移至歷史封存區：%s\n' "${destination}"
}

verify_full_release_matrix() {
	local rows=0 images=0 folder board branch releases_csv expected
	local default_matrix="${repo_dir}/config/bananapi-latest-release-matrix.tsv"
	local -a releases
	[[ "$(readlink -f -- "${matrix_file}")" == "$(readlink -f -- "${default_matrix}")" ]] || {
		printf '使用覆寫矩陣時不得自動封存 R1。\n' >&2
		return 1
	}
	while IFS=$'\t' read -r folder board branch releases_csv; do
		[[ "${folder}" == folder ]] && continue
		IFS=, read -r -a releases <<< "${releases_csv}"
		expected=$(( ${#releases[@]} * 2 ))
		board_is_complete "${folder}" "${board}" "${branch}" "${releases_csv}" "${expected}" || return 1
		rows=$((rows + 1))
		images=$((images + expected))
	done < "${matrix_file}"
	[[ "${rows}" -eq 45 && "${images}" -eq 444 ]] || {
		printf '全域驗收數量錯誤：板卡 %s/45，映像 %s/444。\n' "${rows}" "${images}" >&2
		return 1
	}
}

rebuild_board() {
	local folder="$1" board="$2" branch="$3" releases_csv="$4"
	local stage destination release profile expected old_english
	local -a releases

	find_board_file "${board}" >/dev/null || {
		printf '找不到板卡設定：%s\n' "${board}" >&2
		return 1
	}
	IFS=, read -r -a releases <<< "${releases_csv}"
	expected=$(( ${#releases[@]} * 2 ))
	stage="${release_root}/.staging-${folder}-${source_short}"
	destination="${release_root}/${folder}"
	recover_board_transaction "${folder}" "${board}" "${branch}" "${releases_csv}" "${expected}" || return 1
	if board_is_complete "${folder}" "${board}" "${branch}" "${releases_csv}" "${expected}"; then
		printf '中斷交易已完成驗證，不再進入建置迴圈：%s。\n' "${board}"
		return 0
	fi
	mkdir -p "${stage}" || return 1

	for release in "${releases[@]}"; do
		for profile in minimal xfce; do
			build_item "${stage}" "${folder}" "${board}" "${branch}" "${release}" "${profile}" || return 1
		done
	done

	write_release_note "${stage}/Release-Notes-zh-TW.md" \
		"${board}" "${branch}" "${releases_csv}" "${expected}" || return 1
	old_english="${destination}/Release-Notes-English.md"
	if [[ -f "${old_english}" ]]; then
		cp -- "${old_english}" "${stage}/Release-Notes-English.md" || return 1
	fi
	verify_board_dir "${stage}" "${folder}" "${board}" "${branch}" "${releases_csv}" || return 1
	install_board_transaction "${folder}" "${board}" "${branch}" "${releases_csv}" "${expected}" "${stage}" || return 1
	printf '完成並替換：%s，共 %s 個最新映像。\n' "${board}" "${expected}"
}

prepare_build_context() {
	local source_tree compile_sha extension_sha host_identity
	local pull_log="${state_root}/container-pull.log"
	if ! docker pull "${container_image}" 2>&1 | tee "${pull_log}"; then
		printf '無法固定 Armbian 建置容器：%s\n' "${container_image}" >&2
		return 1
	fi
	[[ -s "${pull_log}" ]] || return 1
	container_image_id="$(docker image inspect --format '{{.Id}}' "${container_image}")" || return 1
	container_image_locked="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
		"${container_image}" | head -n 1)" || return 1
	[[ "${container_image_id}" =~ ^sha256:[0-9a-f]{64}$ &&
		"${container_image_locked}" =~ @sha256:[0-9a-f]{64}$ ]] || {
		printf '容器映像缺少固定 ID 或 registry digest。\n' >&2
		return 1
	}
	source_tree="$(git -C "${repo_dir}" rev-parse "${source_commit}^{tree}")" || return 1
	compile_sha="$(sha256sum "${repo_dir}/compile.sh" | awk '{ print $1 }')" || return 1
	extension_sha="$(sha256sum "${repo_dir}/extensions/bananapi-build-provenance.sh" | awk '{ print $1 }')" || return 1
	host_identity="$(uname -srmo)"
	build_context_sha256="$(
		printf '%s\\0' "${source_tree}" "${matrix_sha256}" "${userpatches_sha256}" \
			"${container_image_id}" "${container_image_locked}" "${compile_sha}" \
			"${extension_sha}" "${host_identity}" "${BASH_VERSION}" "$(xz --version | head -n 1)" |
			sha256sum | awk '{ print $1 }'
	)" || return 1
}

write_build_manifest() {
	local path="${state_root}/build-inputs-${build_context_sha256:0:12}.tsv"
	[[ ! -e "${path}" ]] || return 0
	{
		printf 'key\tvalue\n'
		printf 'bsp_base_commit\t%s\n' "${bsp_base_commit}"
		printf 'source_commit\t%s\n' "${source_commit}"
		printf 'source_tree\t%s\n' "$(git -C "${repo_dir}" rev-parse "${source_commit}^{tree}")"
		printf 'matrix_path\t%s\n' "${matrix_file}"
		printf 'matrix_sha256\t%s\n' "${matrix_sha256}"
		printf 'userpatches_sha256\t%s\n' "${userpatches_sha256}"
		printf 'build_context_sha256\t%s\n' "${build_context_sha256}"
		printf 'source_remote_ref\t%s\n' "${source_remote_ref}"
		printf 'container_image\t%s\n' "${container_image}"
		printf 'container_image_id\t%s\n' "${container_image_id}"
		printf 'container_image_locked\t%s\n' "${container_image_locked}"
		printf 'build_started_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf 'host_kernel\t%s\n' "$(uname -srmo)"
		printf 'bash_version\t%s\n' "${BASH_VERSION}"
		printf 'xz_version\t%s\n' "$(xz --version | head -n 1)"
	} > "${path}.partial" || return 1
	mv -T "${path}.partial" "${path}" || return 1
}

if [[ "${BPI_REBUILD_LIBRARY_ONLY:-no}" == yes ]]; then
	return 0
fi

validate_matrix
matrix_sha256="$(sha256sum "${matrix_file}" | awk '{ print $1 }')"
userpatches_sha256="$(calculate_userpatches_hash)"

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

if [[ -z "${selected_board}" && "${allow_full_rebuild}" != yes ]]; then
	printf '拒絕未盤點的全矩陣重建；請先產生防重帳本，或明確指定 --board。\n' >&2
	exit 2
fi

assert_clean_source
assert_pushed_source
sudo -n true
mkdir -p "${release_root}" "${repo_dir}/output/images" "${state_root}/boards" \
	"${state_root}/items" "${state_root}/logs" "${state_root}/framework-logs" \
	"${state_root}/markers" "${state_root}/runs" "${state_root}/transactions"
exec 9> "${release_root}/.latest-rebuild.lock"
flock -n 9 || {
	printf '另一個最新矩陣重建或替換程序正在執行。\n' >&2
	exit 1
}
exec 8> "${repo_dir}/output/images/.armbian-build.lock"
flock -n 8 || {
	printf '另一個受控映像建置程序正在使用共用輸出目錄。\n' >&2
	exit 1
}
export BPI_ARMBIAN_BUILD_LOCK_HELD=yes
prepare_build_context
if [[ -n "${expected_build_context_sha256}" &&
	"${build_context_sha256}" != "${expected_build_context_sha256}" ]]; then
	printf '建置內容雜湊改變，拒絕重做既有完成項目：預期 %s，實際 %s。\n' \
		"${expected_build_context_sha256}" "${build_context_sha256}" >&2
	exit 1
fi
write_build_manifest

run_uuid="$(uuidgen)"
summary="${state_root}/runs/summary-${run_uuid}.tsv"
printf 'folder\tboard\tbranch\tstatus\n' > "${summary}"
selected=0
failed=0
while IFS=$'\t' read -r folder board branch releases_csv; do
	[[ "${folder}" == folder ]] && continue
	[[ -z "${selected_board}" || "${selected_board}" == "${board}" || "${selected_board}" == "${folder}" ]] || continue
	selected=$((selected + 1))
	IFS=, read -r -a releases <<< "${releases_csv}"
	expected=$(( ${#releases[@]} * 2 ))
	if board_is_complete "${folder}" "${board}" "${branch}" "${releases_csv}" "${expected}"; then
		printf '已完成並重驗：%s。\n' "${board}"
		printf '%s\t%s\t%s\tverified-existing\n' "${folder}" "${board}" "${branch}" >> "${summary}"
		continue
	fi
	set +e
	rebuild_board "${folder}" "${board}" "${branch}" "${releases_csv}"
	status=$?
	set -e
	if ((status == 0)); then
		printf '%s\t%s\t%s\tcomplete\n' "${folder}" "${board}" "${branch}" >> "${summary}"
	else
		if [[ "${status}" =~ ^(129|130|137|141|143)$ ]]; then
			printf '%s\t%s\t%s\tinterrupted-%s\n' "${folder}" "${board}" "${branch}" "${status}" >> "${summary}"
			printf '收到中斷狀態 %s，停止後續板卡並保留舊矩陣。\n' "${status}" >&2
			exit "${status}"
		fi
		failed=$((failed + 1))
		printf '%s\t%s\t%s\tfailed-%s\n' "${folder}" "${board}" "${branch}" "${status}" >> "${summary}"
	fi
done < "${matrix_file}"

((selected > 0)) || {
	printf '沒有符合條件的板卡。\n' >&2
	exit 2
}
if [[ -z "${selected_board}" && "${failed}" -eq 0 ]]; then
	verify_full_release_matrix
	archive_r1_eos
fi
printf '本輪板卡：%s，失敗：%s，摘要：%s\n' "${selected}" "${failed}" "${summary}"
((failed == 0))
