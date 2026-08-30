#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
SDROOT_ROOT="${SDROOT_ROOT:-${SOURCE_ROOT}/sdrootfs/bpi-m2c}"
IMAGE="${IMAGE:-}"
DEVICE="${DEVICE:-}"
YES="${YES:-no}"
DRY_RUN="${DRY_RUN:-no}"
ALLOW_WHOLE_DEVICE="${ALLOW_WHOLE_DEVICE:-no}"

usage() {
	cat <<-EOF
	Usage: $0 --image PATH --device /dev/sdX1 [options]

	Write a generated BPI-M2C SD-rootfs ext4 image to an SD-card partition.
	The script refuses mounted targets and refuses whole disks unless explicitly
	allowed.

	Options:
	  --image PATH             rootfs.ext4 image; default: latest generated image
	  --device PATH            target block device, normally a partition
	  --dry-run                Print the command without writing
	  --yes                    Required before destructive write
	  --allow-whole-device     Permit writing to a whole disk instead of partition
	  -h, --help               Show this help
	EOF
}

while (($#)); do
	case "$1" in
		--image)
			shift
			IMAGE="${1:?missing image path}"
			;;
		--device)
			shift
			DEVICE="${1:?missing device path}"
			;;
		--dry-run)
			DRY_RUN="yes"
			;;
		--yes)
			YES="yes"
			;;
		--allow-whole-device)
			ALLOW_WHOLE_DEVICE="yes"
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

require_cmd() {
	local cmd="$1"
	command -v "${cmd}" >/dev/null 2>&1 || {
		printf 'missing required command: %s\n' "${cmd}" >&2
		exit 1
	}
}

find_latest_image() {
	find "${SDROOT_ROOT}" -type f -name rootfs.ext4 -printf '%T@ %p\n' 2>/dev/null |
		sort -nr |
		awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
}

has_mounts() {
	local device="$1"

	lsblk -nr -o MOUNTPOINT "${device}" | awk 'NF { found=1 } END { exit found ? 0 : 1 }'
}

main() {
	require_cmd awk
	require_cmd blkid
	require_cmd find
	require_cmd lsblk
	require_cmd sha256sum
	require_cmd sort
	require_cmd sudo

	if [[ -z "${IMAGE}" ]]; then
		IMAGE="$(find_latest_image)"
	fi

	if [[ -z "${IMAGE}" || ! -f "${IMAGE}" ]]; then
		printf 'missing rootfs image: %s\n' "${IMAGE:-<empty>}" >&2
		exit 1
	fi

	if [[ -z "${DEVICE}" ]]; then
		printf 'missing --device\n' >&2
		exit 1
	fi

	if [[ ! -b "${DEVICE}" ]]; then
		printf 'target is not a block device: %s\n' "${DEVICE}" >&2
		exit 1
	fi

	local type size image_sha
	type="$(lsblk -dn -o TYPE "${DEVICE}")"
	size="$(lsblk -dn -o SIZE "${DEVICE}")"
	image_sha="$(sha256sum "${IMAGE}" | awk '{print $1}')"

	if [[ "${type}" == "disk" && "${ALLOW_WHOLE_DEVICE}" != "yes" ]]; then
		printf 'refusing whole disk target without --allow-whole-device: %s\n' "${DEVICE}" >&2
		exit 1
	fi

	if has_mounts "${DEVICE}"; then
		printf 'refusing mounted target; unmount it first: %s\n' "${DEVICE}" >&2
		lsblk "${DEVICE}" >&2
		exit 1
	fi

	cat <<-EOF
	SD-rootfs write plan
	====================
	image: ${IMAGE}
	image_sha256: ${image_sha}
	target: ${DEVICE}
	target_type: ${type}
	target_size: ${size}

	command:
	  sudo dd if=${IMAGE} of=${DEVICE} bs=16M conv=fsync status=progress
	EOF

	if [[ "${DRY_RUN}" == "yes" ]]; then
		exit 0
	fi

	if [[ "${YES}" != "yes" ]]; then
		printf 'refusing destructive write without --yes\n' >&2
		exit 1
	fi

	sudo dd if="${IMAGE}" of="${DEVICE}" bs=16M conv=fsync status=progress
	sync
	sudo blkid "${DEVICE}" || true
}

main "$@"
