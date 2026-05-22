#
# SPDX-License-Identifier: GPL-2.0
#
# Banana Pi legacy Sunplus SP7021 BSP integration.
#

ARCH=armhf
ATF_COMPILE="no"
SERIALCON="${SERIALCON:-ttyS0}"
KERNEL_BTF="no"
KERNEL_SKIP_MAKEFILE_VERSION="yes"
EXTRAWIFI="no"
UBOOT_SKIP_MAKEFILE_VERSION="yes"
UBOOT_VERSION_OVERRIDE="2019.04"
KERNEL_IMAGE_TYPE="uImage"
KERNEL_INSTALL_TYPE="uinstall"
NAME_KERNEL="uImage"
UBOOT_TARGET_MAP=";;u-boot-sp/u-boot.img:u-boot.img sp-pack/sp7021/common/bin/ISPBOOOT.BIN:ISPBOOOT.BIN sp-pack/sp7021/common/bin/BPI-F2S-xboot-emmc-boot0-0k.img.gz:BPI-F2S-xboot-emmc-boot0-0k.img.gz"

: "${SUNPLUS_BPI_BSP_REPO:?SUNPLUS_BPI_BSP_REPO is not set}"
: "${SUNPLUS_BPI_BSP_BRANCH:?SUNPLUS_BPI_BSP_BRANCH is not set}"
: "${SUNPLUS_BPI_BSP_BOARD:?SUNPLUS_BPI_BSP_BOARD is not set}"

BOOTSOURCE="${SUNPLUS_BPI_BSP_REPO}"
BOOTBRANCH="${SUNPLUS_BPI_BSP_BRANCH}"
BOOTDIR="${BOOTDIR:-u-boot-${BOARD}}"
BOOTPATCHDIR="u-boot-${LINUXFAMILY}-legacy"

case "${BRANCH}" in
	legacy)
		KERNELSOURCE="${SUNPLUS_BPI_BSP_REPO}"
		KERNELBRANCH="${SUNPLUS_BPI_BSP_BRANCH}"
		KERNEL_MAJOR_MINOR="5.4"
		LINUXCONFIG="linux-${LINUXFAMILY}-legacy"
		KERNELPATCHDIR="archive/${LINUXFAMILY}-5.4"
		KERNEL_COMPILER="${SRC}/cache/sources/linux-kernel-worktree/${KERNEL_MAJOR_MINOR}__${LINUXFAMILY}__${ARCH}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf-"
		;;
esac

function add_host_dependencies__sunplus_sp7021_bpi_legacy() {
	declare -g EXTRA_BUILD_DEPS="${EXTRA_BUILD_DEPS} bc bison flex cpio rsync u-boot-tools libncurses-dev"
}

