#
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2013-2023 Igor Pecovnik, igor@armbian.com
#
# This file is a part of the Armbian Build Framework
# https://github.com/armbian/build/
#
UBOOT_TARGET_MAP=";;dts/${BOARD}-u-boot.dts boot0_sdcard.fex boot_package.fex"
LINUXFAMILY=renesas
ARCH=arm64
SERIALCON=ttySC0

function add_host_dependencies__renesas_bpi_hostdeb() {
	display_alert "$BOARD" "adding legacy build host deps" "info"

	declare -g EXTRA_BUILD_DEPS="${EXTRA_BUILD_DEPS} libssl-dev"
}

function extension_finish_config__renesas_bpi_kernelconfig() {
	display_alert "$BOARD" "Setting kernel config" "info"

        declare -g LINUXSOURCEDIR="linux-kernel-worktree/${KERNELDIR}"
}

function write_uboot_platform()
{
	display_alert "$BOARD" "Writing uboot to image" "info"

	dd if=$1/bl2_bp_sd.bin of=$2 bs=512 seek=1 conv=fsync
	dd if=$1/fip.bin of=$2 bs=512 seek=768 conv=fsync
}

family_tweaks_bsp() {
	mkdir -p $destination/etc/udev/rules.d
	mkdir -p $destination/usr/local/bin

	mkdir -p "$destination"/etc/X11/xorg.conf.d
	case "${BOARD}" in
		"bpi-ai2n"|"bpi-ai2h")
	cat <<-EOF > "$destination"/etc/X11/xorg.conf
	Section "Device"
	    Identifier  "Display Fbdev"
	    
	    Driver "fbdev"
	EndSection
       
	Section "Screen"
	    Identifier "Default Screen"
	    SubSection "Display"
	        Depth 24
	    EndSubSection
	EndSection
	EOF
        ;;
	esac
}

function custom_apt_repo__renesas_bpi_release_apt_list()
{
	display_alert "${BOARD}" "Restoring apt sources.list" "info"

	declare distro=""
 
	# restore apt sources.list
	if [[ "${DOWNLOAD_MIRROR}" == "ustc" && "${BUILD_TYPE}" == "release" ]]; then
		DEBIAN_MIRROR='deb.debian.org/debian'
		DEBIAN_SECURTY='security.debian.org/'
		UBUNTU_MIRROR='ports.ubuntu.com/'
		ARMBIAN_MIRROR='apt.armbian.com'
		if [[ $BETA == "yes" ]]; then
			ARMBIAN_MIRROR='beta.armbian.com'
		fi

		# Add upstream (Debian/Ubuntu) APT repository
		case ${RELEASE} in
			buster | bullseye | bookworm | trixie)
				distro="debian"

				declare -a suites=("${RELEASE}" "${RELEASE}-updates")
				declare -a security_suites=("${RELEASE}-security")
				declare -a components=(main contrib non-free)

				if [[ "$RELEASE" == "buster" ]]; then
					security_suites=("${RELEASE}/updates")
				else
				  suites+=("${RELEASE}-backports")
				fi

				if [[ "$RELEASE" != "buster" && "$RELEASE" != "bullseye" ]]; then
				  components+=("non-free-firmware")
				fi

				cat <<- EOF > "${SDCARD}/etc/apt/sources.list.d/${distro}.sources"
				Types: deb
				URIs: http://${DEBIAN_MIRROR}
				Suites: ${suites[@]}
				Components: ${components[@]}
				Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

				Types: deb
				URIs: http://${DEBIAN_SECURTY}
				Suites: ${security_suites[@]}
				Components: ${components[@]}
				Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
				EOF
				;;

			sid | unstable)
				distro="debian"

				# sid is permanent unstable development and has no such thing as updates or security
				cat <<- EOF > "${SDCARD}/etc/apt/sources.list.d/${distro}.sources"
				Types: deb
				URIs: http://${DEBIAN_MIRROR}
				Suites: ${release}
				Components: main contrib non-free non-free-firmware
				Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
				EOF

				# Required for some packages on riscv64.
				# See: http://lists.debian.org/debian-riscv/2023/07/msg00053.html
				if [[ "${ARCH}" == riscv64 ]]; then
					cat <<- EOF >> "${SDCARD}/etc/apt/sources.list.d/${distro}.sources"

					Types: deb
					URIs: http://deb.debian.org/debian-ports/
					Suites: ${RELEASE}
					Components: main
					Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
					Architectures: riscv64
					EOF
				fi
				;;

			focal | jammy | noble | oracular | plucky)
				distro="ubuntu"

				cat <<- EOF > "${SDCARD}/etc/apt/sources.list.d/${distro}.sources"
				Types: deb
				URIs: http://${UBUNTU_MIRROR}
				Suites: ${RELEASE} ${RELEASE}-security ${RELEASE}-updates ${RELEASE}-backports
				Components: main restricted universe multiverse
				Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
				EOF
				;;
		esac

		if [[ "${SKIP_ARMBIAN_REPO}" == "yes" ]]; then
			# remove armbian.sources
			if [[ -e "${SDCARD}"/etc/apt/sources.list.d/armbian.sources ]]; then
				display_alert "${BOARD}" "Disabling Armbian repo" "info"
				mv "${SDCARD}"/etc/apt/sources.list.d/armbian.sources "${SDCARD}"/etc/apt/sources.list.d/armbian.sources.disabled
			fi
		else
			cat <<- EOF > "${SDCARD}/etc/apt/sources.list.d/armbian.sources"
			Types: deb
			URIs: https://${ARMBIAN_MIRROR}
			Suites: $RELEASE
			Components: ${components[*]}
			Signed-By: ${APT_SIGNING_KEY_FILE}
			EOF
		fi
	fi
}

function post_repo_customize_image__renesas_bpi_install_armbian-config() {
	display_alert "${BOARD}" "repo customize image" "info"

	if [[ "${SKIP_ARMBIAN_REPO}" == "yes" ]]; then
		chroot_sdcard_apt_get_install "armbian-config"
	fi
}

