#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)}"
LINK_MODE="${LINK_MODE:-hardlink}"
MODE="${MODE:-full}"
STAGE_RELEASE="${STAGE_RELEASE:-yes}"

requested_baselines=()

usage() {
	cat <<-EOF
	Usage: $0 [--baseline NAME ...] [--mode full|incremental] [--no-stage]

	Rebuild the local vendor Yocto baselines for the unpublished BPI-M2C
	UNISOC platform and optionally stage PAC/core signed artifacts.

	Baselines:
	  rls-25c-w26-05-5
	  rls-25c-w26-07-2
	  trunk-3-0-dev-w24-05-2-p1-2

	Environment:
	  SOURCE_ROOT       Default: /media/pi/SMCI/bpi/unisoc
	  DATE_TAG          Default: current YYYYMMDD
	  LINK_MODE         hardlink or copy, passed to stage script
	  MODE              Default: full
	  STAGE_RELEASE     yes/no, default yes
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			requested_baselines+=("${1:?missing baseline}")
			;;
		--mode)
			shift
			MODE="${1:?missing mode}"
			;;
		--no-stage)
			STAGE_RELEASE="no"
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

if ((${#requested_baselines[@]} == 0)); then
	requested_baselines=(
		rls-25c-w26-05-5
		rls-25c-w26-07-2
		trunk-3-0-dev-w24-05-2-p1-2
	)
fi

wrapper_for_baseline() {
	case "$1" in
		rls-25c-w26-05-5)
			printf '%s\n' "build_uis7885_05_5_incremental.sh"
			;;
		rls-25c-w26-07-2)
			printf '%s\n' "build_uis7885_07_2_incremental.sh"
			;;
		trunk-3-0-dev-w24-05-2-p1-2)
			printf '%s\n' "build_uis7885_trunk_incremental.sh"
			;;
		*)
			printf 'unknown baseline: %s\n' "$1" >&2
			return 2
			;;
	esac
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${SOURCE_ROOT}/logs"

for baseline in "${requested_baselines[@]}"; do
	wrapper="$(wrapper_for_baseline "${baseline}")"
	if [[ ! -x "${SOURCE_ROOT}/${wrapper}" ]]; then
		printf 'missing executable wrapper: %s\n' "${SOURCE_ROOT}/${wrapper}" >&2
		exit 1
	fi

	log_name="m2c_${baseline//-/_}_${MODE}_$(date +%F_%H%M%S).log"
	log_path="${SOURCE_ROOT}/logs/${log_name}"
	printf '==> %s %s\n' "${wrapper}" "${MODE}"
	(
		cd "${SOURCE_ROOT}"
		"./${wrapper}" "${MODE}"
	) 2>&1 | tee "${log_path}"
	printf 'log: %s\n' "${log_path}"
done

if [[ "${STAGE_RELEASE}" == "yes" ]]; then
	printf '==> stage release DATE_TAG=%s LINK_MODE=%s\n' "${DATE_TAG}" "${LINK_MODE}"
	DATE_TAG="${DATE_TAG}" LINK_MODE="${LINK_MODE}" "${repo_root}/tools/stage-bpi-m2c-unisoc-release.sh"
fi
