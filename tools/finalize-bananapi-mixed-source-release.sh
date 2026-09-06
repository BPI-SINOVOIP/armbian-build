#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly EXPECTED_BOARD_TOTAL=45
readonly EXPECTED_IMAGE_TOTAL=444

tool_repo=""
matrix_file=""
candidate_release=""
candidate_state=""
formal_release=""
formal_parent=""
formal_name=""

output_final=""
output_failed=""
output_staging=""
candidate_policy=""
candidate_audit=""
formal_policy=""
formal_audit=""
previous_path=""
promotion_completed="no"
promotion_started="no"
formal_validated="no"
lock_fd=""
candidate_build_lock_fd=""
formal_build_lock_fd=""
candidate_compat_lock_fd=""
formal_compat_lock_fd=""
candidate_build_lock_path=""
formal_build_lock_path=""
snapshot_repo=""
snapshot_matrix=""
removed_staging_archive=""
promotion_commit_marker=""
journal_phase=""
journal_previous=""
journal_staging=""

declare -a removed_formal_staging=()
declare -a archived_formal_staging=()

fail() {
	printf '錯誤：%s\n' "$*" >&2
	exit 1
}

require_command() {
	local name="$1"
	command -v "${name}" >/dev/null 2>&1 || fail "缺少必要命令：${name}"
}

require_environment() {
	local name="$1"
	[[ -n "${!name:-}" ]] || fail "必須設定環境變數 ${name}。"
}

require_directory() {
	local path="$1" description="$2"
	[[ -d "${path}" && ! -L "${path}" ]] || fail "${description}不是實體目錄：${path}"
}

require_regular_file() {
	local path="$1" description="$2"
	[[ -f "${path}" && ! -L "${path}" ]] || fail "${description}不是實體一般檔案：${path}"
}

directory_is_empty() {
	local path="$1"
	local first_entry
	first_entry="$(find "${path}" -mindepth 1 -maxdepth 1 -print -quit)" || return 2
	[[ -z "${first_entry}" ]]
}

path_is_within() {
	local parent="$1" child="$2"
	[[ "${child}" == "${parent}" || "${child}" == "${parent}/"* ]]
}

reject_overlapping_paths() {
	[[ "${candidate_release}" != "/" ]] || fail "候選發布根目錄不得為根目錄。"
	[[ "${candidate_state}" != "/" ]] || fail "候選狀態根目錄不得為根目錄。"
	[[ "${formal_release}" != "/" ]] || fail "正式發布根目錄不得為根目錄。"
	if path_is_within "${candidate_release}" "${formal_release}" ||
		path_is_within "${formal_release}" "${candidate_release}"; then
		fail "候選與正式發布目錄不得相同或互相包含。"
	fi
	if path_is_within "${candidate_release}" "${candidate_state}" ||
		path_is_within "${candidate_state}" "${candidate_release}" ||
		path_is_within "${formal_release}" "${candidate_state}" ||
		path_is_within "${candidate_state}" "${formal_release}"; then
		fail "候選狀態目錄不得與候選或正式發布目錄相同或互相包含。"
	fi
	if path_is_within "${candidate_release}" "${tool_repo}" ||
		path_is_within "${formal_release}" "${tool_repo}" ||
		path_is_within "${candidate_release}" "${matrix_file}" ||
		path_is_within "${formal_release}" "${matrix_file}"; then
		fail "工具倉庫與矩陣不得位於會被修改或切換的發布目錄內。"
	fi
}

stable_build_lock_path() {
	local root="$1"
	printf '%s/.%s.build.lock\n' "$(dirname -- "${root}")" "$(basename -- "${root}")"
}

acquire_stable_build_lock() {
	local root="$1" description="$2" output_name="$3"
	local path fd actual_path
	path="$(stable_build_lock_path "${root}")"
	[[ ! -L "${path}" ]] || fail "${description}固定鎖不得為符號連結：${path}"
	[[ ! -e "${path}" || -f "${path}" ]] ||
		fail "${description}固定鎖必須是實體一般檔案：${path}"
	exec {fd}<>"${path}" || fail "無法開啟${description}固定鎖：${path}"
	actual_path="$(readlink -- "/proc/self/fd/${fd}")" ||
		fail "無法讀取${description}固定鎖 FD 路徑：${fd}"
	[[ "${actual_path}" == "${path}" && -f "${path}" && ! -L "${path}" &&
		"${path}" -ef "/proc/self/fd/${fd}" ]] ||
		fail "${description}固定鎖在開啟期間被替換：${path}"
	flock -n "${fd}" || fail "${description}仍有建置或發布程序持有固定鎖：${path}"
	printf -v "${output_name}" '%s' "${fd}"
}

acquire_root_compatibility_lock() {
	local root="$1" description="$2" output_name="$3"
	local path="${root}/.latest-rebuild.lock"
	local fd actual_path
	[[ ! -L "${path}" ]] || fail "${description}根內相容鎖不得為符號連結：${path}"
	[[ ! -e "${path}" || -f "${path}" ]] ||
		fail "${description}根內相容鎖必須是實體一般檔案：${path}"
	exec {fd}<>"${path}" || fail "無法開啟${description}根內相容鎖：${path}"
	actual_path="$(readlink -- "/proc/self/fd/${fd}")" ||
		fail "無法讀取${description}根內相容鎖 FD 路徑：${fd}"
	[[ "${actual_path}" == "${path}" && -f "${path}" && ! -L "${path}" &&
		"${path}" -ef "/proc/self/fd/${fd}" ]] ||
		fail "${description}根內相容鎖在開啟期間被替換：${path}"
	flock -n "${fd}" || fail "${description}仍有舊版建置程序持有根內相容鎖：${path}"
	printf -v "${output_name}" '%s' "${fd}"
}

