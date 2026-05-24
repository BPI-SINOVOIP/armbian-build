#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-/media/pi/SMCI/bpi/unisoc}"
TARGET_ROOT="${TARGET_ROOT:-${SOURCE_ROOT}/sdrootfs/bpi-m2c}"
ARMBIAN_ROOTFS_CACHE="${ARMBIAN_ROOTFS_CACHE:-/media/pi/SMCI/armbian/bpi-v26.2.1/cache/rootfs}"
DATE_TAG="${DATE_TAG:-$(date +%Y%m%d)}"
BASELINE="${BASELINE:-sync-20260524-rls-25c}"
MACHINE="${MACHINE:-uis7885-2h10}"
RELEASE="${RELEASE:-trixie}"
ROOTFS_FLAVOR="${ROOTFS_FLAVOR:-cli}"
ROOTFS_LABEL="${ROOTFS_LABEL:-armbian-${RELEASE}-${ROOTFS_FLAVOR}-sdroot}"
ROOTFS_SIZE_MB="${ROOTFS_SIZE_MB:-auto}"
ROOTFS_TAR="${ROOTFS_TAR:-}"
ROOTFS_EXT4="${ROOTFS_EXT4:-}"
FORCE="${FORCE:-no}"
KEEP_ROOTDIR="${KEEP_ROOTDIR:-no}"

usage() {
	cat <<-EOF
	Usage: $0 [options]

	Create a BPI-M2C SD-rootfs ext4 image for testing eMMC/UFS boot plus
	SD-mounted Armbian rootfs. This does not modify PAC files and does not
	write to any block device.

	Options:
	  --baseline NAME       Default: ${BASELINE}
	  --release NAME        Armbian rootfs cache release, default: ${RELEASE}
	  --flavor NAME         Armbian rootfs flavor, default: ${ROOTFS_FLAVOR}
	  --rootfs-tar PATH     Use a rootfs tar.zst/tar.gz/tar instead of cache lookup
	  --rootfs-ext4 PATH    Copy an existing ext4 rootfs image directly
	  --label NAME          Filesystem label, default: armbian-<release>-<flavor>-sdroot
	  --size-mb N           ext4 size for rootfs-tar mode, default: auto
	  --target-root PATH    Default: ${TARGET_ROOT}
	  --force               Remove an existing output work directory
	  -h, --help            Show this help

	Environment:
	  SOURCE_ROOT, TARGET_ROOT, ARMBIAN_ROOTFS_CACHE, DATE_TAG, BASELINE,
	  MACHINE, RELEASE, ROOTFS_FLAVOR, ROOTFS_LABEL, ROOTFS_SIZE_MB,
	  ROOTFS_TAR, ROOTFS_EXT4, FORCE, KEEP_ROOTDIR
	EOF
}

while (($#)); do
	case "$1" in
		--baseline)
			shift
			BASELINE="${1:?missing baseline}"
			;;
		--release)
			shift
			RELEASE="${1:?missing release}"
			ROOTFS_LABEL="armbian-${RELEASE}-${ROOTFS_FLAVOR}-sdroot"
			;;
		--flavor)
			shift
			ROOTFS_FLAVOR="${1:?missing flavor}"
			ROOTFS_LABEL="armbian-${RELEASE}-${ROOTFS_FLAVOR}-sdroot"
			;;
		--rootfs-tar)
			shift
			ROOTFS_TAR="${1:?missing rootfs tar path}"
			;;
		--rootfs-ext4)
			shift
			ROOTFS_EXT4="${1:?missing rootfs ext4 path}"
			;;
		--label)
			shift
			ROOTFS_LABEL="${1:?missing label}"
			;;
		--size-mb)
			shift
			ROOTFS_SIZE_MB="${1:?missing size}"
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

require_cmd() {
	local cmd="$1"
	command -v "${cmd}" >/dev/null 2>&1 || {
		printf 'missing required command: %s\n' "${cmd}" >&2
		exit 1
	}
}

