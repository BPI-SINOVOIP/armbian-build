#!/usr/bin/env bash
set -Eeuo pipefail

readonly OLD_SOURCE_COMMIT="c8673931c96c23c510dd29a28440e77f0b03286f"
readonly OLD_BUILD_CONTEXT="6a9a29372b9ef64baaf790b24ccad2f3b4ba6940a6ddfa932ddd8bb08171a633"
readonly DEFERRED_FOLDER="bpi-m5pro"
readonly EXPECTED_IMAGES=10

tool_repo="${TOOL_REPO:?必須設定 TOOL_REPO}"
build_repo="${BUILD_REPO:?必須設定 BUILD_REPO}"
matrix_file="${MATRIX_FILE:?必須設定 MATRIX_FILE}"
candidate_release="${CANDIDATE_RELEASE:?必須設定 CANDIDATE_RELEASE}"
candidate_state="${CANDIDATE_STATE:?必須設定 CANDIDATE_STATE}"
formal_release="${FORMAL_RELEASE:?必須設定 FORMAL_RELEASE}"
cache_lower="${CACHE_LOWER:?必須設定 CACHE_LOWER}"
cache_overlay_root="${CACHE_OVERLAY_ROOT:?必須設定 CACHE_OVERLAY_ROOT}"
source_commit="${SOURCE_COMMIT:?必須設定 SOURCE_COMMIT}"
source_remote_ref="${SOURCE_REMOTE_REF:?必須設定 SOURCE_REMOTE_REF}"
expected_build_context="${EXPECTED_BUILD_CONTEXT_SHA256:?必須設定 EXPECTED_BUILD_CONTEXT_SHA256}"
container_image="${ARMBIAN_CONTAINER_IMAGE:?必須設定 ARMBIAN_CONTAINER_IMAGE}"
xz_threads="${XZ_THREADS:-6}"
minimum_free_gib="${MINIMUM_FREE_GIB:-120}"

audit_tool="${tool_repo}/tools/audit-bananapi-release-state.py"
policy_tool="${tool_repo}/tools/generate-bananapi-candidate-input-policy.py"
cache_runner="${tool_repo}/tools/run-bananapi-candidates-isolated-cache.sh"
candidate_builder="${build_repo}/tools/rebuild-bananapi-latest-release.sh"

fail() {
	printf '錯誤：%s\n' "$*" >&2
	exit 1
}

require_directory() {
	local path="$1" description="$2"
	[[ -d "${path}" && ! -L "${path}" ]] || fail "${description}不是一般目錄：${path}"
}

require_empty_directory() {
	local path="$1" description="$2"
	require_directory "${path}" "${description}"
	[[ -z "$(find "${path}" -mindepth 1 -maxdepth 1 -print -quit)" ]] || \
		fail "${description}必須為空：${path}"
}

read_marker_value() {
	local marker="$1" key="$2"
	awk -F= -v key="${key}" '
		$1 == key {
			count++
			value = substr($0, length($1) + 2)
		}
		END {
			if (count != 1 || value == "") exit 1
			print value
		}
	' "${marker}"
}

validate_matrix_m5pro() {
	awk -F '\t' '
		NR == 1 {
			if ($0 != "folder\tboard\tbranch\treleases") exit 10
			next
		}
		$1 == "bpi-m5pro" {
			count++
			if ($2 != "bananapim5pro" || $3 != "edge" ||
				$4 != "trixie,bookworm,jammy,noble,resolute") exit 11
		}
		END { if (count != 1) exit 12 }
	' "${matrix_file}" || fail "受控矩陣的 bpi-m5pro 定義不符合十映像延後階段。"
}

