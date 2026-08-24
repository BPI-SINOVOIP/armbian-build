#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${OUTPUT_DIR:-${repo_dir}/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix}"
releases_text="${RELEASES:-bookworm trixie jammy noble resolute}"
profiles_text="${PROFILES:-cli xfce}"
build_tag="${BUILD_TAG:-a1-h618-optimized-792mhz}"

read -r -a releases <<<"${releases_text}"
read -r -a profiles <<<"${profiles_text}"

for command in basename cut find flock git mkdir mv rm sha256sum stat tee unlink wc xz; do
	command -v "${command}" >/dev/null || {
		echo "缺少必要命令：${command}" >&2
		exit 1
	}
done

mkdir -p "${output_dir}/logs" "${repo_dir}/.tmp"
exec 9>"${output_dir}/.build.lock"
flock -n 9 || {
	echo "另一個 M4 Berry 完整映像建置正在執行。" >&2
	exit 1
}

status_file="${output_dir}/COMPLETION_STATUS.txt"
matrix_file="${output_dir}/MATRIX.tsv"
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

printf 'release\tprofile\traw_size\traw_sha256\txz_size\txz_sha256\timg_filename\txz_filename\n' >"${matrix_file}.partial"

for release in "${releases[@]}"; do
	for profile in "${profiles[@]}"; do
		case "${profile}" in
			cli) profile_suffix="minimal" ;;
			xfce) profile_suffix="xfce_desktop" ;;
			*) echo "未知映像類型：${profile}" >&2; exit 2 ;;
		esac

		existing=("${output_dir}"/Armbian-*Bananapim4berry_"${release}"_current_*_"${profile_suffix}"_"${build_tag}".img)
		if [[ -f "${existing[0]}" && -f "${existing[0]}.xz" ]]; then
			image="${existing[0]}"
			xz -t "${image}.xz"
		else
			marker="${repo_dir}/.tmp/m4berry-${release}-${profile}-$RANDOM.marker"
			: >"${marker}"
			log_file="${output_dir}/logs/${release}-${profile}.log"
			common_args=(
				build BOARD=bananapim4berry BRANCH=current RELEASE="${release}"
				KERNEL_CONFIGURE=no EXPERT=yes "COMPRESS_OUTPUTIMAGE=sha,img"
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

			mapfile -t candidates < <(
				find "${repo_dir}/output/images" -maxdepth 1 -type f \
					-name "Armbian-*Bananapim4berry_${release}_current_*_${profile_suffix}.img" \
					-newer "${marker}" -print
			)
			unlink "${marker}"
			[[ ${#candidates[@]} -eq 1 ]] || {
				echo "${release} ${profile} 找到 ${#candidates[@]} 個新映像，預期為 1。" >&2
				exit 1
			}

			source_image="${candidates[0]}"
			image="${output_dir}/$(basename "${source_image}" .img)_${build_tag}.img"
			mv "${source_image}" "${image}"
			rm -f "${image}.xz.partial"
			xz -T0 -6 --stdout "${image}" >"${image}.xz.partial"
			mv "${image}.xz.partial" "${image}.xz"
			xz -t "${image}.xz"
		fi

		raw_size=$(stat -c %s "${image}")
		raw_sha256=$(sha256sum "${image}" | cut -d' ' -f1)
		xz_size=$(stat -c %s "${image}.xz")
		xz_sha256=$(sha256sum "${image}.xz" | cut -d' ' -f1)
		printf '%s  %s\n' "${raw_sha256}" "$(basename "${image}")" >"${image}.sha256"
		printf '%s  %s\n' "${xz_sha256}" "$(basename "${image}.xz")" >"${image}.xz.sha256"

		metadata="${image}.metadata.txt"
		{
			printf 'board=bananapim4berry\n'
			printf 'release=%s\n' "${release}"
			printf 'profile=%s\n' "${profile}"
			printf 'build_method=full_compile_sh_build\n'
			printf 'source_commit=%s\n' "$(git -C "${repo_dir}" rev-parse HEAD)"
			printf 'kernel_branch=current\n'
			printf 'dram_clock_mhz=792\n'
			printf 'cma_mib=256\n'
			printf 'raw_size=%s\nraw_sha256=%s\n' "${raw_size}" "${raw_sha256}"
			printf 'xz_size=%s\nxz_sha256=%s\n' "${xz_size}" "${xz_sha256}"
		} >"${metadata}.partial"
		mv "${metadata}.partial" "${metadata}"

		printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
			"${release}" "${profile}" "${raw_size}" "${raw_sha256}" \
			"${xz_size}" "${xz_sha256}" "$(basename "${image}")" \
			"$(basename "${image}.xz")" >>"${matrix_file}.partial"
	done
done

expected_count=$(( ${#releases[@]} * ${#profiles[@]} ))
actual_count=$(find "${output_dir}" -maxdepth 1 -type f -name '*.img' | wc -l)
[[ ${actual_count} -eq ${expected_count} ]] || {
	echo "映像數量不符：預期 ${expected_count}，實際 ${actual_count}。" >&2
	exit 1
}
mv "${matrix_file}.partial" "${matrix_file}"
echo "完整映像矩陣已完成：${output_dir}"
