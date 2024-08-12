#
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2013-2023 Igor Pecovnik, igor@armbian.com
#
# This file is a part of the Armbian Build Framework
# https://github.com/armbian/build/
#
declare -g SERIALCON="${SERIALCON:-tty1}"            # Consistent serial console device unlikely on UEFI arch pair. Customize if otherwise.
declare -g SKIP_BOOTSPLASH="yes"                     # No splash.
declare -g UEFI_GRUB_TIMEOUT=${UEFI_GRUB_TIMEOUT:-3} # Default 3-seconds timeout for GRUB menu.
declare -g INSTALL_ARMBIAN_FIRMWARE="no"            # Install full firmware for UEFI boards
declare -g BOARD_FIRMWARE_INSTALL="-full"            # Install full firmware for UEFI boards
declare -g DISTRO_GENERIC_KERNEL=no
SKIP_ARMBIAN_REPO=yes


#[[ $ATF_COMPILE != "no" && -z $ATF_COMPILER ]] && ATF_COMPILER="riscv64-linux-gnu-"
[[ $ATF_COMPILE != "no" && -z $ATF_COMPILER ]] && ATF_COMPILER="riscv64-unknown-linux-gnu-"
[[ $ATF_COMPILE != "no" && -z $ATFSOURCE ]] && ATFSOURCE='https://github.com/BPI-SINOVOIP/pi-opensbi'
[[ $ATF_COMPILE != "no" && -z $ATFDIR ]] && ATFDIR='opensbi'
[[ $ATF_COMPILE != "no" && -z $ATFBRANCH ]] && ATFBRANCH='branch:v1.3-k1'
[[ $ATF_COMPILE != "no" && -z $ATF_USE_GCC ]] && ATF_USE_GCC='> 8.0'

KERNEL_IMAGE_TYPE="Image"
SRC_LOADADDR='LOADADDR=0x1400000'
NAME_KERNEL=Image

UBOOT_COMPILER="riscv64-unknown-linux-gnu-"
KERNEL_COMPILER="riscv64-unknown-linux-gnu-"
BOOTSOURCE='https://github.com/BPI-SINOVOIP/pi-u-boot.git'
BOOTBRANCH='branch:v2022.10-k1'
BOOTCONFIG="k1_defconfig"
BOOTDIR='u-boot-k1'
BOOTPATCHDIR='legacy'
BOOTSCRIPT='boot-k1.cmd:boot.cmd'
BOOTENV_FILE='env_k1-x.txt'
UBOOT_TARGET_MAP=';;u-boot.bin FSBL.bin u-boot.itb fw_dynamic.itb'
ATF_TARGET_MAP='PLATFORM_DEFCONFIG=k1_defconfig PLATFORM=generic ;;build/platform/generic/firmware/fw_dynamic.itb build/platform/generic/firmware/fw_dynamic.bin'

case "${BRANCH}" in

	ddk)
		# This will force `KERNELSOURCE='none'` later; no kernel will be built.
		# Instead, the distro's default linux-generic kernel will be installed.
		# rpardini: For some regrettable reason, most of this logic is in the grub extension.
		declare -g DISTRO_GENERIC_KERNEL=yes
		;;

	legacy)

		declare -g DISTRO_GENERIC_KERNEL=no
		declare -g LINUXCONFIG="linux-${LINUXFAMILY}-${BRANCH}"
		declare -g KERNEL_MAJOR_MINOR="6.1"                      # Major and minor versions of this kernel. For mainline caching.
		declare -g KERNELSOURCE='https://github.com/BPI-SINOVOIP/pi-linux.git'
		declare -g KERNELBRANCH="branch:linux-6.1.15-k1"             # Branch or tag to build from. It should match MAJOR_MINOR
		declare -g KERNELPATCHDIR="legacy" # Might be empty.
		;;

	current)

		#
		ATFBRANCH='branch:v1.3-k1-dev'
		BOOTBRANCH='branch:v2022.10-k1-dev'
		#
		declare -g DISTRO_GENERIC_KERNEL=no
		declare -g LINUXCONFIG="linux-${LINUXFAMILY}-${BRANCH}"
		declare -g KERNEL_MAJOR_MINOR="6.6"                       # Major and minor versions of this kernel. For mainline caching.
		declare -g KERNELSOURCE='https://github.com/BPI-SINOVOIP/pi-linux.git'
		declare -g KERNELBRANCH="branch:linux-6.6.36-k1"              # Branch or tag to build from. It should match MAJOR_MINOR
		#declare -g KERNELPATCHDIR="uefi-${LINUXFAMILY}-${BRANCH}" # Might be empty.
		declare -g KERNELPATCHDIR="legacy" # Might be empty.
		;;

	edge)

		declare -g DISTRO_GENERIC_KERNEL=no
		declare -g LINUXCONFIG="linux-uefi-${LINUXFAMILY}-${BRANCH}"
		declare -g KERNEL_MAJOR_MINOR="6.8" # Major and minor versions of this kernel.

		declare -g KERNELBRANCH='branch:master'
		declare -g KERNELPATCHDIR="uefi-${LINUXFAMILY}-${BRANCH}" # Might be empty.
		;;
