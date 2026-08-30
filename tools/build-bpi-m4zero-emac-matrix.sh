#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
work_dir="${WORK_DIR:-${repo_dir}/.tmp/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
log_dir="${LOG_DIR:-${repo_dir}/output/debug/bpi-m4zero-emac-a1-h618-optimized-792-matrix}"
releases_text="${RELEASES:-bookworm trixie jammy noble resolute}"
profiles_text="${PROFILES:-cli xfce}"
build_tag="${BUILD_TAG:-a1-h618-optimized-emac-792mhz}"

read -r -a releases <<<"${releases_text}"
read -r -a profiles <<<"${profiles_text}"

for command in basename cut find flock git mkdir mv sha256sum sort stat tee unlink wc xargs xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

fingerprint_userpatches() {
	(
		cd "${repo_dir}"
		find userpatches \( -type f -o -type l \) -print0 2>/dev/null |
			sort -z |
			xargs -0 -r sha256sum
	) | sha256sum | cut -d' ' -f1
}

source_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
userpatches_sha256="$(fingerprint_userpatches)"
git -C "${repo_dir}" cat-file -e "${source_commit}^{commit}" || {
	echo "目前來源提交不存在：${source_commit}" >&2
	exit 1
}
mkdir -p "${output_dir}" "${work_dir}" "${log_dir}"
exec 9>"${work_dir}/.build.lock"
flock -n 9 || {
	echo "另一個 M4 Zero EMAC 映像矩陣建置正在執行。" >&2
	exit 1
}

status_file="${work_dir}/COMPLETION_STATUS.txt"
matrix_file="${work_dir}/MATRIX.tsv"
printf 'status=in_progress\n' >"${status_file}.partial"
mv "${status_file}.partial" "${status_file}"

finish_status() {
	local exit_status=$?
	if [[ ${exit_status} -eq 0 ]]; then
		printf 'status=complete\n' >"${status_file}.partial"
	else
		printf 'status=failed\nexit_status=%s\n' "${exit_status}" >"${status_file}.partial"
	fi
	mv "${status_file}.partial" "${status_file}"
	exit "${exit_status}"
}
trap finish_status EXIT

reject_existing_item() {
	local release="$1"
	local profile="$2"
	local reason="$3"

	echo "拒絕沿用既有項目 ${release} ${profile}：${reason}。" >&2
	echo "請移除該項目的原始映像、壓縮檔與中繼資料後重新建置。" >&2
	exit 3
}