validate_worktree() {
	local actual_commit status remote_ref
	actual_commit="$(git -C "${build_repo}" rev-parse HEAD)" || \
		fail "無法讀取建置工作樹提交：${build_repo}"
	[[ "${actual_commit}" == "${source_commit}" ]] || \
		fail "建置工作樹不是指定提交：預期 ${source_commit}，實際 ${actual_commit}。"
	status="$(git -C "${build_repo}" status --porcelain=v1 --untracked-files=normal)" || \
		fail "無法檢查建置工作樹狀態。"
	[[ -z "${status}" ]] || fail "建置工作樹不乾淨，拒絕接續。"
	remote_ref="refs/remotes/${source_remote_ref}"
	git -C "${build_repo}" show-ref --verify --quiet "${remote_ref}" || \
		fail "找不到遠端追蹤分支：${source_remote_ref}"
	git -C "${build_repo}" merge-base --is-ancestor "${source_commit}" "${source_remote_ref}" || \
		fail "指定建置提交尚未推送到 ${source_remote_ref}：${source_commit}"
}

remove_safe_old_staging() {
	local staging="${candidate_release}/.staging-${DEFERRED_FOLDER}-${OLD_SOURCE_COMMIT:0:12}"
	[[ ! -e "${staging}" && ! -L "${staging}" ]] && return 0
	[[ -d "${staging}" && ! -L "${staging}" ]] || \
		fail "舊來源 M5 Pro staging 不是一般目錄，拒絕移除：${staging}"
	[[ -z "$(find "${staging}" -mindepth 1 -maxdepth 1 -print -quit)" ]] || \
		fail "舊來源 M5 Pro staging 不是空目錄，保留現場：${staging}"
	rmdir -- "${staging}" || fail "無法移除空的舊來源 M5 Pro staging：${staging}"
	printf '已移除空的舊來源 M5 Pro staging：%s\n' "${staging}"
}

validate_preflight_queue() {
	local queue="$1"
	[[ -f "${queue}" && ! -L "${queue}" ]] || fail "前置盤點未產生待辦佇列：${queue}"
	awk -F '\t' '
		BEGIN {
			release_count = split("trixie,bookworm,jammy,noble,resolute", releases, ",")
			profile_count = split("minimal,xfce", profiles, ",")
			for (release_index = 1; release_index <= release_count; release_index++) {
				for (profile_index = 1; profile_index <= profile_count; profile_index++) {
					expected[releases[release_index] SUBSEP profiles[profile_index]] = 1
				}
			}
		}
		NR == 1 {
			if ($0 != "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因") exit 10
			next
		}
		NF != 7 || $1 != "bpi-m5pro" || $2 != "bananapim5pro" ||
			$3 != "edge" || $6 != "建置缺少項目" { exit 11 }
		{
			key = $4 SUBSEP $5
			if (!(key in expected) || seen[key]++) exit 12
			count++
		}
		END {
			if (count != 10) exit 13
			for (key in expected) if (!(key in seen)) exit 14
		}
	' "${queue}" || fail "舊來源盤點必須恰好只剩 bpi-m5pro 十個建置缺少項目。"
}

