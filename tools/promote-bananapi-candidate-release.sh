#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

CANDIDATE_RELEASE=""
FORMAL_RELEASE=""
MATRIX_FILE=""
AUDIT_OUTPUT=""
EXECUTE="no"

FORMAL_PARENT=""
FORMAL_NAME=""
STAGING_PATH=""
PREVIOUS_PATH=""
TRANSACTION_PHASE="none"
LOCK_FD=""

declare -a MATRIX_FOLDERS=()
declare -A MATRIX_BOARDS=()
declare -A MATRIX_BRANCHES=()
declare -A MATRIX_RELEASES=()
declare -A MATRIX_EXPECTED_IMAGES=()
declare -A MATRIX_FOLDER_SET=()
declare -A MATRIX_BOARD_SET=()
EXPECTED_IMAGE_TOTAL=0

usage() {
	cat <<'EOF'
用法：
  promote-bananapi-candidate-release.sh \
    --candidate-release <候選發布目錄> \
    --formal-release <正式發布目錄> \
    --matrix <受控矩陣.tsv> \
    [--audit-output <完整稽核輸出目錄>] \
    [--execute]

預設只執行完整預檢與交易預演，不會變更候選或正式發布內容。
只有明確指定 --execute 才會建立硬連結 staging 並原子切換正式版本。
EOF
}

log() {
	printf '%s\n' "$*"
}

die() {
	printf '錯誤：%s\n' "$*" >&2
	exit 1
}

require_command() {
	local command_name="$1"
	command -v "${command_name}" >/dev/null 2>&1 ||
		die "缺少必要命令：${command_name}"
}