validate_root_compatibility_lock() {
	local root="$1" description="$2" fd="$3"
	local path="${root}/.latest-rebuild.lock"
	[[ "${fd}" =~ ^[0-9]+$ && -e "/proc/self/fd/${fd}" ]] ||
		fail "${description}根內相容鎖 FD 已遺失：${fd}"
	[[ -f "${path}" && ! -L "${path}" && "${path}" -ef "/proc/self/fd/${fd}" ]] ||
		fail "${description}根內相容鎖不再是受鎖 inode：${path}"
	flock -n "${fd}" || fail "${description}根內相容鎖在流程中遺失：${path}"
}

collect_find_paths() {
	local output_name="$1"
	shift
	local list_file
	local -a found=()
	list_file="$(mktemp "${output_staging}/.find.XXXXXX")" || fail "無法建立安全掃描暫存檔。"
	if ! find "$@" -print0 >"${list_file}"; then
		rm -f -- "${list_file}"
		fail "無法完整掃描檔案系統，拒絕把不完整結果視為通過。"
	fi
	mapfile -d '' -t found <"${list_file}"
	rm -f -- "${list_file}"
	local -n output_ref="${output_name}"
	# shellcheck disable=SC2034
	output_ref=("${found[@]}")
}

prepare_input_snapshot() {
	local name source target relative_target digest
	snapshot_repo="${output_staging}/輸入快照"
	snapshot_matrix="${snapshot_repo}/受控矩陣.tsv"
	mkdir -- "${snapshot_repo}" "${snapshot_repo}/tools"
	cp -p -- "${matrix_file}" "${snapshot_matrix}"
	{
		printf '項目\t原始路徑\t快照路徑\tSHA256\n'
		digest="$(sha256sum -- "${snapshot_matrix}")"
		relative_target="輸入快照/受控矩陣.tsv"
		printf '受控矩陣\t%s\t%s\t%s\n' \
			"${matrix_file}" "${relative_target}" "${digest%% *}"
		for name in \
			generate-bananapi-candidate-input-policy.py \
			generate-bananapi-release-notes.py \
			audit-bananapi-release-state.py \
			promote-bananapi-candidate-release.sh; do
			source="${tool_repo}/tools/${name}"
			target="${snapshot_repo}/tools/${name}"
			relative_target="輸入快照/tools/${name}"
			cp -p -- "${source}" "${target}"
			digest="$(sha256sum -- "${target}")"
			printf '工具\t%s\t%s\t%s\n' "${source}" "${relative_target}" "${digest%% *}"
		done
	} >"${output_staging}/輸入快照.tsv"
	matrix_file="${snapshot_matrix}"
	tool_repo="${snapshot_repo}"
}

require_empty_directory() {
	local path="$1" description="$2"
	require_directory "${path}" "${description}"
	directory_is_empty "${path}" || fail "${description}必須為空，已保留現場：${path}"
}

write_execution_status() {
	local status="$1"
	local status_path="${output_staging}/執行狀態.tsv"
	local status_temporary="${output_staging}/.執行狀態.tsv.tmp"
	if ! {
		printf '欄位\t值\n'
		printf '狀態\t%s\n' "$([[ "${status}" == 0 ]] && printf '成功' || printf '失敗')"
		printf '退出碼\t%s\n' "${status}"
		printf '正式提升完成\t%s\n' "${promotion_completed}"
		printf '正式重新稽核通過\t%s\n' "${formal_validated}"
	} >"${status_temporary}"; then
		printf '錯誤：無法寫入執行狀態暫存檔：%s\n' "${status_temporary}" >&2
		return 94
	fi
	if ! mv -T -- "${status_temporary}" "${status_path}"; then
		printf '錯誤：無法發布執行狀態檔：%s\n' "${status_path}" >&2
		return 94
	fi
	return 0
}

publish_output_directory() {
	local status="$1"
	local target="${output_final}"
	[[ -n "${output_staging}" && -d "${output_staging}" ]] || return "${status}"
	if ((status != 0)); then
		target="${output_failed}"
	fi
	if [[ -e "${target}" || -L "${target}" ]]; then
		printf '錯誤：收斂輸出路徑已存在，拒絕覆寫：%s\n' "${target}" >&2
		output_final="${output_staging}"
		write_execution_status 92 || return $?
		return 92
	fi
	write_execution_status "${status}" || return $?
	if ! mv -T -n -- "${output_staging}" "${target}" ||
		[[ -d "${output_staging}" ]]; then
		output_final="${output_staging}"
		if [[ -e "${target}" || -L "${target}" ]]; then
			printf '錯誤：收斂輸出更名期間出現路徑衝突，已保留未知內容：%s\n' \
				"${target}" >&2
			if ! write_execution_status 92; then
				rm -f -- "${output_staging}/執行狀態.tsv"
				return 94
			fi
			return 92
		fi
		printf '錯誤：無法原子發布收斂輸出：%s\n' "${target}" >&2
		if ! write_execution_status 93; then
			rm -f -- "${output_staging}/執行狀態.tsv"
			return 94
		fi
		return 93
	fi
	output_final="${target}"
	return "${status}"
}

