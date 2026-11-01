# SPDX-License-Identifier: GPL-2.0
# Kernel integration for the in-tree sun60iw2 BSP.

function post_family_config__sun60iw2_kernel_policy() {
	# BTF auto-enables ftrace in the framework. This BSP cannot safely use
	# dynamic text patching; reject an override instead of silently changing it.
	[[ "${KERNEL_BTF:-no}" != "yes" ]] || exit_with_error "sun60iw2 does not support KERNEL_BTF=yes"
	declare -g KERNEL_BTF="no"
}

function kernel_make_config__sun60iw2_bsp() {
	[[ -f bsp/Kconfig && -f bsp/Makefile ]] || exit_with_error "Missing in-tree sun60iw2 BSP" "${kernel_work_dir}"
	common_make_params_quoted+=("BSP_TOP=bsp/" "LICHEE_KERN_DIR=${kernel_work_dir}" "KERNEL_SRC=${kernel_work_dir}")
}

function post_kernel_config__sun60iw2_validate_config() {
	local option
	for option in ARCH_SUN60I ARCH_SUN60IW2 ARCH_SUN60IW2P1 AW_IC_BOARD BOARD_BANANAPI_M8 ARM64_LSE_ATOMICS ARM64_USE_LSE_ATOMICS; do
		grep -qx "CONFIG_${option}=y" .config || exit_with_error "Missing required kernel option" "CONFIG_${option}"
	done
	grep -qx '# CONFIG_FTRACE is not set' .config || exit_with_error "FTRACE must remain disabled on sun60iw2"
}

function pre_package_kernel_image__sun60iw2_gpu_module() {
	local builder="${kernel_work_dir}/bsp/scripts/build-img-bxm-pvrsrvkm.sh"
	local mod_path="${tmp_kernel_install_dirs[INSTALL_MOD_PATH]}"
	[[ -x "${builder}" ]] || exit_with_error "Missing GPU module builder" "${builder}"
	run_host_command_logged env ARCH=arm64 KERNEL_ARCH=arm64 \
		KERNELDIR="${kernel_work_dir}" INSTALL_MOD_PATH="${mod_path}" \
		KERNEL_VERSION="${kernel_version_family}" \
		KERNEL_CROSS_COMPILE="${KERNEL_COMPILER}" CROSS_COMPILE="${KERNEL_COMPILER}" \
		GPU_BUILD_TYPE=release PVR_OUT="${mod_path}/../pvr-bxm-build" "${builder}"
	[[ -f "${mod_path}/lib/modules/${kernel_version_family}/extra/pvrsrvkm.ko" ]] || \
		exit_with_error "GPU module was not installed" "${kernel_version_family}"
}
