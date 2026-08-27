#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json}"
family_config="${repo_dir}/config/sources/families/renesas-rzv2n-bpi.conf"
source_cache_root="${SOURCE_CACHE_ROOT:-${repo_dir}/cache/sources}"
public_release="${PUBLIC_RELEASE:-no}"
policy_only="${POLICY_ONLY:-no}"
evidence_dir="${EVIDENCE_DIR:-}"

for command in cut find git grep mv python3 sha256sum sort; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "AI2N 來源守門失敗：$*" >&2
	exit 1
}

[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"
[[ -f "${family_config}" ]] || fail "找不到 RZ/V2N family 設定"
case "${public_release}" in
	yes | no) ;;
	*) fail "PUBLIC_RELEASE 只接受 yes 或 no" ;;
esac
case "${policy_only}" in
	yes | no) ;;
	*) fail "POLICY_ONLY 只接受 yes 或 no" ;;
esac

readarray -t policy_values < <(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
policy = config["release_policy"]
hardware = config["hardware_evidence"]
print("true" if policy["public_release_allowed"] else "false")
print("true" if policy["public_redistribution_authorized"] else "false")
print("true" if policy["machine_enforced"] else "false")
print("true" if hardware["present"] else "false")
print("true" if hardware["node_presence_is_functional_evidence"] else "false")
print(config["candidate_scope"])
print(config["evidence_level"])
PY
)
[[ "${policy_values[2]}" == false || "${policy_values[2]}" == true ]] || fail "發布政策格式無效"
[[ "${policy_values[2]}" == true ]] || fail "發布阻擋未啟用機器守門"
[[ "${policy_values[3]}" == false ]] || fail "不得宣稱已有實體板證據"
[[ "${policy_values[4]}" == false ]] || fail "不得把 DT 節點存在視為功能通過"
[[ "${policy_values[5]}" == internal-l0 ]] || fail "目前候選範圍只能是 internal-l0"
[[ "${policy_values[6]}" == L0 ]] || fail "目前證據層級只能是 L0"
if [[ "${public_release}" == yes &&
	( "${policy_values[0]}" != true || "${policy_values[1]}" != true ) ]]; then
	fail "目前授權與實體證據不足，禁止建立公開發布候選"
fi
if [[ "${policy_only}" == yes ]]; then
	echo "AI2N 發布政策守門通過。"
	exit 0
fi

for assignment in \
	'KERNELBRANCH="commit:48c742429129c095045823c204209bb2a92fb5b4"' \
	'ATFBRANCH="commit:a011da37865c7649db48efc29b18b36cf87e4bb3"' \
	'BOOTBRANCH="commit:8aec7f20bcf5555d7d219c2bad295b4a627b6521"'; do
	grep -Fq "${assignment}" "${family_config}" || fail "family 缺少固定來源：${assignment}"
done
grep -Fq 'tools/renesas/rz_boot_param' "${family_config}" || fail "family 未由來源建置 bptool"
grep -Fq 'tools/fiptool' "${family_config}" || fail "family 未由來源建置 fiptool"
if grep -Fq 'packages/blobs/bpi-renesas/tools' "${family_config}"; then
	fail "family 仍依賴無授權說明的預建封裝工具"
fi
while IFS= read -r fragment; do
	[[ -n "${fragment}" ]] || continue
	grep -Fq "${fragment}" "${family_config}" || fail "TF-A 建置契約缺少：${fragment}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    for item in json.load(stream)["atf_build_contract"]:
        print(item)
PY
)

source_revision() {
	python3 - "${validation_config}" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["source_commits"][sys.argv[2]]["revision"])
PY
}

find_source_tree() {
	local component=$1 root=$2 override=$3 revision direct candidate matches=()
	revision="$(source_revision "${component}")"
	if [[ -n "${override}" ]]; then
		[[ "$(git -C "${override}" rev-parse HEAD 2>/dev/null || true)" == "${revision}" ]] ||
			fail "${component} 覆寫來源樹不是固定提交"
		printf '%s\n' "${override}"
		return
	fi
	direct="${root}/${revision}"
	if [[ "$(git -C "${direct}" rev-parse HEAD 2>/dev/null || true)" == "${revision}" ]]; then
		printf '%s\n' "${direct}"
		return
	fi
	while IFS= read -r candidate; do
		if [[ "$(git -C "${candidate}" rev-parse HEAD 2>/dev/null || true)" == "${revision}" ]]; then
			matches+=("${candidate}")
		fi
	done < <(find "${root}" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort)
	[[ ${#matches[@]} -gt 0 ]] || fail "找不到 ${component} 固定來源樹 ${revision}"
	printf '%s\n' "${matches[0]}"
}

linux_tree="$(find_source_tree linux "${source_cache_root}/linux-kernel-worktree" "${LINUX_SOURCE_DIR:-}")"
uboot_tree="$(find_source_tree uboot "${source_cache_root}/u-boot-worktree/u-boot" "${UBOOT_SOURCE_DIR:-}")"
atf_tree="$(find_source_tree atf "${source_cache_root}/arm-trusted-firmware" "${ATF_SOURCE_DIR:-}")"

declare -A source_trees=(
	[atf]="${atf_tree}"
	[linux]="${linux_tree}"
	[uboot]="${uboot_tree}"
)

while IFS=$'\t' read -r component revision license_path license_sha256; do
	tree="${source_trees[${component}]}"
	[[ "$(git -C "${tree}" rev-parse HEAD)" == "${revision}" ]] || fail "${component} 提交不符"
	actual_sha256="$(git -C "${tree}" show "HEAD:${license_path}" | sha256sum | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${license_sha256}" ]] || fail "${component} 授權檔雜湊不符"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    sources = json.load(stream)["source_commits"]
for name in sorted(sources):
    item = sources[name]
    print(f"{name}\t{item['revision']}\t{item['license_path']}\t{item['license_sha256']}")
PY
)