tree_for_baseline() {
	case "$1" in
		sync-20260524-rls-25c)
			printf '%s/sync-20260524/source_sync_rls_25c\n' "${SOURCE_ROOT}"
			;;
		rls-25c-w26-05-5)
			printf '%s/source_rls_25c_w26_05_5\n' "${SOURCE_ROOT}"
			;;
		rls-25c-w26-07-2)
			printf '%s/source_rls_25c_w26_07_2\n' "${SOURCE_ROOT}"
			;;
		trunk-3-0-dev-w24-05-2-p1-2)
			printf '%s/source_trunk_3_0_dev_w24_05_2_p1_2\n' "${SOURCE_ROOT}"
			;;
		*)
			printf 'unknown baseline: %s\n' "$1" >&2
			return 2
			;;
	esac
}

sanitize() {
	printf '%s' "$1" | tr -c 'A-Za-z0-9._+-' '-'
}

find_rootfs_tar() {
	local pattern="${ARMBIAN_ROOTFS_CACHE}/rootfs-arm64-${RELEASE}-${ROOTFS_FLAVOR}_" candidate
	candidate="$(find "${ARMBIAN_ROOTFS_CACHE}" -maxdepth 1 -type f -name "$(basename "${pattern}")*.tar.zst" -printf '%T@ %p\n' 2>/dev/null |
		sort -nr |
		awk 'NR == 1 { $1=""; sub(/^ /, ""); print }')"

	if [[ -z "${candidate}" ]]; then
		printf 'no rootfs cache found for arm64 %s %s in %s\n' "${RELEASE}" "${ROOTFS_FLAVOR}" "${ARMBIAN_ROOTFS_CACHE}" >&2
		exit 1
	fi

	printf '%s\n' "${candidate}"
}

extract_rootfs_tar() {
	local tarball="$1"
	local root_dir="$2"

	sudo rm -rf "${root_dir}"
	mkdir -p "${root_dir}"

	case "${tarball}" in
		*.tar.zst)
			sudo tar --numeric-owner --xattrs --acls --zstd -xf "${tarball}" -C "${root_dir}"
			;;
		*.tar.gz | *.tgz)
			sudo tar --numeric-owner --xattrs --acls -xzf "${tarball}" -C "${root_dir}"
			;;
		*.tar)
			sudo tar --numeric-owner --xattrs --acls -xf "${tarball}" -C "${root_dir}"
			;;
		*)
			printf 'unsupported rootfs tar format: %s\n' "${tarball}" >&2
			exit 1
			;;
	esac
}

mount_vendor_rootfs() {
	local vendor_rootfs="$1"
	local mount_dir="$2"

	mkdir -p "${mount_dir}"
	sudo mount -o loop,ro "${vendor_rootfs}" "${mount_dir}"
}

inject_vendor_runtime() {
	local vendor_dir="$1"
	local root_dir="$2"

	if [[ -d "${vendor_dir}/lib/modules" ]]; then
		sudo mkdir -p "${root_dir}/lib/modules"
		sudo cp -a --remove-destination "${vendor_dir}/lib/modules/." "${root_dir}/lib/modules/"
	fi

	if [[ -d "${vendor_dir}/lib/firmware" ]]; then
		sudo mkdir -p "${root_dir}/lib/firmware"
		sudo cp -a --remove-destination "${vendor_dir}/lib/firmware/." "${root_dir}/lib/firmware/"
	fi

	for rel in etc/modprobe.d etc/modules-load.d etc/depmod.d; do
		if [[ -d "${vendor_dir}/${rel}" ]]; then
			sudo mkdir -p "${root_dir}/${rel}"
			sudo cp -a --remove-destination "${vendor_dir}/${rel}/." "${root_dir}/${rel}/"
		fi
	done

	if [[ -d "${root_dir}/lib/modules/5.4.180" ]]; then
		sudo depmod -b "${root_dir}" 5.4.180 || true
	fi
}