restore_removed_formal_staging() {
	local index path archive
	local status=0
	[[ "${promotion_completed}" == "no" ]] || return 0
	for index in "${!removed_formal_staging[@]}"; do
		path="${removed_formal_staging[${index}]}"
		archive="${archived_formal_staging[${index}]}"
		if [[ ( -e "${path}" || -L "${path}" ) &&
			! -e "${archive}" && ! -L "${archive}" ]]; then
			continue
		fi
		if [[ -e "${path}" || -L "${path}" ]]; then
			printf '錯誤：空 staging 復原路徑已被佔用，封存仍保留：%s\n' "${path}" >&2
			status=95
			continue
		fi
		if [[ ! -d "${formal_release}" || -L "${formal_release}" ]]; then
			printf '錯誤：正式發布目錄不可安全復原空 staging：%s\n' \
				"${formal_release}" >&2
			status=95
			continue
		fi
		if [[ ! -d "${archive}" || -L "${archive}" ]]; then
			printf '錯誤：空 staging 封存遺失或類型錯誤：%s\n' "${archive}" >&2
			status=95
			continue
		fi
		if ! mv -T -- "${archive}" "${path}"; then
			printf '錯誤：無法原樣復原提升前移出的空 staging：%s\n' "${path}" >&2
			status=95
		fi
	done
	return "${status}"
}

