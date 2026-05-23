#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
MATRIX_TAG="${MATRIX_TAG:-$(date +%Y%m%d)-matrix}"
MATRIX_DIR="${MATRIX_DIR:-${SOURCE_ROOT}/hybrid/bpi-m2c/${MATRIX_TAG}}"
TARGET_ROOT="${TARGET_ROOT:-${SOURCE_ROOT}/release/bpi-m2c}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)-hybrid-armbian}"
TARGET_DIR="${TARGET_DIR:-${TARGET_ROOT}/${DATE_TAG}}"
LINK_MODE="${LINK_MODE:-hardlink}"

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Stage BPI-M2C UNISOC hybrid Armbian PAC files from a matrix output
	directory into a flat release directory.

	Options:
	  --matrix-dir PATH     Default: ${MATRIX_DIR}
	  --target-dir PATH     Default: ${TARGET_DIR}
	  --link-mode MODE      hardlink or copy, default: ${LINK_MODE}
	  -h, --help            Show this help

	Environment:
	  SOURCE_ROOT, MATRIX_TAG, MATRIX_DIR, TARGET_ROOT, DATE_TAG,
	  TARGET_DIR, LINK_MODE
	EOF
}

while (($#)); do
	case "$1" in
		--matrix-dir)
			shift
			MATRIX_DIR="${1:?missing matrix dir}"
			;;
		--target-dir)
			shift
			TARGET_DIR="${1:?missing target dir}"
			;;
		--link-mode)
			shift
			LINK_MODE="${1:?missing link mode}"
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf 'Unknown argument: %s\n\n' "$1" >&2
			usage >&2
			exit 2
			;;
	esac
	shift
done

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

main() {
	local summary="${MATRIX_DIR}/matrix-summary.tsv"
	local manifest="${TARGET_DIR}/manifest.tsv"
	local missing="${TARGET_DIR}/missing.tsv"
	local checksum_file="${TARGET_DIR}/SHA256SUMS"
	local release flavor status work_dir pac_path pac_name dst meta_dir bytes sha entries failed
	local meta meta_rel

	if [[ ! -f "${summary}" ]]; then
		printf 'missing matrix summary: %s\n' "${summary}" >&2
		exit 1
	fi

	rm -rf "${TARGET_DIR}"
	mkdir -p "${TARGET_DIR}/pac" "${TARGET_DIR}/metadata"
	printf 'release\tflavor\tstatus\tbytes\tsha256\tstaged_path\tsource_path\n' > "${manifest}"
	printf 'release\tflavor\treason\tpath\n' > "${missing}"
	: > "${checksum_file}"

	stage_file "${summary}" "${TARGET_DIR}/matrix-summary.tsv"
	if [[ -f "${MATRIX_DIR}.log" ]]; then
		stage_file "${MATRIX_DIR}.log" "${TARGET_DIR}/matrix.log"
	fi

	entries=0
	failed=0
	while IFS=$'\t' read -r release flavor status work_dir pac_path; do
		if [[ "${release}" == "release" ]]; then
			continue
		fi

		if [[ "${status}" != "ok" ]]; then
			printf '%s\t%s\t%s\t%s\n' "${release}" "${flavor}" "${status}" "${work_dir}" >> "${missing}"
			failed=$((failed + 1))
			continue
		fi

		if [[ ! -f "${pac_path}" ]]; then
			printf '%s\t%s\tmissing-pac\t%s\n' "${release}" "${flavor}" "${pac_path}" >> "${missing}"
			failed=$((failed + 1))
			continue
		fi

		pac_name="$(basename "${pac_path}")"
		dst="${TARGET_DIR}/pac/${pac_name}"
		stage_file "${pac_path}" "${dst}"

		meta_dir="${TARGET_DIR}/metadata/${release}-${flavor}"
		mkdir -p "${meta_dir}"
		for meta in build-info.txt SHA256SUMS; do
			if [[ -f "${work_dir}/${meta}" ]]; then
				stage_file "${work_dir}/${meta}" "${meta_dir}/${meta}"
				meta_rel="metadata/${release}-${flavor}/${meta}"
				sha256sum "${TARGET_DIR}/${meta_rel}" | awk -v rel="${meta_rel}" '{ print $1 "  " rel }' >> "${checksum_file}"
			fi
		done

		bytes="$(stat -c %s "${dst}")"
		sha="$(sha256sum "${dst}" | awk '{print $1}')"
		printf '%s  pac/%s\n' "${sha}" "${pac_name}" >> "${checksum_file}"
		printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${release}" "${flavor}" "${status}" "${bytes}" "${sha}" "${dst}" "${pac_path}" >> "${manifest}"
		entries=$((entries + 1))
	done < "${summary}"

	sort -k2,2 "${checksum_file}" -o "${checksum_file}"

	{
		printf 'source_matrix=%s\n' "${MATRIX_DIR}"
		printf 'target_dir=%s\n' "${TARGET_DIR}"
		printf 'entries=%s\n' "${entries}"
		printf 'failed=%s\n' "${failed}"
		printf 'staged_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} > "${TARGET_DIR}/release-info.txt"

	printf 'target: %s\n' "${TARGET_DIR}"
	printf 'entries: %s\n' "${entries}"
	printf 'failed: %s\n' "${failed}"
	printf 'manifest: %s\n' "${manifest}"
	printf 'missing: %s\n' "${missing}"
	printf 'sha256: %s\n' "${TARGET_DIR}/SHA256SUMS"

	((entries > 0 && failed == 0))
}

main "$@"
