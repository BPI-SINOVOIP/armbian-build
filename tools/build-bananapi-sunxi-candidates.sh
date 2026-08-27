#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-sunxi-a20-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-sunxi-a20-trixie-current-cli}"
boards_text="${BOARDS:-bananapi bananapipro}"
release="${RELEASE:-trixie}"
branch="${BRANCH:-}"
artifact_ignore_cache="${ARTIFACT_IGNORE_CACHE:-yes}"
minimum_free_gib="${MINIMUM_FREE_GIB:-80}"
hard_minimum_free_gib=40
require_isolated_cache="${REQUIRE_ISOLATED_CACHE:-yes}"
require_source_date_epoch_metadata="${REQUIRE_SOURCE_DATE_EPOCH_METADATA:-no}"
candidate_family_name="${CANDIDATE_FAMILY_NAME:-Sunxi}"
candidate_lock_file="${CANDIDATE_LOCK_FILE:-.bananapi-sunxi-build.lock}"

read -r -a boards <<<"${boards_text}"

for command in awk basename cmp cut date df find findmnt flock git grep mkdir \
	mktemp mv python3 sha256sum stat tee unlink xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fail() {
	echo "建置拒絕：$*" >&2
	exit 1
}

validate_default_userpatches() {
	local path relative template
	[[ -d "${repo_dir}/userpatches" ]] || return 0
	while IFS= read -r -d '' path; do
		relative="${path#"${repo_dir}"/userpatches/}"
		case "${relative}" in
			config-example.conf)
				template="${repo_dir}/config/templates/config-example.conf.template"
				;;
			customize-image.sh)
				template="${repo_dir}/config/templates/customize-image.sh.template"
				;;
			*) fail "userpatches 含有來源覆寫：${relative}" ;;
		esac
		cmp --silent "${path}" "${template}" ||
			fail "userpatches 預設檔已被修改：${relative}"
	done < <(find "${repo_dir}/userpatches" -mindepth 1 \
		\( -type f -o -type l \) -print0)
}

[[ -f "${validation_config}" ]] || fail "找不到驗證設定：${validation_config}"
candidate_branch="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("candidate_branch", "current"))
PY
)"
case "${candidate_branch}" in
	current | edge | vendor | legacy) ;;
	*) echo "驗證設定的 candidate_branch 不受支援：${candidate_branch}" >&2; exit 2 ;;
esac
[[ -n "${branch}" ]] || branch="${candidate_branch}"
[[ "${release}" == trixie && "${branch}" == "${candidate_branch}" ]] || {
	echo "${candidate_family_name} 候選守門只接受 RELEASE=trixie 與 BRANCH=${candidate_branch}。" >&2
	exit 2
}
case "${artifact_ignore_cache}" in
	yes | no) ;;
	*) echo "ARTIFACT_IGNORE_CACHE 只接受 yes 或 no。" >&2; exit 2 ;;
esac
case "${require_isolated_cache}" in
	yes | no) ;;
	*) echo "REQUIRE_ISOLATED_CACHE 只接受 yes 或 no。" >&2; exit 2 ;;
esac
case "${require_source_date_epoch_metadata}" in
	yes | no) ;;
	*) echo "REQUIRE_SOURCE_DATE_EPOCH_METADATA 只接受 yes 或 no。" >&2; exit 2 ;;
esac
[[ "${minimum_free_gib}" =~ ^[0-9]+$ ]] || {
	echo "MINIMUM_FREE_GIB 必須是整數。" >&2
	exit 2
}
(( minimum_free_gib >= hard_minimum_free_gib )) ||
	fail "MINIMUM_FREE_GIB 不得低於硬下限 ${hard_minimum_free_gib} GiB"

