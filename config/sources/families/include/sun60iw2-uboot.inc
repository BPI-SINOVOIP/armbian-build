# SPDX-License-Identifier: GPL-2.0
# sun60iw2 boot0 / boot_package packing and installation.

function post_family_config__sun60iw2_pack_hash() {
	# sys_config contains the DRAM tables consumed by boot0. Hash ALL pack
	# inputs, not just the monitor's version string, so changing these tables
	# cannot reuse a bootloader package prepared with an earlier config.
	local hash_files
	calculate_hash_for_all_files_in_dirs \
		"${SRC}/packages/pack-uboot/sun60iw2/bin" \
		"${SRC}/packages/pack-uboot/tools"
	declare -g BL31_BLOB="sun60iw2-pack-${hash_files}"
}

function fetch_sources_tools__sun60iw2_toolchain() {
	local name="gcc-linaro-7.2.1-2017.11-x86_64_arm-linux-gnueabi"
	local archive="${SRC}/cache/toolchain/${name}.tar.xz"
	local expected="89e9bfc7ffe615f40a72c2492df0488f25fc20404e5f474501c8d55941337f71"
	local url="https://github.com/BigQubot/sunxi-bsp-toolchains/releases/download/1.0.0/${name}.tar.xz"
	run_host_command_logged mkdir -p "${SRC}/cache/toolchain"
	if [[ ! -s "${archive}" ]]; then
		run_host_command_logged curl --fail --location --retry 3 -o "${archive}.download" "${url}"
		run_host_command_logged mv "${archive}.download" "${archive}"
	fi
	[[ "$(sha256sum "${archive}" | cut -d ' ' -f1)" == "${expected}" ]] || \
		exit_with_error "Bootloader toolchain checksum mismatch" "${archive}"
	if [[ ! -x "${UBOOT_COMPILER}gcc" ]]; then
		run_host_command_logged tar -xf "${archive}" -C "${SRC}/cache/toolchain"
	fi
}

function uboot_make_config__sun60iw2_dtc() {
	uboot_make_envs+=("DTC=dtc")
}

