#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-rockchip-rk3308-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-rockchip-rk3308-trixie-current-cli}"
boards_text="${BOARDS:-bananapip2pro}"
generic_builder="${GENERIC_CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-sunxi-candidates.sh}"

for command in awk cut date git grep mv python3 sha256sum; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "Rockchip 建置拒絕：$*" >&2
	exit 1
}

[[ -x "${generic_builder}" ]] || fail "找不到共用候選建置器：${generic_builder}"
[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="Rockchip" \
	CANDIDATE_LOCK_FILE=".bananapi-rockchip-build.lock" \
	"${generic_builder}" "$@"

mark_evidence_failure() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 ]]; then
		{
			printf '{\n'
			printf '  "status": "failed",\n'
			printf '  "detail": "Rockchip 固定來源證據建立失敗",\n'
			printf '  "source_commit": "%s",\n' "$(git -C "${repo_dir}" rev-parse HEAD)"
			printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
			printf '}\n'
		} >"${output_dir}/COMPLETION_STATUS.json.partial"
		mv "${output_dir}/COMPLETION_STATUS.json.partial" \
			"${output_dir}/COMPLETION_STATUS.json"
	fi
	exit "${exit_status}"
}
trap mark_evidence_failure EXIT

matrix_file="${output_dir}/CANDIDATES.tsv"
[[ -s "${matrix_file}" ]] || fail "缺少候選映像矩陣"
candidate_commit="$(awk -F '\t' '
	NR == 1 { next }
	NR == 2 { commit = $10; next }
	$10 != commit { exit 2 }
	END {
		if (NR < 2) exit 3
		print commit
	}
' "${matrix_file}")" || fail "候選映像矩陣包含不一致的來源提交"
[[ "${candidate_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "候選來源提交格式不符"
current_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
[[ "${current_commit}" == "${candidate_commit}" ]] ||
	fail "建置期間來源提交已改變：候選 ${candidate_commit}，目前 ${current_commit}"

rkbin_dir="${repo_dir}/cache/sources/rkbin-tools"
[[ -d "${rkbin_dir}/.git" ]] || fail "找不到 rkbin 來源工作樹"
expected_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["rkbin_commit"])
PY
)"
actual_commit="$(git -C "${rkbin_dir}" rev-parse HEAD)"
[[ "${actual_commit}" == "${expected_commit}" ]] ||
	fail "rkbin 提交不符：預期 ${expected_commit}，實際 ${actual_commit}"
unexpected_changes="$(git -C "${rkbin_dir}" status --porcelain --untracked-files=all |
	grep -v '^?? .commit_id$' || true)"
[[ -z "${unexpected_changes}" ]] || fail "rkbin 工作樹含有非預期變更"

manifest="${output_dir}/RKBIN_EVIDENCE.tsv"
printf 'path\tsha256\n' >"${manifest}.partial"
while IFS=$'\t' read -r relative expected_sha256; do
	path="${rkbin_dir}/${relative}"
	[[ -f "${path}" ]] || fail "rkbin 缺少檔案：${relative}"
	actual_sha256="$(sha256sum "${path}" | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] ||
		fail "rkbin 檔案雜湊不符：${relative}"
	printf '%s\t%s\n' "${relative}" "${actual_sha256}" >>"${manifest}.partial"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    blobs = json.load(stream)["rkbin_blobs"]
for path in sorted(blobs):
    print(f"{path}\t{blobs[path]}")
PY
)
mv "${manifest}.partial" "${manifest}"

source_commit="${candidate_commit}"
config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"
status="${output_dir}/RKBIN_STATUS.json"
{
	printf '{\n'
	printf '  "status": "complete",\n'
	printf '  "source_commit": "%s",\n' "${source_commit}"
	printf '  "rkbin_commit": "%s",\n' "${actual_commit}"
	printf '  "validation_config_sha256": "%s",\n' "${config_sha256}"
	printf '  "manifest_sha256": "%s",\n' "${manifest_sha256}"
	printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf '}\n'
} >"${status}.partial"
mv "${status}.partial" "${status}"

wifi_expected_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("wifi_driver_commit", ""))
PY
)"
if [[ -n "${wifi_expected_commit}" ]]; then
	wifi_dir="${repo_dir}/cache/sources/rtl8852bs/${wifi_expected_commit}"
	[[ -e "${wifi_dir}/.git" ]] || fail "找不到 RTL8852BS 固定來源工作樹"
	wifi_actual_commit="$(git -C "${wifi_dir}" rev-parse HEAD)"
	[[ "${wifi_actual_commit}" == "${wifi_expected_commit}" ]] ||
		fail "RTL8852BS 提交不符：預期 ${wifi_expected_commit}，實際 ${wifi_actual_commit}"
	wifi_unexpected_changes="$(git -C "${wifi_dir}" status --porcelain --untracked-files=all |
		grep -v '^?? .commit_id$' || true)"
	[[ -z "${wifi_unexpected_changes}" ]] || fail "RTL8852BS 工作樹含有非預期變更"

	wifi_manifest="${output_dir}/WIFI_DRIVER_EVIDENCE.tsv"
	printf 'path\tsha256\n' >"${wifi_manifest}.partial"
	while IFS=$'\t' read -r relative expected_sha256; do
		path="${wifi_dir}/${relative}"
		[[ -f "${path}" ]] || fail "RTL8852BS 缺少檔案：${relative}"
		actual_sha256="$(sha256sum "${path}" | cut -d' ' -f1)"
		[[ "${actual_sha256}" == "${expected_sha256}" ]] ||
			fail "RTL8852BS 檔案雜湊不符：${relative}"
		printf '%s\t%s\n' "${relative}" "${actual_sha256}" >>"${wifi_manifest}.partial"
	done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    files = json.load(stream)["wifi_driver_files"]
for path in sorted(files):
    print(f"{path}\t{files[path]}")
PY
)
	mv "${wifi_manifest}.partial" "${wifi_manifest}"

	wifi_manifest_sha256="$(sha256sum "${wifi_manifest}" | cut -d' ' -f1)"
	wifi_status="${output_dir}/WIFI_DRIVER_STATUS.json"
	{
		printf '{\n'
		printf '  "status": "complete",\n'
		printf '  "source_commit": "%s",\n' "${source_commit}"
		printf '  "wifi_driver_commit": "%s",\n' "${wifi_actual_commit}"
		printf '  "validation_config_sha256": "%s",\n' "${config_sha256}"
		printf '  "manifest_sha256": "%s",\n' "${wifi_manifest_sha256}"
		printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf '}\n'
	} >"${wifi_status}.partial"
	mv "${wifi_status}.partial" "${wifi_status}"
fi

current_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
[[ "${current_commit}" == "${candidate_commit}" ]] ||
	fail "建立來源證據期間來源提交已改變：候選 ${candidate_commit}，目前 ${current_commit}"

trap - EXIT
echo "Rockchip 固定來源證據完成：${output_dir}"
