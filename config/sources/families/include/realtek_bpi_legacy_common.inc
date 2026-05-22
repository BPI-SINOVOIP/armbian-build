#
# SPDX-License-Identifier: GPL-2.0
#
# Banana Pi legacy Realtek BSP integration.
#

ARCH=arm64
ATF_COMPILE="no"
SERIALCON="${SERIALCON:-ttyS0}"
KERNEL_BTF="no"
KERNEL_SKIP_MAKEFILE_VERSION="yes"
EXTRAWIFI="no"
UBOOT_SKIP_MAKEFILE_VERSION="yes"
UBOOT_VERSION_OVERRIDE="2015.07"
SRC_LOADADDR="UIMAGE_LOADADDR=0x40008000"
UBOOT_TARGET_MAP=";;u-boot-rtk/u-boot.bin:u-boot.bin rtk-pack/rtk/${REALTEK_BPI_VENDOR_BOARD}/configs/default/linux/uEnv.txt:uEnv.txt rtk-pack/rtk/${REALTEK_BPI_VENDOR_BOARD}/configs/default/linux/bluecore.audio:bluecore.audio"

: "${REALTEK_BPI_BSP_REPO:?REALTEK_BPI_BSP_REPO is not set}"
: "${REALTEK_BPI_BSP_BRANCH:?REALTEK_BPI_BSP_BRANCH is not set}"
: "${REALTEK_BPI_BSP_BOARD:?REALTEK_BPI_BSP_BOARD is not set}"

BOOTSOURCE="${REALTEK_BPI_BSP_REPO}"
BOOTBRANCH="${REALTEK_BPI_BSP_BRANCH}"
BOOTDIR="${BOOTDIR:-u-boot-${BOARD}}"
BOOTPATCHDIR="u-boot-${LINUXFAMILY}-legacy"

case "${BRANCH}" in
	legacy)
		KERNELSOURCE="${REALTEK_BPI_BSP_REPO}"
		KERNELBRANCH="${REALTEK_BPI_BSP_BRANCH}"
		KERNEL_MAJOR_MINOR="4.9"
		LINUXCONFIG="linux-${LINUXFAMILY}-legacy"
		KERNELPATCHDIR="archive/${LINUXFAMILY}-4.9"
		KERNEL_COMPILER="${SRC}/cache/sources/linux-kernel-worktree/${KERNEL_MAJOR_MINOR}__${LINUXFAMILY}__${ARCH}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_aarch64-linux-gnu/bin/aarch64-linux-gnu-"
		;;
esac

function add_host_dependencies__realtek_bpi_legacy() {
	declare -g EXTRA_BUILD_DEPS="${EXTRA_BUILD_DEPS} bc bison flex cpio rsync u-boot-tools libncurses-dev"
}

function late_family_config__realtek_bpi_legacy() {
	declare -g BOOTSOURCE="${REALTEK_BPI_BSP_REPO}"
	declare -g BOOTBRANCH="${REALTEK_BPI_BSP_BRANCH}"
	declare -g BOOTDIR="${BOOTDIR:-u-boot-${BOARD}}"
	declare -g BOOTPATCHDIR="u-boot-${LINUXFAMILY}-legacy"

	# The vendor top-level Makefile needs chosen_board.mk before make clean can run.
	# The custom U-Boot builder below always resets and git-cleans before building.
	CLEAN_LEVEL="${CLEAN_LEVEL:-}"
	CLEAN_LEVEL="${CLEAN_LEVEL//make-uboot/}"
	CLEAN_LEVEL="${CLEAN_LEVEL//,,/,}"
	CLEAN_LEVEL="${CLEAN_LEVEL#,}"
	CLEAN_LEVEL="${CLEAN_LEVEL%,}"
	declare -g CLEAN_LEVEL

	declare -g KERNELSOURCE KERNELBRANCH KERNEL_MAJOR_MINOR LINUXCONFIG KERNELPATCHDIR KERNEL_COMPILER
}