function late_family_config__sunplus_sp7021_bpi_legacy() {
	declare -g BOOTSOURCE="${SUNPLUS_BPI_BSP_REPO}"
	declare -g BOOTBRANCH="${SUNPLUS_BPI_BSP_BRANCH}"
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

function build_custom_uboot__sunplus_sp7021_bpi_legacy_bsp() {
	display_alert "${BOARD}" "building Sunplus vendor BSP U-Boot" "info"

	patch_uboot_target

	if [[ "${CREATE_PATCHES}" == "yes" ]]; then
		return 0
	fi

	run_host_command_logged ./configure "${SUNPLUS_BPI_BSP_BOARD}"

	local uboot_cross_compile="${PWD}/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf/bin/arm-linux-gnueabihf-"
	run_host_command_logged make -C u-boot-sp "${BOOTCONFIG}" "CROSS_COMPILE=${uboot_cross_compile}"
	run_host_command_logged make -C u-boot-sp all "CROSS_COMPILE=${uboot_cross_compile}"

	loop_over_uboot_targets_and_do deploy_built_uboot_bins_for_one_target_to_packaging_area
	declare -g EXTENSION_BUILT_UBOOT="yes"
}

function sunplus_sp7021_bpi_flatten_kernel_tree() {
	if [[ -d "linux-sp" && ! -f ".sunplus-bpi-kernel-root-ready" ]]; then
		display_alert "${BOARD}" "flattening vendor linux-sp tree for Armbian kernel build" "info"

		local saved_config=""
		if [[ -f ".config" ]]; then
			saved_config="$(mktemp)"
			run_host_command_logged cp -v ".config" "${saved_config}"
		fi

		run_host_command_logged rsync -a --exclude=.config --exclude=output/ linux-sp/ ./

		if [[ -n "${saved_config}" ]]; then
			run_host_command_logged cp -v "${saved_config}" ".config"
			run_host_command_logged rm -f "${saved_config}"
		fi

		run_host_command_logged touch ".sunplus-bpi-kernel-root-ready"
	fi
}

function custom_kernel_config__sunplus_sp7021_bpi_legacy_bsp() {
	sunplus_sp7021_bpi_flatten_kernel_tree
}

function custom_kernel_make_params__sunplus_sp7021_bpi_legacy_bsp() {
	sunplus_sp7021_bpi_flatten_kernel_tree
}

function prepare_kernel_image_for_packaging__sunplus_sp7021_bpi_legacy_bsp() {
	local kernel_image_source="arch/${KERNEL_SRC_ARCH}/boot/uImage"
	if [[ ! -f "${kernel_image_source}" ]]; then
		kernel_image_source="arch/${KERNEL_SRC_ARCH}/boot/zImage"
	fi

	[[ -f "${kernel_image_source}" ]] || exit_with_error "Sunplus BSP kernel image not found" "${kernel_image_source}"

	display_alert "${BOARD}" "copying Sunplus BSP kernel image for Armbian packaging: ${kernel_image_source}" "info"
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

	display_alert "${BOARD}" "Writing Sunplus BSP U-Boot to ${DEVICE}" "info"
	if [[ ! -b "${DEVICE}" ]]; then
		display_alert "${BOARD}" "Device ${DEVICE} is not a block device" "err"
		return 1
	fi

	dd if="${DIR}/u-boot.img" of="${DEVICE}" bs=512 seek=34 conv=fsync,notrunc status=none
}

function post_write_uboot_platform__sunplus_sp7021_bpi_copy_xboot_to_fat_boot() {
	if [[ -n "${MOUNT:-}" && -f "${UBOOT_CHROOT_DIR}/ISPBOOOT.BIN" && -d "${MOUNT}/boot" ]]; then
		display_alert "${BOARD}" "copying Sunplus xboot file to FAT /boot" "info"
		run_host_command_logged cp -v "${UBOOT_CHROOT_DIR}/ISPBOOOT.BIN" "${MOUNT}/boot/ISPBOOOT.BIN"
	fi
}

function pre_umount_final_image__sunplus_sp7021_bpi_vendor_boot_layout() {
	local boot_dir="${MOUNT}/boot"
	local vendor_board="${SUNPLUS_BPI_VENDOR_BOARD:-${SUNPLUS_BPI_BSP_BOARD}}"
	local vendor_linux_dir="${boot_dir}/bananapi/${vendor_board}/linux"

	[[ -d "${boot_dir}" ]] || exit_with_error "Sunplus BSP boot directory is missing" "${boot_dir}"

	display_alert "${BOARD}" "installing Sunplus BPI FAT boot layout" "${vendor_linux_dir}" "info"
	run_host_command_logged mkdir -p "${vendor_linux_dir}"

	[[ -f "${boot_dir}/${NAME_KERNEL}" ]] || exit_with_error "Sunplus BSP kernel file is missing" "${boot_dir}/${NAME_KERNEL}"
	[[ -f "${boot_dir}/${NAME_INITRD}" ]] || exit_with_error "Sunplus BSP initrd file is missing" "${boot_dir}/${NAME_INITRD}"

	local dtb_source="${boot_dir}/dtb/${BOOT_FDT_FILE}"
	if [[ ! -f "${dtb_source}" ]]; then
		dtb_source="$(find "${boot_dir}" -path "*/${BOOT_FDT_FILE}" -type f | head -n 1)"
	fi
	[[ -n "${dtb_source}" && -f "${dtb_source}" ]] || exit_with_error "Sunplus BSP DTB file is missing" "${BOOT_FDT_FILE}"

	run_host_command_logged cp -v "${boot_dir}/${NAME_KERNEL}" "${vendor_linux_dir}/uImage"
	run_host_command_logged cp -v "${boot_dir}/${NAME_INITRD}" "${vendor_linux_dir}/uInitrd"
	run_host_command_logged cp -v "${dtb_source}" "${vendor_linux_dir}/${BOOT_FDT_FILE}"

	cat > "${boot_dir}/uEnv.txt" <<- EOF
		bpi=bananapi
		board=${vendor_board}
		chip=${SUNPLUS_BPI_VENDOR_CHIP:-SP7021}
		service=linux
		kernel=uImage
		dtb=${BOOT_FDT_FILE}
		initrd=uInitrd
		rootopt=rw rootfstype=${ROOTFS_TYPE:-ext4} rootwait
		console=earlyprintk console=tty1 fbcon=map:0 console=${SERIALCON:-ttyS0},115200
		bootopts=loglevel=8 initcall_debug=0 user_debug=255 fsck.mode=force fsck.repair=yes
		setroot=if test -z "\${root}"; then setenv root /dev/mmcblk1p2; fi
		setbootdev=if test -z "\${device}"; then setenv device mmc; fi; if test -z "\${partition}"; then setenv partition 0:1; fi
		abootargs=setenv bootargs board=\${board} console=\${console} root=\${root} \${rootopt} service=\${service} sdmmc_on=\${sdmmc_on} \${bootopts}
		ahello=echo Banana Pi \${board} chip: \${chip} Service: \${service}
		aload_dtb=fatload \${device} \${partition} \${fdt_addr_r} \${bpi}/\${board}/\${service}/\${dtb}
		aload_kernel=fatload \${device} \${partition} \${kernel_addr_r} \${bpi}/\${board}/\${service}/\${kernel}
		aload_initrd=fatload \${device} \${partition} \${ramdisk_addr_r} \${bpi}/\${board}/\${service}/\${initrd}
		aboot=bootm \${kernel_addr_r} \${ramdisk_addr_r} \${fdt_addr_r}
		uenvcmd=run ahello setroot setbootdev abootargs aload_dtb aload_kernel aload_initrd aboot
		usercmd=run ahello setroot setbootdev abootargs
	EOF
}