read_promotion_journal() {
	local -a lines=()
	local recorded_formal
	[[ -n "${promotion_commit_marker}" && -f "${promotion_commit_marker}" &&
		! -L "${promotion_commit_marker}" ]] || return 1
	mapfile -t lines <"${promotion_commit_marker}" || return 1
	((${#lines[@]} == 5)) || return 1
	[[ "${lines[0]}" == $'欄位\t值' ]] || return 1
	journal_phase="${lines[1]#*$'\t'}"
	recorded_formal="${lines[2]#*$'\t'}"
	journal_previous="${lines[3]#*$'\t'}"
	journal_staging="${lines[4]#*$'\t'}"
	[[ "${lines[1]}" == $'狀態\t'"${journal_phase}" &&
		"${journal_phase}" =~ ^(準備|已備份|已提交)$ &&
		"${lines[2]}" == $'正式路徑\t'"${recorded_formal}" &&
		"${lines[3]}" == $'previous\t'"${journal_previous}" &&
		"${lines[4]}" == $'staging\t'"${journal_staging}" &&
		"${recorded_formal}" == "${formal_release}" ]] || return 1
	[[ "${journal_previous}" != "無" &&
		"$(dirname -- "${journal_previous}")" == "${formal_parent}" &&
		"$(basename -- "${journal_previous}")" == ".${formal_name}.previous-"* ]] || return 1
	[[ "${journal_staging}" != "無" &&
		"$(dirname -- "${journal_staging}")" == "${formal_parent}" &&
		"$(basename -- "${journal_staging}")" == ".${formal_name}.staging-"* ]] || return 1
}

detect_committed_promotion() {
	read_promotion_journal || return 1
	[[ "${journal_phase}" == "已提交" &&
		-d "${formal_release}" && ! -L "${formal_release}" &&
		-d "${journal_previous}" && ! -L "${journal_previous}" &&
		! -e "${journal_staging}" && ! -L "${journal_staging}" ]] || return 1
	promotion_completed="yes"
	previous_path="${journal_previous}"
}

recover_interrupted_promotion() {
	local archived_staging="${output_staging}/中斷提升候選暫存"
	local actual_previous="無" actual_staging="無"
	local marker_valid="no"
	local -a previous_candidates=() staging_candidates=()

	collect_find_paths previous_candidates "${formal_parent}" -mindepth 1 -maxdepth 1 \
		-name ".${formal_name}.previous-*"
	collect_find_paths staging_candidates "${formal_parent}" -mindepth 1 -maxdepth 1 \
		-name ".${formal_name}.staging-*"
	((${#previous_candidates[@]} <= 1 && ${#staging_candidates[@]} <= 1)) || return 96
	if ((${#previous_candidates[@]} == 1)); then
		actual_previous="${previous_candidates[0]}"
		[[ -d "${actual_previous}" && ! -L "${actual_previous}" ]] || return 96
	fi
	if ((${#staging_candidates[@]} == 1)); then
		actual_staging="${staging_candidates[0]}"
		[[ -d "${actual_staging}" && ! -L "${actual_staging}" ]] || return 96
	fi
	if [[ ! -e "${promotion_commit_marker}" && ! -L "${promotion_commit_marker}" &&
		"${actual_previous}" == "無" && "${actual_staging}" == "無" &&
		-d "${formal_release}" && ! -L "${formal_release}" ]]; then
		return 0
	fi
	if read_promotion_journal; then
		marker_valid="yes"
		[[ "${actual_previous}" == "無" || "${journal_previous}" == "${actual_previous}" ]] ||
			return 96
		[[ "${actual_staging}" == "無" || "${journal_staging}" == "${actual_staging}" ]] ||
			return 96
	else
		printf '警告：提升交易日誌遺失或損壞，改以受鎖父目錄的唯一交易拓撲復原。\n' >&2
	fi
	journal_previous="${actual_previous}"
	journal_staging="${actual_staging}"
	[[ ! -L "${formal_release}" ]] || return 96

	if [[ -d "${formal_release}" && "${journal_previous}" != "無" &&
		"${journal_staging}" == "無" ]]; then
		promotion_completed="yes"
		previous_path="${journal_previous}"
		printf '偵測到子程序在正式目錄就位後中斷，將回復提升前正式版本。\n' >&2
		return 0
	fi
	if [[ ! -e "${formal_release}" && "${journal_previous}" != "無" ]]; then
		mv -T -- "${journal_previous}" "${formal_release}" || return 97
		if [[ "${journal_staging}" != "無" ]]; then
			[[ ! -e "${archived_staging}" && ! -L "${archived_staging}" ]] || return 97
			mv -T -- "${journal_staging}" "${archived_staging}" || return 97
		fi
		printf '偵測到子程序在舊正式版本移出後中斷；已復原舊版本並封存候選 staging。\n' >&2
		return 0
	fi
	if [[ -d "${formal_release}" && "${journal_previous}" == "無" &&
		"${journal_staging}" != "無" ]]; then
		[[ ! -e "${archived_staging}" && ! -L "${archived_staging}" ]] || return 97
		mv -T -- "${journal_staging}" "${archived_staging}" || return 97
		printf '偵測到子程序在正式切換前中斷；舊正式版本未變，候選 staging 已封存。\n' >&2
		return 0
	fi
	if [[ -d "${formal_release}" && "${journal_previous}" == "無" &&
		"${journal_staging}" == "無" ]]; then
		[[ "${marker_valid}" == "no" || "${journal_phase}" == "準備" ]] || return 96
		return 0
	fi
	return 96
}

safe_remove_failed_formal() {
	local path="$1"
	[[ "$(dirname -- "${path}")" == "${formal_parent}" ]] || return 1
	case "$(basename -- "${path}")" in
	".${formal_name}.failed-finalizer-"*) rm -rf --one-file-system -- "${path}" ;;
	*) return 1 ;;
	esac
}

rollback_promoted_formal() {
	local failed_path="${formal_parent}/.${formal_name}.failed-finalizer-$$"
	local path
	local -a previous_candidates=()
	[[ "${promotion_completed}" == "yes" && "${formal_validated}" == "no" ]] || return 0
	if [[ -z "${previous_path}" ]]; then
		shopt -s nullglob
		previous_candidates=("${formal_parent}/.${formal_name}.previous-"*)
		shopt -u nullglob
		((${#previous_candidates[@]} == 1)) || {
			printf '嚴重錯誤：正式重新稽核失敗，但無法唯一找出 previous 目錄。\n' >&2
			return 90
		}
		path="${previous_candidates[0]}"
		[[ -d "${path}" && ! -L "${path}" ]] || return 90
		previous_path="${path}"
	fi
	[[ -d "${formal_release}" && ! -L "${formal_release}" ]] || {
		printf '嚴重錯誤：正式重新稽核失敗，且目前正式路徑不可安全回復：%s\n' \
			"${formal_release}" >&2
		return 90
	}
	[[ ! -e "${failed_path}" && ! -L "${failed_path}" ]] || return 90
	mv -T -- "${formal_release}" "${failed_path}" || return 90
	if [[ "${previous_path}" != "無" ]]; then
		if ! mv -T -- "${previous_path}" "${formal_release}"; then
			mv -T -- "${failed_path}" "${formal_release}" || true
			printf '嚴重錯誤：正式重新稽核失敗，舊正式版本也無法復原：%s\n' \
				"${previous_path}" >&2
			return 91
		fi
	fi
	if ! safe_remove_failed_formal "${failed_path}"; then
		printf '嚴重錯誤：舊正式版本已復原，但無法安全清理失敗候選：%s\n' \
			"${failed_path}" >&2
		return 92
	fi
	promotion_completed="no"
	printf '正式重新稽核未通過，已復原提升前正式版本。\n' >&2
}

on_exit() {
	local status=$?
	local marker_status rollback_status restore_status
	trap - EXIT INT TERM HUP
	set +e
	if ((status != 0)); then
		if [[ "${promotion_started}" == "yes" && "${promotion_completed}" == "no" ]]; then
			recover_interrupted_promotion
			marker_status=$?
			if ((marker_status != 0)); then
				printf '嚴重錯誤：提升交易標記存在但無法安全復原：%s\n' \
					"${promotion_commit_marker}" >&2
				status=96
			fi
		fi
		rollback_promoted_formal
		rollback_status=$?
		if ((rollback_status != 0)); then
			status="${rollback_status}"
		fi
		restore_removed_formal_staging
		restore_status=$?
		if ((restore_status != 0)); then
			status="${restore_status}"
		fi
	fi
	publish_output_directory "${status}"
	status=$?
	if [[ -n "${output_final}" && -d "${output_final}" ]]; then
		if ((status == 0)); then
			printf '收斂輸出：%s\n' "${output_final}"
		else
			printf '失敗證據：%s\n' "${output_final}" >&2
		fi
	fi
	exit "${status}"
}

run_logged() {
	local log_path="$1"
	shift
	if "$@" >"${log_path}" 2>&1; then
		return 0
	else
		local status=$?
		printf '步驟失敗，完整輸出：%s\n' "${log_path}" >&2
		cat -- "${log_path}" >&2
		return "${status}"
	fi
}

validate_matrix_size() {
	local counts boards images
	counts="$({
		awk -F '\t' '
			NR == 1 {
				if ($0 != "folder\tboard\tbranch\treleases") exit 10
				next
			}
			NF != 4 || $1 == "" || $2 == "" || $3 == "" || $4 == "" { exit 11 }
			{
				if (folders[$1]++ || boards_seen[$2]++) exit 12
				release_count = split($4, releases, ",")
				for (release_index = 1; release_index <= release_count; release_index++) {
					if (releases[release_index] == "" ||
						release_seen[$1 SUBSEP releases[release_index]]++) exit 13
				}
				boards++
				images += release_count * 2
			}
			END { print boards + 0, images + 0 }
		' "${matrix_file}"
	} 2>/dev/null)" || fail "受控矩陣格式、唯一性或發行版欄位錯誤：${matrix_file}"
	read -r boards images <<<"${counts}"
	[[ "${boards}" == "${EXPECTED_BOARD_TOTAL}" ]] ||
		fail "受控矩陣必須恰好有 ${EXPECTED_BOARD_TOTAL} 板，實際為 ${boards}。"
	[[ "${images}" == "${EXPECTED_IMAGE_TOTAL}" ]] ||
		fail "受控矩陣必須恰好有 ${EXPECTED_IMAGE_TOTAL} 個映像，實際為 ${images}。"
}

reject_compile_process() {
	local process_log="${output_staging}/進行中程序.txt"
	local status
	if pgrep -af '[c]ompile\.sh' >"${process_log}"; then
		printf '錯誤：偵測到進行中的 compile.sh，拒絕收斂。\n' >&2
		cat -- "${process_log}" >&2
		return 1
	else
		status=$?
		[[ "${status}" == 1 ]] || fail "無法可靠檢查進行中的 compile.sh。"
	fi
	rm -f -- "${process_log}"
}

reject_candidate_release_residue() {
	local path
	local -a paths=()
	collect_find_paths paths "${candidate_release}" -mindepth 1 -maxdepth 1 \
		\( -name '.staging-*' -o -name '.failed-*' -o -name '.previous-*' \)
	for path in "${paths[@]}"; do
		fail "候選發布含 staging、failed 或 previous 殘留，已保留現場：${path}"
	done
}

reject_formal_parent_residue() {
	local path
	local -a paths=()
	collect_find_paths paths "${formal_parent}" -mindepth 1 -maxdepth 1 \
		\( -name ".${formal_name}.staging-*" -o \
			-name ".${formal_name}.failed-*" -o \
			-name ".${formal_name}.previous-*" \)
	for path in "${paths[@]}"; do
		fail "正式發布父目錄含既有交易殘留，已保留現場：${path}"
	done
}

remove_safe_formal_staging() {
	local path archive
	local -a staging=()
	local -a residue=()
	collect_find_paths staging "${formal_release}" -mindepth 1 -maxdepth 1 -name '.staging-*'

	for path in "${staging[@]}"; do
		[[ -d "${path}" && ! -L "${path}" ]] ||
			fail "正式舊目錄的 staging 不是實體目錄，已保留現場：${path}"
		directory_is_empty "${path}" ||
			fail "正式舊目錄的 staging 不是空目錄，已保留現場：${path}"
	done

	mkdir -- "${removed_staging_archive}"
	for path in "${staging[@]}"; do
		archive="${removed_staging_archive}/$(basename -- "${path}")"
		[[ ! -e "${archive}" && ! -L "${archive}" ]] ||
			fail "空 staging 封存路徑衝突：${archive}"
		removed_formal_staging+=("${path}")
		archived_formal_staging+=("${archive}")
		mv -T -- "${path}" "${archive}" ||
			fail "無法原樣移出正式舊目錄的空 staging：${path}"
	done

	collect_find_paths residue "${formal_release}" -mindepth 1 -maxdepth 1 \
		\( -name '.failed-*' -o -name '.previous-*' \)
	for path in "${residue[@]}"; do
		fail "正式舊目錄含 failed 或 previous 殘留，已保留現場：${path}"
	done
}

tsv_data_rows() {
	local path="$1"
	awk 'NR > 1 && $0 !~ /^[[:space:]]*$/ { count++ } END { print count + 0 }' "${path}"
}

validate_header() {
	local path="$1" expected="$2" actual
	require_regular_file "${path}" "稽核輸出"
	IFS= read -r actual <"${path}" || fail "稽核輸出是空檔：${path}"
	actual="${actual%$'\r'}"
	[[ "${actual}" == "${expected}" ]] || fail "稽核輸出欄位錯誤：${path}"
}

validate_complete_audit() {
	local audit_root="$1" policy="$2" allow_formal_extras="$3" rows path

	validate_header "${audit_root}/映像盤點.tsv" \
		$'唯一鍵\t板目錄\t板卡\t分支\t發行版\t類型\t狀態\t選用來源\t映像\tSHA256\t來源提交\t建置內容雜湊\t處置'
	rows="$(tsv_data_rows "${audit_root}/映像盤點.tsv")"
	[[ "${rows}" == "${EXPECTED_IMAGE_TOTAL}" ]] ||
		fail "映像盤點必須恰好有 ${EXPECTED_IMAGE_TOTAL} 列，實際為 ${rows}：${audit_root}"
	awk -F '\t' 'NR > 1 && $0 !~ /^[[:space:]]*$/ && (NF != 13 || $7 != "已驗證候選") { exit 1 }' \
		"${audit_root}/映像盤點.tsv" || fail "映像盤點含未驗證候選：${audit_root}"

	validate_header "${audit_root}/板卡決策.tsv" \
		$'板目錄\t板卡\t分支\t預期映像數\t決策\t選用來源'
	rows="$(tsv_data_rows "${audit_root}/板卡決策.tsv")"
	[[ "${rows}" == "${EXPECTED_BOARD_TOTAL}" ]] ||
		fail "板卡決策必須恰好有 ${EXPECTED_BOARD_TOTAL} 列，實際為 ${rows}：${audit_root}"
	awk -F '\t' 'NR > 1 && $0 !~ /^[[:space:]]*$/ && (NF != 6 || $5 != "沿用完整候選") { exit 1 }' \
		"${audit_root}/板卡決策.tsv" || fail "板卡決策含非完整候選：${audit_root}"

	validate_header "${audit_root}/候選輸入政策.tsv" \
		$'folder\tsource_commit\tbuild_context_sha256'
	rows="$(tsv_data_rows "${audit_root}/候選輸入政策.tsv")"
	[[ "${rows}" == "${EXPECTED_BOARD_TOTAL}" ]] ||
		fail "稽核政策必須恰好有 ${EXPECTED_BOARD_TOTAL} 列：${audit_root}"
	cmp -s -- "${policy}" "${audit_root}/候選輸入政策.tsv" ||
		fail "稽核輸出的逐板政策與固定政策不一致：${audit_root}"

	python3 - "${matrix_file}" "${policy}" \
		"${audit_root}/映像盤點.tsv" "${audit_root}/板卡決策.tsv" <<'PY' ||
import csv
import re
import sys
from pathlib import Path

matrix_path, policy_path, ledger_path, decisions_path = map(Path, sys.argv[1:])


def read_rows(path, expected_header):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != expected_header:
            raise SystemExit(f"欄位不符：{path}")
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise SystemExit(f"欄位數不符：{path}:{line_number}")
            if not any(row.values()):
                continue
            if any(value == "" for value in row.values()):
                raise SystemExit(f"欄位不得為空：{path}:{line_number}")
            rows.append(row)
    return rows


matrix_rows = read_rows(matrix_path, ["folder", "board", "branch", "releases"])
policy_rows = read_rows(
    policy_path, ["folder", "source_commit", "build_context_sha256"]
)
policy_by_folder = {row["folder"]: row for row in policy_rows}
if len(policy_by_folder) != len(policy_rows):
    raise SystemExit("候選政策含重複板目錄")
for folder, row in policy_by_folder.items():
    if not re.fullmatch(r"[0-9a-f]{40}", row["source_commit"]):
        raise SystemExit(f"候選政策來源提交格式錯誤：{folder}")
    if not re.fullmatch(r"[0-9a-f]{64}", row["build_context_sha256"]):
        raise SystemExit(f"候選政策建置內容雜湊格式錯誤：{folder}")

expected_images = {}
expected_boards = {}
for row in matrix_rows:
    folder = row["folder"]
    releases = row["releases"].split(",")
    expected_boards[folder] = (row["board"], row["branch"], len(releases) * 2)
    for release in releases:
        for profile in ("minimal", "xfce"):
            key = "|".join(
                (folder, row["board"], row["branch"], release, profile)
            )
            expected_images[key] = (
                folder,
                row["board"],
                row["branch"],
                release,
                profile,
            )
if set(policy_by_folder) != set(expected_boards):
    raise SystemExit("候選政策沒有精確覆蓋受控板目錄")

ledger_rows = read_rows(
    ledger_path,
    [
        "唯一鍵",
        "板目錄",
        "板卡",
        "分支",
        "發行版",
        "類型",
        "狀態",
        "選用來源",
        "映像",
        "SHA256",
        "來源提交",
        "建置內容雜湊",
        "處置",
    ],
)
actual_images = {}
sources_by_folder = {}
for row in ledger_rows:
    key = row["唯一鍵"]
    identity = (
        row["板目錄"],
        row["板卡"],
        row["分支"],
        row["發行版"],
        row["類型"],
    )
    if key in actual_images:
        raise SystemExit(f"映像盤點含重複唯一鍵：{key}")
    actual_images[key] = identity
    if key not in expected_images or expected_images[key] != identity:
        raise SystemExit(f"映像盤點含矩陣外或身分不符項目：{key}")
    policy_row = policy_by_folder[row["板目錄"]]
    if row["來源提交"] != policy_row["source_commit"]:
        raise SystemExit(f"映像來源提交與政策不符：{key}")
    if row["建置內容雜湊"] != policy_row["build_context_sha256"]:
        raise SystemExit(f"映像建置內容雜湊與政策不符：{key}")
    if not re.fullmatch(r"[0-9a-f]{64}", row["SHA256"]):
        raise SystemExit(f"映像 SHA256 格式錯誤：{key}")
    if row["狀態"] != "已驗證候選" or row["處置"] != "不再建置":
        raise SystemExit(f"映像稽核狀態或處置錯誤：{key}")
    sources_by_folder.setdefault(row["板目錄"], set()).add(row["選用來源"])
if actual_images != expected_images:
    missing = sorted(set(expected_images) - set(actual_images))
    raise SystemExit(f"映像盤點沒有精確覆蓋受控矩陣：缺少 {missing[:1]}")

decision_rows = read_rows(
    decisions_path,
    ["板目錄", "板卡", "分支", "預期映像數", "決策", "選用來源"],
)
actual_boards = {}
for row in decision_rows:
    folder = row["板目錄"]
    if folder in actual_boards:
        raise SystemExit(f"板卡決策含重複板目錄：{folder}")
    try:
        image_count = int(row["預期映像數"])
    except ValueError as error:
        raise SystemExit(f"板卡決策映像數不是整數：{folder}") from error
    actual_boards[folder] = (row["板卡"], row["分支"], image_count)
    if row["決策"] != "沿用完整候選":
        raise SystemExit(f"板卡決策不是完整候選：{folder}")
    if sources_by_folder.get(folder) != {row["選用來源"]}:
        raise SystemExit(f"板卡決策與映像選用來源不符：{folder}")
if actual_boards != expected_boards:
    raise SystemExit("板卡決策沒有精確覆蓋受控矩陣")
PY
		fail "稽核帳本沒有精確覆蓋受控矩陣：${audit_root}"

	validate_header "${audit_root}/待辦佇列.tsv" \
		$'板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因'
	validate_header "${audit_root}/中止產物.tsv" \
		$'候選來源\t類別\t大小bytes\t處置\t路徑'
	validate_header "${audit_root}/舊暫存目錄.tsv" \
		$'目錄\t檔案數\t大小bytes\t處置\t路徑'
	validate_header "${audit_root}/矩陣外項目.tsv" \
		$'板目錄\t映像數\t處置\t路徑'
	validate_header "${audit_root}/候選交易殘留.tsv" \
		$'候選來源\t類別\t板目錄\t項目\t檔案數\t大小bytes\t處置\t路徑'
	for path in 待辦佇列.tsv 中止產物.tsv 舊暫存目錄.tsv 候選交易殘留.tsv; do
		rows="$(tsv_data_rows "${audit_root}/${path}")"
		[[ "${rows}" == 0 ]] || fail "完整稽核仍有阻擋項目：${audit_root}/${path}（${rows} 列）"
	done
	rows="$(tsv_data_rows "${audit_root}/矩陣外項目.tsv")"
	if [[ "${allow_formal_extras}" != "yes" && "${rows}" != 0 ]]; then
		fail "正式完整稽核仍有矩陣外項目：${audit_root}/矩陣外項目.tsv（${rows} 列）"
	fi
}

find_previous_path() {
	local path
	local -a previous=()
	collect_find_paths previous "${formal_parent}" -mindepth 1 -maxdepth 1 \
		-name ".${formal_name}.previous-*"
	((${#previous[@]} <= 1)) || fail "提升完成後出現多個 previous 目錄，必須人工查明。"
	if ((${#previous[@]} == 1)); then
		previous_path="${previous[0]}"
	else
		previous_path="無"
	fi
}

write_result_summary() {
	local temporary="${output_staging}/.收斂結果.tsv.tmp"
	{
		printf '項目\t路徑\n'
		printf '候選政策\t%s\n' "${output_final}/候選輸入政策.tsv"
		printf '候選稽核\t%s\n' "${output_final}/候選完整稽核"
		printf '正式政策\t%s\n' "${output_final}/正式輸入政策.tsv"
		printf '正式稽核\t%s\n' "${output_final}/正式完整稽核"
		printf 'previous\t%s\n' "${previous_path}"
	} >"${temporary}"
	mv -- "${temporary}" "${output_staging}/收斂結果.tsv"
}

main() {
	local command tool run_id original_tool_repo original_matrix_file

	(($# == 0)) || fail "本工具只接受環境變數，不接受命令列參數。"
	for command in awk basename cat cmp cp date dirname find flock mapfile mkdir mktemp mv pgrep \
		python3 realpath readlink rm rmdir sha256sum sort stat; do
		require_command "${command}"
	done
	for command in TOOL_REPO MATRIX_FILE CANDIDATE_RELEASE CANDIDATE_STATE FORMAL_RELEASE; do
		require_environment "${command}"
	done

	# 這五個值刻意只由外部環境提供。
	# shellcheck disable=SC2153
	tool_repo="${TOOL_REPO}"
	# shellcheck disable=SC2153
	matrix_file="${MATRIX_FILE}"
	# shellcheck disable=SC2153
	candidate_release="${CANDIDATE_RELEASE}"
	# shellcheck disable=SC2153
	candidate_state="${CANDIDATE_STATE}"
	# shellcheck disable=SC2153
	formal_release="${FORMAL_RELEASE}"
	require_directory "${tool_repo}" "工具倉庫"
	require_regular_file "${matrix_file}" "受控矩陣"
	require_directory "${candidate_release}" "候選發布根目錄"
	require_directory "${candidate_state}" "候選狀態根目錄"
	require_directory "${formal_release}" "正式發布根目錄"

	tool_repo="$(realpath -e -- "${tool_repo}")"
	matrix_file="$(realpath -e -- "${matrix_file}")"
	candidate_release="$(realpath -e -- "${candidate_release}")"
	candidate_state="$(realpath -e -- "${candidate_state}")"
	formal_release="$(realpath -e -- "${formal_release}")"
	formal_parent="$(dirname -- "${formal_release}")"
	formal_name="$(basename -- "${formal_release}")"
	reject_overlapping_paths
	[[ "$(stat -c '%d' -- "${candidate_state}")" == "$(stat -c '%d' -- "${formal_release}")" ]] ||
		fail "候選狀態與正式發布不在同一檔案系統，無法原樣封存及復原空 staging。"

	for tool in \
		generate-bananapi-candidate-input-policy.py \
		generate-bananapi-release-notes.py \
		audit-bananapi-release-state.py \
		promote-bananapi-candidate-release.sh; do
		require_regular_file "${tool_repo}/tools/${tool}" "必要工具"
	done
	validate_matrix_size
	original_tool_repo="${tool_repo}"
	original_matrix_file="${matrix_file}"

	[[ ! -L "${candidate_state}/.incremental-queue.lock" ]] ||
		fail "增量鎖不得為符號連結：${candidate_state}/.incremental-queue.lock"
	exec {lock_fd}<>"${candidate_state}/.incremental-queue.lock"
	flock -n "${lock_fd}" || {
		printf '錯誤：候選狀態的增量佇列仍持有鎖，拒絕收斂。\n' >&2
		exit 73
	}
	candidate_build_lock_path="$(stable_build_lock_path "${candidate_release}")"
	formal_build_lock_path="$(stable_build_lock_path "${formal_release}")"
	acquire_stable_build_lock "${candidate_release}" "候選發布" candidate_build_lock_fd
	acquire_stable_build_lock "${formal_release}" "正式發布" formal_build_lock_fd
	acquire_root_compatibility_lock "${candidate_release}" "候選發布" candidate_compat_lock_fd
	acquire_root_compatibility_lock "${formal_release}" "正式發布" formal_compat_lock_fd
	export BANANAPI_CANDIDATE_BUILD_LOCK_FD="${candidate_build_lock_fd}"
	export BANANAPI_FORMAL_BUILD_LOCK_FD="${formal_build_lock_fd}"
	export BANANAPI_CANDIDATE_COMPAT_LOCK_FD="${candidate_compat_lock_fd}"
	export BANANAPI_FORMAL_COMPAT_LOCK_FD="${formal_compat_lock_fd}"

	run_id="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
	output_final="${candidate_state}/final-${run_id}"
	output_failed="${candidate_state}/failed-final-${run_id}"
	output_staging="${candidate_state}/.final-${run_id}.staging"
	promotion_commit_marker="${output_staging}/提升提交標記.tsv"
	[[ ! -e "${output_final}" && ! -L "${output_final}" &&
		! -e "${output_failed}" && ! -L "${output_failed}" &&
		! -e "${output_staging}" && ! -L "${output_staging}" ]] ||
		fail "本輪輸出路徑已存在，拒絕覆寫：${output_final}"
	mkdir -- "${output_staging}"
	export BANANAPI_PROMOTION_COMMIT_MARKER="${promotion_commit_marker}"
	trap on_exit EXIT
	trap 'exit 130' INT TERM HUP

	candidate_policy="${output_staging}/候選輸入政策.tsv"
	candidate_audit="${output_staging}/候選完整稽核"
	formal_policy="${output_staging}/正式輸入政策.tsv"
	formal_audit="${output_staging}/正式完整稽核"
	removed_staging_archive="${output_staging}/提升前空暫存"
	prepare_input_snapshot
	validate_matrix_size
	printf '原始工具倉庫\t%s\n原始矩陣\t%s\n候選固定鎖\t%s\n正式固定鎖\t%s\n' \
		"${original_tool_repo}" "${original_matrix_file}" \
		"${candidate_build_lock_path}" "${formal_build_lock_path}" \
		>"${output_staging}/執行輸入.tsv"

	reject_compile_process
	require_empty_directory "${candidate_state}/raw-items" "候選 raw-items 目錄"
	require_empty_directory "${candidate_state}/transactions" "候選 transactions 目錄"
	reject_candidate_release_residue
	reject_formal_parent_residue
	remove_safe_formal_staging

	run_logged "${output_staging}/01-產生候選政策.log" \
		python3 "${tool_repo}/tools/generate-bananapi-candidate-input-policy.py" \
		--matrix "${matrix_file}" \
		--candidate-state "${candidate_state}" \
		--output "${candidate_policy}"
	require_regular_file "${candidate_policy}" "候選逐板政策"
	[[ "$(tsv_data_rows "${candidate_policy}")" == "${EXPECTED_BOARD_TOTAL}" ]] ||
		fail "候選逐板政策不是 ${EXPECTED_BOARD_TOTAL} 板：${candidate_policy}"

	run_logged "${output_staging}/02-更新繁中說明.log" \
		python3 "${tool_repo}/tools/generate-bananapi-release-notes.py" \
		--matrix "${matrix_file}" \
		--candidate-release "${candidate_release}" \
		--replace

	run_logged "${output_staging}/03-候選完整稽核.log" \
		python3 "${tool_repo}/tools/audit-bananapi-release-state.py" \
		--matrix "${matrix_file}" \
		--formal-release "${formal_release}" \
		--candidate "整併候選|${candidate_release}|${candidate_state}" \
		--candidate-input-policy "${candidate_policy}" \
		--output-dir "${candidate_audit}" \
		--verify-digests \
		--verify-xz
	validate_complete_audit "${candidate_audit}" "${candidate_policy}" yes

	run_logged "${output_staging}/04-提升預演.log" \
		bash "${tool_repo}/tools/promote-bananapi-candidate-release.sh" \
		--candidate-release "${candidate_release}" \
		--formal-release "${formal_release}" \
		--matrix "${matrix_file}" \
		--audit-output "${candidate_audit}"

	promotion_started="yes"
	run_logged "${output_staging}/05-正式提升.log" \
		bash "${tool_repo}/tools/promote-bananapi-candidate-release.sh" \
		--candidate-release "${candidate_release}" \
		--formal-release "${formal_release}" \
		--matrix "${matrix_file}" \
		--audit-output "${candidate_audit}" \
		--execute
	detect_committed_promotion || fail "正式提升已回傳成功，但缺少可驗證的提交標記。"
	validate_root_compatibility_lock "${candidate_release}" "候選發布" \
		"${candidate_compat_lock_fd}"
	validate_root_compatibility_lock "${formal_release}" "正式發布" \
		"${formal_compat_lock_fd}"
	find_previous_path

	run_logged "${output_staging}/06-正式完整稽核.log" \
		python3 "${tool_repo}/tools/audit-bananapi-release-state.py" \
		--matrix "${matrix_file}" \
		--formal-release "${formal_release}" \
		--candidate "正式發布|${formal_release}|${candidate_state}" \
		--candidate-input-policy "${candidate_policy}" \
		--output-dir "${formal_audit}" \
		--verify-digests \
		--verify-xz
	validate_complete_audit "${formal_audit}" "${candidate_policy}" no
	cp -- "${formal_audit}/候選輸入政策.tsv" "${formal_policy}.tmp"
	mv -- "${formal_policy}.tmp" "${formal_policy}"
	cmp -s -- "${candidate_policy}" "${formal_policy}" ||
		fail "候選政策與正式政策不一致。"
	require_empty_directory "${candidate_state}/raw-items" "最終候選 raw-items 目錄"
	require_empty_directory "${candidate_state}/transactions" "最終候選 transactions 目錄"

	write_result_summary
	formal_validated="yes"
	printf '全矩陣混合來源發布收斂完成：%s 板、%s 個映像。\n' \
		"${EXPECTED_BOARD_TOTAL}" "${EXPECTED_IMAGE_TOTAL}"
	printf '候選政策：%s\n' "${output_final}/候選輸入政策.tsv"
	printf '候選稽核：%s\n' "${output_final}/候選完整稽核"
	printf '正式政策：%s\n' "${output_final}/正式輸入政策.tsv"
	printf '正式稽核：%s\n' "${output_final}/正式完整稽核"
	printf 'previous：%s\n' "${previous_path}"
}

main "$@"