function build_custom_uboot__realtek_bpi_legacy_bsp() {
	display_alert "${BOARD}" "building Realtek vendor BSP U-Boot" "info"

	patch_uboot_target

	if [[ "${CREATE_PATCHES}" == "yes" ]]; then
		return 0
	fi

	run_host_command_logged ./configure "${REALTEK_BPI_BSP_BOARD}"

	if [[ -n "${REALTEK_BPI_UBOOT_CROSS_COMPILE:-}" ]]; then
		local uboot_cross_compile="${PWD}/${REALTEK_BPI_UBOOT_CROSS_COMPILE}"
		run_host_command_logged make -C u-boot-rtk "${BOOTCONFIG}" "CROSS_COMPILE=${uboot_cross_compile}"
		run_host_command_logged make -C u-boot-rtk "CROSS_COMPILE=${uboot_cross_compile}" BUILD_BOOTCODE_ONLY=true
	else
		run_host_command_logged make u-boot
	fi

	loop_over_uboot_targets_and_do deploy_built_uboot_bins_for_one_target_to_packaging_area
	declare -g EXTENSION_BUILT_UBOOT="yes"
}

function realtek_bpi_legacy_flatten_kernel_tree() {
	local kernel_tree="${kernel_work_dir:-${PWD}}"
	local previous_pwd="${PWD}"

	cd "${kernel_tree}" || exit_with_error "Can't cd to Realtek kernel tree" "${kernel_tree}"

	if [[ -d "linux-rtk" && ! -f ".realtek-bpi-kernel-root-ready" ]]; then
		display_alert "${BOARD}" "flattening vendor linux-rtk tree for Armbian kernel build" "info"

		local saved_config=""
		if [[ -f ".config" ]]; then
			saved_config="$(mktemp)"
			run_host_command_logged cp -v ".config" "${saved_config}"
		fi

		run_host_command_logged rsync -a --exclude=.config --exclude=output/ linux-rtk/ ./

		if [[ -n "${saved_config}" ]]; then
			run_host_command_logged cp -v "${saved_config}" ".config"
			run_host_command_logged rm -f "${saved_config}"
		fi

		run_host_command_logged touch ".realtek-bpi-kernel-root-ready"
	fi

	cd "${previous_pwd}" || exit_with_error "Can't restore working directory" "${previous_pwd}"
}

function kernel_copy_extra_sources__realtek_bpi_legacy_bsp() {
	realtek_bpi_legacy_flatten_kernel_tree
}

function custom_kernel_config__realtek_bpi_legacy_bsp() {
	realtek_bpi_legacy_flatten_kernel_tree

	if [[ -n "${kernel_work_dir:-}" ]]; then
		version="$(grab_version "${kernel_work_dir}")"
		display_alert "${BOARD}" "using Realtek BSP kernel version after linux-rtk flatten" "${version}" "info"
	fi
}

function custom_kernel_make_params__realtek_bpi_legacy_bsp() {
	realtek_bpi_legacy_flatten_kernel_tree

	common_make_params_quoted+=("UIMAGE_LOADADDR=0x40008000")
}

function prepare_kernel_image_for_packaging__realtek_bpi_legacy_bsp() {
	local kernel_image_source="arch/${KERNEL_SRC_ARCH}/boot/${NAME_KERNEL:-Image}"
	if [[ ! -f "${kernel_image_source}" ]]; then
		kernel_image_source="arch/${KERNEL_SRC_ARCH}/boot/Image"
	fi

	[[ -f "${kernel_image_source}" ]] || exit_with_error "Realtek BSP kernel image not found" "${kernel_image_source}"

	display_alert "${BOARD}" "copying Realtek BSP kernel image for Armbian packaging: ${kernel_image_source}" "info"
	run_host_command_logged mkdir -p "${kernel_pre_package_path}"
	run_host_command_logged cp -v "${kernel_image_source}" "${kernel_pre_package_path}/vmlinuz-${kernel_version_family}"

	if [[ -f "System.map" ]]; then
		run_host_command_logged cp -v "System.map" "${kernel_pre_package_path}/System.map-${kernel_version_family}"
	fi
	if [[ -f ".config" ]]; then
		run_host_command_logged cp -v ".config" "${kernel_pre_package_path}/config-${kernel_version_family}"
	fi
}