esac

if [[ "${QEMU_UBOOT_BOOTCONFIG}" != "" ]]; then
	# Allowance for using the UEFI kernels, but not an UEFI bootloader, instead, use qemu with uboot.
	# Used in the qemu-uboot-x86 and qemu-uboot-arm64 "boards".
	display_alert "Using UEFI as base for qemu-uboot-${LINUXFAMILY}" "Using BOOTCONFIG: ${QEMU_UBOOT_BOOTCONFIG}" "debug"
	declare -g ATF_COMPILE="no"

	declare -g BOOTDIR="qemu-uboot-${LINUXFAMILY}"
	declare -g BOOTBRANCH='tag:v2023.10'
	declare -g BOOTSOURCE='https://github.com/u-boot/u-boot' # Gotta set this again, it is unset by grub extension

	declare -g BOOTCONFIG="${QEMU_UBOOT_BOOTCONFIG}"
	add_packages_to_image spice-vdagent qemu-guest-agent # Nice-to-have packages for running under qemu

	# A patch to convert to `q35` machine type DTS (x86 only)
	declare -g BOOTPATCHDIR="u-boot-qemu-${LINUXFAMILY}"

	# Config for the qcow2 image
	declare -g QCOW2_RESIZE_AMOUNT="+50G" # resize the qcow2 image to be 50G bigger
	declare -g QCOW2_KEEP_IMG="no"        # produce only the .qcow2 image, not raw .img

	# Enable the qcow2 output extension.
	enable_extension "image-output-qcow2"

	write_uboot_platform() {
		echo "Not writing uboot for qemu-uboot-xxxx platform"
	}

fi


