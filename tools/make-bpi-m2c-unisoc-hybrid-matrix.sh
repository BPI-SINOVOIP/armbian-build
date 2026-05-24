#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
TARGET_ROOT="${TARGET_ROOT:-${SOURCE_ROOT}/hybrid/bpi-m2c}"
ARMBIAN_ROOTFS_CACHE="${ARMBIAN_ROOTFS_CACHE:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache/rootfs}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)-matrix}"
BASELINE="${BASELINE:-sync-20260524-rls-25c}"
SIGN_PROFILE="${SIGN_PROFILE:-QOGIRN6PRO_UIS7885_2H10_SEC}"
RELEASES="${RELEASES:-bookworm trixie jammy noble resolute}"
FLAVORS="${FLAVORS:-cli xfce-desktop-mid}"
FORCE="${FORCE:-no}"

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Build a BPI-M2C UNISOC hybrid PAC matrix from existing Armbian arm64
	rootfs cache tarballs.

	Options:
	  --baseline NAME       Default: ${BASELINE}
	  --date-tag NAME       Default: ${DATE_TAG}
	  --releases LIST       Space-separated list, default: "${RELEASES}"
	  --flavors LIST        Space-separated list, default: "${FLAVORS}"
	  --target-root PATH    Default: ${TARGET_ROOT}
	  --force               Recreate existing output directories
	  -h, --help            Show this help

	Environment:
	  SOURCE_ROOT, TARGET_ROOT, ARMBIAN_ROOTFS_CACHE, DATE_TAG, BASELINE,
	  SIGN_PROFILE, RELEASES, FLAVORS, FORCE, ROOTFS_SIZE_MB
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			BASELINE="${1:?missing baseline}"
			;;
		--date-tag)
			shift
			DATE_TAG="${1:?missing date tag}"
			;;
		--releases)
			shift
			RELEASES="${1:?missing releases list}"
			;;
		--flavors)
			shift
			FLAVORS="${1:?missing flavors list}"
			;;
		--target-root)
			shift
			TARGET_ROOT="${1:?missing target root}"
			;;
		--force)
			FORCE="yes"
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

sanitize() {
	printf '%s' "$1" | tr -c 'A-Za-z0-9._+-' '-'
}

has_rootfs_cache() {
	local release="$1"
	local flavor="$2"

	find "${ARMBIAN_ROOTFS_CACHE}" -maxdepth 1 -type f \
		-name "rootfs-arm64-${release}-${flavor}_*.tar.zst" \
		-print -quit 2>/dev/null | grep -q .
}

main() {
	local helper="${SCRIPT_DIR}/make-bpi-m2c-unisoc-hybrid-pac.sh"
	local matrix_root="${TARGET_ROOT}/${DATE_TAG}"
	local summary="${matrix_root}/matrix-summary.tsv"
	local baseline_safe force_arg failures release flavor label label_safe work_dir pac_path status
	local -a releases flavors

	if [[ ! -x "${helper}" ]]; then
		printf 'missing helper: %s\n' "${helper}" >&2
		exit 1
	fi

	read -r -a releases <<< "${RELEASES}"
	read -r -a flavors <<< "${FLAVORS}"
	baseline_safe="$(sanitize "${BASELINE}")"
	force_arg=()
	failures=0

	if [[ "${FORCE}" == "yes" ]]; then
		force_arg=(--force)
	fi

	mkdir -p "${matrix_root}"
	printf 'release\tflavor\tstatus\twork_dir\tpac\n' > "${summary}"

	for release in "${releases[@]}"; do
		for flavor in "${flavors[@]}"; do
			label="armbian-${release}-${flavor}"
			label_safe="$(sanitize "${label}")"
			work_dir="${matrix_root}/${baseline_safe}-${label_safe}"
			pac_path="${work_dir}/product/cp_sign/${SIGN_PROFILE}/bpi-m2c_${baseline_safe}_${label_safe}_${SIGN_PROFILE}.pac"

			if ! has_rootfs_cache "${release}" "${flavor}"; then
				printf '%s\t%s\tmissing-rootfs-cache\t%s\t\n' "${release}" "${flavor}" "${work_dir}" | tee -a "${summary}"
				failures=$((failures + 1))
				continue
			fi

			printf 'build hybrid PAC: release=%s flavor=%s\n' "${release}" "${flavor}"
			if DATE_TAG="${DATE_TAG}" BASELINE="${BASELINE}" TARGET_ROOT="${TARGET_ROOT}" \
				RELEASE="${release}" ROOTFS_FLAVOR="${flavor}" SIGN_PROFILE="${SIGN_PROFILE}" \
				"${helper}" "${force_arg[@]}"; then
				if (cd "${work_dir}" && sha256sum -c SHA256SUMS); then
					status="ok"
				else
					status="checksum-failed"
					failures=$((failures + 1))
				fi
			else
				status="build-failed"
				failures=$((failures + 1))
			fi

			printf '%s\t%s\t%s\t%s\t%s\n' "${release}" "${flavor}" "${status}" "${work_dir}" "${pac_path}" | tee -a "${summary}"
		done
	done

	printf 'summary: %s\n' "${summary}"
	if ((failures > 0)); then
		printf 'matrix completed with %d missing or failed entries\n' "${failures}" >&2
		return 1
	fi
}

main "$@"