function write_uboot_platform() {
	local DIR="$1"
	local DEVICE="$2"

	display_alert "${BOARD}" "Writing Realtek BSP U-Boot to ${DEVICE}" "info"
	if [[ ! -b "${DEVICE}" ]]; then
		display_alert "${BOARD}" "Device ${DEVICE} is not a block device" "err"
		return 1
	fi

	dd if="${DIR}/u-boot.bin" of="${DEVICE}" bs=1K seek=40 conv=fsync,notrunc status=none
}

function realtek_bpi_legacy_vendor_linux_dir() {
	local boot_dir="${MOUNT}/boot"
	local vendor_board="${REALTEK_BPI_VENDOR_BOARD:-${REALTEK_BPI_BSP_BOARD}}"
	echo "${boot_dir}/bananapi/${vendor_board}/linux"
}

function post_write_uboot_platform__realtek_bpi_copy_fat_boot_assets() {
	if [[ -z "${MOUNT:-}" || ! -d "${MOUNT}/boot" ]]; then
		return 0
	fi

	local vendor_linux_dir
	vendor_linux_dir="$(realtek_bpi_legacy_vendor_linux_dir)"
	display_alert "${BOARD}" "copying Realtek BPI FAT boot assets" "${vendor_linux_dir}" "info"
	run_host_command_logged mkdir -p "${vendor_linux_dir}"

	if [[ -f "${UBOOT_CHROOT_DIR}/uEnv.txt" ]]; then
		run_host_command_logged cp -v "${UBOOT_CHROOT_DIR}/uEnv.txt" "${MOUNT}/boot/uEnv.txt"
		run_host_command_logged cp -v "${UBOOT_CHROOT_DIR}/uEnv.txt" "${vendor_linux_dir}/uEnv.txt"
	fi

	if [[ -f "${UBOOT_CHROOT_DIR}/bluecore.audio" ]]; then
		run_host_command_logged cp -v "${UBOOT_CHROOT_DIR}/bluecore.audio" "${vendor_linux_dir}/bluecore.audio"
	fi
}

function pre_umount_final_image__realtek_bpi_vendor_boot_layout() {
	local boot_dir="${MOUNT}/boot"
	local vendor_linux_dir
	vendor_linux_dir="$(realtek_bpi_legacy_vendor_linux_dir)"

	[[ -d "${boot_dir}" ]] || exit_with_error "Realtek BSP boot directory is missing" "${boot_dir}"

	display_alert "${BOARD}" "installing Realtek BPI FAT boot layout" "${vendor_linux_dir}" "info"
	run_host_command_logged mkdir -p "${vendor_linux_dir}"

	local kernel_source="${boot_dir}/${NAME_KERNEL}"
	if [[ ! -f "${kernel_source}" ]]; then
		kernel_source="$(find "${boot_dir}" -maxdepth 1 -type f -name "vmlinuz-*${LINUXFAMILY}" | head -n 1)"
	fi
	[[ -n "${kernel_source}" && -f "${kernel_source}" ]] || exit_with_error "Realtek BSP kernel file is missing" "${NAME_KERNEL}"
	[[ -f "${boot_dir}/${NAME_INITRD}" ]] || exit_with_error "Realtek BSP initrd file is missing" "${boot_dir}/${NAME_INITRD}"

	run_host_command_logged cp -v "${kernel_source}" "${vendor_linux_dir}/uImage"
	run_host_command_logged cp -v "${boot_dir}/${NAME_INITRD}" "${vendor_linux_dir}/uInitrd"

	local dtb_file dtb_source
	for dtb_file in ${REALTEK_BPI_BOOT_DTB_FILES:-${BOOT_FDT_FILE}}; do
		dtb_source="${boot_dir}/dtb/${dtb_file}"
		if [[ ! -f "${dtb_source}" ]]; then
			dtb_source="$(find "${boot_dir}" -path "*/${dtb_file}" -type f | head -n 1)"
		fi
		[[ -n "${dtb_source}" && -f "${dtb_source}" ]] || exit_with_error "Realtek BSP DTB file is missing" "${dtb_file}"
		run_host_command_logged cp -v "${dtb_source}" "${vendor_linux_dir}/$(basename "${dtb_file}")"
	done
}