function post_uboot_custom_postprocess__sun60iw2_pack() {
	local assets="${SRC}/packages/pack-uboot/sun60iw2/bin"
	local tools="${SRC}/packages/pack-uboot/tools"
	local board_dtb="${PWD}/arch/arm/dts/sun60i-a733-bananapi-m8.dtb"
	local sys_config="${assets}/sys_config/sys_config_${BOARD}.fex"
	local output="${PWD}"
	[[ -s "${board_dtb}" && -s "${sys_config}" && -s "${output}/u-boot.bin" ]] || \
		exit_with_error "Missing board DTB or boot0 configuration" "${board_dtb} ${sys_config}"
	local medium input
	for input in monitor.fex scp.fex boot_package.cfg; do
		[[ -s "${assets}/${input}" ]] || exit_with_error "Missing boot package input" "${input}"
	done
	for input in script update_boot0 update_dtb update_uboot dragonsecboot dtc; do
		[[ -x "${tools}/${input}" ]] || exit_with_error "Missing executable pack tool" "${input}"
	done
	for medium in sdcard emmc nand spinor ufs; do
		[[ -s "${assets}/boot0_${medium}_${BOARD}.fex" ]] || \
			exit_with_error "Missing board boot0" "${medium}"
	done

	# Isolate generated files from the U-Boot source tree and cached pack data.
	local stage
	stage="$(mktemp -d "${PWD}/.sun60iw2-pack.XXXXXX")" || exit_with_error "Cannot create boot package stage"
	(
		cd "${stage}" || exit 1
		cp "${sys_config}" sys_config.fex || exit 1
		cp "${output}/u-boot.bin" u-boot.fex || exit 1
		cp "${board_dtb}" sunxi.fex || exit 1
		cp "${assets}/monitor.fex" "${assets}/scp.fex" "${assets}/boot_package.cfg" . || exit 1
		for medium in sdcard emmc nand spinor ufs; do
			cp "${assets}/boot0_${medium}_${BOARD}.fex" "boot0_${medium}.fex" || exit 1
		done
		run_host_command_logged busybox unix2dos sys_config.fex || exit 1
		run_host_command_logged "${tools}/script" sys_config.fex || exit 1
		[[ -s sys_config.bin ]] || exit 1
		run_host_command_logged "${tools}/update_boot0" boot0_sdcard.fex sys_config.bin SDMMC_CARD || exit 1
		run_host_command_logged "${tools}/update_boot0" boot0_emmc.fex sys_config.bin SDMMC_CARD || exit 1
		run_host_command_logged "${tools}/update_boot0" boot0_nand.fex sys_config.bin NAND || exit 1
		run_host_command_logged "${tools}/update_boot0" boot0_spinor.fex sys_config.bin SPINOR_FLASH || exit 1
		run_host_command_logged "${tools}/update_boot0" boot0_ufs.fex sys_config.bin UFS || exit 1
		run_host_command_logged "${tools}/update_dtb" sunxi.fex 4096 || exit 1
		run_host_command_logged "${tools}/update_uboot" -no_merge u-boot.fex sys_config.bin || exit 1
		run_host_command_logged busybox unix2dos boot_package.cfg || exit 1
		run_host_command_logged "${tools}/dragonsecboot" -pack boot_package.cfg || exit 1
		[[ -s boot_package.fex ]] || exit 1
		for medium in sdcard emmc nand spinor ufs; do
			[[ -s "boot0_${medium}.fex" ]] || exit 1
		done
	) || exit_with_error "Boot package creation failed; staging retained" "${stage}"
	for medium in sdcard emmc nand spinor ufs; do
		run_host_command_logged cp "${stage}/boot0_${medium}.fex" "${output}/boot0_${medium}.fex" || \
			exit_with_error "Cannot install packed boot0; staging retained" "${stage}"
	done
	run_host_command_logged cp "${stage}/boot_package.fex" "${output}/boot_package.fex" || \
		exit_with_error "Cannot install boot package; staging retained" "${stage}"
	run_host_command_logged mkdir -p "${output}/dts" || exit_with_error "Cannot create DT output directory"
	run_host_command_logged "${tools}/dtc" -I dtb -O dts -o "${output}/dts/${BOARD}-u-boot.dts" "${board_dtb}" || \
		exit_with_error "Cannot export board DT; staging retained" "${stage}"
	# stage is an exact mktemp-created directory beneath this build worktree.
	run_host_command_logged rm -rf "${stage}"
}