parse_args() {
	while (($#)); do
		case "$1" in
		--candidate-release)
			(($# >= 2)) || die "--candidate-release 缺少路徑"
			CANDIDATE_RELEASE="$2"
			shift 2
			;;
		--formal-release)
			(($# >= 2)) || die "--formal-release 缺少路徑"
			FORMAL_RELEASE="$2"
			shift 2
			;;
		--matrix)
			(($# >= 2)) || die "--matrix 缺少路徑"
			MATRIX_FILE="$2"
			shift 2
			;;
		--audit-output)
			(($# >= 2)) || die "--audit-output 缺少路徑"
			AUDIT_OUTPUT="$2"
			shift 2
			;;
		--execute)
			EXECUTE="yes"
			shift
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			die "未知參數：$1"
			;;
		esac
	done

	[[ -n "${CANDIDATE_RELEASE}" ]] || die "必須指定 --candidate-release"
	[[ -n "${FORMAL_RELEASE}" ]] || die "必須指定 --formal-release"
	[[ -n "${MATRIX_FILE}" ]] || die "必須指定 --matrix"
}

canonicalize_inputs() {
	[[ -d "${CANDIDATE_RELEASE}" ]] ||
		die "候選發布目錄不存在：${CANDIDATE_RELEASE}"
	[[ ! -L "${CANDIDATE_RELEASE}" ]] ||
		die "候選發布根目錄不得為符號連結：${CANDIDATE_RELEASE}"
	[[ -f "${MATRIX_FILE}" ]] || die "矩陣檔不存在：${MATRIX_FILE}"
	[[ ! -L "${MATRIX_FILE}" ]] || die "矩陣檔不得為符號連結：${MATRIX_FILE}"

	CANDIDATE_RELEASE="$(realpath -e -- "${CANDIDATE_RELEASE}")"
	MATRIX_FILE="$(realpath -e -- "${MATRIX_FILE}")"
	FORMAL_RELEASE="$(realpath -m -- "${FORMAL_RELEASE}")"
	FORMAL_PARENT="$(dirname -- "${FORMAL_RELEASE}")"
	FORMAL_NAME="$(basename -- "${FORMAL_RELEASE}")"

	[[ "${FORMAL_RELEASE}" != "/" ]] || die "正式發布目錄不得為根目錄"
	[[ -d "${FORMAL_PARENT}" ]] ||
		die "正式發布父目錄不存在：${FORMAL_PARENT}"
	[[ ! -L "${FORMAL_PARENT}" ]] ||
		die "正式發布父目錄不得為符號連結：${FORMAL_PARENT}"
	[[ -w "${FORMAL_PARENT}" ]] ||
		die "正式發布父目錄不可寫入：${FORMAL_PARENT}"

	if [[ -n "${AUDIT_OUTPUT}" ]]; then
		[[ -d "${AUDIT_OUTPUT}" ]] ||
			die "稽核輸出目錄不存在：${AUDIT_OUTPUT}"
		[[ ! -L "${AUDIT_OUTPUT}" ]] ||
			die "稽核輸出目錄不得為符號連結：${AUDIT_OUTPUT}"
		AUDIT_OUTPUT="$(realpath -e -- "${AUDIT_OUTPUT}")"
	fi

	case "${FORMAL_RELEASE}/" in
	"${CANDIDATE_RELEASE}/"*)
		die "正式發布目錄不得位於候選發布目錄內：${FORMAL_RELEASE}"
		;;
	esac
	case "${CANDIDATE_RELEASE}/" in
	"${FORMAL_RELEASE}/"*)
		die "候選發布目錄不得位於正式發布目錄內：${CANDIDATE_RELEASE}"
		;;
	esac
}

acquire_lock() {
	# 鎖定父目錄本身，不在發布樹留下鎖檔，也不產生清理鎖檔的競態。
	exec {LOCK_FD}<"${FORMAL_PARENT}"
	flock -n "${LOCK_FD}" ||
		die "另一個發布提升交易正鎖定正式發布父目錄：${FORMAL_PARENT}"
}

read_matrix() {
	local header line_number=1 folder board branch releases extra release
	local -a release_list=()
	local -A release_seen=()

	exec 3<"${MATRIX_FILE}"
	IFS= read -r header <&3 || die "矩陣檔為空：${MATRIX_FILE}"
	header="${header%$'\r'}"
	[[ "${header}" == $'folder\tboard\tbranch\treleases' ]] ||
		die "矩陣欄位錯誤，必須依序為 folder、board、branch、releases：${MATRIX_FILE}"

	while IFS=$'\t' read -r folder board branch releases extra <&3 ||
		[[ -n "${folder}${board}${branch}${releases}${extra}" ]]; do
		((line_number += 1))
		releases="${releases%$'\r'}"
		[[ -n "${folder}${board}${branch}${releases}${extra}" ]] || continue
		[[ -z "${extra}" ]] || die "矩陣第 ${line_number} 列含多餘欄位"
		[[ "${folder}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
			die "矩陣第 ${line_number} 列的板目錄名稱不安全：${folder}"
		[[ "${board}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
			die "矩陣第 ${line_number} 列的板卡名稱不安全：${board}"
		[[ "${branch}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
			die "矩陣第 ${line_number} 列的分支名稱不安全：${branch}"
		[[ -n "${releases}" ]] || die "矩陣第 ${line_number} 列沒有發行版：${folder}"
		[[ -z "${MATRIX_FOLDER_SET[${folder}]+存在}" ]] ||
			die "矩陣含重複板目錄：${folder}"
		[[ -z "${MATRIX_BOARD_SET[${board}]+存在}" ]] ||
			die "矩陣含重複板卡：${board}"

		IFS=',' read -r -a release_list <<<"${releases}"
		((${#release_list[@]} > 0)) || die "矩陣沒有有效發行版：${folder}"
		release_seen=()
		for release in "${release_list[@]}"; do
			[[ "${release}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
				die "矩陣含不安全或空白的發行版：${folder} / ${release}"
			[[ -z "${release_seen[${release}]+存在}" ]] ||
				die "矩陣同一板卡重複列出發行版：${folder} / ${release}"
			release_seen["${release}"]=1
		done

		MATRIX_FOLDERS+=("${folder}")
		MATRIX_BOARDS["${folder}"]="${board}"
		MATRIX_BRANCHES["${folder}"]="${branch}"
		MATRIX_RELEASES["${folder}"]="${releases}"
		MATRIX_EXPECTED_IMAGES["${folder}"]=$((${#release_list[@]} * 2))
		MATRIX_FOLDER_SET["${folder}"]=1
		MATRIX_BOARD_SET["${board}"]=1
		((EXPECTED_IMAGE_TOTAL += ${#release_list[@]} * 2))
	done
	exec 3<&-

	((${#MATRIX_FOLDERS[@]} > 0)) || die "矩陣沒有任何板卡資料：${MATRIX_FILE}"
}

reject_residual_name() {
	local root="$1"
	local entry name

	while IFS= read -r -d '' entry; do
		name="${entry##*/}"
		case "${name}" in
		.staging-* | .failed-* | .previous-*)
			die "發布根目錄含殘留交易目錄，必須先人工查明：${entry}"
			;;
		esac
	done < <(find "${root}" -mindepth 1 -maxdepth 1 -print0)
}

validate_candidate_root() {
	local entry name folder

	reject_residual_name "${CANDIDATE_RELEASE}"
	while IFS= read -r -d '' entry; do
		name="${entry##*/}"
		if [[ -f "${entry}" && ! -L "${entry}" ]]; then
			die "候選發布根目錄不得含一般檔案：${entry}"
		fi
		[[ -d "${entry}" && ! -L "${entry}" ]] ||
			die "候選發布根目錄只允許實體板目錄：${entry}"
		[[ -n "${MATRIX_FOLDER_SET[${name}]+存在}" ]] ||
			die "候選發布含矩陣外板目錄：${entry}"
	done < <(find "${CANDIDATE_RELEASE}" -mindepth 1 -maxdepth 1 -print0)

	for folder in "${MATRIX_FOLDERS[@]}"; do
		entry="${CANDIDATE_RELEASE}/${folder}"
		[[ -d "${entry}" && ! -L "${entry}" ]] ||
			die "候選發布缺少矩陣板目錄：${entry}"
	done
}

validate_formal_root() {
	local entry name

	if [[ -e "${FORMAL_RELEASE}" || -L "${FORMAL_RELEASE}" ]]; then
		[[ -d "${FORMAL_RELEASE}" && ! -L "${FORMAL_RELEASE}" ]] ||
			die "正式發布路徑必須是實體目錄：${FORMAL_RELEASE}"
		[[ "$(stat -c '%d' -- "${FORMAL_RELEASE}")" == "$(stat -c '%d' -- "${FORMAL_PARENT}")" ]] ||
			die "正式發布目錄與父目錄不在同一檔案系統，無法原子更名：${FORMAL_RELEASE}"
		reject_residual_name "${FORMAL_RELEASE}"
		while IFS= read -r -d '' entry; do
			name="${entry##*/}"
			if [[ -f "${entry}" && ! -L "${entry}" ]]; then
				die "正式發布根目錄不得含一般檔案：${entry}"
			fi
			[[ -d "${entry}" && ! -L "${entry}" ]] ||
				die "正式發布根目錄只允許實體板目錄：${entry}"
		done < <(find "${FORMAL_RELEASE}" -mindepth 1 -maxdepth 1 -print0)
	fi
}

validate_parent_transaction_residue() {
	local entry
	local -a residues=()

	shopt -s nullglob
	residues=(
		"${FORMAL_PARENT}/.${FORMAL_NAME}.staging-"*
		"${FORMAL_PARENT}/.${FORMAL_NAME}.failed-"*
		"${FORMAL_PARENT}/.${FORMAL_NAME}.previous-"*
	)
	shopt -u nullglob
	for entry in "${residues[@]}"; do
		[[ -e "${entry}" || -L "${entry}" ]] || continue
		die "正式發布父目錄含前次交易殘留，必須先人工查明：${entry}"
	done
}

validate_sidecar() {
	local archive="$1"
	local sidecar="${archive}.sha"
	local digest referenced extra actual
	local -a lines=()

	[[ -f "${sidecar}" && ! -L "${sidecar}" ]] ||
		die "映像缺少同名 SHA 邊車：${sidecar}"
	mapfile -t lines <"${sidecar}"
	((${#lines[@]} == 1)) || die "SHA 邊車必須只有一列：${sidecar}"
	read -r digest referenced extra <<<"${lines[0]}"
	[[ -z "${extra}" && "${digest}" =~ ^[0-9a-f]{64}$ && -n "${referenced}" ]] ||
		die "SHA 邊車格式錯誤：${sidecar}"
	referenced="${referenced#\*}"
	[[ "${referenced}" == "${archive##*/}" ]] ||
		die "SHA 邊車指向錯誤檔名：${sidecar} -> ${referenced}"
	actual="$(sha256sum -- "${archive}")"
	actual="${actual%% *}"
	[[ "${actual}" == "${digest}" ]] ||
		die "映像 SHA-256 驗證失敗：${archive}"
}

validate_board_directory() {
	local folder="$1"
	local board="${MATRIX_BOARDS[${folder}]}"
	local branch="${MATRIX_BRANCHES[${folder}]}"
	local releases="${MATRIX_RELEASES[${folder}]}"
	local directory="${CANDIDATE_RELEASE}/${folder}"
	local token="${board^}"
	local entry archive name release profile suffix sidecar_count match_count
	local -a archives=() matches=() release_list=()
	local -A archive_match_counts=()

	while IFS= read -r -d '' entry; do
		[[ -f "${entry}" && ! -L "${entry}" ]] ||
			die "板目錄只允許第一層實體檔案：${entry}"
	done < <(find "${directory}" -mindepth 1 -maxdepth 1 -print0)

	mapfile -d '' archives < <(
		find "${directory}" -mindepth 1 -maxdepth 1 -type f -name '*.img.xz' -print0 | sort -z
	)
	((${#archives[@]} == MATRIX_EXPECTED_IMAGES[${folder}])) ||
		die "板目錄映像數量錯誤：${folder}，預期 ${MATRIX_EXPECTED_IMAGES[${folder}]}，實際 ${#archives[@]}"

	sidecar_count="$(find "${directory}" -mindepth 1 -maxdepth 1 -type f -name '*.img.xz.sha' -printf '.' | wc -c)"
	[[ "${sidecar_count}" == "${MATRIX_EXPECTED_IMAGES[${folder}]}" ]] ||
		die "板目錄 SHA 邊車數量錯誤：${folder}，預期 ${MATRIX_EXPECTED_IMAGES[${folder}]}，實際 ${sidecar_count}"

	IFS=',' read -r -a release_list <<<"${releases}"
	for release in "${release_list[@]}"; do
		for profile in minimal xfce; do
			if [[ "${profile}" == "minimal" ]]; then
				suffix="_minimal.img.xz"
			else
				suffix="_xfce_desktop.img.xz"
			fi
			matches=()
			for archive in "${archives[@]}"; do
				name="${archive##*/}"
				if [[ "${name}" == *"_${token}_${release}_${branch}_"* && "${name}" == *"${suffix}" ]]; then
					matches+=("${archive}")
				fi
			done
			((${#matches[@]} == 1)) ||
				die "矩陣唯一映像不完整或重複：${folder} / ${board} / ${branch} / ${release} / ${profile}，實際 ${#matches[@]}"
			archive_match_counts["${matches[0]}"]=$((
				${archive_match_counts[${matches[0]}]:-0} + 1
			))
		done
	done

	for archive in "${archives[@]}"; do
		match_count="${archive_match_counts[${archive}]:-0}"
		[[ "${match_count}" == "1" ]] ||
			die "映像檔名未唯一對應矩陣項目：${archive}，對應次數 ${match_count}"
		validate_sidecar "${archive}"
	done
}

tsv_data_rows() {
	local path="$1"
	awk 'NR > 1 && $0 !~ /^[[:space:]]*$/ { count += 1 } END { print count + 0 }' "${path}"
}

validate_tsv_header() {
	local path="$1"
	local expected="$2"
	local actual

	[[ -f "${path}" && ! -L "${path}" ]] || die "稽核輸出缺少必要檔案：${path}"
	IFS= read -r actual <"${path}" || die "稽核輸出檔為空：${path}"
	actual="${actual%$'\r'}"
	[[ "${actual}" == "${expected}" ]] || die "稽核輸出欄位錯誤：${path}"
}

validate_audit_output() {
	local path rows policy_folders matrix_folders

	[[ -n "${AUDIT_OUTPUT}" ]] || return 0

	validate_tsv_header "${AUDIT_OUTPUT}/映像盤點.tsv" \
		$'唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t映像\tSHA256\t來源提交\t建置內容雜湊\t處置'
	rows="$(tsv_data_rows "${AUDIT_OUTPUT}/映像盤點.tsv")"
	[[ "${rows}" == "${EXPECTED_IMAGE_TOTAL}" ]] ||
		die "稽核映像盤點列數錯誤，預期 ${EXPECTED_IMAGE_TOTAL}，實際 ${rows}"
	if ! awk -F '\t' 'NR > 1 && $0 !~ /^[[:space:]]*$/ && $7 != "已驗證候選" { exit 1 }' \
		"${AUDIT_OUTPUT}/映像盤點.tsv"; then
		die "稽核映像盤點含未達『已驗證候選』的項目：${AUDIT_OUTPUT}/映像盤點.tsv"
	fi

	validate_tsv_header "${AUDIT_OUTPUT}/板卡決策.tsv" \
		$'板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源'
	rows="$(tsv_data_rows "${AUDIT_OUTPUT}/板卡決策.tsv")"
	[[ "${rows}" == "${#MATRIX_FOLDERS[@]}" ]] ||
		die "稽核板卡決策列數錯誤，預期 ${#MATRIX_FOLDERS[@]}，實際 ${rows}"
	if ! awk -F '\t' 'NR > 1 && $0 !~ /^[[:space:]]*$/ && $5 != "沿用完整候選" { exit 1 }' \
		"${AUDIT_OUTPUT}/板卡決策.tsv"; then
		die "稽核板卡決策含非完整候選：${AUDIT_OUTPUT}/板卡決策.tsv"
	fi

	validate_tsv_header "${AUDIT_OUTPUT}/候選輸入政策.tsv" \
		$'folder\tsource_commit\tbuild_context_sha256'
	rows="$(tsv_data_rows "${AUDIT_OUTPUT}/候選輸入政策.tsv")"
	[[ "${rows}" == "${#MATRIX_FOLDERS[@]}" ]] ||
		die "稽核候選輸入政策列數錯誤，預期 ${#MATRIX_FOLDERS[@]}，實際 ${rows}"
	if ! awk -F '\t' '
		NR == 1 { next }
		NF != 3 || $1 !~ /^[A-Za-z0-9][A-Za-z0-9._-]*$/ ||
			$2 !~ /^[0-9a-f]{40}$/ || $3 !~ /^[0-9a-f]{64}$/ { exit 1 }
	' "${AUDIT_OUTPUT}/候選輸入政策.tsv"; then
		die "稽核候選輸入政策含非法欄位：${AUDIT_OUTPUT}/候選輸入政策.tsv"
	fi
	policy_folders="$(awk -F '\t' 'NR > 1 { print $1 }' \
		"${AUDIT_OUTPUT}/候選輸入政策.tsv" | LC_ALL=C sort)"
	matrix_folders="$(printf '%s\n' "${MATRIX_FOLDERS[@]}" | LC_ALL=C sort)"
	[[ "${policy_folders}" == "${matrix_folders}" ]] ||
		die "稽核候選輸入政策未精確覆蓋受控矩陣"

	validate_tsv_header "${AUDIT_OUTPUT}/待辦佇列.tsv" \
		$'板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因'
	validate_tsv_header "${AUDIT_OUTPUT}/中止產物.tsv" \
		$'候選來源\t類別\t大小bytes\t處置\t路徑'
	validate_tsv_header "${AUDIT_OUTPUT}/舊暫存目錄.tsv" \
		$'目錄\t檔案數\t大小bytes\t處置\t路徑'
	validate_tsv_header "${AUDIT_OUTPUT}/矩陣外項目.tsv" \
		$'板目錄\t映像數\t處置\t路徑'
	validate_tsv_header "${AUDIT_OUTPUT}/候選交易殘留.tsv" \
		$'候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑'
	for path in 待辦佇列.tsv 中止產物.tsv 舊暫存目錄.tsv 矩陣外項目.tsv 候選交易殘留.tsv; do
		rows="$(tsv_data_rows "${AUDIT_OUTPUT}/${path}")"
		[[ "${rows}" == "0" ]] ||
			die "稽核輸出仍有阻擋項目：${AUDIT_OUTPUT}/${path}，共 ${rows} 列"
	done
}

validate_same_filesystem() {
	local candidate_device parent_device

	candidate_device="$(stat -c '%d' -- "${CANDIDATE_RELEASE}")"
	parent_device="$(stat -c '%d' -- "${FORMAL_PARENT}")"
	[[ "${candidate_device}" == "${parent_device}" ]] ||
		die "候選與正式發布父目錄不在同一檔案系統；為避免靜默複製，拒絕交易：${CANDIDATE_RELEASE} -> ${FORMAL_PARENT}"
}

preflight() {
	local folder

	read_matrix
	validate_candidate_root
	validate_formal_root
	validate_parent_transaction_residue
	validate_same_filesystem
	for folder in "${MATRIX_FOLDERS[@]}"; do
		validate_board_directory "${folder}"
	done
	validate_audit_output

	log "預檢通過：${#MATRIX_FOLDERS[@]} 個板目錄、${EXPECTED_IMAGE_TOTAL} 個映像及同名 SHA 邊車均完整。"
}

remove_generated_staging() {
	local path="$1"
	[[ -n "${path}" ]] || return 0
	[[ "$(dirname -- "${path}")" == "${FORMAL_PARENT}" ]] ||
		die "拒絕清理不在正式發布父目錄內的路徑：${path}"
	case "$(basename -- "${path}")" in
	".${FORMAL_NAME}.staging-"*) rm -rf -- "${path}" ;;
	*) die "拒絕清理非本工具 staging 路徑：${path}" ;;
	esac
}

rollback_transaction() {
	local status="$1"
	local failed_path

	trap - EXIT INT TERM HUP
	if [[ "${EXECUTE}" == "yes" && "${TRANSACTION_PHASE}" == "promoted" ]]; then
		failed_path="${FORMAL_PARENT}/.${FORMAL_NAME}.failed-rollback-$$"
		if [[ -e "${FORMAL_RELEASE}" ]] && mv -T -- "${FORMAL_RELEASE}" "${failed_path}"; then
			if [[ -n "${PREVIOUS_PATH}" && -d "${PREVIOUS_PATH}" ]]; then
				if mv -T -- "${PREVIOUS_PATH}" "${FORMAL_RELEASE}"; then
					log "交易異常，已將舊正式版本復原：${FORMAL_RELEASE}" >&2
					rm -rf -- "${failed_path}"
				else
					printf '嚴重錯誤：無法復原舊正式版本；舊版本仍在 %s，新候選暫存在 %s\n' \
						"${PREVIOUS_PATH}" "${failed_path}" >&2
					status=90
				fi
			else
				rm -rf -- "${failed_path}"
			fi
		fi
	elif [[ "${EXECUTE}" == "yes" && "${TRANSACTION_PHASE}" == "backed_up" ]]; then
		if [[ ! -e "${FORMAL_RELEASE}" && -n "${PREVIOUS_PATH}" && -d "${PREVIOUS_PATH}" ]]; then
			if mv -T -- "${PREVIOUS_PATH}" "${FORMAL_RELEASE}"; then
				log "交易失敗，已將舊正式版本復原：${FORMAL_RELEASE}" >&2
			else
				printf '嚴重錯誤：交易失敗且無法復原；舊正式版本仍在 %s\n' "${PREVIOUS_PATH}" >&2
				status=91
			fi
		fi
	fi

	if [[ -n "${STAGING_PATH}" && -e "${STAGING_PATH}" ]]; then
		remove_generated_staging "${STAGING_PATH}"
	fi
	exit "${status}"
}

create_hardlink_staging() {
	local folder source_directory target_directory source target
	local linked_files=0

	mkdir -- "${STAGING_PATH}"
	for folder in "${MATRIX_FOLDERS[@]}"; do
		source_directory="${CANDIDATE_RELEASE}/${folder}"
		target_directory="${STAGING_PATH}/${folder}"
		mkdir -- "${target_directory}"
		while IFS= read -r -d '' source; do
			target="${target_directory}/${source##*/}"
			ln -- "${source}" "${target}" ||
				die "建立硬連結失敗；不會改用複製：${source} -> ${target}"
			[[ "${source}" -ef "${target}" ]] ||
				die "staging 檔案不是候選原件的硬連結：${target}"
			((linked_files += 1))
		done < <(find "${source_directory}" -mindepth 1 -maxdepth 1 -type f -print0 | sort -z)
	done
	log "已建立同檔案系統硬連結 staging：${STAGING_PATH}（${linked_files} 個檔案）"
}

execute_transaction() {
	local transaction_id

	transaction_id="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
	STAGING_PATH="${FORMAL_PARENT}/.${FORMAL_NAME}.staging-${transaction_id}"
	PREVIOUS_PATH="${FORMAL_PARENT}/.${FORMAL_NAME}.previous-${transaction_id}"
	trap 'rollback_transaction $?' EXIT
	trap 'exit 130' INT TERM HUP

	create_hardlink_staging
	if [[ -e "${FORMAL_RELEASE}" ]]; then
		if ! mv -T -- "${FORMAL_RELEASE}" "${PREVIOUS_PATH}"; then
			die "無法把舊正式版本原子更名為 previous：${FORMAL_RELEASE} -> ${PREVIOUS_PATH}"
		fi
		TRANSACTION_PHASE="backed_up"
	fi

	if ! mv -T -- "${STAGING_PATH}" "${FORMAL_RELEASE}"; then
		die "無法把候選 staging 原子切換為正式版本：${STAGING_PATH} -> ${FORMAL_RELEASE}"
	fi
	TRANSACTION_PHASE="promoted"
	STAGING_PATH=""
	TRANSACTION_PHASE="committed"
	trap - EXIT INT TERM HUP

	log "正式發布提升完成：${FORMAL_RELEASE}"
	if [[ -d "${PREVIOUS_PATH}" ]]; then
		log "舊正式版本保留於：${PREVIOUS_PATH}"
		log "需要復原時，先移走目前正式目錄，再以 mv -T 將上述 previous 原子更名回：${FORMAL_RELEASE}"
	else
		log "原先沒有正式版本，因此本次沒有 previous 目錄。"
	fi
}

main() {
	local command_name

	for command_name in awk basename date dirname find flock ln mapfile mkdir mv realpath rm sha256sum sort stat wc; do
		require_command "${command_name}"
	done
	parse_args "$@"
	canonicalize_inputs
	acquire_lock
	preflight

	if [[ "${EXECUTE}" != "yes" ]]; then
		log "預演完成：未修改候選或正式發布內容。若要執行交易，請明確加入 --execute。"
		return 0
	fi
	execute_transaction
}

main "$@"
