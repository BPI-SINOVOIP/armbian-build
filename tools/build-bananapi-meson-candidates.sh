#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_config="${VALIDATION_CONFIG:-${repo_dir}/config/validation/bananapi-meson-current.json}"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bananapi-meson-trixie-current-cli}"
boards_text="${BOARDS:-bananapim5 bananapim2pro bananapicm4io bananapim2s}"
release="${RELEASE:-trixie}"
branch="${BRANCH:-current}"
artifact_ignore_cache="${ARTIFACT_IGNORE_CACHE:-yes}"
minimum_free_gib="${MINIMUM_FREE_GIB:-100}"
require_isolated_cache="${REQUIRE_ISOLATED_CACHE:-yes}"

read -r -a boards <<<"${boards_text}"

for command in awk basename cut date df find findmnt flock git grep mkdir mktemp mv \
	python3 sha256sum sort stat tee unlink wc xargs xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

[[ -f "${validation_config}" ]] || {
	echo "找不到驗證設定：${validation_config}" >&2
	exit 1
}
[[ "${release}" == trixie && "${branch}" == current ]] || {
	echo "第一批守門只接受 RELEASE=trixie 與 BRANCH=current。" >&2
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
[[ "${minimum_free_gib}" =~ ^[0-9]+$ ]] || {
	echo "MINIMUM_FREE_GIB 必須是整數。" >&2
	exit 2
}

if [[ -n "$(git -C "${repo_dir}" status --porcelain --untracked-files=all)" ]]; then
	echo "來源工作樹有已追蹤或未追蹤變更，拒絕建立不可重現映像。" >&2
	exit 1
fi
if [[ -d "${repo_dir}/userpatches" ]] &&
	find "${repo_dir}/userpatches" -mindepth 1 \( -type f -o -type l \) -print -quit |
	grep -q .; then
	echo "userpatches 含有 Git 未追蹤的來源覆寫，拒絕建置。" >&2
	exit 1
fi
if [[ "${require_isolated_cache}" == yes ]] &&
	[[ "$(findmnt -no FSTYPE -T "${repo_dir}/cache" 2>/dev/null || true)" != overlay ]]; then
	echo "cache 不是 OverlayFS；請使用隔離快取執行器。" >&2
	exit 1
fi

source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
source_tree="$(git -C "${repo_dir}" rev-parse 'HEAD^{tree}')"
validation_config_sha256="$(sha256sum "${validation_config}" | cut -d' ' -f1)"
expected_fip_commit="$(python3 - "${validation_config}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)["fip_commit"])
PY
)"

board_field() {
	local board=$1
	local field=$2
	python3 - "${validation_config}" "${board}" "${field}" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
value = data["boards"][sys.argv[2]][sys.argv[3]]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, list):
    print(" ".join(str(item) for item in value))