[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "來源工作樹有已追蹤或未追蹤變更"
validate_default_userpatches
if [[ "${require_isolated_cache}" == yes ]] &&
	[[ "$(findmnt -no FSTYPE -T "${repo_dir}/cache" 2>/dev/null || true)" != overlay ]]; then
	fail "cache 不是 OverlayFS；請使用隔離快取執行器"
fi

source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
source_tree="$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')"
validation_config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
validation_source_date_epoch="$(python3 - "${validation_config}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream).get("source_date_epoch", ""))
PY
)"
if [[ -n "${validation_source_date_epoch}" ]]; then
	[[ "${validation_source_date_epoch}" =~ ^[1-9][0-9]*$ ]] ||
		fail "驗證契約的 SOURCE_DATE_EPOCH 無效"
	if [[ -n "${SOURCE_DATE_EPOCH:-}" && "${SOURCE_DATE_EPOCH}" != "${validation_source_date_epoch}" ]]; then
		fail "環境 SOURCE_DATE_EPOCH 與驗證契約不一致"
	fi
	export SOURCE_DATE_EPOCH="${validation_source_date_epoch}"
fi
source_date_epoch="${SOURCE_DATE_EPOCH:-}"

assert_source_identity() {
	[[ "$(git -C "${repo_dir}" rev-parse HEAD)" == "${source_commit}" ]] ||
		fail "建置期間來源提交已改變"
	[[ "$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')" == "${source_tree}" ]] ||
		fail "建置期間來源 tree 已改變"
	[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
		fail "建置期間來源工作樹已改變"
}

board_field() {
	python3 - "${validation_config}" "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]][sys.argv[3]]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

board_field_optional() {
	python3 - "${validation_config}" "$1" "$2" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["boards"][sys.argv[2]].get(sys.argv[3], "")
if isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
PY
}

top_field_optional() {
	python3 - "${validation_config}" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream).get(sys.argv[2], "")
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

validate_firmware_source_log() {
	local log_file=$1
	grep -Fq "Fetching SHA1 of 'commit' '${firmware_revision}'" "${log_file}" ||
		fail "建置日誌未以精確提交解析 Armbian 韌體"
	grep -Fq "SHA1 of commit ${firmware_revision}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體完整提交解析結果"
	grep -Fq "${firmware_git_source}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體來源"
	grep -Fq "armbian-firmware-git ${firmware_revision}" "${log_file}" ||
		fail "建置日誌缺少 Armbian 韌體固定來源取用紀錄"
}

validate_firmware_runtime_sources_log() {
	local log_file=$1
	[[ -z "${firmware_runtime_sources_sha256}" ]] && return 0
	python3 - "${validation_config}" "${log_file}" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    sources = json.load(stream).get("firmware_runtime_sources", [])
with open(sys.argv[2], encoding="utf-8", errors="replace") as stream:
    lines = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line) for line in stream]
if not isinstance(sources, list) or not sources:
    raise SystemExit("韌體執行期來源契約不是非空清單")
for source in sources:
    if not isinstance(source, dict):
        raise SystemExit("韌體執行期來源契約格式不符")
    commit = source.get("commit", "")
    expected = (
        source.get("log_marker", ""), source.get("source", ""),
        source.get("ref", ""), commit,
    )
    if (
        not all(expected)
        or source.get("ref") != f"commit:{commit}"
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
    ):
        raise SystemExit("韌體執行期來源契約欄位不完整")
    if not any(all(value in line for value in expected) for line in lines):
        raise SystemExit(f"建置日誌缺少韌體來源原子紀錄：{source.get('name', '未命名')}")
PY
}

validate_board() {
	python3 - "${validation_config}" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    boards = json.load(stream)["boards"]
raise SystemExit(0 if sys.argv[2] in boards else 1)
PY
}