validate_board_result() {
	local marker="${candidate_state}/boards/${DEFERRED_FOLDER}.complete"
	local release_dir="${candidate_release}/${DEFERRED_FOLDER}"
	[[ -f "${marker}" && ! -L "${marker}" ]] || fail "缺少 M5 Pro 板級完成標記：${marker}"
	[[ "$(read_marker_value "${marker}" source_commit)" == "${source_commit}" ]] || \
		fail "M5 Pro 板級標記的來源提交不符。"
	[[ "$(read_marker_value "${marker}" build_context_sha256)" == "${expected_build_context}" ]] || \
		fail "M5 Pro 板級標記的建置內容雜湊不符。"
	[[ "$(read_marker_value "${marker}" folder)" == "${DEFERRED_FOLDER}" ]] || \
		fail "M5 Pro 板級標記的板目錄不符。"
	[[ "$(read_marker_value "${marker}" images)" == "${EXPECTED_IMAGES}" ]] || \
		fail "M5 Pro 板級標記的映像數不是 ${EXPECTED_IMAGES}。"
	[[ "$(read_marker_value "${marker}" status)" == complete ]] || \
		fail "M5 Pro 板級標記尚未完成。"
	require_directory "${release_dir}" "M5 Pro 候選發布目錄"
	[[ "$(find "${release_dir}" -maxdepth 1 -type f -name '*.img.xz' -print | wc -l)" -eq "${EXPECTED_IMAGES}" ]] || \
		fail "M5 Pro 候選發布目錄不是十個 XZ 映像。"
	[[ "$(find "${release_dir}" -maxdepth 1 -type f -name '*.img.xz.sha' -print | wc -l)" -eq "${EXPECTED_IMAGES}" ]] || \
		fail "M5 Pro 候選發布目錄不是十個同名 SHA 邊車。"
	while IFS= read -r -d '' archive; do
		[[ -f "${archive}.sha" && ! -L "${archive}.sha" ]] || \
			fail "M5 Pro 映像缺少同名 SHA 邊車：${archive}"
	done < <(find "${release_dir}" -maxdepth 1 -type f -name '*.img.xz' -print0)
}

validate_zero_pending() {
	local queue="$1"
	[[ -f "${queue}" && ! -L "${queue}" ]] || fail "最終結構稽核未產生待辦佇列：${queue}"
	awk -F '\t' '
		NR == 1 {
			if ($0 != "板目錄\t板卡\t分支\t發行版\t類型\t動作\t原因") exit 10
			next
		}
		{ count++ }
		END { if (count != 0) exit 11 }
	' "${queue}" || fail "逐板政策結構稽核仍有待辦項目。"
}