else:
    print(value)
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
	local metadata_file=$1
	local key=$2
	local matches=()
	mapfile -t matches < <(grep -E "^${key}=" "${metadata_file}")
	[[ ${#matches[@]} -eq 1 ]] || return 1
	printf '%s\n' "${matches[0]#*=}"
}

require_metadata_value() {
	local metadata_file=$1
	local key=$2
	local expected=$3
	local actual
	actual="$(read_metadata_value "${metadata_file}" "${key}")" || {
		echo "中繼資料缺少唯一欄位 ${key}：${metadata_file}" >&2
		return 1
	}
	[[ "${actual}" == "${expected}" ]] || {
		echo "中繼資料欄位 ${key} 不符：預期 ${expected}，實際 ${actual}。" >&2
		return 1
	}
}

free_bytes() {
	df --output=avail -B1 "${repo_dir}" | awk 'NR == 2 { print $1 }'
}

write_status() {
	local status=$1
	local detail=$2
	local temporary="${status_file}.partial"
	{
		printf '{\n'
		printf '  "status": "%s",\n' "${status}"
		printf '  "detail": "%s",\n' "${detail}"
		printf '  "source_commit": "%s",\n' "${source_commit}"
		printf '  "updated_utc": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf '}\n'
	} >"${temporary}"
	mv "${temporary}" "${status_file}"
}

mkdir -p "${output_dir}/logs" "${repo_dir}/.tmp"
exec 9>"${repo_dir}/.tmp/.bananapi-meson-build.lock"
flock -n 9 || {
	echo "此工作樹已有另一個 Meson 候選映像建置。" >&2
	exit 1
}

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

printf 'board\trelease\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_path\txz_path\tsource_commit\tfip_commit\n' >"${matrix_file}.partial"

for board in "${boards[@]}"; do
	validate_board "${board}" || {
		echo "驗證設定未登錄板卡：${board}" >&2
		exit 2
	}

	required_bytes=$((minimum_free_gib * 1024 * 1024 * 1024))
	available_bytes="$(free_bytes)"
	(( available_bytes >= required_bytes )) || {
		echo "可用空間不足 ${minimum_free_gib} GiB，停止 ${board} 建置。" >&2
		exit 1
	}

	board_dir="${output_dir}/${board}"
	metadata="${board_dir}/artifact.metadata.txt"
	mkdir -p "${board_dir}"
	fip_directory="$(board_field "${board}" fip_directory)"
	expected_fip_manifest_sha256="$(board_field "${board}" fip_manifest_sha256)"
	family="$(board_field "${board}" family)"
	uboot_tag="$(board_field "${board}" uboot_tag)"
	build_parameters="BOARD=${board} BRANCH=${branch} RELEASE=${release} BUILD_DESKTOP=no BUILD_MINIMAL=yes KERNEL_CONFIGURE=no EXPERT=yes ARTIFACT_IGNORE_CACHE=${artifact_ignore_cache} COMPRESS_OUTPUTIMAGE=sha,img"
	build_parameters_sha256="$(printf '%s\n' "${build_parameters}" | sha256sum | cut -d' ' -f1)"

	if [[ -f "${metadata}" ]]; then
		require_metadata_value "${metadata}" schema_version 1
		require_metadata_value "${metadata}" board "${board}"
		require_metadata_value "${metadata}" release "${release}"
		require_metadata_value "${metadata}" branch "${branch}"
		require_metadata_value "${metadata}" profile cli
		require_metadata_value "${metadata}" source_commit "${source_commit}"
		require_metadata_value "${metadata}" source_tree "${source_tree}"
		require_metadata_value "${metadata}" validation_config_sha256 "${validation_config_sha256}"
		require_metadata_value "${metadata}" build_parameters_sha256 "${build_parameters_sha256}"
		require_metadata_value "${metadata}" artifact_ignore_cache "${artifact_ignore_cache}"
		require_metadata_value "${metadata}" fip_commit "${expected_fip_commit}"
		require_metadata_value "${metadata}" fip_directory "${fip_directory}"
		require_metadata_value "${metadata}" family "${family}"
		require_metadata_value "${metadata}" uboot_tag "${uboot_tag}"
		fip_manifest="${board_dir}/fip-blobs.sha256"
		[[ -s "${fip_manifest}" ]] || {
			echo "${board} 的既有候選缺少 FIP blob 清單。" >&2
			exit 1
		}
		require_metadata_value "${metadata}" fip_manifest_sha256 \
			"${expected_fip_manifest_sha256}"
		[[ "$(sha256sum "${fip_manifest}" | cut -d' ' -f1)" == \
			"${expected_fip_manifest_sha256}" ]] || {
			echo "${board} 的既有 FIP blob 清單與受控設定不符。" >&2
			exit 1
		}
		image_filename="$(read_metadata_value "${metadata}" image_filename)"
		archive_filename="$(read_metadata_value "${metadata}" archive_filename)"
		image="${board_dir}/${image_filename}"
		archive="${board_dir}/${archive_filename}"
		[[ -f "${image}" && -f "${archive}" ]] || {
			echo "${board} 的既有中繼資料與 IMG/XZ 不成對，拒絕沿用。" >&2
			exit 1
		}
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
		if [[ "${artifact_ignore_cache}" == yes ]]; then
			build_args+=("CLEAN_LEVEL=make-kernel,make-uboot")
		fi

		echo "完整建置 ${board} ${release} ${branch} CLI。"
		(
			cd "${repo_dir}"
			./compile.sh "${build_args[@]}"
		) |& tee "${log_file}"

		mapfile -t candidates < <(
			find "${repo_dir}/output/images" -maxdepth 1 -type f \
				-iname "Armbian-*_${board}_${release}_${branch}_*.img" \
				-newer "${marker}" -print
		)
		unlink "${marker}"
		[[ ${#candidates[@]} -eq 1 ]] || {
			echo "${board} 找到 ${#candidates[@]} 個新 IMG，預期為 1。" >&2
			exit 1
		}

		source_image="${candidates[0]}"
		image="${board_dir}/$(basename "${source_image}")"
		mv "${source_image}" "${image}"
		archive="${image}.xz"
		xz -T0 -6 --stdout "${image}" >"${archive}.partial"
		mv "${archive}.partial" "${archive}"
		xz -t "${archive}"

		fip_root="${repo_dir}/cache/sources/amlogic-boot-fip"
		[[ -d "${fip_root}/${fip_directory}" ]] || {
			echo "${board} 缺少 FIP 目錄：${fip_directory}" >&2
			exit 1
		}
		fip_commit_actual="$(git -C "${fip_root}" rev-parse HEAD)"
		[[ "${fip_commit_actual}" == "${expected_fip_commit}" ]] || {
			echo "${board} 的 FIP 提交不符：${fip_commit_actual}" >&2
			exit 1
		}
		[[ -z "$(git -C "${fip_root}" status --porcelain --untracked-files=all)" ]] || {
			echo "${board} 的 FIP 工作樹不是乾淨狀態。" >&2
			exit 1
		}
		(
			cd "${fip_root}"
			git ls-files -z -- "${fip_directory}" | sort -z | xargs -0 sha256sum
		) >"${board_dir}/fip-blobs.sha256"
		[[ -s "${board_dir}/fip-blobs.sha256" ]] || {
			echo "${board} 的 FIP blob 清單是空的。" >&2
			exit 1
		}
		fip_manifest_sha256="$(sha256sum "${board_dir}/fip-blobs.sha256" | cut -d' ' -f1)"
		[[ "${fip_manifest_sha256}" == "${expected_fip_manifest_sha256}" ]] || {
			echo "${board} 的 FIP blob 雜湊與受控設定不符。" >&2
			exit 1
		}

		raw_size="$(stat -c %s "${image}")"
		raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
		xz_size="$(stat -c %s "${archive}")"
		xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
		decompressed_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
		[[ "${decompressed_sha256}" == "${raw_sha256}" ]] || {
			echo "${board} 的 XZ 解壓資料與 IMG 不一致。" >&2
			exit 1
		}

		{
			printf 'schema_version=1\n'
			printf 'board=%s\nrelease=%s\nbranch=%s\nprofile=cli\n' "${board}" "${release}" "${branch}"
			printf 'build_method=full_compile_sh_build\n'
			printf 'build_parameters_sha256=%s\n' "${build_parameters_sha256}"
			printf 'artifact_ignore_cache=%s\n' "${artifact_ignore_cache}"
			printf 'source_commit=%s\nsource_tree=%s\n' "${source_commit}" "${source_tree}"
			printf 'validation_config_sha256=%s\n' "${validation_config_sha256}"
			printf 'family=%s\nuboot_tag=%s\n' "${family}" "${uboot_tag}"
			printf 'fip_commit=%s\nfip_directory=%s\n' "${fip_commit_actual}" "${fip_directory}"
			printf 'fip_manifest_sha256=%s\n' "${fip_manifest_sha256}"
			printf 'image_filename=%s\narchive_filename=%s\n' "$(basename "${image}")" "$(basename "${archive}")"
			printf 'raw_size=%s\nraw_sha256=%s\n' "${raw_size}" "${raw_sha256}"
			printf 'xz_size=%s\nxz_sha256=%s\n' "${xz_size}" "${xz_sha256}"
			printf 'build_log=%s\n' "logs/${board}.log"
			printf 'evidence_level=L1\n'
			printf 'built_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		} >"${metadata}.partial"
		mv "${metadata}.partial" "${metadata}"
	fi

	image_filename="$(read_metadata_value "${metadata}" image_filename)"
	archive_filename="$(read_metadata_value "${metadata}" archive_filename)"
	image="${board_dir}/${image_filename}"
	archive="${board_dir}/${archive_filename}"
	raw_size="$(stat -c %s "${image}")"
	raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
	xz_size="$(stat -c %s "${archive}")"
	xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
	require_metadata_value "${metadata}" raw_size "${raw_size}"
	require_metadata_value "${metadata}" raw_sha256 "${raw_sha256}"
	require_metadata_value "${metadata}" xz_size "${xz_size}"
	require_metadata_value "${metadata}" xz_sha256 "${xz_sha256}"
	[[ "$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)" == "${raw_sha256}" ]] || {
		echo "${board} 的既有 XZ 解壓資料與 IMG 不一致。" >&2
		exit 1
	}

	printf '%s  %s\n' "${raw_sha256}" "${board}/${image_filename}" >"${image}.sha256"
	printf '%s  %s\n' "${xz_sha256}" "${board}/${archive_filename}" >"${archive}.sha256"
	printf '%s\t%s\tcli\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"${board}" "${release}" "${raw_size}" "${raw_sha256}" \
		"${xz_size}" "${xz_sha256}" "${board}/${image_filename}" \
		"${board}/${archive_filename}" "${source_commit}" "${expected_fip_commit}" \
		>>"${matrix_file}.partial"
done

actual_rows="$(awk 'NR > 1 { count++ } END { print count + 0 }' "${matrix_file}.partial")"
[[ "${actual_rows}" -eq "${#boards[@]}" ]] || {
	echo "候選矩陣筆數不符：預期 ${#boards[@]}，實際 ${actual_rows}。" >&2
	exit 1
}
mv "${matrix_file}.partial" "${matrix_file}"
write_status complete "指定板卡的 L1 候選已完整建置"
trap - EXIT
echo "Meson 候選映像建置完成：${output_dir}"