read_metadata_value() {
	local metadata_file=$1 key=$2 matches=()
	mapfile -t matches < <(grep -E "^${key}=" "${metadata_file}")
	[[ ${#matches[@]} -eq 1 ]] || return 1
	printf '%s\n' "${matches[0]#*=}"
}

require_metadata_value() {
	local actual
	actual="$(read_metadata_value "$1" "$2")" ||
		fail "中繼資料缺少唯一欄位 $2：$1"
	[[ "${actual}" == "$3" ]] ||
		fail "中繼資料欄位 $2 不符：預期 $3，實際 ${actual}"
}

write_status() {
	local temporary="${status_file}.partial" candidates_sha256="${3:-}"
	{
		printf '{\n'
		printf '  "status": "%s",\n' "$1"
		printf '  "detail": "%s",\n' "$2"
		printf '  "source_commit": "%s",\n' "${source_commit}"
		printf '  "source_tree": "%s",\n' "${source_tree}"
		printf '  "validation_config_sha256": "%s",\n' "${validation_config_sha256}"
		if [[ -n "${source_contract_projection_sha256}" ]]; then
			printf '  "source_contract_projection_sha256": "%s",\n' \
				"${source_contract_projection_sha256}"
		fi
		if [[ -n "${firmware_runtime_sources_sha256}" ]]; then
			printf '  "firmware_runtime_sources_sha256": "%s",\n' \
				"${firmware_runtime_sources_sha256}"
		fi
		if [[ -n "${source_date_epoch}" ]]; then
			printf '  "source_date_epoch": %s,\n' "${source_date_epoch}"
		fi
		if [[ -n "${candidates_sha256}" ]]; then
			printf '  "candidates_sha256": "%s",\n' "${candidates_sha256}"
		fi
		printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf '}\n'
	} >"${temporary}"
	mv "${temporary}" "${status_file}"
}

source_contract_projection_sha256="$(top_field_optional source_contract_projection_sha256)"
firmware_runtime_sources_sha256="$(python3 - "${validation_config}" <<'PY'
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    sources = json.load(stream).get("firmware_runtime_sources")
if sources is None:
    print("")
else:
    encoded = json.dumps(
        sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    print(hashlib.sha256(encoded).hexdigest())
PY
)"
for digest_name in source_contract_projection_sha256 firmware_runtime_sources_sha256; do
	digest_value="${!digest_name}"
	[[ -z "${digest_value}" || "${digest_value}" =~ ^[0-9a-f]{64}$ ]] ||
		fail "${digest_name} 格式不符"
done

mkdir -p "${output_dir}/logs" "${repo_dir}/.tmp"
exec 9>"${repo_dir}/.tmp/${candidate_lock_file}"
flock -n 9 || fail "此工作樹已有另一個 ${candidate_family_name} 候選映像建置"

status_file="${output_dir}/COMPLETION_STATUS.json"
matrix_file="${output_dir}/CANDIDATES.tsv"
write_status in_progress "建置執行中"
finish_status() {
	local exit_status=$?
	trap - EXIT
	if [[ ${exit_status} -ne 0 ]]; then
		write_status failed "建置失敗，請檢查 logs"
	fi
	exit "${exit_status}"
}
trap finish_status EXIT

printf 'board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tuboot_tag\n' >"${matrix_file}.partial"

linux_git_source="$(top_field_optional linux_source)"
linux_revision="$(top_field_optional linux_commit)"
linux_git_ref="$(top_field_optional linux_ref)"
[[ -n "${linux_git_ref}" || -z "${linux_revision}" ]] || linux_git_ref="commit:${linux_revision}"
rkbin_revision="$(top_field_optional rkbin_commit)"
rkbin_git_source="$(top_field_optional rkbin_source)"
[[ -n "${rkbin_git_source}" || -z "${rkbin_revision}" ]] || rkbin_git_source="https://github.com/armbian/rkbin"
rkbin_git_ref="$(top_field_optional rkbin_ref)"
[[ -n "${rkbin_git_ref}" || -z "${rkbin_revision}" ]] || rkbin_git_ref="commit:${rkbin_revision}"
verify_firmware_source_resolution="$(top_field_optional verify_firmware_source_resolution)"
firmware_git_source=""
firmware_git_ref=""
firmware_revision=""
case "${verify_firmware_source_resolution}" in
	"" | false) verify_firmware_source_resolution="" ;;
	true)
		firmware_git_source="$(top_field_optional firmware_source)"
		firmware_git_ref="$(top_field_optional firmware_ref)"
		firmware_revision="$(top_field_optional firmware_commit)"
		[[ -n "${firmware_git_source}" &&
			"${firmware_git_ref}" == "commit:${firmware_revision}" &&
			"${firmware_revision}" =~ ^[0-9a-f]{40}$ ]] ||
			fail "Armbian 韌體固定來源政策欄位不完整"
		;;
	*) fail "verify_firmware_source_resolution 只接受 true 或 false" ;;