while IFS=$'\t' read -r component path expected_sha256 used; do
	tree="${source_trees[${component}]}"
	actual_sha256="$(git -C "${tree}" show "HEAD:${path}" | sha256sum | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] || fail "${component} 來源內二進位清單不符：${path}"
	case "${used}" in true | false) ;; *) fail "來源內二進位使用狀態無效：${path}" ;; esac
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    inventory = json.load(stream)["source_tree_binary_inventory"]
for component in sorted(inventory):
    for path in sorted(inventory[component]):
        item = inventory[component][path]
        used = "true" if item["used_by_ai2n_build"] else "false"
        print(f"{component}\t{path}\t{item['sha256']}\t{used}")
PY
)

while IFS=$'\t' read -r component path expected_sha256; do
	tree="${source_trees[${component}]}"
	actual_sha256="$(git -C "${tree}" show "HEAD:${path}" | sha256sum | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] || fail "來源封裝工具雜湊不符：${path}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    tools = json.load(stream)["source_built_packaging_tools"]
for name in sorted(tools):
    item = tools[name]
    print(f"{item['component']}\t{item['source_path']}\t{item['source_sha256']}")
PY
)

while IFS=$'\t' read -r kind relative expected_sha256; do
	path="${repo_dir}/${relative}"
	[[ -f "${path}" ]] || fail "缺少 ${kind} 資產：${relative}"
	actual_sha256="$(sha256sum "${path}" | cut -d' ' -f1)"
	[[ "${actual_sha256}" == "${expected_sha256}" ]] || fail "${kind} 資產雜湊不符：${relative}"
done < <(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
for key, kind in (("proprietary_assets", "專有"), ("unused_prebuilt_packaging_tools", "停用預建工具")):
    for path in sorted(config[key]):
        print(f"{kind}\t{path}\t{config[key][path]}")
PY
)

if [[ -n "${evidence_dir}" ]]; then
	mkdir -p "${evidence_dir}"
	manifest="${evidence_dir}/RENESAS_SOURCE_EVIDENCE.tsv"
	status="${evidence_dir}/RENESAS_SOURCE_STATUS.json"
	{
		printf 'kind\tname\tpath_or_source\tref_or_usage\trevision\tsha256\n'
		python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
for name in sorted(config["source_commits"]):
    item = config["source_commits"][name]
    print(f"source\t{name}\t{item['source']}\t{item['ref']}\t{item['revision']}\t{item['license_sha256']}")
for path in sorted(config["proprietary_assets"]):
    print(f"proprietary\tasset\t{path}\tpublic_release_blocked\t-\t{config['proprietary_assets'][path]}")
for path in sorted(config["unused_prebuilt_packaging_tools"]):
    print(f"unused-tool\ttool\t{path}\tunused\t-\t{config['unused_prebuilt_packaging_tools'][path]}")
PY
	} >"${manifest}.partial"
	mv "${manifest}.partial" "${manifest}"
	source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
	config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
	manifest_sha256="$(sha256sum "${manifest}" | cut -d' ' -f1)"
	python3 - "${status}.partial" "${source_commit}" "${config_sha256}" "${manifest_sha256}" <<'PY'
import json
import sys
status = {
    "status": "complete",
    "evidence_level": "L0",
    "evidence_scope": "source-contract",
    "source_commit": sys.argv[2],
    "validation_config_sha256": sys.argv[3],
    "manifest_sha256": sys.argv[4],
    "public_release_allowed": False,
    "hardware_evidence_present": False,
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(status, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY
	mv "${status}.partial" "${status}"
fi

echo "AI2N 固定來源、授權邊界與二進位資產守門通過。"