function pre_install_distribution_specific__add_spacemit_packages() {
	# If we're skipping the Armbian repo, and on Debian, we'll install Debian version of Wifi/BT-related firmware.
	if [[ "${SKIP_ARMBIAN_REPO}" == "yes" ]]; then
		display_alert "SKIP_ARMBIAN_REPO is set to" "${SKIP_ARMBIAN_REPO} - for Armbian" "info"
		if [[ "${DISTRIBUTION}" == "Debian" ]]; then
			display_alert "Installing Debian-sourced vim" "Debian::bspacemiT::${BOARD}" "info"
			do_with_retries 3 chroot_sdcard_apt_get_update
			chroot_sdcard_apt_get_install vim
		fi
		if [[ "${DISTRIBUTION}" == "Ubuntu" ]]; then
			display_alert "Installing Ubuntu-sourced vim" "Ubuntu::spacemiT::${BOARD}" "info"
			if [[ ${BUILD_DESKTOP} == "yes" ]] && [[ ${DESKTOP_ENVIRONMENT} = "gnome" ]]; then
				display_alert "BUILD_DESKTOP=y ${DESKTOP_ENVIRONMENT}" "Ubuntu::spacemiT::${BOARD}" "info"
				if [[ "${RELEASE}" == "mantic" ]]; then
				display_alert "Installing mantic packages" "Ubuntu::spacemiT::${BOARD}" "info"
	echo "BPI: pre_install_distribution_specific__add_spacemit_packages --- START ---"
	IN=$SRC/packages/bsp/bananapif3/hw/hw.tgz
	MNTDIR=$SDCARD
	  echo "tar xvf $IN --keep-directory-symlink -C $MNTDIR"
	  tar xvf $IN --keep-directory-symlink -C $MNTDIR
	echo "BPI: pre_install_distribution_specific__add_spacemit_packages ---  END ---"
				echo "BPI:chroot_sdcard find /etc/apt"
				chroot_sdcard find /etc/apt
				#chroot_sdcard cp -a /etc/apt/keyrings/bianbu-archive-keyring.gpg /etc/apt/trusted.gpg.d
				echo chroot_sdcard rm -f /etc/apt/sources.list.d/armbian*
				chroot_sdcard rm -f /etc/apt/sources.list.d/armbian*
				chroot_sdcard find /etc/apt
				do_with_retries 3 chroot_sdcard_apt_get_update
				chroot_sdcard apt-mark hold libc6 libc-bin
				chroot_sdcard apt-get update
				chroot_sdcard apt-mark showhold

				#chroot_sdcard apt-get --allow-downgrades -y install initramfs-tools
				chroot_sdcard apt-get --allow-downgrades -y install vim bianbu-esos img-gpu-powervr k1x-vpu-firmware k1x-cam spacemit-uart-bt spacemit-modules-usrload

				#chroot_sdcard_apt_get_install vim bianbu-esos img-gpu-powervr k1x-vpu-firmware k1x-cam spacemit-uart-bt spacemit-modules-usrload
				chroot_sdcard apt-get --allow-downgrades -y upgrade
				chroot_sdcard apt-get --allow-downgrades -y install gstreamer1.0-plugins-bad chromium-browser-stable ffmpegthumbnailer glmark2-es2-wayland
				fi
			fi
		fi
	else
		## disable armbian repository
		echo mv "${SDCARD}"/etc/apt/sources.list.d/armbian.list "${SDCARD}"/etc/apt/sources.list.d/armbian.list.disabled
	fi
}

function family_tweaks_bsp() {
	echo "BPI: family_tweaks_bsp --- START ---"
	for IN in `ls $SRC/packages/bsp/bananapif3/*.tgz` ; do
	  MNTDIR=$destination

	  echo "tar xf $IN --keep-directory-symlink -C $MNTDIR"
	  tar xf $IN --keep-directory-symlink -C $MNTDIR
	done
	ldconfig
	echo "BPI: family_tweaks_bsp ---  END  ---"
}

function fetch_sources_tools__bananapi_f3_download_toolchain() {
	display_alert "$BOARD" "preparing bananapi-f3 toolchain" "info"

	if [[ "${HOSTARCH}" != "amd64" ]]; then
		exit_with_error "bananapi-f3 requires amd64 system to build"
	fi

	declare tversion="13.2"
	declare tbasedir="${SRC}/cache/toolchain"
	declare tdir="${tbasedir}/spacemit-toolchain-linux-glibc-x86_64-13.2-2024.04"
	declare tdir2="${tbasedir}/spacemit-toolchain-linux-glibc-x86_64-v1.0.0"
	#declare tfile="spacemit-toolchain-linux-glibc-x86_64-13.2-2024.03.tar.xz"
	declare tfile="spacemit-toolchain-linux-glibc-x86_64-v1.0.0.tar.xz"
	#declare turl="https://dl.armbian.com/_toolchain/${tfile}"
	declare turl="http://archive.spacemit.com/toolchain/${tfile}"

	if [[ ! -d "${tdir}" ]]; then
		run_host_command_logged wget --progress=dot:giga -O "${tbasedir}/${tfile}" "${turl}"
		run_host_command_logged tar -C "${tbasedir}" -xf "${tbasedir}/${tfile}"
		run_host_command_logged mv "${tdir2} ${tdir}"
		#run_host_command_logged rm -fv "${tbasedir}/${tfile}"
	fi
	run_host_command_logged pip install pyyaml

	# Symlinks are not enough: riscv-none-embed-ld: cannot find -lgcc, So we need to add the toolchain to the PATH
	export PATH="${tdir}/bin:${PATH}"
	echo PATH=$PATH

	declare -g -r PRE_TOOLCHAIN_PATH="${PATH}"
}
