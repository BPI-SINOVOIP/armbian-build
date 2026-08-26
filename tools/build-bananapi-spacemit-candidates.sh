#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-spacemit-k1-f3-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-spacemit-k1-f3-trixie-current-cli}"
boards_text="${BOARDS:-bananapif3}"
generic_builder="${GENERIC_CANDIDATE_BUILDER:-${repo_dir}/tools/build-bananapi-sunxi-candidates.sh}"

for command in cut date git mv python3 sha256sum stat; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "SpacemiT 建置拒絕：$*" >&2
	exit 1
}

[[ -x "${generic_builder}" ]] || fail "找不到共用候選建置器：${generic_builder}"
[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"

VALIDATION_CONFIG="${validation_config}" OUTPUT_DIR="${output_dir}" \
	BOARDS="${boards_text}" CANDIDATE_FAMILY_NAME="SpacemiT" \
	CANDIDATE_LOCK_FILE=".bananapi-spacemit-build.lock" \
	"${generic_builder}" "$@"

mark_evidence_failure() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 ]]; then
		{
			printf '{\n'
			printf '  "status": "failed",\n'
			printf '  "detail": "SpacemiT 來源證據建立失敗",\n'
			printf '  "source_commit": "%s",\n' "$(git -C "${repo_dir}" rev-parse HEAD)"
			printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
			printf '}\n'
		} >"${output_dir}/SPACEMIT_SOURCE_STATUS.json.partial"
		mv "${output_dir}/SPACEMIT_SOURCE_STATUS.json.partial" \
			"${output_dir}/SPACEMIT_SOURCE_STATUS.json"
	fi
	exit "${exit_status}"
}
trap mark_evidence_failure EXIT

manifest="${output_dir}/SPACEMIT_SOURCE_EVIDENCE.tsv"
printf 'kind\tname\tsource_or_path\tref\trevision\tsha256\n' >"${manifest}.partial"
while IFS=$'\t' read -r component source ref revision worktree; do
	worktree_path="${repo_dir}/${worktree}"
	[[ -e "${worktree_path}/.git" ]] || fail "找不到 ${component} 來源工作樹：${worktree}"
	actual_revision="$(git -C "${worktree_path}" rev-parse HEAD)"
	[[ "${actual_revision}" == "${revision}" ]] ||
		fail "${component} 提交不符：預期 ${revision}，實際 ${actual_revision}"
	printf 'source\t%s\t%s\t%s\t%s\t-\n' \
		"${component}" "${source}" "${ref}" "${actual_revision}" >>"${manifest}.partial"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    sources = json.load(stream)["source_commits"]
for name in sorted(sources):
    source = sources[name]
    print(
        f"{name}\t{source['source']}\t{source['ref']}\t"
        f"{source['revision']}\t{source['worktree']}"
    )
PY
)

while IFS=$'\t' read -r relative expected_sha256; do
	path="${repo_dir}/${relative}"
	[[ -f "${path}" ]] || fail "缺少受控韌體：${relative}"
	actual_sha256="$(sha256sum "${path}" | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] ||
		fail "受控韌體雜湊不符：${relative}"
	printf 'firmware\tesos.elf\t%s\t-\t-\t%s\n' \
		"${relative}" "${actual_sha256}" >>"${manifest}.partial"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    blobs = json.load(stream)["firmware_blobs"]
for path in sorted(blobs):
    print(f"{path}\t{blobs[path]}")
PY
)
mv "${manifest}.partial" "${manifest}"

source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"
status="${output_dir}/SPACEMIT_SOURCE_STATUS.json"
{
	printf '{\n'
	printf '  "status": "complete",\n'
	printf '  "source_commit": "%s",\n' "${source_commit}"
	printf '  "validation_config_sha256": "%s",\n' "${config_sha256}"
	printf '  "manifest_sha256": "%s",\n' "${manifest_sha256}"
	printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf '}\n'
} >"${status}.partial"
mv "${status}.partial" "${status}"

trap - EXIT
echo "SpacemiT 來源證據完成：${output_dir}"