function write_uboot_platform() {
	# All offsets are 512-byte sectors. The first filesystem starts at 32 MiB.
	[[ -s "$1/boot0_sdcard.fex" && -s "$1/boot_package.fex" ]] || return 1
	local package_bytes boot0_bytes
	boot0_bytes=$(stat -c %s "$1/boot0_sdcard.fex") || return 1
	package_bytes=$(stat -c %s "$1/boot_package.fex") || return 1
	(( boot0_bytes <= (24576 - 16) * 512 )) || return 1
	# Backup at 12 MiB must not overlap the primary at sector 32800.
	(( package_bytes <= (32800 - 24576) * 512 )) || return 1

	# Check the on-disk table before ANY write, including when updating an older
	# installation. Both the image builder and installer create the table first.
	local layout
	layout=$(LC_ALL=C parted --machine --script "$2" unit B print) || {
		echo "Cannot read partition layout on $2; bootloader was not written." >&2
		return 1
	}
	local -a layout_lines starts=(8192 12582912 16793600)
	local -a lengths=("${boot0_bytes}" "${package_bytes}" "${package_bytes}")
	mapfile -t layout_lines <<< "${layout}"
	local disk_path disk_bytes disk_type logical_sector physical_sector table rest
	IFS=: read -r disk_path disk_bytes disk_type logical_sector physical_sector table rest <<< "${layout_lines[1]:-}"
	# boot0 at 8 KiB would also overwrite a standard GPT's partition entries.
	if [[ ${layout_lines[0]:-} != 'BYT;' || ${table} != msdos || ${logical_sector} != 512 || ! ${disk_bytes} =~ ^[1-9][0-9]*B$ ]]; then
		echo "Unsupported partition layout on $2 (requires a 512-byte-sector MBR); bootloader was not written." >&2
		return 1
	fi
	disk_bytes=${disk_bytes%B}
	local line number start end size region partitions=0
	for region in "${!starts[@]}"; do
		(( starts[region] + lengths[region] <= disk_bytes )) || return 1
	done
	for line in "${layout_lines[@]:2}"; do
		IFS=: read -r number start end size rest <<< "${line}"
		if [[ ! ${number} =~ ^[1-9][0-9]*$ || ! ${start} =~ ^[0-9]+B$ || ! ${end} =~ ^[0-9]+B$ ]]; then
			echo "Invalid partition boundary on $2; bootloader was not written." >&2
			return 1
		fi
		start=${start%B}
		end=${end%B}
		(( end >= start && end < disk_bytes )) || return 1
		for region in "${!starts[@]}"; do
			if (( starts[region] <= end && starts[region] + lengths[region] > start )); then
				echo "Bootloader overlaps partition ${number} on $2; reinstall with partitions starting at 32 MiB or later. Nothing was written." >&2
				return 1
			fi
		done
		partitions=$((partitions + 1))
	done
	if (( partitions == 0 )); then
		echo "No partitions found on $2; create the 32 MiB reserved layout before writing the bootloader." >&2
		return 1
	fi
	dd if="$1/boot0_sdcard.fex" of="$2" bs=512 seek=16 conv=fsync,notrunc status=none || return 1
	dd if="$1/boot_package.fex" of="$2" bs=512 seek=32800 conv=fsync,notrunc status=none || return 1
	dd if="$1/boot_package.fex" of="$2" bs=512 seek=24576 conv=fsync,notrunc status=none
}

function write_uboot_platform_mtd() {
	[[ -c /dev/mtd0 && -b /dev/mtdblock0 ]] || return 1
	[[ -s "$1/boot0_spinor.fex" && -s "$1/boot_package.fex" ]] || return 1
	local boot0_size package_size erase_size device_size erase_blocks value
	boot0_size=$(stat -c %s "$1/boot0_spinor.fex") || return 1
	package_size=$(stat -c %s "$1/boot_package.fex") || return 1
	erase_size=$(cat /sys/class/mtd/mtd0/erasesize) || return 1
	device_size=$(cat /sys/class/mtd/mtd0/size) || return 1
	for value in "${boot0_size}" "${package_size}" "${erase_size}" "${device_size}"; do
		[[ "${value}" =~ ^[1-9][0-9]*$ ]] || return 1
	done
	# Preserve the reserved flash layout beyond the boot package region.
	(( boot0_size <= 262144 && package_size <= 3014656 )) || return 1
	erase_blocks=$(( (262144 + package_size + erase_size - 1) / erase_size ))
	(( erase_blocks * erase_size <= device_size && erase_blocks * erase_size <= 3276800 )) || return 1
	flash_erase /dev/mtd0 0 "${erase_blocks}" || return 1
	mtd_debug write /dev/mtd0 0 "${boot0_size}" "$1/boot0_spinor.fex" || return 1
	mtd_debug write /dev/mtd0 262144 "${package_size}" "$1/boot_package.fex" || return 1
	sync
}

function write_uboot_platform_ufs() {
	[[ -s "$1/boot0_ufs.fex" && -s "$1/boot_package.fex" ]] || return 1
	local boot0_bytes package_bytes
	boot0_bytes=$(stat -c %s "$1/boot0_ufs.fex") || return 1
	package_bytes=$(stat -c %s "$1/boot_package.fex") || return 1
	(( boot0_bytes <= (32800 - 16) * 512 && package_bytes <= (65536 - 32800) * 512 )) || return 1
	dd if="$1/boot0_ufs.fex" of="$2" bs=512 seek=16 conv=fsync,notrunc status=none || return 1
	dd if="$1/boot_package.fex" of="$2" bs=512 seek=32800 conv=fsync,notrunc status=none
}