assert_clean_source() {
	local stage=$1
	local actual_commit
	local -a untracked_source_files=()
	local -a ignored_config_files=()

	actual_commit="$(git -C "${repo_dir}" rev-parse HEAD)"
	[[ "${actual_commit}" == "${source_commit}" ]] || {
		echo "${stage}時來源提交已改變：${actual_commit}" >&2
		exit 1
	}
	git -C "${repo_dir}" diff --quiet -- || {
		echo "${stage}時來源工作樹含有未提交變更，拒絕記錄不可重現的建置。" >&2
		exit 1
	}
	git -C "${repo_dir}" diff --cached --quiet -- || {
		echo "${stage}時來源索引含有未提交變更，拒絕記錄不可重現的建置。" >&2
		exit 1
	}
	mapfile -t untracked_source_files < <(
		git -C "${repo_dir}" ls-files --others --exclude-standard -- \
			config patch packages lib tools
	)
	[[ ${#untracked_source_files[@]} -eq 0 ]] || {
		echo "${stage}時來源目錄含有未追蹤檔案：${untracked_source_files[0]}" >&2
		exit 1
	}
	mapfile -t ignored_config_files < <(
		find "${repo_dir}" -maxdepth 1 -type f -name 'config-*.conf' -print
	)
	[[ ${#ignored_config_files[@]} -eq 0 ]] || {
		echo "${stage}時倉庫根目錄含有會影響建置的忽略設定檔：${ignored_config_files[0]}" >&2
		exit 1
	}
	[[ "$(fingerprint_userpatches)" == "${userpatches_sha256}" ]] || {
		echo "${stage}時 userpatches 指紋已改變，拒絕記錄不可重現的建置。" >&2
		exit 1
	}
}

validate_existing_metadata() {
	local release="$1"
	local profile="$2"
	local image="$3"
	local archive="$4"
	local metadata="$5"
	local line key value required_key
	local actual_raw_size actual_raw_sha256 actual_xz_size actual_xz_sha256
	local decompressed_raw_sha256
	local -A metadata_values=()

	while IFS= read -r line || [[ -n "${line}" ]]; do
		[[ -n "${line}" ]] || continue
		[[ "${line}" == *=* ]] || \
			reject_existing_item "${release}" "${profile}" "中繼資料含有無法解析的資料列"
		key="${line%%=*}"
		value="${line#*=}"
		[[ "${key}" =~ ^[a-z_][a-z0-9_]*$ ]] || \
			reject_existing_item "${release}" "${profile}" "中繼資料含有無效欄位名稱"
		[[ ! -v "metadata_values[${key}]" ]] || \
			reject_existing_item "${release}" "${profile}" "中繼資料欄位重複：${key}"
		metadata_values["${key}"]="${value}"
	done <"${metadata}"

	for required_key in \
		board release profile kernel_branch build_method source_commit \
		dram_clock_mhz cma_mib raw_size raw_sha256 xz_size xz_sha256; do
		[[ -v "metadata_values[${required_key}]" && -n "${metadata_values[${required_key}]}" ]] || \
			reject_existing_item "${release}" "${profile}" "中繼資料缺少必要欄位：${required_key}"
	done

	[[ "${metadata_values[board]}" == bananapim4zeroemac ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 board 不符"
	[[ "${metadata_values[release]}" == "${release}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 release 不符"
	[[ "${metadata_values[profile]}" == "${profile}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 profile 不符"
	[[ "${metadata_values[kernel_branch]}" == current ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 kernel_branch 不符"
	[[ "${metadata_values[build_method]}" == full_compile_sh_build ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 build_method 不符"
	[[ "${metadata_values[dram_clock_mhz]}" == 792 ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 dram_clock_mhz 不符"
	[[ "${metadata_values[cma_mib]}" == 256 ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 cma_mib 不符"
	[[ "${metadata_values[source_commit]}" =~ ^[0-9a-f]{40,64}$ ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 source_commit 格式不符"
	git -C "${repo_dir}" cat-file -e "${metadata_values[source_commit]}^{commit}" || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 source_commit 不存在於倉庫"
	if [[ -v "metadata_values[userpatches_sha256]" ]]; then
		[[ "${metadata_values[userpatches_sha256]}" =~ ^[0-9a-f]{64}$ ]] || \
			reject_existing_item "${release}" "${profile}" "中繼資料的 userpatches_sha256 格式不符"
	else
		echo "警告：${release} ${profile} 是尚未記錄 userpatches 指紋的舊產物。" >&2
	fi
	[[ "${metadata_values[raw_size]}" =~ ^[0-9]+$ ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 raw_size 格式不符"
	[[ "${metadata_values[xz_size]}" =~ ^[0-9]+$ ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 xz_size 格式不符"
	[[ "${metadata_values[raw_sha256]}" =~ ^[0-9a-f]{64}$ ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 raw_sha256 格式不符"
	[[ "${metadata_values[xz_sha256]}" =~ ^[0-9a-f]{64}$ ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 xz_sha256 格式不符"

	xz -t "${archive}" || \
		reject_existing_item "${release}" "${profile}" "壓縮串流檢查失敗"
	actual_raw_size="$(stat -c %s "${image}")"
	actual_raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
	actual_xz_size="$(stat -c %s "${archive}")"
	actual_xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
	decompressed_raw_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"

	[[ "${metadata_values[raw_size]}" == "${actual_raw_size}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 raw_size 與映像不符"
	[[ "${metadata_values[raw_sha256]}" == "${actual_raw_sha256}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 raw_sha256 與映像不符"
	[[ "${metadata_values[xz_size]}" == "${actual_xz_size}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 xz_size 與壓縮檔不符"
	[[ "${metadata_values[xz_sha256]}" == "${actual_xz_sha256}" ]] || \
		reject_existing_item "${release}" "${profile}" "中繼資料的 xz_sha256 與壓縮檔不符"
	[[ "${decompressed_raw_sha256}" == "${actual_raw_sha256}" ]] || \
		reject_existing_item "${release}" "${profile}" "壓縮檔解壓內容與原始映像不一致"
}

printf 'release\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_filename\txz_filename\n' \
	>"${matrix_file}.partial"

for release in "${releases[@]}"; do
	for profile in "${profiles[@]}"; do
		case "${profile}" in
			cli) profile_suffix="minimal" ;;
			xfce) profile_suffix="xfce_desktop" ;;
			*) echo "未知映像類型：${profile}" >&2; exit 2 ;;
		esac

		mapfile -t existing_images < <(
			find "${work_dir}" -maxdepth 1 -type f \
				-name "Armbian-*Bananapim4zeroemac_${release}_current_*_${profile_suffix}_${build_tag}.img" \
				-print
		)
		mapfile -t existing_archives < <(
			find "${output_dir}" -maxdepth 1 -type f \
				-name "Armbian-*Bananapim4zeroemac_${release}_current_*_${profile_suffix}_${build_tag}.img.xz" \
				-print
		)
		mapfile -t existing_metadata < <(
			find "${work_dir}" -maxdepth 1 -type f \
				-name "Armbian-*Bananapim4zeroemac_${release}_current_*_${profile_suffix}_${build_tag}.img.metadata.txt" \
				-print
		)
		existing_count=$(( ${#existing_images[@]} + ${#existing_archives[@]} + ${#existing_metadata[@]} ))

		if [[ ${existing_count} -gt 0 ]]; then
			[[ ${#existing_images[@]} -eq 1 && ${#existing_archives[@]} -eq 1 && ${#existing_metadata[@]} -eq 1 ]] || \
				reject_existing_item "${release}" "${profile}" "既有產物不完整或數量不唯一"
			image="${existing_images[0]}"
			archive="${existing_archives[0]}"
			metadata="${existing_metadata[0]}"
			[[ "$(basename "${archive}" .xz)" == "$(basename "${image}")" ]] || \
				reject_existing_item "${release}" "${profile}" "原始映像與壓縮檔名稱不一致"
			[[ "$(basename "${metadata}" .metadata.txt)" == "$(basename "${image}")" ]] || \
				reject_existing_item "${release}" "${profile}" "原始映像與中繼資料名稱不一致"
			validate_existing_metadata \
				"${release}" "${profile}" "${image}" "${archive}" "${metadata}"
			echo "沿用已完成項目：${release} ${profile}"
			built_new=no
		else
			built_new=yes
			assert_clean_source "建置前"
			marker="${work_dir}/${release}-${profile}-$RANDOM.marker"
			: >"${marker}"
			log_file="${log_dir}/${release}-${profile}.log"
			common_args=(
				build BOARD=bananapim4zeroemac BRANCH=current RELEASE="${release}"
				KERNEL_CONFIGURE=no EXPERT=yes SHARE_LOG=no
				"COMPRESS_OUTPUTIMAGE=sha,img"
			)
			if [[ "${profile}" == cli ]]; then
				build_args=(BUILD_DESKTOP=no BUILD_MINIMAL=yes)
			else
				build_args=(
					BUILD_DESKTOP=yes BUILD_MINIMAL=no DESKTOP_ENVIRONMENT=xfce
					DESKTOP_TIER=mid
				)
			fi

			echo "完整建置 ${release} ${profile}。"
			(
				cd "${repo_dir}"
				./compile.sh "${common_args[@]}" "${build_args[@]}"
			) |& tee "${log_file}"
			assert_clean_source "建置後"

			mapfile -t candidates < <(
				find "${repo_dir}/output/images" -maxdepth 1 -type f \
					-name "Armbian-*Bananapim4zeroemac_${release}_current_*_${profile_suffix}.img" \
					-newer "${marker}" -print
			)
			unlink "${marker}"
			[[ ${#candidates[@]} -eq 1 ]] || {
				echo "${release} ${profile} 找到 ${#candidates[@]} 個新映像，預期為 1。" >&2
				exit 1
			}

			source_image="${candidates[0]}"
			image="${work_dir}/$(basename "${source_image}" .img)_${build_tag}.img"
			archive="${output_dir}/$(basename "${image}").xz"
			mv "${source_image}" "${image}"
			[[ ! -e "${archive}.partial" ]] || unlink "${archive}.partial"
			xz -T0 -6 --stdout "${image}" >"${archive}.partial"
			mv "${archive}.partial" "${archive}"
			xz -t "${archive}"
		fi

		raw_size="$(stat -c %s "${image}")"
		raw_sha256="$(sha256sum "${image}" | cut -d' ' -f1)"
		xz_size="$(stat -c %s "${archive}")"
		xz_sha256="$(sha256sum "${archive}" | cut -d' ' -f1)"
		decompressed_raw_sha256="$(xz -dc -- "${archive}" | sha256sum | cut -d' ' -f1)"
		[[ "${decompressed_raw_sha256}" == "${raw_sha256}" ]] || {
			echo "${release} ${profile} 的壓縮檔解壓內容與原始映像不一致。" >&2
			exit 1
		}
		printf '%s  %s\n' "${raw_sha256}" "$(basename "${image}")" >"${image}.sha"
		printf '%s  %s\n' "${xz_sha256}" "$(basename "${archive}")" >"${archive}.sha"

		if [[ "${built_new}" == yes ]]; then
			metadata="${work_dir}/$(basename "${image}").metadata.txt"
			{
				printf 'board=bananapim4zeroemac\n'
				printf 'release=%s\n' "${release}"
				printf 'profile=%s\n' "${profile}"
				printf 'build_method=full_compile_sh_build\n'
				printf 'source_commit=%s\n' "${source_commit}"
				printf 'userpatches_sha256=%s\n' "${userpatches_sha256}"
				printf 'kernel_branch=current\n'
				printf 'dram_clock_mhz=792\n'
				printf 'cma_mib=256\n'
				printf 'raw_size=%s\nraw_sha256=%s\n' "${raw_size}" "${raw_sha256}"
				printf 'xz_size=%s\nxz_sha256=%s\n' "${xz_size}" "${xz_sha256}"
			} >"${metadata}.partial"
			mv "${metadata}.partial" "${metadata}"
		fi

		printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
			"${release}" "${profile}" "${raw_size}" "${raw_sha256}" \
			"${xz_size}" "${xz_sha256}" "$(basename "${image}")" \
			"$(basename "${archive}")" >>"${matrix_file}.partial"
	done
done

expected_count=$(( ${#releases[@]} * ${#profiles[@]} ))
raw_count="$(find "${work_dir}" -maxdepth 1 -type f -name '*.img' | wc -l)"
archive_count="$(find "${output_dir}" -maxdepth 1 -type f -name '*.img.xz' | wc -l)"
[[ ${raw_count} -eq ${expected_count} ]] || {
	echo "原始映像數量不符：預期 ${expected_count}，實際 ${raw_count}。" >&2
	exit 1
}
[[ ${archive_count} -eq ${expected_count} ]] || {
	echo "壓縮映像數量不符：預期 ${expected_count}，實際 ${archive_count}。" >&2
	exit 1
}
mv "${matrix_file}.partial" "${matrix_file}"
echo "完整映像矩陣已完成：${output_dir}"
