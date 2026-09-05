#!/usr/bin/env bash
set -Eeuo pipefail

tool_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
matrix_file="${MATRIX_FILE:-${tool_repo}/config/bananapi-latest-release-matrix.tsv}"
formal_release="${FORMAL_RELEASE:?必須設定 FORMAL_RELEASE}"
candidate_release="${CANDIDATE_RELEASE:?必須設定 CANDIDATE_RELEASE}"
candidate_state="${CANDIDATE_STATE:?必須設定 CANDIDATE_STATE}"
build_repo="${BUILD_REPO:?必須設定 BUILD_REPO}"
source_commit="${SOURCE_COMMIT:?必須設定 SOURCE_COMMIT}"
build_context="${BUILD_CONTEXT_SHA256:?必須設定 BUILD_CONTEXT_SHA256}"
container_image="${ARMBIAN_CONTAINER_IMAGE:?必須設定 ARMBIAN_CONTAINER_IMAGE}"
source_remote_ref="${SOURCE_REMOTE_REF:-origin/bpi-integration-20260829}"
audit_root="${AUDIT_ROOT:-${candidate_state}/audits}"
run_uuid="$(uuidgen)"
progress="${candidate_state}/runs/queue-${run_uuid}.tsv"
declare -a matrix_rows

required_commands=(awk date flock git mkdir mv python3 readlink uuidgen)
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
for path in "${matrix_file}" "${formal_release}" "${candidate_release}" \
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
	awk 'END { print NR > 0 ? NR - 1 : 0 }' "${audit_root}/current/待辦佇列.tsv"
}

board_queue_count() {
	local folder="$1"
	awk -F '\t' -v folder="${folder}" 'NR > 1 && $1 == folder { count++ } END { print count + 0 }' \
		"${audit_root}/current/待辦佇列.tsv"
}

printf '時間UTC\t板目錄\t板卡\t分支\t執行前待辦\t板卡待辦\t執行後待辦\t狀態\n' > "${progress}"
run_audit
mapfile -t matrix_rows < <(awk -F '\t' 'NR > 1 { print $1 "\t" $2 "\t" $3 }' "${matrix_file}")

for matrix_row in "${matrix_rows[@]}"; do
	IFS=$'\t' read -r folder board branch <<< "${matrix_row}"
	before="$(queue_count)"
	board_before="$(board_queue_count "${folder}")"
	if ((board_before == 0)); then
		printf '%s：帳本無待辦，跳過。\n' "${folder}"
		printf '%s\t%s\t%s\t%s\t%s\t0\t%s\t跳過已完成\n' \
			"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${folder}" "${board}" "${branch}" \
			"${before}" "${before}" >> "${progress}"
		continue
	fi

	printf '%s：執行前全域待辦 %s，本板待辦 %s。\n' \
		"${folder}" "${before}" "${board_before}"
	REPO_DIR="${build_repo}" \
	MATRIX_FILE="${matrix_file}" \
	RELEASE_ROOT="${candidate_release}" \
	STATE_ROOT="${candidate_state}" \
	SOURCE_COMMIT="${source_commit}" \
	SOURCE_REMOTE_REF="${source_remote_ref}" \
	EXPECTED_BUILD_CONTEXT_SHA256="${build_context}" \
	ARMBIAN_CONTAINER_IMAGE="${container_image}" \
		"${tool_repo}/tools/rebuild-bananapi-latest-release.sh" --board "${folder}"

	run_audit
	after="$(queue_count)"
	board_after="$(board_queue_count "${folder}")"
	if ((board_after != 0 || after >= before)); then
		printf '%s 執行後待辦未依預期下降：全域 %s -> %s，本板剩餘 %s。\n' \
			"${folder}" "${before}" "${after}" "${board_after}" >&2
		printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t進度異常\n' \
			"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${folder}" "${board}" "${branch}" \
			"${before}" "${board_before}" "${after}" >> "${progress}"
		exit 1
	fi
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t完成\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${folder}" "${board}" "${branch}" \
		"${before}" "${board_before}" "${after}" >> "${progress}"
done

remaining="$(queue_count)"
((remaining == 0)) || {
	printf '矩陣走完後仍有 %s 個待辦，拒絕宣告完成。\n' "${remaining}" >&2
	exit 1
}
printf '增量佇列完成，待辦為零：%s\n' "${progress}"
