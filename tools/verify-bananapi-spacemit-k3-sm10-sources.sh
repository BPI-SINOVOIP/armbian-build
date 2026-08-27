#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-spacemit-k3-sm10-current.json}"
sdk_root="${SDK_ROOT:-/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0}"
output_root="${SOURCE_EVIDENCE_ROOT:-${repo_dir}/.tmp/bananapi-sm10-source-evidence}"
policy_checker="${repo_dir}/tools/check-bananapi-spacemit-k3-sm10-policy.py"

for command in git python3 repo sha256sum stat; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "SM10 來源驗證失敗：$*" >&2
	exit 1
}

[[ -f "${config}" ]] || fail "找不到驗證契約：${config}"
[[ -x "${policy_checker}" ]] || fail "找不到政策檢查器：${policy_checker}"
[[ -d "${sdk_root}/.repo" ]] || fail "找不到完整 repo SDK：${sdk_root}"
"${policy_checker}" "${config}"

mkdir -p "${output_root}"
manifest="${output_root}/resolved-manifest.xml"
(cd "${sdk_root}" && repo manifest -r -o "${manifest}")

expected_manifest_commit="$(python3 - "${config}" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["sdk"]["manifest_commit"])
PY
)"
actual_manifest_commit="$(git --git-dir="${sdk_root}/.repo/manifests.git" rev-parse HEAD)"
[[ "${actual_manifest_commit}" == "${expected_manifest_commit}" ]] ||
	fail "manifest 提交不符：${actual_manifest_commit}"

expected_manifest_sha256="$(python3 - "${config}" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["sdk"]["resolved_manifest_sha256"])
PY
)"
actual_manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"
[[ "${actual_manifest_sha256}" == "${expected_manifest_sha256}" ]] ||
	fail "固定 revision manifest 雜湊不符：${actual_manifest_sha256}"

project_evidence="${output_root}/PROJECTS.tsv"
printf 'path\texpected_revision\tactual_revision\tbranch\tworktree_status\n' >"${project_evidence}.partial"
while IFS=$'\t' read -r path expected; do
	project="${sdk_root}/${path}"
	[[ -e "${project}/.git" ]] || fail "缺少 repo 專案：${path}"
	actual="$(git -C "${project}" rev-parse HEAD)"
	[[ "${actual}" == "${expected}" ]] ||
		fail "${path} 提交不符：預期 ${expected}，實際 ${actual}"
	status="$(git -C "${project}" status --porcelain --untracked-files=all)"
	[[ -z "${status}" ]] || fail "${path} 工作樹不乾淨"
	branch="$(git -C "${project}" symbolic-ref --short -q HEAD || printf 'detached')"
	printf '%s\t%s\t%s\t%s\tclean\n' \
		"${path}" "${expected}" "${actual}" "${branch}" >>"${project_evidence}.partial"
done < <(python3 - "${config}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for path, revision in sorted(data["source_commits"].items()):
    print(f"{path}\t{revision}")
PY
)
mv "${project_evidence}.partial" "${project_evidence}"

project_count="$(($(wc -l <"${project_evidence}") - 1))"
expected_count="$(python3 - "${config}" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["sdk"]["project_count"])
PY
)"
[[ "${project_count}" == "${expected_count}" ]] ||
	fail "專案數量不符：${project_count}"

status_file="${output_root}/SOURCE_STATUS.json"
python3 - "${status_file}.partial" "${sdk_root}" "${actual_manifest_commit}" \
	"${actual_manifest_sha256}" "${project_count}" <<'PY'
import json, sys
path, sdk_root, commit, digest, count = sys.argv[1:]
data = {
    "status": "complete",
    "sdk_root": sdk_root,
    "manifest_commit": commit,
    "resolved_manifest_sha256": digest,
    "project_count": int(count),
    "all_projects_clean": True,
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(data, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY
mv "${status_file}.partial" "${status_file}"

echo "SM10 SDK 來源驗證完成：${output_root}"