(($# == 0)) || fail "本工具只接受環境變數，不接受命令列參數。"

for command in awk date find flock git mkdir mktemp pgrep python3 readlink rmdir wc; do
	command -v "${command}" >/dev/null || fail "缺少必要命令：${command}"
done
[[ "${source_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "SOURCE_COMMIT 格式錯誤。"
[[ "${expected_build_context}" =~ ^[0-9a-f]{64}$ ]] || \
	fail "EXPECTED_BUILD_CONTEXT_SHA256 格式錯誤。"
if [[ ! "${xz_threads}" =~ ^[0-9]+$ ]] || ((xz_threads == 0)); then
	fail "XZ_THREADS 必須是正整數。"
fi
if [[ ! "${minimum_free_gib}" =~ ^[0-9]+$ ]] || ((minimum_free_gib == 0)); then
	fail "MINIMUM_FREE_GIB 必須是正整數。"
fi

require_directory "${tool_repo}" "工具倉庫"
require_directory "${build_repo}" "建置工作樹"
require_directory "${candidate_release}" "候選發布根目錄"
require_directory "${candidate_state}" "候選狀態根目錄"
require_directory "${formal_release}" "正式發布根目錄"
require_directory "${cache_lower}" "唯讀快取下層"
[[ -f "${matrix_file}" && ! -L "${matrix_file}" ]] || fail "受控矩陣不是一般檔案：${matrix_file}"
[[ -f "${audit_tool}" && ! -L "${audit_tool}" ]] || fail "找不到候選稽核工具：${audit_tool}"
[[ -f "${policy_tool}" && ! -L "${policy_tool}" ]] || fail "找不到逐板政策產生器：${policy_tool}"
[[ -x "${cache_runner}" && ! -L "${cache_runner}" ]] || fail "找不到隔離快取執行器：${cache_runner}"
[[ -x "${candidate_builder}" && ! -L "${candidate_builder}" ]] || fail "找不到指定建置工作樹的重建工具：${candidate_builder}"
validate_matrix_m5pro
validate_worktree

mkdir -p "${candidate_state}/audits"
exec 8>"${candidate_state}/.incremental-queue.lock"
flock -n 8 || {
	printf '錯誤：前一增量佇列仍持有鎖，拒絕啟動 M5 Pro 延後階段。\n' >&2
	exit 73
}
if pgrep -af '[c]ompile.sh.*build' >/dev/null; then
	printf '錯誤：偵測到其他 Armbian compile.sh build，拒絕啟動。\n' >&2
	pgrep -af '[c]ompile.sh.*build' >&2 || true
	exit 1
fi
require_empty_directory "${candidate_state}/raw-items" "原始映像狀態目錄"
require_empty_directory "${candidate_state}/transactions" "候選交易狀態目錄"
remove_safe_old_staging

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
preflight_audit="${candidate_state}/audits/deferred-m5pro-preflight-${run_id}"
final_audit="${candidate_state}/audits/deferred-m5pro-final-${run_id}"
policy_file="${candidate_state}/audits/deferred-m5pro-policy-${run_id}.tsv"
[[ ! -e "${preflight_audit}" && ! -e "${final_audit}" && ! -e "${policy_file}" ]] || \
	fail "本輪稽核輸出路徑已存在，拒絕覆寫。"

printf '以舊來源身分執行 M5 Pro 延後階段前置盤點。\n'
python3 "${audit_tool}" \
	--matrix "${matrix_file}" \
	--formal-release "${formal_release}" \
	--target-source-commit "${OLD_SOURCE_COMMIT}" \
	--target-build-context "${OLD_BUILD_CONTEXT}" \
	--candidate "整併候選|${candidate_release}|${candidate_state}" \
	--output-dir "${preflight_audit}"
validate_preflight_queue "${preflight_audit}/待辦佇列.tsv"

printf '開始受控接續 M5 Pro 十映像建置。\n'
CANDIDATE_BUILDER="${candidate_builder}" \
CACHE_LOWER="${cache_lower}" \
CACHE_TARGET="${build_repo}/cache" \
CACHE_OVERLAY_ROOT="${cache_overlay_root}" \
REPO_DIR="${build_repo}" \
MATRIX_FILE="${matrix_file}" \
RELEASE_ROOT="${candidate_release}" \
STATE_ROOT="${candidate_state}" \
SOURCE_COMMIT="${source_commit}" \
SOURCE_REMOTE_REF="${source_remote_ref}" \
EXPECTED_BUILD_CONTEXT_SHA256="${expected_build_context}" \
ARMBIAN_CONTAINER_IMAGE="${container_image}" \
XZ_THREADS="${xz_threads}" \
MINIMUM_FREE_GIB="${minimum_free_gib}" \
	"${cache_runner}" --board "${DEFERRED_FOLDER}"

validate_board_result
require_empty_directory "${candidate_state}/raw-items" "建置後原始映像狀態目錄"
require_empty_directory "${candidate_state}/transactions" "建置後候選交易狀態目錄"

printf '產生完整逐板候選輸入政策。\n'
python3 "${policy_tool}" \
	--matrix "${matrix_file}" \
	--candidate-state "${candidate_state}" \
	--output "${policy_file}"
[[ -f "${policy_file}" && ! -L "${policy_file}" ]] || fail "逐板政策未正確產生：${policy_file}"

printf '執行不重跑完整 XZ 串流的逐板政策結構稽核。\n'
python3 "${audit_tool}" \
	--matrix "${matrix_file}" \
	--formal-release "${formal_release}" \
	--candidate "整併候選|${candidate_release}|${candidate_state}" \
	--candidate-input-policy "${policy_file}" \
	--output-dir "${final_audit}"
validate_zero_pending "${final_audit}/待辦佇列.tsv"
require_empty_directory "${candidate_state}/raw-items" "最終原始映像狀態目錄"
require_empty_directory "${candidate_state}/transactions" "最終候選交易狀態目錄"

printf 'M5 Pro 延後階段接續完成：十個映像、逐板政策與零待辦結構稽核均已通過。\n'
printf '逐板政策：%s\n最終結構稽核：%s\n' "${policy_file}" "${final_audit}"