configure_armbian_rootfs() {
	local root_dir="$1"
	local release_file="${root_dir}/etc/armbian-release"

	sudo mkdir -p \
		"${root_dir}/mnt/userdata" \
		"${root_dir}/mnt/data" \
		"${root_dir}/etc/productinfo" \
		"${root_dir}/etc/systemd/system/getty.target.wants" \
		"${root_dir}/etc/systemd/system/serial-getty@ttyS1.service.d"

	sudo tee "${root_dir}/etc/fstab" >/dev/null <<-'EOF'
		/dev/root       /                auto    defaults,noatime        0 1
		proc            /proc            proc    defaults                0 0
		sysfs           /sys             sysfs   defaults                0 0
		devpts          /dev/pts         devpts  gid=5,mode=620          0 0
		tmpfs           /run             tmpfs   mode=0755,nosuid,nodev  0 0
		tmpfs           /tmp             tmpfs   defaults,nosuid,nodev   0 0
		/dev/userdata   /mnt/userdata    ext4    defaults,nofail         0 2
		/dev/mmcblk0p1  /etc/productinfo ext4    defaults,nofail         0 0
	EOF

	sudo tee "${root_dir}/etc/systemd/system/serial-getty@ttyS1.service.d/override.conf" >/dev/null <<-'EOF'
		[Service]
		ExecStart=
		ExecStart=-/sbin/agetty -o '-p -- \u' 921600 ttyS1 vt102
	EOF

	sudo ln -sf /lib/systemd/system/serial-getty@.service \
		"${root_dir}/etc/systemd/system/getty.target.wants/serial-getty@ttyS1.service"

	printf 'bpi-m2c\n' | sudo tee "${root_dir}/etc/hostname" >/dev/null
	sudo tee "${root_dir}/etc/hosts" >/dev/null <<-'EOF'
		127.0.0.1 localhost
		127.0.1.1 bpi-m2c
		::1 localhost ip6-localhost ip6-loopback
	EOF

	if [[ -f "${release_file}" ]]; then
		sudo sed -i \
			-e '/^BOARD=/d' \
			-e '/^BOARDFAMILY=/d' \
			-e '/^BRANCH=/d' \
			-e '/^IMAGE_TYPE=/d' \
			"${release_file}"
	fi

	{
		printf 'BOARD=bananapim2c\n'
		printf 'BOARDFAMILY=unisoc-uis7885-bpi\n'
		printf 'BRANCH=vendor\n'
		printf 'IMAGE_TYPE=sd-rootfs-test\n'
	} | sudo tee -a "${release_file}" >/dev/null
}

make_ext4_from_rootdir() {
	local root_dir="$1"
	local image="$2"
	local size_mb="$3"
	local used_mb final_mb

	if [[ "${size_mb}" == "auto" ]]; then
		used_mb="$(sudo du -sm "${root_dir}" | awk '{print $1}')"
		final_mb=$((used_mb + used_mb / 3 + 512))
		if ((final_mb < 2048)); then
			final_mb=2048
		fi
	else
		final_mb="${size_mb}"
	fi

	rm -f "${image}"
	truncate -s "${final_mb}M" "${image}"
	sudo mkfs.ext4 -F -L "${ROOTFS_LABEL:0:16}" -d "${root_dir}" "${image}" >/dev/null
	sudo e2fsck -fy "${image}" >/dev/null
	printf '%s\n' "${final_mb}"
}

rootfs_uuid() {
	local image="$1"

	blkid -s UUID -o value "${image}"
}

write_build_info() {
	local info="$1"
	local tree="$2"
	local product_dir="$3"
	local rootfs_source="$4"
	local rootfs_size_mb="$5"
	local image="$6"
	local uuid="$7"

	{
		printf 'board_id=bananapim2c\n'
		printf 'image_type=sd-rootfs-test\n'
		printf 'baseline=%s\n' "${BASELINE}"
		printf 'machine=%s\n' "${MACHINE}"
		printf 'source_tree=%s\n' "${tree}"
		printf 'vendor_product_dir=%s\n' "${product_dir}"
		printf 'rootfs_label=%s\n' "${ROOTFS_LABEL:0:16}"
		printf 'rootfs_uuid=%s\n' "${uuid}"
		printf 'rootfs_source=%s\n' "${rootfs_source}"
		printf 'rootfs_size_mb=%s\n' "${rootfs_size_mb}"
		printf 'rootfs_image=%s\n' "${image}"
		printf 'suggested_kernel_root=root=UUID=%s rootfstype=ext4 rootwait rw\n' "${uuid}"
		printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} > "${info}"
}

write_readme() {
	local readme="$1"
	local image="$2"
	local uuid="$3"

	cat > "${readme}" <<-EOF
	BPI-M2C SD rootfs test package
	================================

	This package is for eMMC/UFS boot plus SD-mounted Armbian rootfs.
	It is not a full SD first-stage boot image.

	Rootfs image:
	  ${image}

	Filesystem UUID:
	  ${uuid}

	Kernel root argument to test:
	  root=UUID=${uuid} rootfstype=ext4 rootwait rw

	Write to an existing SD ext4 rootfs partition:
	  sudo dd if=${image} of=/dev/sdX1 bs=16M conv=fsync status=progress

	Do not run the dd command until /dev/sdX1 has been replaced with the
	correct SD-card partition.
	EOF
}