esac
source_date_epoch=""
if [[ "${require_source_date_epoch_metadata}" == yes ]]; then
	source_date_epoch="$(top_field_optional source_date_epoch)"
	[[ "${source_date_epoch}" =~ ^[1-9][0-9]*$ ]] ||
		fail "驗證設定缺少有效的 source_date_epoch 正整數"
	if [[ -n "${SOURCE_DATE_EPOCH:-}" && "${SOURCE_DATE_EPOCH}" != "${source_date_epoch}" ]]; then
		fail "SOURCE_DATE_EPOCH 與驗證設定的固定契約不符"
	fi
	export SOURCE_DATE_EPOCH="${source_date_epoch}"
fi

for board in "${boards[@]}"; do
	validate_board "${board}" || { echo "驗證設定未登錄板卡：${board}" >&2; exit 2; }
	available_bytes="$(df --output=avail -B1 "${repo_dir}" | awk 'NR == 2 { print $1 }')"
	(( available_bytes >= minimum_free_gib * 1024 * 1024 * 1024 )) ||
		fail "可用空間不足 ${minimum_free_gib} GiB，停止 ${board} 建置"

	board_dir="${output_dir}/${board}"
	metadata="${board_dir}/artifact.metadata.txt"
	mkdir -p "${board_dir}"
	family="$(board_field "${board}" family)"
	uboot_tag="$(board_field "${board}" uboot_tag)"
	uboot_git_source="$(board_field_optional "${board}" uboot_git_source)"
	uboot_git_ref="$(board_field_optional "${board}" uboot_git_ref)"
	uboot_revision="$(board_field_optional "${board}" uboot_revision)"
	uboot_version="$(board_field_optional "${board}" uboot_version)"
	output_image_prefix="$(board_field_optional "${board}" output_image_prefix)"
	output_image_board_token="$(board_field_optional "${board}" output_image_board_token)"
	output_image_prefix_effective="${output_image_prefix:-Armbian-*_}"
	output_image_board_token_effective="${output_image_board_token:-${board}}"
	[[ "${output_image_prefix_effective}" =~ ^[[:alnum:]._*+-]+$ ]] ||
		fail "${board} 的輸出映像前綴不合法"
	[[ "${output_image_board_token_effective}" =~ ^[[:alnum:].+-]+$ ]] ||
		fail "${board} 的輸出映像板名 token 不合法"
	output_image_glob="${output_image_prefix_effective}${output_image_board_token_effective}_${release}_${branch}_*.img"
	atf_git_source="$(board_field_optional "${board}" atf_git_source)"
	atf_git_ref="$(board_field_optional "${board}" atf_git_ref)"
	atf_revision="$(board_field_optional "${board}" atf_revision)"
	crust_git_source="$(board_field_optional "${board}" crust_git_source)"
	crust_git_ref="$(board_field_optional "${board}" crust_git_ref)"
	crust_revision="$(board_field_optional "${board}" crust_revision)"
	dtb="$(board_field "${board}" dtb)"
	build_parameters="BOARD=${board} BRANCH=${branch} RELEASE=${release} BUILD_DESKTOP=no BUILD_MINIMAL=yes KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=${artifact_ignore_cache} COMPRESS_OUTPUTIMAGE=sha,img"
	[[ -z "${source_date_epoch}" ]] || build_parameters+=" SOURCE_DATE_EPOCH=${source_date_epoch}"
	if [[ "${artifact_ignore_cache}" == yes ]]; then
		build_parameters+=" CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust"
	fi
	build_parameters_sha256="$(printf '%s\n' "${build_parameters}" | sha256sum | cut -d' ' -f1)"

	if [[ -f "${metadata}" ]]; then
		for item in \
			"schema_version 1" "board ${board}" "release ${release}" \
			"branch ${branch}" "profile cli" "source_commit ${source_commit}" \
			"source_tree ${source_tree}" \
			"validation_config_sha256 ${validation_config_sha256}" \
			"build_parameters_sha256 ${build_parameters_sha256}" \
			"artifact_ignore_cache ${artifact_ignore_cache}" \
			"family ${family}" "dtb ${dtb}" "uboot_tag ${uboot_tag}"; do
			read -r key expected <<<"${item}"
			require_metadata_value "${metadata}" "${key}" "${expected}"
		done
		[[ -z "${source_date_epoch}" ]] ||
			require_metadata_value "${metadata}" source_date_epoch "${source_date_epoch}"
		for item in "uboot_git_source ${uboot_git_source}" \
			"uboot_git_ref ${uboot_git_ref}" "uboot_revision ${uboot_revision}" \
			"uboot_version ${uboot_version}" \
			"output_image_prefix ${output_image_prefix}" \
			"output_image_board_token ${output_image_board_token}" \
			"atf_git_source ${atf_git_source}" "atf_git_ref ${atf_git_ref}" \
			"atf_revision ${atf_revision}" "crust_git_source ${crust_git_source}" \
			"crust_git_ref ${crust_git_ref}" "crust_revision ${crust_revision}" \
			"linux_git_source ${linux_git_source}" "linux_git_ref ${linux_git_ref}" \
			"linux_revision ${linux_revision}" "rkbin_git_source ${rkbin_git_source}" \
			"rkbin_git_ref ${rkbin_git_ref}" "rkbin_revision ${rkbin_revision}" \
			"verify_firmware_source_resolution ${verify_firmware_source_resolution}" \
			"firmware_git_source ${firmware_git_source}" \
			"firmware_git_ref ${firmware_git_ref}" "firmware_revision ${firmware_revision}" \
			"source_contract_projection_sha256 ${source_contract_projection_sha256}" \
			"firmware_runtime_sources_sha256 ${firmware_runtime_sources_sha256}" \
			"source_date_epoch ${source_date_epoch}"; do
			read -r key expected <<<"${item}"
			[[ -z "${expected}" ]] ||
				require_metadata_value "${metadata}" "${key}" "${expected}"
		done
		image="${board_dir}/$(read_metadata_value "${metadata}" image_filename)"
		archive="${board_dir}/$(read_metadata_value "${metadata}" archive_filename)"
		[[ -f "${image}" && -f "${archive}" ]] || fail "${board} 的 IMG/XZ 不成對"
		echo "沿用來源與雜湊均可追溯的既有候選：${board}"
	else
		marker="$(mktemp "${repo_dir}/.tmp/${board}.XXXXXX.marker")"
		log_file="${output_dir}/logs/${board}.log"
		build_args=(
			build "BOARD=${board}" "BRANCH=${branch}" "RELEASE=${release}"
			BUILD_DESKTOP=no BUILD_MINIMAL=yes KERNEL_CONFIGURE=no EXPERT=yes
			"ARTIFACT_IGNORE_CACHE=${artifact_ignore_cache}"
			"COMPRESS_OUTPUTIMAGE=sha,img"
		)
		[[ -z "${source_date_epoch}" ]] || build_args+=("SOURCE_DATE_EPOCH=${source_date_epoch}")
		if [[ "${artifact_ignore_cache}" == yes ]]; then
			build_args+=("CLEAN_LEVEL=make-kernel,make-uboot,make-atf,make-crust")
		fi
		echo "完整建置 ${board} ${release} ${branch} CLI。"
		(cd "${repo_dir}" && ./compile.sh "${build_args[@]}") |& tee "${log_file}"
		assert_source_identity
		[[ "$(sha256sum "${validation_config}" | cut -d' ' -f1)" == "${validation_config_sha256}" ]] ||
			fail "${board} 建置期間 validation 已改變"

		mapfile -t candidates < <(find "${repo_dir}/output/images" -maxdepth 1 \
			-type f -iname "${output_image_glob}" \
			-newer "${marker}" -print)
		unlink "${marker}"
		[[ ${#candidates[@]} -eq 1 ]] ||
			fail "${board} 找到 ${#candidates[@]} 個新 IMG，預期為 1"
		image="${board_dir}/$(basename "${candidates[0]}")"
		mv "${candidates[0]}" "${image}"
		archive="${image}.xz"
		xz -T0 -6 --stdout "${image}" >"${archive}.partial"
		mv "${archive}.partial" "${archive}"
		xz -t "${archive}"

		raw_size="$(stat -c %s "${image}")"
		raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
		xz_size="$(stat -c %s "${archive}")"
		xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
		decompressed_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
		[[ "${decompressed_sha256}" == "${raw_sha256}" ]] ||
			fail "${board} 的 XZ 解壓資料與 IMG 不一致"
		{
			printf 'schema_version=1\nboard=%s\nrelease=%s\nbranch=%s\nprofile=cli\n' "${board}" "${release}" "${branch}"
			printf 'build_method=full_compile_sh_build\nbuild_parameters_sha256=%s\n' "${build_parameters_sha256}"
			printf 'artifact_ignore_cache=%s\nsource_commit=%s\nsource_tree=%s\n' "${artifact_ignore_cache}" "${source_commit}" "${source_tree}"
			printf 'validation_config_sha256=%s\nfamily=%s\ndtb=%s\nuboot_tag=%s\n' "${validation_config_sha256}" "${family}" "${dtb}" "${uboot_tag}"
			[[ -z "${source_contract_projection_sha256}" ]] || printf \
				'source_contract_projection_sha256=%s\n' "${source_contract_projection_sha256}"
			[[ -z "${firmware_runtime_sources_sha256}" ]] || printf \
				'firmware_runtime_sources_sha256=%s\n' "${firmware_runtime_sources_sha256}"
			[[ -z "${source_date_epoch}" ]] || printf 'source_date_epoch=%s\n' "${source_date_epoch}"
			for item in "uboot_git_source ${uboot_git_source}" \
				"uboot_git_ref ${uboot_git_ref}" "uboot_revision ${uboot_revision}" \
				"uboot_version ${uboot_version}" \
				"output_image_prefix ${output_image_prefix}" \
				"output_image_board_token ${output_image_board_token}" \
				"atf_git_source ${atf_git_source}" "atf_git_ref ${atf_git_ref}" \
				"atf_revision ${atf_revision}" "crust_git_source ${crust_git_source}" \
				"crust_git_ref ${crust_git_ref}" "crust_revision ${crust_revision}" \
				"linux_git_source ${linux_git_source}" "linux_git_ref ${linux_git_ref}" \
				"linux_revision ${linux_revision}" "rkbin_git_source ${rkbin_git_source}" \
				"rkbin_git_ref ${rkbin_git_ref}" "rkbin_revision ${rkbin_revision}" \
				"verify_firmware_source_resolution ${verify_firmware_source_resolution}" \
				"firmware_git_source ${firmware_git_source}" \
				"firmware_git_ref ${firmware_git_ref}" "firmware_revision ${firmware_revision}"; do
				read -r key value <<<"${item}"
				[[ -z "${value}" ]] || printf '%s=%s\n' "${key}" "${value}"
			done
			printf 'image_filename=%s\narchive_filename=%s\n' "$(basename "${image}")" "$(basename "${archive}")"
			printf 'raw_size=%s\nraw_sha256=%s\nxz_size=%s\nxz_sha256=%s\n' "${raw_size}" "${raw_sha256}" "${xz_size}" "${xz_sha256}"
			printf 'build_log=logs/%s.log\nevidence_level=L1\nbuilt_at_utc=%s\n' "${board}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		} >"${metadata}.partial"
		mv "${metadata}.partial" "${metadata}"
	fi
	if [[ "${verify_firmware_source_resolution}" == true ]]; then
		build_log_relative="$(read_metadata_value "${metadata}" build_log)" ||
			fail "${board} 的中繼資料缺少建置日誌"
		[[ "${build_log_relative}" =~ ^logs/[A-Za-z0-9._+-]+\.log$ ]] ||
			fail "${board} 的建置日誌路徑不合法"
		[[ -f "${output_dir}/${build_log_relative}" ]] ||
			fail "${board} 缺少建置日誌"
		validate_firmware_source_log "${output_dir}/${build_log_relative}"
	fi
	if [[ -n "${firmware_runtime_sources_sha256}" ]]; then
		build_log_relative="$(read_metadata_value "${metadata}" build_log)" ||
			fail "${board} 的中繼資料缺少建置日誌"
		[[ "${build_log_relative}" =~ ^logs/[A-Za-z0-9._+-]+\.log$ &&
			-f "${output_dir}/${build_log_relative}" ]] ||
			fail "${board} 缺少合法的建置日誌"
		validate_firmware_runtime_sources_log "${output_dir}/${build_log_relative}"
	fi

	image="${board_dir}/$(read_metadata_value "${metadata}" image_filename)"
	archive="${board_dir}/$(read_metadata_value "${metadata}" archive_filename)"
	raw_size="$(stat -c %s "${image}")"
	raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
	xz_size="$(stat -c %s "${archive}")"
	xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
	for item in "raw_size ${raw_size}" "raw_sha256 ${raw_sha256}" \
		"xz_size ${xz_size}" "xz_sha256 ${xz_sha256}"; do
		read -r key expected <<<"${item}"
		require_metadata_value "${metadata}" "${key}" "${expected}"
	done
	decompressed_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
	[[ "${decompressed_sha256}" == "${raw_sha256}" ]] ||
		fail "${board} 的既有 XZ 解壓資料與 IMG 不一致"
	printf '%s  %s\n' "${raw_sha256}" "${board}/$(basename "${image}")" >"${image}.sha256"
	printf '%s  %s\n' "${xz_sha256}" "${board}/$(basename "${archive}")" >"${archive}.sha256"
	printf '%s\t%s\tcli\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"${board}" "${release}" "${raw_size}" "${raw_sha256}" "${xz_size}" \
		"${xz_sha256}" "${board}/$(basename "${image}")" \
		"${board}/$(basename "${archive}")" "${source_commit}" "${uboot_tag}" \
		>>"${matrix_file}.partial"
done

assert_source_identity

actual_rows="$(awk 'NR > 1 { count++ } END { print count + 0 }' "${matrix_file}.partial")"
[[ "${actual_rows}" -eq "${#boards[@]}" ]] || fail "候選矩陣筆數不符"
[[ "$(git -C "${repo_dir}" rev-parse HEAD)" == "${source_commit}" &&
	"$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')" == "${source_tree}" ]] ||
	fail "建立完成證據期間來源提交或 tree 已改變"
[[ -z "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]] ||
	fail "建立完成證據期間來源工作樹已改變"
[[ "$(sha256sum "${validation_config}" | cut -d' ' -f1)" == "${validation_config_sha256}" ]] ||
	fail "建立完成證據期間 validation 已改變"
mv "${matrix_file}.partial" "${matrix_file}"
candidates_sha256="$(sha256sum "${matrix_file}" | cut -d' ' -f1)"
write_status complete "指定板卡的 L1 候選已完整建置" "${candidates_sha256}"
trap - EXIT
echo "${candidate_family_name} 候選映像建置完成：${output_dir}"
