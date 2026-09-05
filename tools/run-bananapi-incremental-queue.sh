#!/usr/bin/env bash
set -Eeuo pipefail

tool_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
matrix_file="${MATRIX_FILE:-${tool_repo}/config/bananapi-latest-release-matrix.tsv}"
group_file="${GROUP_FILE:-${tool_repo}/config/bananapi-latest-build-groups.tsv}"
formal_release="${FORMAL_RELEASE:?必須設定 FORMAL_RELEASE}"
candidate_release="${CANDIDATE_RELEASE:?必須設定 CANDIDATE_RELEASE}"
candidate_state="${CANDIDATE_STATE:?必須設定 CANDIDATE_STATE}"
build_repo="${BUILD_REPO:?必須設定 BUILD_REPO}"
source_commit="${SOURCE_COMMIT:?必須設定 SOURCE_COMMIT}"
build_context="${BUILD_CONTEXT_SHA256:?必須設定 BUILD_CONTEXT_SHA256}"
container_image="${ARMBIAN_CONTAINER_IMAGE:?必須設定 ARMBIAN_CONTAINER_IMAGE}"
source_remote_ref="${SOURCE_REMOTE_REF:-origin/bpi-integration-20260829}"
audit_root="${AUDIT_ROOT:-${candidate_state}/audits}"
max_raw_queue="${MAX_RAW_QUEUE:-4}"
minimum_free_gib="${MINIMUM_FREE_GIB:-40}"
compression_xz_threads="${COMPRESSION_XZ_THREADS:-2}"
defer_folders="${DEFER_FOLDERS:-}"
run_uuid="$(uuidgen)"
progress="${candidate_state}/runs/queue-${run_uuid}.tsv"
compression_progress="${candidate_state}/runs/compression-${run_uuid}.tsv"
compression_log="${candidate_state}/runs/compression-${run_uuid}.log"
done_signal="${candidate_state}/runs/compression-producer-done-${run_uuid}"
worklist="${candidate_state}/runs/worklist-${run_uuid}.tsv"
compressor_pid=""

required_commands=(awk cut date df find flock git kill mkdir mv python3 readlink rm sleep sort touch uuidgen wait)
for command in "${required_commands[@]}"; do
	command -v "${command}" >/dev/null || {
		printf '缺少必要命令：%s\n' "${command}" >&2
		exit 1
	}
done

[[ "${source_commit}" =~ ^[0-9a-f]{40}$ ]] || {
	printf 'SOURCE_COMMIT 格式錯誤。\n' >&2
	exit 2
}
[[ "${build_context}" =~ ^[0-9a-f]{64}$ ]] || {
	printf 'BUILD_CONTEXT_SHA256 格式錯誤。\n' >&2
	exit 2
}
for value_name in max_raw_queue minimum_free_gib compression_xz_threads; do
	value="${!value_name}"
	[[ "${value}" =~ ^[0-9]+$ ]] || {
		printf '%s 必須是非負整數。\n' "${value_name}" >&2
		exit 2
	}
done
((max_raw_queue > 0)) || {
	printf 'MAX_RAW_QUEUE 必須大於零。\n' >&2
	exit 2
}
for path in "${matrix_file}" "${group_file}" "${formal_release}" "${candidate_release}" \
	"${candidate_state}" "${build_repo}"; do
	[[ -e "${path}" ]] || {
		printf '必要路徑不存在：%s\n' "${path}" >&2
		exit 1
	}
done
[[ "$(git -C "${build_repo}" rev-parse HEAD)" == "${source_commit}" ]] || {
	printf '建置來源工作樹不是指定提交：%s\n' "${source_commit}" >&2
	exit 1
}
[[ -z "$(git -C "${build_repo}" status --porcelain=v1 --untracked-files=normal)" ]] || {
	printf '建置來源工作樹不乾淨，拒絕執行。\n' >&2
	exit 1
}

