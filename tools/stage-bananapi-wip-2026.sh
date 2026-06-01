#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-output/images}"
TARGET_DIR="${TARGET_DIR:-output/images/2026.05-wip}"
VERIFY_XZ="${VERIFY_XZ:-yes}"
VERIFY_JOBS="${VERIFY_JOBS:-4}"
LINK_MODE="${LINK_MODE:-hardlink}"
VERSION_PREFIX="${VERSION_PREFIX:-Armbian-unofficial_26.05.0-trunk}"
RELEASES="${RELEASES:-bookworm trixie jammy noble resolute}"

declare -A BOARD_DIR=(
	[Bananapir3]=bpi-r3
	[Bananapir3mini]=bpi-r3mini
	[Bananapir64]=bpi-r64
	[Bananapir4lite]=bpi-r4lite
	[Bananapir4pro]=bpi-r4pro
	[Bananapicm2]=bpi-cm2
	[Bananapicm5pro]=bpi-cm5pro
	[Bananapiw2]=bpi-w2
	[Bananapiw3]=bpi-w3
	[Bananapim4]=bpi-m4
	[Bananapim4super]=bpi-m4super
	[Bananapif2s]=bpi-f2s
	[Bananapim6]=bpi-m6
	[Bananapicm6]=bpi-cm6
	[Bananapi6204]=bpi-6204
)

boards=(
	Bananapir3
	Bananapir3mini
	Bananapir64
	Bananapir4lite
	Bananapir4pro
	Bananapicm2
	Bananapicm5pro
	Bananapiw2
	Bananapiw3
	Bananapim4
	Bananapim4super
	Bananapif2s
	Bananapim6
	Bananapicm6
	Bananapi6204
)

is_expected_skip() {
	local board="$1"
	local release="$2"

	[[ "${board}" == "Bananapicm6" && "${release}" == "bookworm" ]]
}

is_desktop_artifact() {
	local path="$1"

	[[ "${path}" == *_desktop.img.xz ]]
}

find_artifact() {
	local board="$1"
	local release="$2"
	local image_type="$3"
	local match path
	local -a matches=()

	shopt -s nullglob
	for path in "${SOURCE_DIR}/${VERSION_PREFIX}_${board}_${release}_"*.img.xz; do
		case "${image_type}" in
			server)
				is_desktop_artifact "${path}" && continue
				[[ "${path}" == *_minimal.img.xz ]] && continue
				;;
			desktop)
				is_desktop_artifact "${path}" || continue
				;;
			*)
				printf 'unknown image type: %s\n' "${image_type}" >&2
				return 2
				;;
		esac
		matches+=("${path}")
	done
	shopt -u nullglob

	if ((${#matches[@]} == 0)); then
		return 1
	fi

	if ((${#matches[@]} > 1)); then
		printf 'ambiguous artifacts for %s %s %s:\n' "${board}" "${release}" "${image_type}" >&2
		printf '  %s\n' "${matches[@]}" >&2
		return 3
	fi

	match="${matches[0]}"
	printf '%s\n' "${match}"
}

stage_file() {
	local src="$1"
	local dst="$2"

	mkdir -p "$(dirname "${dst}")"
	rm -f "${dst}"

	case "${LINK_MODE}" in
		hardlink)
			ln "${src}" "${dst}" 2>/dev/null || cp -a "${src}" "${dst}"
			;;
		copy)
			cp -a "${src}" "${dst}"
			;;
		*)
			printf 'unknown LINK_MODE: %s\n' "${LINK_MODE}" >&2
			return 2
			;;
	esac
}

write_local_sha() {
	local xz_path="$1"
	local dir base

	dir="$(dirname "${xz_path}")"
	base="$(basename "${xz_path}")"
	(
		cd "${dir}"
		sha256sum "${base}" > "${base}.sha"
	)
}

main() {
	local manifest="${TARGET_DIR}/manifest.tsv"
	local missing="${TARGET_DIR}/missing.tsv"
	local board folder release image_type src dst src_txt dst_txt
	local staged=0 missing_count=0

	mkdir -p "${TARGET_DIR}"
	printf 'board\tfolder\trelease\ttype\tartifact\n' > "${manifest}"
	printf 'board\tfolder\trelease\ttype\treason\n' > "${missing}"

	for board in "${boards[@]}"; do
		folder="${BOARD_DIR[${board}]}"
		for release in ${RELEASES}; do
			if is_expected_skip "${board}" "${release}"; then
				printf '%s\t%s\t%s\tserver\texpected-skip\n' "${board}" "${folder}" "${release}" >> "${missing}"
				printf '%s\t%s\t%s\tdesktop\texpected-skip\n' "${board}" "${folder}" "${release}" >> "${missing}"
				continue
			fi

			for image_type in server desktop; do
				if ! src="$(find_artifact "${board}" "${release}" "${image_type}")"; then
					printf '%s\t%s\t%s\t%s\tmissing-artifact\n' "${board}" "${folder}" "${release}" "${image_type}" >> "${missing}"
					((missing_count += 1))
					continue
				fi

				dst="${TARGET_DIR}/${folder}/$(basename "${src}")"
				stage_file "${src}" "${dst}"

				src_txt="${src%.xz}.txt"
				dst_txt="${dst%.xz}.txt"
				if [[ -f "${src_txt}" ]]; then
					stage_file "${src_txt}" "${dst_txt}"
				else
					printf '%s\t%s\t%s\t%s\tmissing-txt:%s\n' "${board}" "${folder}" "${release}" "${image_type}" "${src_txt}" >> "${missing}"
					((missing_count += 1))
				fi

				write_local_sha "${dst}"
				printf '%s\t%s\t%s\t%s\t%s\n' "${board}" "${folder}" "${release}" "${image_type}" "${dst}" >> "${manifest}"
				((staged += 1))
			done
		done
	done

	if [[ "${VERIFY_XZ}" == "yes" ]]; then
		find "${TARGET_DIR}" -mindepth 2 -maxdepth 2 -type f -name '*.img.xz' -print0 |
			sort -z |
			xargs -0 -n1 -P "${VERIFY_JOBS}" xz -t
	fi

	find "${TARGET_DIR}" -mindepth 2 -maxdepth 2 -type f -name '*.img.xz.sha' -print0 |
		sort -z |
		xargs -0 -n1 -P "${VERIFY_JOBS}" sh -c '
			sha="$1"
			cd "$(dirname "$sha")"
			sha256sum -c "$(basename "$sha")"
		' _

	printf 'target: %s\n' "${TARGET_DIR}"
	printf 'staged images: %s\n' "${staged}"
	printf 'missing errors: %s\n' "${missing_count}"
	printf 'manifest: %s\n' "${manifest}"
	printf 'missing: %s\n' "${missing}"

	((missing_count == 0))
}

main "$@"