main() {
	require_cmd awk
	require_cmd blkid
	require_cmd depmod
	require_cmd e2fsck
	require_cmd find
	require_cmd mkfs.ext4
	require_cmd sha256sum
	require_cmd sudo
	require_cmd tar

	local tree source_product work_dir root_dir vendor_mount label_safe baseline_safe rootfs_source rootfs_size image uuid

	tree="$(tree_for_baseline "${BASELINE}")"
	source_product="${tree}/out/target/product/${MACHINE}"
	if [[ ! -d "${source_product}" ]]; then
		printf 'missing vendor product output: %s\n' "${source_product}" >&2
		exit 1
	fi

	if [[ ! -f "${source_product}/rootfs.ext4" ]]; then
		printf 'missing vendor rootfs for runtime injection: %s/rootfs.ext4\n' "${source_product}" >&2
		exit 1
	fi

	if [[ -n "${ROOTFS_TAR}" && -n "${ROOTFS_EXT4}" ]]; then
		printf 'use only one of ROOTFS_TAR or ROOTFS_EXT4\n' >&2
		exit 1
	fi

	if [[ -z "${ROOTFS_TAR}" && -z "${ROOTFS_EXT4}" ]]; then
		ROOTFS_TAR="$(find_rootfs_tar)"
	fi

	label_safe="$(sanitize "${ROOTFS_LABEL}")"
	baseline_safe="$(sanitize "${BASELINE}")"
	work_dir="${TARGET_ROOT}/${DATE_TAG}/${baseline_safe}-${label_safe}"
	root_dir="${work_dir}/rootfs-dir"
	vendor_mount="${work_dir}/vendor-rootfs"
	image="${work_dir}/rootfs.ext4"

	if [[ -e "${work_dir}" ]]; then
		if [[ "${FORCE}" != "yes" ]]; then
			printf 'output already exists: %s (use --force)\n' "${work_dir}" >&2
			exit 1
		fi
		sudo rm -rf "${work_dir}"
	fi

	mkdir -p "${work_dir}"

	if [[ -n "${ROOTFS_EXT4}" ]]; then
		rootfs_source="${ROOTFS_EXT4}"
		cp -a --reflink=auto "${ROOTFS_EXT4}" "${image}"
		rootfs_size="existing"
	else
		rootfs_source="${ROOTFS_TAR}"
		printf 'extract rootfs: %s\n' "${ROOTFS_TAR}"
		extract_rootfs_tar "${ROOTFS_TAR}" "${root_dir}"
		mount_vendor_rootfs "${source_product}/rootfs.ext4" "${vendor_mount}"
		trap 'sudo umount "${vendor_mount}" >/dev/null 2>&1 || true' EXIT
		inject_vendor_runtime "${vendor_mount}" "${root_dir}"
		sudo umount "${vendor_mount}"
		trap - EXIT
		configure_armbian_rootfs "${root_dir}"
		printf 'make SD rootfs image: %s\n' "${image}"
		rootfs_size="$(make_ext4_from_rootdir "${root_dir}" "${image}" "${ROOTFS_SIZE_MB}")"
	fi

	uuid="$(rootfs_uuid "${image}")"
	write_build_info "${work_dir}/build-info.txt" "${tree}" "${source_product}" "${rootfs_source}" "${rootfs_size}" "${image}" "${uuid}"
	write_readme "${work_dir}/README.txt" "${image}" "${uuid}"

	(
		cd "${work_dir}"
		sha256sum rootfs.ext4 build-info.txt README.txt > SHA256SUMS
	)

	if [[ "${KEEP_ROOTDIR}" != "yes" && -d "${root_dir}" ]]; then
		sudo rm -rf "${root_dir}"
	fi

	printf 'work_dir: %s\n' "${work_dir}"
	printf 'rootfs_image: %s\n' "${image}"
	printf 'rootfs_uuid: %s\n' "${uuid}"
	printf 'kernel_root: root=UUID=%s rootfstype=ext4 rootwait rw\n' "${uuid}"
	printf 'rootfs_source: %s\n' "${rootfs_source}"
}

main "$@"