awk -F '\t' '
	NR == FNR {
		if (NR == 1 && $0 != "folder\tboard\tbranch\treleases") exit 10
		if (NR > 1) matrix[$1] = $2 SUBSEP $3
		next
	}
	FNR == 1 {
		if ($0 != "folder\tboard\tbranch\tarchitecture\tarmbian_arch\tfamily") exit 11
		next
	}
	NF != 6 || seen[$1]++ { exit 12 }
	$4 !~ /^(arm32|arm64|riscv64)$/ { exit 13 }
	($4 == "arm32" && $5 != "armhf") || ($4 != "arm32" && $4 != $5) { exit 14 }
	!($1 in matrix) || matrix[$1] != $2 SUBSEP $3 { exit 15 }
	{ grouped[$1] = 1 }
	END {
		for (folder in matrix) if (!(folder in grouped)) exit 16
	}
' "${matrix_file}" "${group_file}" || {
	printf '架構分組與受控矩陣不一致。\n' >&2
	exit 2
}

folder_is_deferred() {
	local folder="$1"
	[[ " ${defer_folders} " == *" ${folder} "* ]]
}

for deferred_folder in ${defer_folders}; do
	awk -F '\t' -v folder="${deferred_folder}" '
		NR > 1 && $1 == folder { found = 1 }
		END { exit(found ? 0 : 1) }
	' "${matrix_file}" || {
		printf '延後板目錄不在受控矩陣：%s。\n' "${deferred_folder}" >&2
		exit 2
	}
done

mkdir -p "${audit_root}" "${candidate_state}/runs"
resolved_state="$(readlink -f -- "${candidate_state}")"
resolved_audit="$(readlink -f -- "${audit_root}")"
[[ "${resolved_audit}" == "${resolved_state}"/* ]] || {
	printf 'AUDIT_ROOT 必須位於 CANDIDATE_STATE 之下。\n' >&2
	exit 2
}
exec 8> "${candidate_state}/.incremental-queue.lock"
flock -n 8 || {
	printf '另一個增量佇列正在執行。\n' >&2
	exit 73
}

run_audit() {
	local temporary="${audit_root}/.current-${run_uuid}"
	[[ "$(readlink -m -- "${temporary}")" == "${resolved_audit}"/.current-* ]] || return 1
	rm -rf -- "${temporary}"
	python3 "${tool_repo}/tools/audit-bananapi-release-state.py" \
		--matrix "${matrix_file}" \
		--formal-release "${formal_release}" \
		--target-source-commit "${source_commit}" \
		--target-build-context "${build_context}" \
		--candidate "整併候選|${candidate_release}|${candidate_state}" \
		--output-dir "${temporary}"
	rm -rf -- "${audit_root}/current.previous-${run_uuid}"
	if [[ -d "${audit_root}/current" ]]; then
		mv -T "${audit_root}/current" "${audit_root}/current.previous-${run_uuid}"
	fi
	mv -T "${temporary}" "${audit_root}/current"
	rm -rf -- "${audit_root}/current.previous-${run_uuid}"
}

queue_count() {
	awk 'END { print (NR > 0 ? NR - 1 : 0) }' "${audit_root}/current/待辦佇列.tsv"
}

board_queue_count() {
	local folder="$1"
	awk -F '\t' -v folder="${folder}" 'NR > 1 && $1 == folder { count++ } END { print count + 0 }' \
		"${audit_root}/current/待辦佇列.tsv"
}

action_count() {
	local action="$1"
	awk -F '\t' -v action="${action}" 'NR > 1 && $6 == action { count++ } END { print count + 0 }' \
		"${audit_root}/current/待辦佇列.tsv"
}

item_action_count() {
	local folder="$1" release="$2" profile="$3" action="$4"
	awk -F '\t' -v folder="${folder}" -v release="${release}" -v profile="${profile}" \
		-v action="${action}" \
		'NR > 1 && $1 == folder && $4 == release && $5 == profile && $6 == action { count++ }
		END { print count + 0 }' "${audit_root}/current/待辦佇列.tsv"
}

raw_queue_count() {
	find "${candidate_state}/raw-items" -maxdepth 1 -type f \
		\( -name '*.handoff' -o -name '*.ready' -o -name '*.compressing' \) -print |
		awk 'END { print NR + 0 }'
}

compressor_is_alive() {
	[[ -n "${compressor_pid}" ]] && kill -0 "${compressor_pid}" 2>/dev/null
}

stop_compressor_on_exit() {
	local status=$?
	if compressor_is_alive; then
		kill "${compressor_pid}" 2>/dev/null || true
		wait "${compressor_pid}" 2>/dev/null || true
	fi
	return "${status}"
}
trap stop_compressor_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_rebuild() {
	DEFER_XZ="${1}" \
	REPO_DIR="${build_repo}" \
	MATRIX_FILE="${matrix_file}" \
	RELEASE_ROOT="${candidate_release}" \
	STATE_ROOT="${candidate_state}" \
	SOURCE_COMMIT="${source_commit}" \
	SOURCE_REMOTE_REF="${source_remote_ref}" \
	EXPECTED_BUILD_CONTEXT_SHA256="${build_context}" \
	ARMBIAN_CONTAINER_IMAGE="${container_image}" \
	MINIMUM_FREE_GIB="${minimum_free_gib}" \
		"${tool_repo}/tools/rebuild-bananapi-latest-release.sh" "${@:2}"
}

start_compressor() {
	rm -f -- "${done_signal}"
	CANDIDATE_RELEASE="${candidate_release}" \
	CANDIDATE_STATE="${candidate_state}" \
	SOURCE_COMMIT="${source_commit}" \
	BUILD_CONTEXT_SHA256="${build_context}" \
	COMPRESSION_DONE_SIGNAL="${done_signal}" \
	COMPRESSION_PROGRESS="${compression_progress}" \
	XZ_THREADS="${compression_xz_threads}" \
		"${tool_repo}/tools/compress-bananapi-image-queue.sh" \
		>> "${compression_log}" 2>&1 &
	compressor_pid=$!
	sleep 1
	compressor_is_alive || {
		wait "${compressor_pid}" || true
		printf '壓縮工作無法啟動：%s\n' "${compression_log}" >&2
		exit 1
	}
}

wait_for_capacity() {
	local raw_count free_kib required_kib
	required_kib=$((minimum_free_gib * 1024 * 1024))
	while true; do
		compressor_is_alive || {
			printf '壓縮工作意外停止：%s\n' "${compression_log}" >&2
			return 1
		}
		raw_count="$(raw_queue_count)"
		free_kib="$(df -Pk "${candidate_state}" | awk 'NR == 2 { print $4 }')"
		if ((raw_count < max_raw_queue && free_kib >= required_kib)); then
			return 0
		fi
		printf '等待壓縮釋放容量：原始映像 %s/%s，可用 %s KiB。\n' \
			"${raw_count}" "${max_raw_queue}" "${free_kib}"
		sleep 15
	done
}

finalize_pending_boards() {
	local folder board_remaining
	while mapfile -t finalize_folders < <(
		awk -F '\t' 'NR > 1 && $6 == "補整板驗證" && !seen[$1]++ { print $1 }' \
			"${audit_root}/current/待辦佇列.tsv"
	) && ((${#finalize_folders[@]})); do
		for folder in "${finalize_folders[@]}"; do
			printf '執行整板發布驗證：%s。\n' "${folder}"
			run_rebuild no --board "${folder}"
			run_audit
			board_remaining="$(board_queue_count "${folder}")"
			((board_remaining == 0)) || {
				printf '整板發布驗證後仍有待辦：%s。\n' "${folder}" >&2
				return 1
			}
		done
	done
}

printf '時間UTC\t架構\t發行版\t類型\t元件群組\t板目錄\t板卡\t分支\t執行前待編譯\t執行後待編譯\t原始映像佇列\t狀態\n' > "${progress}"
run_audit
finalize_pending_boards
start_compressor

awk -F '\t' '
	NR == FNR {
		if (FNR > 1) {
			architecture[$1] = $4
			family[$1] = $6
			board_order[$1] = FNR
		}
		next
	}
	FNR > 1 && $6 == "建置缺少項目" {
		architecture_order = architecture[$1] == "arm32" ? 0 :
			(architecture[$1] == "arm64" ? 1 : 2)
		release_order = $4 == "trixie" ? 0 : ($4 == "bookworm" ? 1 :
			($4 == "jammy" ? 2 : ($4 == "noble" ? 3 : 4)))
		profile_order = $5 == "minimal" ? 0 : 1
		print architecture_order "\t" release_order "\t" profile_order "\t" \
			family[$1] "\t" board_order[$1] "\t" architecture[$1] "\t" \
			family[$1] "\t" $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5
	}
' "${group_file}" "${audit_root}/current/待辦佇列.tsv" |
	LC_ALL=C sort -t $'\t' -k1,1n -k2,2n -k3,3n -k4,4 -k5,5n |
	cut -f6- > "${worklist}"

while IFS=$'\t' read -r architecture family folder board branch release profile; do
	item_pending=""
	[[ -n "${folder}" ]] || continue
	if folder_is_deferred "${folder}"; then
		printf '受控延後：%s / %s / %s，本階段不建置。\n' \
			"${folder}" "${release}" "${profile}"
		continue
	fi
	run_audit
	item_pending="$(item_action_count "${folder}" "${release}" "${profile}" "建置缺少項目")"
	if ((item_pending == 0)); then
		printf '%s %s %s：已完成或已在壓縮佇列，跳過。\n' "${folder}" "${release}" "${profile}"
		continue
	fi
	wait_for_capacity
	before="$(action_count "建置缺少項目")"
	printf '開始建置：%s / %s / %s / %s。\n' "${architecture}" "${release}" "${profile}" "${folder}"
	run_rebuild yes --board "${folder}" --release "${release}" --profile "${profile}"
	run_audit
	after="$(action_count "建置缺少項目")"
	raw_count="$(raw_queue_count)"
	item_pending="$(item_action_count "${folder}" "${release}" "${profile}" "建置缺少項目")"
	if ((item_pending != 0 || after >= before)); then
		printf '單項建置未離開待編譯狀態：%s %s %s，%s -> %s。\n' \
			"${folder}" "${release}" "${profile}" "${before}" "${after}" >&2
		status="進度異常"
	else
		status="已移交壓縮或完成"
	fi
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${architecture}" "${release}" "${profile}" \
		"${family}" "${folder}" "${board}" "${branch}" "${before}" "${after}" \
		"${raw_count}" "${status}" >> "${progress}"
	[[ "${status}" != "進度異常" ]] || exit 1
done < "${worklist}"

touch "${done_signal}"
if ! wait "${compressor_pid}"; then
	compressor_pid=""
	printf '壓縮工作失敗：%s\n' "${compression_log}" >&2
	exit 1
fi
compressor_pid=""
run_audit
waiting_compression="$(action_count "等待壓縮")"
((waiting_compression == 0)) || {
	printf '壓縮工作結束後仍有待壓縮項目。\n' >&2
	exit 1
}
finalize_pending_boards
run_audit
remaining="$(queue_count)"
if ((remaining != 0)); then
	nondeferred=0
	while IFS=$'\t' read -r folder _; do
		[[ "${folder}" == folder ]] && continue
		folder_is_deferred "${folder}" || nondeferred=$((nondeferred + 1))
	done < "${audit_root}/current/待辦佇列.tsv"
	((nondeferred == 0)) || {
		printf '矩陣走完後仍有 %s 個非延後待辦，拒絕宣告本階段完成。\n' \
			"${nondeferred}" >&2
		exit 1
	}
	printf '本階段已完成；受控延後項目尚餘 %s 個：%s。\n' \
		"${remaining}" "${defer_folders}"
	exit 0
fi
printf '增量佇列完成，待辦為零：%s；壓縮紀錄：%s。\n' "${progress}" "${compression_progress}"
